"""Core forwarding service.

Connects the authenticated user session to Telegram, watches Source
Channel A for new media, and server-side forwards it to Destination
Channel B — without downloading or re-uploading file bytes.

Design note: MTProto's forwardMessages call must be issued by an account
that can see both the source and destination chats. Since the same user
account is already a member of Channel A and can post in Channel B, a
single Telethon client handles both reading and forwarding. This is what
makes true server-side forwarding possible; a separate bot account could
only post to Channel B by downloading and re-uploading content via the
Bot API, which is exactly what this service is designed to avoid.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Iterable, List, Optional

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.custom.message import Message

from .config import Config, ConfigError
from .storage import Storage

logger = logging.getLogger("forwarder")


def _chat_id(entity) -> int:
    """Normalize any resolved Telethon entity to its plain chat id."""
    return entity.id if hasattr(entity, "id") else int(entity)


class AlbumBuffer:
    """Buffers messages that share a Telegram `grouped_id` (i.e. an
    album / multi-file post) so they can be forwarded together in a
    single server-side call. This preserves the album grouping in
    Channel B instead of splitting it into separate individual posts.

    Live events arrive one message at a time with no explicit "album
    complete" signal, so each new message for a group resets a short
    debounce timer; the group is flushed once no new message for it
    arrives within `flush_delay` seconds.
    """

    def __init__(self, flush_delay: float, on_flush):
        self._flush_delay = flush_delay
        self._on_flush = on_flush
        self._groups: dict[int, List[Message]] = {}
        self._tasks: dict[int, asyncio.Task] = {}

    def add(self, message: Message) -> None:
        gid = message.grouped_id
        self._groups.setdefault(gid, []).append(message)

        existing = self._tasks.get(gid)
        if existing and not existing.done():
            existing.cancel()
        self._tasks[gid] = asyncio.create_task(self._flush_after_delay(gid))

    async def _flush_after_delay(self, gid: int) -> None:
        try:
            await asyncio.sleep(self._flush_delay)
        except asyncio.CancelledError:
            return
        messages = sorted(self._groups.pop(gid, []), key=lambda m: m.id)
        self._tasks.pop(gid, None)
        if messages:
            await self._on_flush(messages)

    async def flush_all(self) -> None:
        """Force-flush any pending groups immediately (used on shutdown)."""
        for gid in list(self._groups.keys()):
            task = self._tasks.pop(gid, None)
            if task:
                task.cancel()
            messages = sorted(self._groups.pop(gid, []), key=lambda m: m.id)
            if messages:
                await self._on_flush(messages)


class ForwarderService:
    def __init__(self, client: TelegramClient, config: Config, storage: Storage):
        self.client = client
        self.config = config
        self.storage = storage

        self.source_entity = None
        self.dest_entity = None
        self.source_id: int = 0

        self._album_buffer = AlbumBuffer(config.album_flush_delay, self._forward_batch)

    async def start(self) -> None:
        await self.client.start()
        me = await self.client.get_me()
        logger.info("Logged in as %s (id=%s)", getattr(me, "first_name", ""), me.id)

        # Populate the session's entity cache (access hashes) so private
        # channels — which have no @username — can be resolved by numeric
        # ID below. The account is already a member, so this is enough.
        await self.client.get_dialogs()

        self.source_entity = await self._resolve(self.config.source_channel, "SOURCE_CHANNEL_ID")
        self.dest_entity = await self._resolve(self.config.dest_channel, "DEST_CHANNEL_ID")
        self.source_id = _chat_id(self.source_entity)

        logger.info(
            "Source channel resolved: %s (id=%s)",
            getattr(self.source_entity, "title", self.config.source_channel),
            self.source_id,
        )
        logger.info(
            "Destination channel resolved: %s (id=%s)",
            getattr(self.dest_entity, "title", self.config.dest_channel),
            _chat_id(self.dest_entity),
        )

        await self._catch_up()

        self.client.add_event_handler(
            self._on_new_message, events.NewMessage(chats=self.source_entity)
        )
        logger.info("Listening for new messages on Source Channel A...")

    async def _resolve(self, channel_ref, env_name: str):
        try:
            return await self.client.get_entity(channel_ref)
        except (ValueError, RPCError) as exc:
            raise ConfigError(
                f"Could not resolve {env_name}={channel_ref!r}. Make sure the "
                "authenticated account is a member of this channel (or can see "
                "it), and that the ID is correct. Run scripts/list_channels.py "
                "to list the chats visible to this account with their IDs."
            ) from exc

    # -- Catch-up (runs once at startup) ------------------------------------

    async def _catch_up(self) -> None:
        """Forward anything posted while the service was offline, resuming
        from the last processed message id stored in SQLite.

        On a brand-new install (no prior state at all), only the current
        tip of the channel is recorded as the baseline — new messages from
        that point on are forwarded — unless FORWARD_EXISTING_ON_FIRST_RUN
        is enabled, in which case the existing backlog is forwarded too.
        """
        last_id = self.storage.get_last_processed_id(self.source_id)

        if last_id is None:
            if not self.config.forward_existing_on_first_run:
                latest = await self.client.get_messages(self.source_entity, limit=1)
                baseline = latest[0].id if latest else 0
                self.storage.set_last_processed_id(self.source_id, baseline)
                logger.info(
                    "First run: baseline set to message id=%s. "
                    "Only messages newer than this will be forwarded.",
                    baseline,
                )
                return
            last_id = 0
            logger.info("First run: forwarding existing backlog from Source Channel A...")
        else:
            logger.info("Catching up on messages after id=%s...", last_id)

        total_scanned = 0
        while True:
            batch: List[Message] = []
            async for message in self.client.iter_messages(
                self.source_entity,
                min_id=last_id,
                reverse=True,
                limit=self.config.catch_up_limit,
            ):
                batch.append(message)

            if not batch:
                break

            for group in self._group_consecutive(batch):
                await self._process_group(group)

            last_id = batch[-1].id
            total_scanned += len(batch)

            if len(batch) < self.config.catch_up_limit:
                break

        if total_scanned:
            logger.info("Catch-up complete (%d messages scanned).", total_scanned)

    @staticmethod
    def _group_consecutive(messages: Iterable[Message]) -> Iterable[List[Message]]:
        """Group a chronologically-ordered message list by `grouped_id`
        while preserving order, so catch-up forwards albums as a unit too.
        """
        batch: List[Message] = []
        current_gid: object = object()
        for msg in messages:
            gid = msg.grouped_id if msg.grouped_id is not None else object()
            if batch and gid != current_gid:
                yield batch
                batch = []
            batch.append(msg)
            current_gid = gid
        if batch:
            yield batch

    # -- Live event handling --------------------------------------------------

    async def _on_new_message(self, event: events.NewMessage.Event) -> None:
        message: Message = event.message
        if not message.media:
            self.storage.set_last_processed_id(self.source_id, message.id)
            return

        if message.grouped_id:
            self._album_buffer.add(message)
        else:
            await self._process_group([message])

    # -- Shared processing ------------------------------------------------

    async def _process_group(self, messages: List[Message]) -> None:
        media_messages = [m for m in messages if m.media]

        if not media_messages:
            for m in messages:
                self.storage.set_last_processed_id(self.source_id, m.id)
            return

        unforwarded = [
            m for m in media_messages if not self.storage.is_forwarded(self.source_id, m.id)
        ]
        if unforwarded:
            await self._forward_batch(unforwarded)

        for m in messages:
            self.storage.set_last_processed_id(self.source_id, m.id)

    async def _forward_batch(self, messages: List[Message]) -> None:
        """Server-side forward one message, or one album's worth of
        messages, from Channel A to Channel B — with flood-wait handling
        and bounded retries for transient errors.
        """
        if not messages:
            return

        pending = [m for m in messages if not self.storage.is_forwarded(self.source_id, m.id)]
        if not pending:
            return

        attempt = 0
        while True:
            try:
                result = await self.client.forward_messages(
                    entity=self.dest_entity,
                    messages=pending,
                    from_peer=self.source_entity,
                    drop_author=self.config.drop_author,
                )
                results = result if isinstance(result, list) else [result]

                for src_msg, dst_msg in zip(pending, results):
                    dst_id: Optional[int] = getattr(dst_msg, "id", None)
                    self.storage.mark_forwarded(self.source_id, src_msg.id, dst_id)
                    self.storage.set_last_processed_id(self.source_id, src_msg.id)
                    logger.info("Forwarded message %s -> %s", src_msg.id, dst_id)

                if self.config.forward_delay_seconds > 0:
                    await asyncio.sleep(self.config.forward_delay_seconds)
                return

            except FloodWaitError as e:
                wait = e.seconds + 1
                logger.warning("Flood wait triggered: sleeping %ss before retrying", wait)
                await asyncio.sleep(wait)
                # Not counted against max_retries — flood waits are an
                # expected, well-defined signal from Telegram, not a fault.

            except (RPCError, ConnectionError, OSError) as e:
                attempt += 1
                ids = [m.id for m in pending]
                if attempt > self.config.max_retries:
                    logger.error(
                        "Giving up on messages %s after %d attempts: %s",
                        ids, attempt - 1, e,
                    )
                    # Advance past these messages so a permanently broken
                    # message can't block the whole channel forever; the
                    # failure is recorded in the log for manual follow-up.
                    for m in pending:
                        self.storage.set_last_processed_id(self.source_id, m.id)
                    return

                backoff = min(60, 2 ** attempt)
                logger.error(
                    "Error forwarding messages %s: %s. Retrying in %ss (attempt %d/%d)",
                    ids, e, backoff, attempt, self.config.max_retries,
                )
                await asyncio.sleep(backoff)

    async def shutdown(self) -> None:
        """Flush any buffered albums before disconnecting."""
        await self._album_buffer.flush_all()

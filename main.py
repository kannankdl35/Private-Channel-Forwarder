"""Entry point for the Telegram Private Channel File Forwarder.

Run with:
    python main.py

For 24x7 operation on a VPS, run this via the provided systemd unit
instead (see systemd/telegram-forwarder.service and README.md).
"""
from __future__ import annotations

import asyncio
import logging
import signal

from telethon import TelegramClient
from telethon.sessions import StringSession

from forwarder.config import ConfigError, load_config
from forwarder.logger import setup_logging
from forwarder.service import ForwarderService
from forwarder.storage import Storage

RECONNECT_BACKOFF_START = 5
RECONNECT_BACKOFF_MAX = 300


async def _run_forever() -> None:
    config = load_config()
    logger = setup_logging(config.log_level, config.log_file)
    storage = Storage(config.db_path)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass  # signal handlers aren't available on some platforms (e.g. Windows)

    backoff = RECONNECT_BACKOFF_START

    try:
        while not stop_event.is_set():
            client = TelegramClient(
                StringSession(config.session_string),
                config.api_id,
                config.api_hash,
                flood_sleep_threshold=config.flood_sleep_threshold,
            )
            service = ForwarderService(client, config, storage)

            try:
                await service.start()
                backoff = RECONNECT_BACKOFF_START  # reset after a clean start

                disconnect_task = asyncio.create_task(client.run_until_disconnected())
                stop_task = asyncio.create_task(stop_event.wait())
                done, pending = await asyncio.wait(
                    {disconnect_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()

                if stop_event.is_set():
                    logger.info("Shutdown signal received, disconnecting...")
                    await service.shutdown()
                    break

                logger.warning("Client disconnected unexpectedly. Reconnecting...")

            except ConfigError:
                raise  # misconfiguration is not retryable — fail fast and loudly

            except Exception as exc:  # noqa: BLE001 - deliberate top-level resilience
                logger.exception("Unhandled error in forwarder service: %s", exc)

            finally:
                if client.is_connected():
                    await client.disconnect()

            if not stop_event.is_set():
                logger.info("Retrying in %ss...", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX)
    finally:
        storage.close()
        logger.info("Forwarder stopped.")


def main() -> None:
    try:
        asyncio.run(_run_forever())
    except ConfigError as exc:
        logging.getLogger("forwarder").critical("Configuration error: %s", exc)
        raise SystemExit(1)


if __name__ == "__main__":
    main()

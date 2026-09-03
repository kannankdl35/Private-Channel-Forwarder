"""List the chats/channels visible to the account behind SESSION_STRING,
along with their numeric IDs — handy for filling in SOURCE_CHANNEL_ID and
DEST_CHANNEL_ID in your .env file.

Usage:
    python scripts/list_channels.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from telethon import TelegramClient  # noqa: E402
from telethon.sessions import StringSession  # noqa: E402

from forwarder.config import load_config  # noqa: E402


async def main() -> None:
    config = load_config()
    client = TelegramClient(StringSession(config.session_string), config.api_id, config.api_hash)

    async with client:
        print(f"{'ID':>16}  {'Type':<10}  Title")
        print("-" * 60)
        async for dialog in client.iter_dialogs():
            kind = type(dialog.entity).__name__
            print(f"{dialog.id:>16}  {kind:<10}  {dialog.name}")


if __name__ == "__main__":
    asyncio.run(main())

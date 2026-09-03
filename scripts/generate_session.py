"""One-time helper to generate a Telethon StringSession for a user account.

Run this locally/interactively (not as part of the unattended VPS service)
whenever you need to create or regenerate SESSION_STRING. It logs in as a
normal Telegram user (phone number + login code, and 2FA password if you
have one enabled) and prints a session string to paste into your .env file.

Usage:
    python scripts/generate_session.py

The account used here must already be a member of Source Channel A (and
able to post in Destination Channel B) for the forwarder to work.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from telethon.sessions import StringSession  # noqa: E402
from telethon.sync import TelegramClient  # noqa: E402


def main() -> None:
    api_id = input("API_ID: ").strip()
    api_hash = input("API_HASH: ").strip()

    with TelegramClient(StringSession(), int(api_id), api_hash) as client:
        session_string = client.session.save()
        print("\nLogin successful. Add this line to your .env file:\n")
        print(f"SESSION_STRING={session_string}\n")
        print("Keep this value secret — anyone with it has full access to this Telegram account.")


if __name__ == "__main__":
    main()

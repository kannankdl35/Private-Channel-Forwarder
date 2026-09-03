"""Telegram Private Channel File Forwarder.

A small Telethon (MTProto) service that watches a private source channel
using an authenticated user session and server-side forwards newly posted
media to a private destination channel, without downloading or
re-uploading file content.
"""

__version__ = "1.0.0"

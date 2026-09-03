"""Persistent state for the forwarder.

Tracks (a) which source messages have already been forwarded, so restarts
never produce duplicates, and (b) the last processed message id per source
chat, so the service can catch up on exactly what it missed after a
restart or network outage — no more, no less.

Backed by SQLite so state survives process and VPS restarts without any
external dependency.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from typing import Optional


class Storage:
    def __init__(self, db_path: str):
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self._lock = threading.Lock()
        # check_same_thread=False: Telethon's asyncio loop and any future
        # helper threads may share this connection; access is serialized
        # by _lock regardless.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS forwarded_messages (
                    source_chat_id INTEGER NOT NULL,
                    source_msg_id  INTEGER NOT NULL,
                    dest_msg_id    INTEGER,
                    forwarded_at   TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (source_chat_id, source_msg_id)
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_state (
                    source_chat_id     INTEGER PRIMARY KEY,
                    last_processed_id  INTEGER NOT NULL
                )
                """
            )

    def is_forwarded(self, source_chat_id: int, source_msg_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM forwarded_messages "
                "WHERE source_chat_id = ? AND source_msg_id = ?",
                (source_chat_id, source_msg_id),
            )
            return cur.fetchone() is not None

    def mark_forwarded(
        self, source_chat_id: int, source_msg_id: int, dest_msg_id: Optional[int]
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO forwarded_messages
                    (source_chat_id, source_msg_id, dest_msg_id)
                VALUES (?, ?, ?)
                """,
                (source_chat_id, source_msg_id, dest_msg_id),
            )

    def get_last_processed_id(self, source_chat_id: int) -> Optional[int]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT last_processed_id FROM sync_state WHERE source_chat_id = ?",
                (source_chat_id,),
            )
            row = cur.fetchone()
            return row[0] if row else None

    def set_last_processed_id(self, source_chat_id: int, message_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO sync_state (source_chat_id, last_processed_id)
                VALUES (?, ?)
                ON CONFLICT(source_chat_id) DO UPDATE SET
                    last_processed_id = MAX(last_processed_id, excluded.last_processed_id)
                """,
                (source_chat_id, message_id),
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

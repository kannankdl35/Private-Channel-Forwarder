"""Configuration loading and validation for the forwarder service.

All secrets and per-deployment settings are read from environment
variables (typically via a .env file — see .env.example). Nothing is
ever hard-coded here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Union

from dotenv import load_dotenv

ChannelRef = Union[int, str]


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid.

    Treated as non-retryable by the service: it means the deployment is
    misconfigured, not that Telegram is temporarily unavailable.
    """


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    session_string: str

    source_channel: ChannelRef
    dest_channel: ChannelRef

    drop_author: bool
    forward_existing_on_first_run: bool
    catch_up_limit: int
    max_retries: int
    forward_delay_seconds: float
    album_flush_delay: float
    flood_sleep_threshold: int

    log_level: str
    log_file: str
    db_path: str


def _require(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise ConfigError(
            f"Missing required environment variable: {name}. "
            f"Check your .env file against .env.example."
        )
    return value.strip()


def _require_int(name: str) -> int:
    raw = _require(name)
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got: {raw!r}") from exc


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got: {value!r}") from exc


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got: {value!r}") from exc


def _parse_channel(name: str) -> ChannelRef:
    """Accept either a numeric chat ID (e.g. -1001234567890) or a
    @username. Private channels normally have no username, so a numeric
    ID is expected in most setups — see scripts/list_channels.py.
    """
    raw = _require(name)
    try:
        return int(raw)
    except ValueError:
        return raw if raw.startswith("@") else f"@{raw}"


def load_config(env_file: str = ".env") -> Config:
    """Load and validate configuration from the environment / .env file.

    Existing process environment variables always take precedence over
    values in the .env file, which matters for systemd or container
    deployments that inject environment variables directly.
    """
    load_dotenv(env_file, override=False)

    return Config(
        api_id=_require_int("API_ID"),
        api_hash=_require("API_HASH"),
        session_string=_require("SESSION_STRING"),
        source_channel=_parse_channel("SOURCE_CHANNEL_ID"),
        dest_channel=_parse_channel("DEST_CHANNEL_ID"),
        drop_author=_get_bool("DROP_AUTHOR", False),
        forward_existing_on_first_run=_get_bool("FORWARD_EXISTING_ON_FIRST_RUN", False),
        catch_up_limit=_get_int("CATCH_UP_LIMIT", 500),
        max_retries=_get_int("MAX_RETRIES", 5),
        forward_delay_seconds=_get_float("FORWARD_DELAY_SECONDS", 0.3),
        album_flush_delay=_get_float("ALBUM_FLUSH_DELAY", 2.0),
        flood_sleep_threshold=_get_int("FLOOD_SLEEP_THRESHOLD", 60),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        log_file=os.getenv("LOG_FILE", "logs/forwarder.log").strip(),
        db_path=os.getenv("DB_PATH", "data/forwarder.db").strip(),
    )

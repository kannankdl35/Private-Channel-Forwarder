"""Logging setup: console output plus a rotating log file, suitable for
both interactive runs and a systemd-managed service (systemd captures
stdout/stderr into the journal automatically).
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(log_level: str, log_file: str) -> logging.Logger:
    logger = logging.getLogger("forwarder")

    level = getattr(logging, log_level, None)
    if not isinstance(level, int):
        level = logging.INFO
    logger.setLevel(level)
    logger.propagate = False

    if logger.handlers:
        return logger  # already configured, e.g. re-entered in tests

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    file_handler = RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Telethon's own logger is very chatty at INFO (every request/update);
    # keep it quiet unless we're actively debugging.
    logging.getLogger("telethon").setLevel(
        logging.DEBUG if level == logging.DEBUG else logging.WARNING
    )

    return logger

"""Console + rotating-file logging, configured once per process."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from backend.config.settings import settings

_CONFIGURED = False

_FMT = "%(asctime)s | %(levelname)-7s | %(name)-22s | %(message)s"
_DATEFMT = "%H:%M:%S"


def setup_logging(level: str | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    lvl = getattr(logging, (level or settings.log_level).upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(lvl)
    root.handlers.clear()

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(logging.Formatter(_FMT, _DATEFMT))
    root.addHandler(stream)

    fh = RotatingFileHandler(
        settings.paths.logs / "system.log",
        maxBytes=4_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setFormatter(logging.Formatter(_FMT, _DATEFMT))
    root.addHandler(fh)

    for noisy in ("httpx", "urllib3", "sentence_transformers", "transformers", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)

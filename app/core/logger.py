"""
Structured logger.
- Console handler: plain text for readability.
- File handler: rotating (10 MB × 5 files), JSON-ish format for grep-ability.
- Every log record includes source, url, stage, status fields when provided
  via the `extra` dict.

Usage:
    from app.core.logger import get_logger
    log = get_logger(__name__)
    log.info("parsed", extra={"source": "TradeIndia", "url": url, "stage": "parse", "status": "OK"})
"""
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


_FORMATTER_CONSOLE = logging.Formatter(
    fmt="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per line for file output."""

    STANDARD_KEYS = {"name", "msg", "args", "levelname", "levelno", "pathname",
                     "filename", "module", "exc_info", "exc_text", "stack_info",
                     "lineno", "funcName", "created", "msecs", "relativeCreated",
                     "thread", "threadName", "processName", "process", "message",
                     "asctime", "taskName"}

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        base = {
            "ts":      self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level":   record.levelname,
            "logger":  record.name,
            "msg":     record.message,
        }
        # Attach structured extras (source, url, stage, status, error …)
        for k, v in record.__dict__.items():
            if k not in self.STANDARD_KEYS:
                base[k] = v
        if record.exc_info:
            base["exc"] = self.formatException(record.exc_info)
        return json.dumps(base, default=str)


def _build_logger(name: str, log_dir: Path, level: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:          # already configured
        return logger
    logger.setLevel(level)
    logger.propagate = False

    # Console
    ch = logging.StreamHandler()
    ch.setFormatter(_FORMATTER_CONSOLE)
    ch.setLevel(level)
    logger.addHandler(ch)

    # Rotating file
    log_file = log_dir / "scraper.log"
    fh = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5,
                             encoding="utf-8")
    fh.setFormatter(_JsonFormatter())
    fh.setLevel(level)
    logger.addHandler(fh)

    return logger


# One module-level root logger; call get_logger() for per-module loggers.
def get_logger(name: str = "scraper") -> logging.Logger:
    from app.config.settings import settings          # lazy import avoids circular
    return _build_logger(name, settings.log_dir_abs, settings.log_level)

"""
safeslicesql.logging_config — Project-wide logging setup.

Establishes consistent logging conventions across all SafeSliceSQL
modules with both console and rotating file output.

Conventions:
- Logger names:  safeslicesql.<module>
- Log levels:    DEBUG for candidate decisions, INFO for workflow steps,
                 WARNING for skipped items, ERROR for failures
- Timestamps:    ISO 8601 format
- Run context:   Log run ID, method, seed, and budget at INFO level
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s | %(name)-30s | %(levelname)-8s | %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"

_configured = False


def setup_logging(
    level: str = "INFO",
    log_dir: str = "logs",
    log_file: str = "safeslicesql.log",
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
    force: bool = False,
) -> None:
    """
    Configure project-wide logging with console and rotating file output.

    Safe to call multiple times; only configures once unless *force* is used.

    Parameters
    ----------
    level : str
        Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    log_dir : str
        Directory for log files. Created if it does not exist.
    log_file : str
        Name of the rotating log file.
    max_bytes : int
        Maximum size of a single log file before rotation.
    backup_count : int
        Number of rotated log files to keep.
    force : bool
        If True, reconfigure all handlers even if already initialized.
    """
    global _configured
    log_level = getattr(logging, level.upper(), logging.INFO)
    root_logger = logging.getLogger("safeslicesql")

    if _configured and not force:
        root_logger.setLevel(log_level)
        for handler in root_logger.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(
                handler, RotatingFileHandler
            ):
                handler.setLevel(log_level)
        return

    _configured = True
    root_logger.handlers.clear()
    root_logger.setLevel(log_level)

    # Formatter
    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    # Console handler (stderr)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File handler (rotating)
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_path / log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)  # Always capture DEBUG to file
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    root_logger.info(
        "Logging configured: level=%s, log_dir=%s", level, log_dir,
    )

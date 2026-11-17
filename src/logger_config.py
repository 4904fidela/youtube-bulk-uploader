"""
logger_config.py - Konfigurasi logging terpusat untuk aplikasi.
"""

import logging
import logging.handlers
import os
from pathlib import Path


def setup_logging(
    log_dir: str = "logs",
    log_level: str = "INFO",
    console: bool = True,
) -> logging.Logger:
    """
    Konfigurasi logging ke file dan konsol.

    Args:
        log_dir: Direktori untuk menyimpan file log.
        log_level: Level logging (DEBUG, INFO, WARNING, ERROR).
        console: Tampilkan log di konsol juga.

    Returns:
        Root logger yang sudah dikonfigurasi.
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    log_file = log_path / "uploader.log"

    level = getattr(logging, log_level.upper(), logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Hapus handler yang sudah ada
    root_logger.handlers.clear()

    # File handler dengan rotasi (maks 5 MB, simpan 5 file)
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    root_logger.addHandler(file_handler)

    # Console handler
    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.setLevel(level)
        root_logger.addHandler(console_handler)

    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    return root_logger

# =============================================================================
# logger_setup.py — Zentrales Logging für den Trading Agent
# C:\mt5_agent\logger_setup.py
#
# Features:
#   - Console: INFO+  |  Datei: DEBUG+ (rotierend 5×5MB)
#   - Jedes Modul bekommt eigenen Named Logger
#   - Einmalige Root-Konfiguration, danach nur noch getLogger()
# =============================================================================

from __future__ import annotations
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_LOG_DIR  = r"C:\mt5_agent"
_LOG_FILE = os.path.join(_LOG_DIR, "agent.log")

_MAX_BYTES    = 5 * 1024 * 1024   # 5 MB pro Datei
_BACKUP_COUNT = 5                  # max 5 Dateien behalten

_initialized = False


def _setup_root():
    """Konfiguriert Root-Logger einmalig."""
    global _initialized
    if _initialized:
        return
    _initialized = True

    root = logging.getLogger("mt5agent")
    root.setLevel(logging.DEBUG)
    root.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s [%(name)-22s] %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console: INFO+
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # Datei: DEBUG+ mit Rotation
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        fh = RotatingFileHandler(
            _LOG_FILE,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception as e:
        root.warning(f"Log-Datei nicht zugänglich: {e} — nur Console-Logging")


def get_logger(name: str) -> logging.Logger:
    """
    Gibt benannten Logger zurück.
    Mehrfacher Aufruf mit gleichem Namen → selber Logger.
    """
    _setup_root()
    return logging.getLogger(f"mt5agent.{name}")

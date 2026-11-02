"""
Application Configuration.

Central settings for local file processing, confidence thresholds,
directory layouts, and OCR provider configuration.
"""

from __future__ import annotations

from config.settings import (
    Settings,
    __version__,
    get_settings,
    reset_settings,
)

__all__ = [
    "Settings",
    "get_settings",
    "reset_settings",
    "__version__",
]

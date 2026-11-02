"""
Structured Logging and Correlation ID Tracking for Azure AI Foundry Hosted Agents.

Supports:
  - Context-local correlation IDs across async tasks.
  - JSON and human-readable structured logging.
  - Azure Application Insights / CloudWatch compatible log metadata.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Context variable to hold active correlation ID for request tracing
_correlation_id_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "correlation_id", default=None
)


def get_correlation_id() -> str:
    """Retrieve the active correlation ID or generate a new one if not present."""
    cid = _correlation_id_ctx.get()
    if not cid:
        cid = f"corr-{uuid.uuid4().hex[:12]}"
        _correlation_id_ctx.set(cid)
    return cid


def set_correlation_id(correlation_id: Optional[str]) -> str:
    """Explicitly set the correlation ID for the current async execution context."""
    if not correlation_id or not correlation_id.strip():
        correlation_id = f"corr-{uuid.uuid4().hex[:12]}"
    _correlation_id_ctx.set(correlation_id.strip())
    return correlation_id.strip()


class StructuredLogFormatter(logging.Formatter):
    """Formats log records as structured text with timestamps, levels, and correlation IDs."""

    def format(self, record: logging.LogRecord) -> str:
        cid = _correlation_id_ctx.get() or "corr-none"
        record.correlation_id = cid
        timestamp = datetime.now(timezone.utc).isoformat()
        return f"{timestamp} [{record.levelname}] [{cid}] {record.name}: {record.getMessage()}"


def setup_monitoring_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure root and application loggers with structured formatter."""
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Avoid duplicate handlers
    if not any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredLogFormatter())
        root_logger.addHandler(handler)
    else:
        for handler in root_logger.handlers:
            handler.setFormatter(StructuredLogFormatter())

    app_logger = logging.getLogger("havi")
    app_logger.setLevel(level)
    return app_logger


def log_event(logger: logging.Logger, event_name: str, **kwargs: Any) -> None:
    """Log a structured operational event."""
    cid = get_correlation_id()
    payload = {
        "event": event_name,
        "correlation_id": cid,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **kwargs,
    }
    logger.info("[EVENT:%s] %s", event_name, json.dumps(payload, default=str))

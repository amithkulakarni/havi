"""
Inbound Payload Normalization & Extraction Tools.

Handles:
  - Logic Apps, Event Grid, Outlook Email, Base64, and REST payloads.
  - HTML tag stripping and whitespace sanitization.
  - Safe JSON extraction.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("havi.tools.payload_tools")


def clean_html_content(raw_html: str) -> str:
    """Sanitize and strip HTML tags from email bodies or webhook contents."""
    if not raw_html:
        return ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", raw_html)
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"(?i)</tr>", "\n", text)
    text = re.sub(r"(?i)</td>", "\t", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def extract_base64_payload(data_str: str) -> Optional[bytes]:
    """Safely decode base64 strings with or without data URI prefix."""
    if not data_str or not isinstance(data_str, str):
        return None
    cleaned = data_str.strip()
    if "," in cleaned and cleaned.startswith("data:"):
        cleaned = cleaned.split(",", 1)[1]
    try:
        return base64.b64decode(cleaned)
    except Exception as exc:
        logger.warning("[PAYLOAD_TOOLS_B64_WARN] Base64 decode error: %s", exc)
        return None

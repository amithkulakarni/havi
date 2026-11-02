"""
Notification Adapter for HITL Alerts.

Supports both:
  1. DUMMY / SIMULATION MODE (Default):
     Formats the exact notification payload, logs the dispatch event,
     and saves the payload to output/notifications/<doc_id>_alert.json.
  2. LIVE HTTP WEBHOOK / MESSAGE API MODE:
     When HITL_NOTIFICATION_API_URL and HITL_NOTIFICATION_API_KEY are configured in .env,
     dispatches a live HTTP POST request with bearer/header authentication.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("havi.adapters.notification")


class HITLNotificationAdapter:
    """Pluggable notification client for HITL review alerts."""

    def __init__(self) -> None:
        self.api_url = os.environ.get("HITL_NOTIFICATION_API_URL", "").strip()
        self.api_key = os.environ.get("HITL_NOTIFICATION_API_KEY", "").strip()
        self.notifications_dir = Path("output/notifications")
        self.notifications_dir.mkdir(parents=True, exist_ok=True)

    @property
    def is_live(self) -> bool:
        """True if real notification endpoint is configured and not a placeholder."""
        return bool(self.api_url) and not any(
            p in self.api_url.lower() for p in ["dummy", "mock", "example", "placeholder"]
        )

    def send_hitl_alert(
        self,
        document_id: str,
        source_filename: str,
        reasons: list[str],
        extracted_fields: Dict[str, Any],
        confidence_score: float,
    ) -> Dict[str, Any]:
        """
        Send a notification alert when a document requires human review.

        Returns:
            Dictionary describing the delivery outcome.
        """
        payload = {
            "event": "HITL_REVIEW_REQUIRED",
            "document_id": document_id,
            "source_filename": source_filename,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "confidence_score": round(confidence_score, 2),
            "pending_issues": reasons,
            "extracted_fields_summary": extracted_fields,
            "action_required": "Please review and approve this document in the operations portal.",
            "resume_command_hint": f"python main.py --review {document_id} --corrections '{{\"po_number\": \"...\"}}'",
        }

        # 1. If real API URL is configured, perform live HTTP POST
        if self.is_live:
            try:
                import httpx
                headers = {"Content-Type": "application/json"}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"

                logger.info("[HITL_NOTIFICATION_DISPATCH] Sending live alert to %s for %s", self.api_url, document_id)
                response = httpx.post(self.api_url, json=payload, headers=headers, timeout=10.0)
                response.raise_for_status()

                logger.info("[HITL_NOTIFICATION_SUCCESS] Live notification sent (status: %d)", response.status_code)
                return {
                    "mode": "LIVE_API",
                    "status": "DELIVERED",
                    "status_code": response.status_code,
                    "payload": payload,
                }
            except Exception as exc:
                logger.error("[HITL_NOTIFICATION_FAILED] Live delivery failed: %s. Falling back to local logging.", exc)
                return {
                    "mode": "LIVE_API",
                    "status": "FAILED",
                    "error": str(exc),
                    "payload": payload,
                }

        # 2. DUMMY / SIMULATION MODE
        # Save outgoing payload to output/notifications/ for inspection
        alert_file = self.notifications_dir / f"{document_id}_alert.json"
        try:
            with open(alert_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except OSError as e:
            logger.warning("Could not write notification file: %s", e)

        logger.info(
            "[DUMMY_NOTIFICATION_SENT] Simulated HITL alert successfully generated for doc '%s' (saved to %s).",
            document_id,
            alert_file,
        )

        return {
            "mode": "DUMMY_SIMULATION",
            "status": "SIMULATED_SUCCESS",
            "message": "Dummy alert generated. Once real API URL and key are provided in .env, live HTTP dispatch will activate.",
            "artifact_file": str(alert_file),
            "payload": payload,
        }


_adapter_instance: Optional[HITLNotificationAdapter] = None


def get_notification_adapter() -> HITLNotificationAdapter:
    """Singleton getter for HITLNotificationAdapter."""
    global _adapter_instance
    if _adapter_instance is None:
        _adapter_instance = HITLNotificationAdapter()
    return _adapter_instance

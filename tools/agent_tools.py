"""
Agent Tools and Inter-Agent Communication Utilities.

Provides structured tools for:
  - Agent dispatch formatting and routing payload validation.
  - Calculation of field confidence score aggregates.
  - Formatting human review packages for operations.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("havi.tools.agent_tools")


def format_agent_dispatch(
    source_agent: str,
    target_agent: str,
    document_id: str,
    action: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Format an agent-to-agent dispatch message with execution envelope."""
    return {
        "source_agent": source_agent,
        "target_agent": target_agent,
        "document_id": document_id,
        "action": action,
        "payload": payload,
    }


def aggregate_field_confidence(field_confidences: Dict[str, float]) -> Tuple[float, List[str]]:
    """
    Compute weighted extraction confidence score and identify low confidence fields.
    Returns (overall_confidence, low_confidence_field_names).
    """
    if not field_confidences:
        return 0.0, []

    scores = list(field_confidences.values())
    avg = sum(scores) / len(scores)
    low_fields = [k for k, v in field_confidences.items() if v < 0.80]
    return round(avg, 2), low_fields


def build_hitl_escalation_details(
    document_id: str,
    reasons: List[str],
    extracted_fields: Dict[str, Any],
    overall_confidence: float,
) -> Dict[str, Any]:
    """Generate structured HITL escalation summary."""
    return {
        "document_id": document_id,
        "requires_hitl": True,
        "escalation_reasons": reasons,
        "overall_confidence": overall_confidence,
        "extracted_fields_summary": extracted_fields,
    }

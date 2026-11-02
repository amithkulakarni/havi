"""
Unit tests for monitoring and tools packages.
"""

from __future__ import annotations

import logging
import pytest

from monitoring.health import check_system_health
from monitoring.logger import (
    get_correlation_id,
    log_event,
    set_correlation_id,
    setup_monitoring_logging,
)
from monitoring.telemetry import WorkflowMetricsTracker, WorkflowTelemetrySpan
from tools.agent_tools import (
    aggregate_field_confidence,
    build_hitl_escalation_details,
    format_agent_dispatch,
)
from tools.payload_tools import clean_html_content, extract_base64_payload


def test_correlation_id_management():
    """Verify correlation ID generation and override."""
    cid = set_correlation_id("test-corr-12345")
    assert cid == "test-corr-12345"
    assert get_correlation_id() == "test-corr-12345"

    # Reset
    cid_new = set_correlation_id(None)
    assert cid_new.startswith("corr-")


def test_workflow_telemetry_span():
    """Verify telemetry timing spans."""
    tracker = WorkflowMetricsTracker()
    span = tracker.start_span("SupervisorClassification", agent="SupervisorAgent")
    assert span.span_name == "SupervisorClassification"
    assert span.status == "IN_PROGRESS"

    duration = span.complete(status="SUCCESS", confidence=0.95)
    assert duration >= 0.0
    assert span.status == "SUCCESS"
    assert span.metadata["confidence"] == 0.95

    summary = tracker.summary()
    assert summary["total_spans"] == 1
    assert summary["total_duration_ms"] >= 0.0


def test_health_check():
    """Verify health probe returns status and runtime details."""
    health = check_system_health()
    assert health["status"] in ("HEALTHY", "DEGRADED")
    assert health["service"] == "havi-mcd-hosted-agent"
    assert "version" in health
    assert "runtime_root" in health


def test_agent_tools_dispatch_formatting():
    """Verify agent-to-agent dispatch message envelope."""
    msg = format_agent_dispatch(
        source_agent="SupervisorAgent",
        target_agent="OrderExtractionAgent",
        document_id="DOC-99",
        action="EXTRACT_ORDER",
        payload={"sample": "data"},
    )
    assert msg["source_agent"] == "SupervisorAgent"
    assert msg["target_agent"] == "OrderExtractionAgent"
    assert msg["document_id"] == "DOC-99"
    assert msg["payload"]["sample"] == "data"


def test_aggregate_field_confidence():
    """Verify confidence score aggregation and low confidence identification."""
    confidences = {
        "po_number": 0.95,
        "customer": 0.85,
        "quantity": 0.60,
    }
    overall, low_fields = aggregate_field_confidence(confidences)
    assert overall == 0.80
    assert low_fields == ["quantity"]


def test_payload_html_cleaning():
    """Verify HTML sanitization for email payloads."""
    html = "<p>Order for Customer McD<br/>PO Number: PO-12345</p><div>Quantity: 100</div>"
    cleaned = clean_html_content(html)
    assert "<" not in cleaned
    assert "Order for Customer McD" in cleaned
    assert "PO-12345" in cleaned


def test_base64_payload_extraction():
    """Verify base64 decoding with and without data URI headers."""
    raw = b"Hello Freight Automation"
    import base64
    b64_plain = base64.b64encode(raw).decode("utf-8")
    b64_uri = f"data:application/pdf;base64,{b64_plain}"

    assert extract_base64_payload(b64_plain) == raw
    assert extract_base64_payload(b64_uri) == raw
    assert extract_base64_payload("") is None

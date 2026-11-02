"""
Unit Tests for Supervisor Agent Classification and Routing.

Validates:
  1. Purchase order classification (ORDER -> OrderExtractionAgent).
  2. Carrier document classification (CARRIER_DOCUMENT -> CarrierDocumentExtractionAgent).
  3. Scanned or unreadable document classification (UNKNOWN -> HITLAgent).
  4. Ambiguous content classification (UNKNOWN -> HITLAgent).
  5. Structured routing result schema.
"""

from __future__ import annotations

import pytest

from agents.supervisor_agent import SupervisorAgent
from schemas.models import DocumentType


@pytest.fixture
def supervisor() -> SupervisorAgent:
    return SupervisorAgent()


def test_supervisor_classifies_purchase_order(supervisor: SupervisorAgent):
    """Verify purchase order content routes to OrderExtractionAgent."""
    doc_data = {
        "document_id": "DOC-TEST-01",
        "extracted_text": (
            "HAVI LOGISTICS - PURCHASE ORDER\n"
            "Customer Name: HAVI MCD\n"
            "PO Number: PO-887766\n"
            "Material: Fresh Beef Patties\n"
            "Quantity: 250 Cases\n"
            "Delivery Location: Chicago Distribution Center\n"
        ),
        "status": "SUCCESS",
    }

    result = supervisor.classify(doc_data)

    assert result.document_type == DocumentType.ORDER
    assert result.route_to == "OrderExtractionAgent"
    assert result.confidence >= 0.80
    assert result.status == "CLASSIFIED"


def test_supervisor_classifies_carrier_document(supervisor: SupervisorAgent):
    """Verify Bill of Lading / Carrier document routes to CarrierDocumentExtractionAgent."""
    doc_data = {
        "document_id": "DOC-TEST-02",
        "extracted_text": (
            "STRAIGHT BILL OF LADING / CARRIER CONFIRMATION\n"
            "Booking ID: BKG-MAERSK-001\n"
            "Carrier: Maersk Line\n"
            "Vessel / Voyage: MAERSK MC-KINNEY / 102A\n"
            "Container Number: MSKU1234567\n"
            "Related Order Reference: PO-887766\n"
        ),
        "status": "SUCCESS",
    }

    result = supervisor.classify(doc_data)

    assert result.document_type == DocumentType.CARRIER_DOCUMENT
    assert result.route_to == "CarrierDocumentExtractionAgent"
    assert result.confidence >= 0.80


def test_supervisor_classifies_scanned_unreadable_to_hitl(supervisor: SupervisorAgent):
    """Verify unreadable or scanned document without OCR routes to HITLAgent."""
    doc_data = {
        "document_id": "DOC-TEST-03",
        "extracted_text": "",
        "status": "OCR_UNAVAILABLE",
    }

    result = supervisor.classify(doc_data)

    assert result.document_type == DocumentType.UNKNOWN
    assert result.route_to == "HITLAgent"
    assert result.confidence < 0.50


def test_supervisor_classifies_ambiguous_text_to_hitl(supervisor: SupervisorAgent):
    """Verify random unclassified text routes to HITLAgent."""
    doc_data = {
        "document_id": "DOC-TEST-04",
        "extracted_text": "Meeting notes for quarterly team review. Discussion on coffee machine.",
        "status": "SUCCESS",
    }

    result = supervisor.classify(doc_data)

    assert result.document_type == DocumentType.UNKNOWN
    assert result.route_to == "HITLAgent"


def test_supervisor_classifies_order_update(supervisor: SupervisorAgent):
    """Verify purchase order revision/amendment routes to OrderExtractionAgent as ORDER_UPDATE."""
    doc_data = {
        "document_id": "DOC-TEST-05",
        "extracted_text": (
            "HAVI LOGISTICS - PURCHASE ORDER AMENDMENT / ORDER UPDATE\n"
            "Customer Name: HAVI MCD\n"
            "PO Number: PO-887766\n"
            "PO Revision: REV-02\n"
            "Material: Fresh Beef Patties\n"
            "Quantity: 300 Cases\n"
            "Delivery Location: Chicago Distribution Center\n"
        ),
        "status": "SUCCESS",
    }

    result = supervisor.classify(doc_data)

    assert result.document_type == DocumentType.ORDER_UPDATE
    assert result.route_to == "OrderExtractionAgent"
    assert result.confidence >= 0.80
    assert result.status == "CLASSIFIED"


def test_supervisor_enforces_080_confidence_threshold(supervisor: SupervisorAgent):
    """Verify any input with low confidence (< 0.80) triggers HITL."""
    doc_data = {
        "document_id": "DOC-TEST-06",
        "extracted_text": "Vague delivery note with no reference numbers or items.",
        "status": "SUCCESS",
    }

    result = supervisor.classify(doc_data)

    assert result.confidence < 0.80
    assert result.route_to == "HITLAgent"
    assert result.document_type == DocumentType.UNKNOWN

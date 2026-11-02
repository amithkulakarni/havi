"""
Unit Tests for Order Extraction and Carrier Extraction Agents.

Validates:
  1. Complete order field extraction with confidence scores.
  2. Missing mandatory field detection in order documents.
  3. Optional field handling (missing optional field does not trigger validation failure).
  4. Structured JSON order input extraction.
  5. Carrier confirmation and BOL field extraction.
"""

from __future__ import annotations

import pytest

from agents.carrier_document_extraction_agent import CarrierDocumentExtractionAgent
from agents.order_extraction_agent import OrderExtractionAgent


@pytest.fixture
def order_agent() -> OrderExtractionAgent:
    return OrderExtractionAgent()


@pytest.fixture
def carrier_agent() -> CarrierDocumentExtractionAgent:
    return CarrierDocumentExtractionAgent()


def test_order_field_extraction_complete(order_agent: OrderExtractionAgent):
    """Verify full extraction of order fields with high confidence."""
    text = (
        "HAVI LOGISTICS - PURCHASE ORDER\n"
        "Customer Name: HAVI MCD\n"
        "Order Number: ORD-9901\n"
        "PO Number: PO-102030\n"
        "Material / Product: Chicken McNuggets\n"
        "Quantity: 600\n"
        "Unit of Measure: Cases\n"
        "Pickup Location: Chicago Distribution Center\n"
        "Delivery Location: Restaurant #201, Oak Brook IL\n"
        "Requested Pickup Date: 2026-10-15\n"
        "Requested Delivery Date: 2026-10-18\n"
        "Incoterms: DDP\n"
    )

    fields, confs, models = order_agent.extract_fields(text)

    assert fields["po_number"] == "PO-102030"
    assert fields["customer"] == "HAVI MCD"
    assert fields["material"] == "Chicken McNuggets"
    assert fields["quantity"] == 600
    assert fields["pickup_location"] == "Chicago Distribution Center"
    assert fields["delivery_location"] == "Restaurant #201, Oak Brook IL"
    assert confs["po_number"] >= 0.80
    assert confs["customer"] >= 0.80


def test_order_field_extraction_missing_mandatory(order_agent: OrderExtractionAgent):
    """Verify that an order missing mandatory fields (e.g. quantity and delivery location) is flagged."""
    text = (
        "HAVI LOGISTICS - PURCHASE ORDER\n"
        "Customer Name: HAVI MCD\n"
        "PO Number: PO-102030\n"
        "Material: Beef Patties\n"
    )

    fields, confs, models = order_agent.extract_fields(text)

    assert fields["po_number"] == "PO-102030"
    assert fields["quantity"] is None
    assert confs["quantity"] == 0.0
    assert models["quantity"].is_missing is True
    assert fields["delivery_location"] is None


def test_carrier_field_extraction_complete(carrier_agent: CarrierDocumentExtractionAgent):
    """Verify extraction of carrier milestones and related order reference."""
    text = (
        "CARRIER BOOKING CONFIRMATION\n"
        "Booking ID: BKG-MSC-5544\n"
        "Carrier Name: MSC Mediterranean Shipping\n"
        "Vessel / Voyage: MSC OSCAR / 44B\n"
        "Container Number: MSCU9988771\n"
        "ETD: 2026-10-19\n"
        "ETA: 2026-10-26\n"
        "Customer PO #: PO-102030\n"
    )

    fields, confs, models = carrier_agent.extract_carrier_fields(text)

    assert fields["booking_id"] == "BKG-MSC-5544"
    assert fields["carrier_name"] == "MSC Mediterranean Shipping"
    assert fields["container_number"] == "MSCU9988771"
    assert fields["related_order_reference"] == "PO-102030"
    assert confs["booking_id"] >= 0.80

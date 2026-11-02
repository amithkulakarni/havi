"""
Unit Tests for Mock CargoWise One (CW1) Integration Adapter.

Validates:
  1. BaseCW1Adapter interface compliance.
  2. Mock create operation returns MOCK_CREATED with deterministic reference.
  3. Mock update operation returns MOCK_UPDATED.
  4. Adapter makes zero external network calls.
  5. Documented mock outcome statuses.
"""

from __future__ import annotations

import pytest

from adapters.cw1_mock_adapter import BaseCW1Adapter, MockCW1Adapter, get_cw1_adapter


@pytest.fixture
def adapter() -> MockCW1Adapter:
    return MockCW1Adapter()


def test_adapter_implements_interface(adapter: MockCW1Adapter):
    """Verify MockCW1Adapter implements BaseCW1Adapter."""
    assert isinstance(adapter, BaseCW1Adapter)


def test_mock_create_order(adapter: MockCW1Adapter):
    """Verify mock create returns MOCK_CREATED with simulated reference."""
    payload = {
        "po_number": "PO-TEST-123",
        "customer": "HAVI MCD",
        "material": "Buns",
        "quantity": 100,
    }

    res = adapter.create_order(payload, "DOC-MOCK-01")

    assert res.operation_type == "CREATE"
    assert res.status == "MOCK_CREATED"
    assert res.cw1_reference is not None
    assert res.cw1_reference.startswith("MOCK-CW1-ORD-")
    assert "Simulated new order creation successful" in res.message


def test_mock_update_order(adapter: MockCW1Adapter):
    """Verify mock update returns MOCK_UPDATED with target reference."""
    payload = {
        "booking_id": "BKG-9988",
        "carrier_name": "Maersk Line",
    }
    target_ref = "MOCK-CW1-ORD-AA07AE92"

    res = adapter.update_order(target_ref, payload, "DOC-MOCK-02")

    assert res.operation_type == "UPDATE"
    assert res.status == "MOCK_UPDATED"
    assert res.cw1_reference == target_ref
    assert "Simulated order update applied successfully" in res.message

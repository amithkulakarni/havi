"""
Unit Tests for SQLite Duplicate Store and Idempotency Protection.

Validates:
  1. Tier A: Exact document file hash deduplication.
  2. Tier C: Business key deduplication (PO Number + Customer).
  3. Case-insensitive normalization of business keys.
  4. Idempotent re-runs preventing repeated creation operations.
  5. Carrier shipment order matching.
"""

from __future__ import annotations

import uuid
from pathlib import Path
import pytest

from persistence.duplicate_store import DuplicateStore


@pytest.fixture
def dup_store(tmp_path: Path) -> DuplicateStore:
    db_file = tmp_path / "test_duplicate.db"
    return DuplicateStore(db_path=db_file, sync_to_cloud=False)


def test_document_hash_deduplication(dup_store: DuplicateStore):
    """Verify Tier A file hash tracking."""
    test_hash = "abcdef1234567890abcdef1234567890"
    doc_id = "DOC-HASH-1"

    assert dup_store.check_document_duplicate(test_hash) is None

    dup_store.record_document(
        file_hash=test_hash,
        document_id=doc_id,
        file_name="invoice.pdf",
        document_type="ORDER",
        workflow_status="MOCK_CREATED",
        cw1_reference="MOCK-CW1-ORD-1122",
    )

    match = dup_store.check_document_duplicate(test_hash)
    assert match is not None
    assert match["document_id"] == doc_id
    assert match["cw1_reference"] == "MOCK-CW1-ORD-1122"


def test_business_order_deduplication(dup_store: DuplicateStore):
    """Verify Tier C (PO Number + Customer) prevents repeated order creation."""
    po = "PO-UNIQUE-9901"
    customer = "HAVI MCD"

    assert dup_store.check_business_order_duplicate(po, customer) is None

    inserted = dup_store.record_created_order(
        po_number=po,
        customer=customer,
        order_number="ORD-01",
        document_id="DOC-01",
        cw1_reference="MOCK-CW1-ORD-99",
    )
    assert inserted is True

    # Re-insert should fail duplicate constraint
    inserted_again = dup_store.record_created_order(
        po_number=po,
        customer=customer,
        order_number="ORD-01",
        document_id="DOC-02",
        cw1_reference="MOCK-CW1-ORD-100",
    )
    assert inserted_again is False

    # Lookup matches case-insensitively
    match = dup_store.check_business_order_duplicate(po.lower(), customer.lower())
    assert match is not None
    assert match["cw1_reference"] == "MOCK-CW1-ORD-99"


def test_find_order_by_reference_for_carrier_updates(dup_store: DuplicateStore):
    """Verify carrier document can match existing order by PO or order number."""
    dup_store.record_created_order(
        po_number="PO-CARRIER-MATCH",
        customer="HAVI MCD",
        order_number="ORD-MATCH-123",
        document_id="DOC-01",
        cw1_reference="MOCK-CW1-ORD-5555",
    )

    match_by_po = dup_store.find_order_by_reference("po-carrier-match")
    assert match_by_po is not None
    assert match_by_po["cw1_reference"] == "MOCK-CW1-ORD-5555"

    match_by_order_no = dup_store.find_order_by_reference("ORD-MATCH-123")
    assert match_by_order_no is not None
    assert match_by_order_no["po_number"] == "PO-CARRIER-MATCH"

    match_missing = dup_store.find_order_by_reference("NON-EXISTENT-PO")
    assert match_missing is None

"""
Unit Tests for Validation Policy and HITL Review Lifecycle.

Validates:
  1. Passing order validation when all mandatory fields meet >= 0.80 confidence.
  2. Missing mandatory field failure (triggers HITL regardless of overall confidence).
  3. Low-confidence field failure (triggers HITL).
  4. Missing optional field allowance (does not fail validation).
  5. HITL review package creation.
  6. HITL human correction revalidation and workflow resumption.
"""

from __future__ import annotations

import pytest

from agents.hitl_agent import HITLAgent
from persistence.json_store import get_json_store
from validation.validators import DocumentValidator, get_validator


@pytest.fixture
def validator() -> DocumentValidator:
    return get_validator()


@pytest.fixture
def hitl_agent() -> HITLAgent:
    return HITLAgent()


def test_validation_passes_complete_order(validator: DocumentValidator):
    """Verify validation succeeds when all mandatory fields are present and confident."""
    fields = {
        "po_number": "PO-991122",
        "customer": "HAVI MCD",
        "material": "Frying Oil",
        "quantity": 300,
        "pickup_location": "Des Plaines, IL",
        "delivery_location": "Oak Brook, IL",
        "order_number": None,  # optional missing
        "uom": "Drums",
    }
    confs = {k: 0.95 for k in fields.keys() if fields[k] is not None}

    res = validator.validate_order(fields, confs)

    assert res.is_valid is True
    assert res.requires_hitl is False
    assert len(res.missing_mandatory_fields) == 0


def test_validation_fails_missing_mandatory(validator: DocumentValidator):
    """Verify missing mandatory field triggers HITL even if other fields have 1.0 confidence."""
    fields = {
        "po_number": "PO-991122",
        "customer": "HAVI MCD",
        "material": "Frying Oil",
        "quantity": None,  # MISSING MANDATORY
        "pickup_location": "Des Plaines, IL",
        "delivery_location": "Oak Brook, IL",
    }
    confs = {
        "po_number": 1.0,
        "customer": 1.0,
        "material": 1.0,
        "pickup_location": 1.0,
        "delivery_location": 1.0,
    }

    res = validator.validate_order(fields, confs)

    assert res.is_valid is False
    assert res.requires_hitl is True
    assert "quantity" in res.missing_mandatory_fields


def test_validation_fails_low_confidence(validator: DocumentValidator):
    """Verify field with confidence < 0.80 triggers HITL."""
    fields = {
        "po_number": "PO-991122",
        "customer": "HAVI MCD",
        "material": "Frying Oil",
        "quantity": 300,
        "pickup_location": "Des Plaines, IL",
        "delivery_location": "Oak Brook, IL",
    }
    confs = {
        "po_number": 0.65,  # Low confidence (< 0.80)
        "customer": 0.95,
        "material": 0.95,
        "quantity": 0.95,
        "pickup_location": 0.95,
        "delivery_location": 0.95,
    }

    res = validator.validate_order(fields, confs)

    assert res.is_valid is False
    assert res.requires_hitl is True
    assert "po_number" in res.low_confidence_fields


def test_hitl_apply_corrections_and_resume(hitl_agent: HITLAgent):
    """Verify that applying human corrections to a pending HITL review revalidates and creates order."""
    import uuid
    unique_po = f"PO-CORRECT-{uuid.uuid4().hex[:6]}"
    doc_id = f"DOC-HITL-{uuid.uuid4().hex[:6]}"
    json_store = get_json_store()

    # Create a pending review with missing quantity
    from schemas.models import HITLReviewPackage
    pkg = HITLReviewPackage(
        document_id=doc_id,
        source_filename="test_broken_order.pdf",
        raised_by_agent="OrderExtractionAgent",
        status="HITL_PENDING",
        missing_fields=["quantity"],
        validation_errors=["Mandatory field 'quantity' is missing."],
        original_extracted_values={
            "po_number": unique_po,
            "customer": "HAVI MCD",
            "material": "Apples",
            "pickup_location": "Hub A",
            "delivery_location": "Store B",
        },
    )
    json_store.save_review(pkg)

    # 1. First correction attempt with still-invalid value (empty string)
    res_partial = hitl_agent.apply_corrections_and_resume(doc_id, {"quantity": ""})
    assert res_partial.workflow_status.value == "HITL_PENDING"

    # 2. Second correction attempt with valid numeric quantity
    res_success = hitl_agent.apply_corrections_and_resume(doc_id, {"quantity": 500})
    assert res_success.workflow_status.value == "MOCK_CREATED"
    assert res_success.mock_result is not None
    assert res_success.mock_result.cw1_reference.startswith("MOCK-CW1-ORD-")

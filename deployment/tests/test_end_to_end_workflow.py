"""
End-to-End Integration Tests for MAF Freight Automation Platform.

Validates the full workflow execution:
  1. Valid local PDF -> Supervisor -> Order Extraction -> Validation -> CW1 Create -> Output JSON.
  2. Carrier confirmation -> Supervisor -> Carrier Extraction -> Order Match -> CW1 Update -> Output JSON.
  3. Scanned PDF -> Supervisor -> HITL Case saved as JSON.
  4. Duplicate detection & idempotency across multiple runs.
  5. Correct persistence of result, payload, review, and log JSON files.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from config import get_settings
from document_processing.pdf_parser import get_pdf_parser
from orchestration.workflow import FreightOrchestrator, get_orchestrator
from schemas.models import DocumentType, ExtractionStatus, WorkflowStatus


@pytest.fixture
def orchestrator() -> FreightOrchestrator:
    return get_orchestrator()


@pytest.fixture
def parser():
    return get_pdf_parser()


@pytest.fixture
def test_data_dir() -> Path:
    return get_settings().test_data_dir / "incoming"


def test_e2e_new_order_pipeline_success(orchestrator: FreightOrchestrator, parser, test_data_dir: Path):
    """Verify end-to-end new order workflow on text-based PDF."""
    settings = get_settings()
    sample_pdf = test_data_dir / "sample.pdf"
    assert sample_pdf.exists()

    # Step 1: Parse
    doc = parser.parse_document(str(sample_pdf))
    assert doc.status.value == "SUCCESS"

    # Step 2: Execute MAF Workflow
    result = orchestrator.execute(doc)

    assert result.document_type == DocumentType.ORDER
    # Either MOCK_CREATED on first run or DUPLICATE_DETECTED on subsequent runs
    assert result.workflow_status in (WorkflowStatus.MOCK_CREATED, WorkflowStatus.DUPLICATE_DETECTED)
    assert result.route_to == "CW1CreateAgent"

    # Verify JSON outputs exist
    result_file = settings.output_results_dir / f"{doc.document_id}_result.json"
    assert result_file.exists(), f"Result JSON must exist at {result_file}"

    with open(result_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["document_id"] == doc.document_id
    assert "extracted_fields" in data


def test_e2e_scanned_pdf_routes_to_hitl(orchestrator: FreightOrchestrator, test_data_dir: Path, monkeypatch):
    """Verify scanned PDF without OCR routes through Supervisor to HITLAgent and saves review JSON."""
    settings = get_settings()
    dominos_pdf = test_data_dir / "dominos.pdf"
    assert dominos_pdf.exists()

    from document_processing.ocr_service import OCRService
    from document_processing.pdf_parser import PDFParser
    no_ocr_service = OCRService()
    monkeypatch.setattr(no_ocr_service, "check_availability", lambda: (False, "Local OCR engine is not installed.", {}))
    custom_parser = PDFParser(ocr_service=no_ocr_service)

    doc = custom_parser.parse_document(str(dominos_pdf))
    assert doc.is_scanned is True
    assert doc.status == ExtractionStatus.OCR_UNAVAILABLE

    result = orchestrator.execute(doc)

    assert result.document_type == DocumentType.UNKNOWN
    assert result.route_to == "HITLAgent"
    assert result.workflow_status == WorkflowStatus.HITL_PENDING
    assert result.hitl_details is not None

    review_file = settings.output_reviews_dir / f"{doc.document_id}_review.json"
    assert review_file.exists(), f"Review JSON must exist at {review_file}"


def test_e2e_carrier_document_matching_and_update(orchestrator: FreightOrchestrator, parser, test_data_dir: Path):
    """Verify carrier document updates matched order in CargoWise."""
    carrier_json = test_data_dir / "carrier_update.json"
    assert carrier_json.exists()

    doc = parser.parse_document(str(carrier_json))
    assert doc.status.value == "SUCCESS"

    result = orchestrator.execute(doc)

    assert result.document_type == DocumentType.CARRIER_DOCUMENT
    assert result.route_to == "CW1UpdateAgent"
    assert result.workflow_status in (WorkflowStatus.MOCK_UPDATED, WorkflowStatus.HITL_PENDING)


def test_e2e_idempotency_protection(orchestrator: FreightOrchestrator, parser, test_data_dir: Path):
    """Verify repeated execution of the same document is idempotent and prevents duplicate creation."""
    sample_pdf = test_data_dir / "sample.pdf"
    doc = parser.parse_document(str(sample_pdf))

    # Run twice
    res1 = orchestrator.execute(doc)
    res2 = orchestrator.execute(doc)

    assert res2.workflow_status == WorkflowStatus.DUPLICATE_DETECTED
    assert res2.duplicate_status == "DUPLICATE_FILE_DETECTED"
    assert res2.mock_result is not None
    assert res2.mock_result.status == "DUPLICATE_DETECTED"

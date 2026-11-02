"""
Unit and Integration Tests for Local Document Processing and Extraction.

Validates:
  1. Text-based PDF extraction fidelity (characters, pages, hash, metadata).
  2. Scanned PDF detection (zero chars, images present, OCR needed).
  3. OCR service availability checking and clear diagnostic guidance.
  4. Unsupported file extension rejection.
  5. Missing file handling without exceptions.
  6. Empty/corrupted file detection.
  7. JSON document ingestion.
  8. Plain text document ingestion.
  9. Deterministic document ID generation (idempotency key generation).
  10. Local JSON persistence verification.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from config import get_settings
from document_processing.ocr_service import OCRService, get_ocr_service
from document_processing.pdf_parser import PDFParser, get_pdf_parser
from main import run_document_extraction
from schemas.models import ExtractionStatus


@pytest.fixture
def parser() -> PDFParser:
    return get_pdf_parser()


@pytest.fixture
def ocr_service() -> OCRService:
    return get_ocr_service()


@pytest.fixture
def test_data_dir() -> Path:
    return get_settings().test_data_dir / "incoming"


def test_text_pdf_extraction_success(parser: PDFParser, test_data_dir: Path):
    """Verify that a standard text-based PDF is read and extracted accurately."""
    sample_pdf = test_data_dir / "sample.pdf"
    assert sample_pdf.exists(), "Test fixture sample.pdf must exist"

    doc = parser.parse_document(str(sample_pdf))

    assert doc.status == ExtractionStatus.SUCCESS
    assert doc.is_scanned is False
    assert doc.ocr_applied is False
    assert doc.total_pages == 1
    assert len(doc.extracted_text) > 200
    assert "HAVI LOGISTICS" in doc.extracted_text
    assert "PO-998877" in doc.extracted_text
    assert doc.file_name == "sample.pdf"
    assert len(doc.file_hash_sha256) == 64
    assert doc.document_id.startswith("DOC-")
    assert doc.error_message is None


def test_scanned_pdf_detection_and_ocr_fallback(test_data_dir: Path, monkeypatch):
    """Verify that a scanned PDF with 0 text characters is identified and not fabricated when OCR is unavailable."""
    dominos_pdf = test_data_dir / "dominos.pdf"
    assert dominos_pdf.exists(), "Test fixture dominos.pdf must exist"

    from document_processing.ocr_service import OCRService
    no_ocr_service = OCRService()
    monkeypatch.setattr(no_ocr_service, "check_availability", lambda: (False, "Local OCR engine is not installed.", {}))
    custom_parser = PDFParser(ocr_service=no_ocr_service)

    doc = custom_parser.parse_document(str(dominos_pdf))

    # Scanned detection
    assert doc.is_scanned is True
    assert doc.total_pages == 1
    assert len(doc.extracted_text) == 0  # Crucial: zero characters fabricated
    assert doc.status in (ExtractionStatus.OCR_UNAVAILABLE, ExtractionStatus.SCANNED_NEEDS_OCR)
    assert doc.ocr_applied is False

    # Check setup warnings
    assert len(doc.warnings) >= 1
    assert any("scanned" in w.lower() for w in doc.warnings)
    assert doc.error_message is not None
    assert "OCR fallback is required" in doc.error_message


def test_ocr_service_availability_check(ocr_service: OCRService):
    """Verify OCRService reports diagnostic instructions when Tesseract is uninstalled."""
    avail, msg, details = ocr_service.check_availability()
    # On this machine, Tesseract is not installed on PATH
    assert isinstance(avail, bool)
    assert isinstance(msg, str)
    assert isinstance(details, dict)
    if not avail:
        assert "winget install" in msg or "Tesseract" in msg
        assert "pip install pytesseract" in msg


def test_missing_file_handling(parser: PDFParser):
    """Verify that a non-existent file returns FILE_NOT_FOUND without crashing."""
    doc = parser.parse_document("non_existent_folder/missing_file.pdf")

    assert doc.status == ExtractionStatus.FILE_NOT_FOUND
    assert doc.error_message is not None
    assert "does not exist" in doc.error_message
    assert doc.total_pages == 0


def test_empty_corrupted_file_handling(parser: PDFParser, test_data_dir: Path):
    """Verify that a 0-byte file returns CORRUPTED without crashing."""
    empty_pdf = test_data_dir / "empty_corrupted.pdf"
    assert empty_pdf.exists()

    doc = parser.parse_document(str(empty_pdf))

    assert doc.status == ExtractionStatus.CORRUPTED
    assert doc.error_message is not None
    assert "empty" in doc.error_message.lower()


def test_unsupported_file_extension(parser: PDFParser, tmp_path: Path):
    """Verify that unsupported file extensions are safely rejected."""
    bad_file = tmp_path / "payload.exe"
    bad_file.write_text("binary content", encoding="utf-8")

    doc = parser.parse_document(str(bad_file))

    assert doc.status == ExtractionStatus.UNSUPPORTED
    assert doc.error_message is not None
    assert "Unsupported file extension" in doc.error_message


def test_json_document_ingestion(parser: PDFParser, test_data_dir: Path):
    """Verify that JSON format documents are ingested and formatted."""
    json_file = test_data_dir / "sample_order.json"
    assert json_file.exists()

    doc = parser.parse_document(str(json_file))

    assert doc.status == ExtractionStatus.SUCCESS
    assert doc.is_scanned is False
    assert "PO-100200" in doc.extracted_text
    assert "HAVI MCD" in doc.extracted_text
    assert doc.mime_type == "application/json"


def test_plain_text_document_ingestion(parser: PDFParser, tmp_path: Path):
    """Verify plain text document reading."""
    txt_file = tmp_path / "order_manifest.txt"
    txt_file.write_text("Order PO-556677 for Customer McD", encoding="utf-8")

    doc = parser.parse_document(str(txt_file))

    assert doc.status == ExtractionStatus.SUCCESS
    assert doc.extracted_text == "Order PO-556677 for Customer McD"
    assert doc.mime_type == "text/plain"


def test_deterministic_document_id(parser: PDFParser, test_data_dir: Path):
    """Verify that parsing the same file twice produces the same SHA-256 and Document ID."""
    sample_pdf = test_data_dir / "sample.pdf"

    doc1 = parser.parse_document(str(sample_pdf))
    doc2 = parser.parse_document(str(sample_pdf))

    assert doc1.file_hash_sha256 == doc2.file_hash_sha256
    assert doc1.document_id == doc2.document_id
    assert doc1.document_id == f"DOC-{doc1.file_hash_sha256[:8].upper()}"


def test_cli_execution_runner(test_data_dir: Path):
    """Verify main.py CLI runner saves result JSON to output/results/ directory."""
    settings = get_settings()
    sample_pdf = test_data_dir / "sample.pdf"

    exit_code = run_document_extraction(str(sample_pdf), save_output=True)

    assert exit_code == 0
    # Find generated JSON
    results = list(settings.output_results_dir.glob("DOC-*_result.json"))
    assert len(results) > 0

    latest = max(results, key=lambda p: p.stat().st_mtime)
    with open(latest, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert "document_id" in data
    assert data["source_filename"] == "sample.pdf"
    assert data["extraction"]["status"] == "SUCCESS"

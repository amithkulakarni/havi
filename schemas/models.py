"""
Domain Schemas and Data Models.

Defines typed Pydantic models for document ingestion, page extractions,
field-level extractions with confidence scores, validation outcomes,
routing results, mock CargoWise payloads, and HITL review packages.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class DocumentType(str, Enum):
    """Business classifications for ingested logistics documents."""
    ORDER = "ORDER"
    NEW_ORDER = "NEW_ORDER"
    ORDER_UPDATE = "ORDER_UPDATE"
    CARRIER_DOCUMENT = "CARRIER_DOCUMENT"
    CARRIER_CONFIRMATION = "CARRIER_CONFIRMATION"
    UNKNOWN = "UNKNOWN"


class ExtractionStatus(str, Enum):
    """Status of the document reading and text extraction phase."""
    SUCCESS = "SUCCESS"
    SCANNED_NEEDS_OCR = "SCANNED_NEEDS_OCR"
    OCR_UNAVAILABLE = "OCR_UNAVAILABLE"
    CORRUPTED = "CORRUPTED"
    UNSUPPORTED = "UNSUPPORTED"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"


class WorkflowStatus(str, Enum):
    """End-to-end lifecycle status of a document."""
    PENDING = "PENDING"
    EXTRACTED = "EXTRACTED"
    CLASSIFIED = "CLASSIFIED"
    VALIDATED = "VALIDATED"
    HITL_PENDING = "HITL_PENDING"
    MOCK_READY = "MOCK_READY"
    MOCK_CREATED = "MOCK_CREATED"
    MOCK_UPDATED = "MOCK_UPDATED"
    DUPLICATE_DETECTED = "DUPLICATE_DETECTED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    PROCESSING_FAILED = "PROCESSING_FAILED"
    FAILED = "FAILED"


class PageExtraction(BaseModel):
    """Extracted text and structural attributes for an individual page."""
    page_number: int = Field(..., description="1-indexed page number")
    text: str = Field(default="", description="Extracted textual content")
    char_count: int = Field(default=0, description="Character count extracted")
    image_count: int = Field(default=0, description="Number of embedded raster images")
    has_images: bool = Field(default=False, description="True if page contains images")
    is_scanned_page: bool = Field(default=False, description="True if page is primarily image with little/no text")


class ExtractedDocument(BaseModel):
    """Result of document reading, parsing, and text extraction."""
    document_id: str = Field(..., description="Unique deterministic identifier e.g. DOC-XXXX")
    source_file_path: str = Field(..., description="Absolute path to input file")
    file_name: str = Field(..., description="Original filename")
    source_filename: Optional[str] = Field(default=None, description="Filename alias")
    file_size_bytes: int = Field(..., ge=0, description="File size in bytes")
    file_hash_sha256: str = Field(..., description="SHA-256 hash of file content for idempotency")
    mime_type: str = Field(..., description="Detected MIME type")
    total_pages: int = Field(default=1, ge=0, description="Total document pages")
    extracted_text: str = Field(default="", description="Combined text content across pages")
    pages: List[PageExtraction] = Field(default_factory=list, description="Page-level details")
    status: ExtractionStatus = Field(default=ExtractionStatus.SUCCESS)
    is_scanned: bool = Field(default=False, description="True if document appears to be scanned/image-only")
    ocr_applied: bool = Field(default=False, description="True if OCR was executed to obtain text")
    error_message: Optional[str] = Field(default=None, description="Detailed error message if failed")
    warnings: List[str] = Field(default_factory=list, description="Warnings or setup notices")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SupervisorRoutingResult(BaseModel):
    """Structured routing and classification decision from Supervisor Agent."""
    document_id: str
    document_type: DocumentType
    route_to: str
    confidence: float = Field(default=0.90, ge=0.0, le=1.0)
    status: str = "CLASSIFIED"
    reasoning: Optional[str] = None


class FieldExtraction(BaseModel):
    """Single extracted business field with evidence and confidence score."""
    value: Optional[Any] = Field(default=None, description="Normalized or extracted value")
    raw_value: Optional[str] = Field(default=None, description="Exact text verbatim from source")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Extraction confidence score (0.0 to 1.0)")
    page_number: Optional[int] = Field(default=None, description="Page number where field was located")
    is_missing: bool = Field(default=False, description="True if field was not present in document")
    is_uncertain: bool = Field(default=False, description="True if confidence is below threshold")


class ValidationResult(BaseModel):
    """Result of mandatory-field and business rules validation."""
    is_valid: bool = Field(default=True)
    overall_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    missing_mandatory_fields: List[str] = Field(default_factory=list)
    low_confidence_fields: List[str] = Field(default_factory=list)
    validation_errors: List[str] = Field(default_factory=list)
    requires_hitl: bool = Field(default=False)


class CW1OperationResult(BaseModel):
    """Outcome of a mock CargoWise One create or update operation."""
    operation_type: str = Field(..., description="CREATE or UPDATE")
    status: str = Field(..., description="MOCK_CREATED, MOCK_UPDATED, DUPLICATE_DETECTED, etc.")
    cw1_reference: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class HITLReviewPackage(BaseModel):
    """Human-in-the-Loop review package saved to output/reviews/<doc_id>_review.json."""
    document_id: str
    source_filename: str
    raised_by_agent: str
    status: str = "HITL_PENDING"  # HITL_PENDING, RESOLVED, REJECTED
    missing_fields: List[str] = Field(default_factory=list)
    low_confidence_fields: List[str] = Field(default_factory=list)
    validation_errors: List[str] = Field(default_factory=list)
    original_extracted_values: Dict[str, Any] = Field(default_factory=dict)
    human_corrections: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: Optional[str] = None


class ProcessingResult(BaseModel):
    """Comprehensive execution result persisted to output/results/<doc_id>_result.json."""
    document_id: str
    source_filename: str
    file_hash_sha256: str
    document_type: DocumentType = DocumentType.UNKNOWN
    extraction: ExtractedDocument
    extracted_fields: Dict[str, Any] = Field(default_factory=dict)
    field_confidence: Dict[str, float] = Field(default_factory=dict)
    validation: Optional[ValidationResult] = None
    duplicate_status: Optional[str] = None
    workflow_status: WorkflowStatus = WorkflowStatus.PENDING
    route_to: Optional[str] = None
    hitl_details: Optional[HITLReviewPackage] = None
    mock_result: Optional[CW1OperationResult] = None
    timestamps: Dict[str, str] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)
    history: List[Dict[str, Any]] = Field(default_factory=list)

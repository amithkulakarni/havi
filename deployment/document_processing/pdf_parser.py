"""
Local PDF and Document Parser.

Provides local text extraction using pdfplumber and pypdf,
identifies scanned documents vs. text-based documents,
extracts page-level metrics and embedded images,
and interfaces with OCRService for fallback processing.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import mimetypes
from pathlib import Path
from typing import List, Optional, Tuple

import pdfplumber
import pypdf

from config import Settings, get_settings
from document_processing.ocr_service import OCRService, get_ocr_service
from schemas.models import ExtractedDocument, ExtractionStatus, PageExtraction

logger = logging.getLogger("havi.document_processing.parser")


class PDFParser:
    """Robust local parser for PDFs, images, text, and JSON documents."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        ocr_service: Optional[OCRService] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.ocr_service = ocr_service or get_ocr_service()

    def parse_document(self, file_path_str: str) -> ExtractedDocument:
        """
        Validate, inspect, and extract textual content and metadata from a local file.

        Args:
            file_path_str: Path to the target document.

        Returns:
            ExtractedDocument containing text, page details, and status.
        """
        path = Path(file_path_str).resolve()
        logger.info("[DOC_PARSE_START] Parsing document at path: %s", path)

        # 1. Existence Check
        if not path.exists() or not path.is_file():
            err = f"File does not exist or is not a regular file: {file_path_str}"
            logger.error("[FILE_NOT_FOUND] %s", err)
            return ExtractedDocument(
                document_id="DOC-UNKNOWN",
                source_file_path=str(path),
                file_name=path.name,
                file_size_bytes=0,
                file_hash_sha256="",
                mime_type="unknown",
                total_pages=0,
                status=ExtractionStatus.FILE_NOT_FOUND,
                error_message=err,
            )

        # 2. File Size & Corruption Check
        file_size = path.stat().st_size
        if file_size == 0:
            err = f"File is empty (0 bytes): {path.name}"
            logger.error("[CORRUPTED_FILE] %s", err)
            return ExtractedDocument(
                document_id="DOC-EMPTY",
                source_file_path=str(path),
                file_name=path.name,
                file_size_bytes=0,
                file_hash_sha256="",
                mime_type="application/octet-stream",
                total_pages=0,
                status=ExtractionStatus.CORRUPTED,
                error_message=err,
            )

        # 3. Compute SHA-256 Hash and Document ID
        raw_bytes = path.read_bytes()
        file_hash = hashlib.sha256(raw_bytes).hexdigest()
        doc_id = f"DOC-{file_hash[:8].upper()}"

        # 4. Extension & MIME Validation
        ext = path.suffix.lower()
        if ext not in self.settings.supported_extensions:
            err = (
                f"Unsupported file extension '{ext}' for file {path.name}. "
                f"Supported: {self.settings.supported_extensions}"
            )
            logger.error("[UNSUPPORTED_FORMAT] %s", err)
            return ExtractedDocument(
                document_id=doc_id,
                source_file_path=str(path),
                file_name=path.name,
                file_size_bytes=file_size,
                file_hash_sha256=file_hash,
                mime_type=mimetypes.guess_type(str(path))[0] or "application/octet-stream",
                total_pages=0,
                status=ExtractionStatus.UNSUPPORTED,
                error_message=err,
            )

        mime_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"

        # 5. Route by format
        if ext == ".pdf":
            return self._parse_pdf(path, doc_id, file_hash, file_size, mime_type, raw_bytes)
        elif ext in (".json",):
            return self._parse_json(path, doc_id, file_hash, file_size, raw_bytes)
        elif ext in (".txt",):
            return self._parse_txt(path, doc_id, file_hash, file_size, raw_bytes)
        elif ext in (".png", ".jpg", ".jpeg"):
            return self._parse_image(path, doc_id, file_hash, file_size, mime_type, raw_bytes)
        else:
            return ExtractedDocument(
                document_id=doc_id,
                source_file_path=str(path),
                file_name=path.name,
                file_size_bytes=file_size,
                file_hash_sha256=file_hash,
                mime_type=mime_type,
                total_pages=0,
                status=ExtractionStatus.UNSUPPORTED,
                error_message=f"No parser handler implemented for {ext}",
            )

    def _parse_pdf(
        self,
        path: Path,
        doc_id: str,
        file_hash: str,
        file_size: int,
        mime_type: str,
        raw_bytes: bytes,
    ) -> ExtractedDocument:
        """Extract text, inspect images, and detect scanned state for PDF documents."""
        pages_extracted: List[PageExtraction] = []
        warnings: List[str] = []

        try:
            with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
                total_pages = len(pdf.pages)
                total_chars = 0
                total_images = 0

                for idx, page in enumerate(pdf.pages, start=1):
                    page_text = page.extract_text() or ""
                    char_count = len(page_text.strip())
                    image_count = len(page.images)
                    has_images = image_count > 0

                    is_scanned_page = (
                        char_count < self.settings.scanned_char_threshold and has_images
                    )

                    pages_extracted.append(
                        PageExtraction(
                            page_number=idx,
                            text=page_text,
                            char_count=char_count,
                            image_count=image_count,
                            has_images=has_images,
                            is_scanned_page=is_scanned_page,
                        )
                    )

                    total_chars += char_count
                    total_images += image_count

        except Exception as exc:
            logger.warning("[PDFPLUMBER_NOTICE] pdfplumber failed on %s (%s). Attempting pypdf fallback...", path.name, exc)
            try:
                reader = pypdf.PdfReader(io.BytesIO(raw_bytes))
                total_pages = len(reader.pages)
                total_chars = 0
                total_images = 0
                pages_extracted.clear()

                for idx, page in enumerate(reader.pages, start=1):
                    page_text = page.extract_text() or ""
                    char_count = len(page_text.strip())
                    img_count = len(page.images) if hasattr(page, "images") else 0
                    has_images = img_count > 0
                    is_scanned_page = char_count < self.settings.scanned_char_threshold and has_images

                    pages_extracted.append(
                        PageExtraction(
                            page_number=idx,
                            text=page_text,
                            char_count=char_count,
                            image_count=img_count,
                            has_images=has_images,
                            is_scanned_page=is_scanned_page,
                        )
                    )
                    total_chars += char_count
                    total_images += img_count

            except Exception as fallback_exc:
                err_msg = f"Failed to parse PDF document structure: {fallback_exc}"
                logger.error("[PDF_CORRUPT] %s", err_msg)
                return ExtractedDocument(
                    document_id=doc_id,
                    source_file_path=str(path),
                    file_name=path.name,
                    file_size_bytes=file_size,
                    file_hash_sha256=file_hash,
                    mime_type=mime_type,
                    total_pages=0,
                    status=ExtractionStatus.CORRUPTED,
                    error_message=err_msg,
                )

        # Evaluate if the document as a whole is scanned
        is_scanned = (
            total_chars == 0 and total_images > 0
        ) or (
            total_pages > 0 and all(p.is_scanned_page for p in pages_extracted)
        )

        combined_text = "\n\n--- Page Break ---\n\n".join(
            p.text for p in pages_extracted if p.text.strip()
        ).strip()

        # Handle scanned document without text
        if is_scanned or (total_chars == 0):
            logger.info("[SCANNED_PDF_DETECTED] Document %s has 0 extractable characters and %d images.", path.name, total_images)

            # Check OCR provider availability
            is_avail, ocr_message, details = self.ocr_service.check_availability()
            if not is_avail:
                warnings.append("Document appears to be a scanned image with 0 native text characters.")
                warnings.append(ocr_message)
                return ExtractedDocument(
                    document_id=doc_id,
                    source_file_path=str(path),
                    file_name=path.name,
                    file_size_bytes=file_size,
                    file_hash_sha256=file_hash,
                    mime_type=mime_type,
                    total_pages=total_pages,
                    extracted_text="",
                    pages=pages_extracted,
                    status=ExtractionStatus.OCR_UNAVAILABLE,
                    is_scanned=True,
                    ocr_applied=False,
                    error_message=(
                        f"Scanned document detected ({total_images} image(s), 0 text characters). "
                        "OCR fallback is required but no local OCR engine is configured."
                    ),
                    warnings=warnings,
                )
            else:
                # OCR is available: attempt page-by-page OCR via Azure AI Vision or local engine
                logger.info("[OCR_TRIGGERED] Attempting OCR fallback for %s...", path.name)
                ocr_text_pages: List[str] = []

                # Extract images using pypdf or render via pdfplumber
                try:
                    reader = pypdf.PdfReader(io.BytesIO(raw_bytes))
                    for p_idx, page in enumerate(reader.pages, start=1):
                        imgs = getattr(page, "images", [])
                        if imgs:
                            for img in imgs:
                                success, text, err = self.ocr_service.extract_text_from_image_bytes(img.data)
                                if success and text:
                                    ocr_text_pages.append(text)
                        else:
                            # Render page directly
                            try:
                                im = pdf.pages[p_idx - 1].to_image(resolution=150)
                                buf = io.BytesIO()
                                im.original.save(buf, format="PNG")
                                success, text, err = self.ocr_service.extract_text_from_image_bytes(buf.getvalue())
                                if success and text:
                                    ocr_text_pages.append(text)
                            except Exception as render_exc:
                                logger.warning("[PDF_RENDER_WARN] Page %d render failed: %s", p_idx, render_exc)
                except Exception as pypdf_img_exc:
                    logger.warning("[PDF_IMAGE_EXTRACT_WARN] Could not extract images via pypdf (%s); attempting page render.", pypdf_img_exc)
                    try:
                        for p_idx, page in enumerate(pdf.pages, start=1):
                            im = page.to_image(resolution=150)
                            buf = io.BytesIO()
                            im.original.save(buf, format="PNG")
                            success, text, err = self.ocr_service.extract_text_from_image_bytes(buf.getvalue())
                            if success and text:
                                ocr_text_pages.append(text)
                    except Exception as fallback_render_exc:
                        logger.error("[PDF_PAGE_RENDER_FAILED] Fallback render failed: %s", fallback_render_exc)

                if ocr_text_pages:
                    combined_ocr = "\n".join(ocr_text_pages).strip()
                    return ExtractedDocument(
                        document_id=doc_id,
                        source_file_path=str(path),
                        file_name=path.name,
                        file_size_bytes=file_size,
                        file_hash_sha256=file_hash,
                        mime_type=mime_type,
                        total_pages=total_pages,
                        extracted_text=combined_ocr,
                        pages=pages_extracted,
                        status=ExtractionStatus.SUCCESS,
                        is_scanned=True,
                        ocr_applied=True,
                        warnings=["Extracted using local OCR engine."],
                    )
                else:
                    return ExtractedDocument(
                        document_id=doc_id,
                        source_file_path=str(path),
                        file_name=path.name,
                        file_size_bytes=file_size,
                        file_hash_sha256=file_hash,
                        mime_type=mime_type,
                        total_pages=total_pages,
                        extracted_text="",
                        pages=pages_extracted,
                        status=ExtractionStatus.EXTRACTION_FAILED,
                        is_scanned=True,
                        ocr_applied=True,
                        error_message="OCR engine executed but could not resolve characters from image streams.",
                    )

        # Standard text-based PDF
        logger.info("[PDF_EXTRACT_SUCCESS] Extracted %d characters across %d pages for %s.", len(combined_text), total_pages, path.name)
        return ExtractedDocument(
            document_id=doc_id,
            source_file_path=str(path),
            file_name=path.name,
            file_size_bytes=file_size,
            file_hash_sha256=file_hash,
            mime_type=mime_type,
            total_pages=total_pages,
            extracted_text=combined_text,
            pages=pages_extracted,
            status=ExtractionStatus.SUCCESS,
            is_scanned=False,
            ocr_applied=False,
            warnings=warnings,
        )

    def _parse_json(
        self,
        path: Path,
        doc_id: str,
        file_hash: str,
        file_size: int,
        raw_bytes: bytes,
    ) -> ExtractedDocument:
        """Extract structured JSON documents."""
        try:
            data = json.loads(raw_bytes.decode("utf-8", errors="replace"))
            formatted = json.dumps(data, indent=2)
            page = PageExtraction(
                page_number=1,
                text=formatted,
                char_count=len(formatted),
                image_count=0,
                has_images=False,
                is_scanned_page=False,
            )
            return ExtractedDocument(
                document_id=doc_id,
                source_file_path=str(path),
                file_name=path.name,
                file_size_bytes=file_size,
                file_hash_sha256=file_hash,
                mime_type="application/json",
                total_pages=1,
                extracted_text=formatted,
                pages=[page],
                status=ExtractionStatus.SUCCESS,
                is_scanned=False,
            )
        except Exception as exc:
            return ExtractedDocument(
                document_id=doc_id,
                source_file_path=str(path),
                file_name=path.name,
                file_size_bytes=file_size,
                file_hash_sha256=file_hash,
                mime_type="application/json",
                total_pages=0,
                status=ExtractionStatus.CORRUPTED,
                error_message=f"Invalid JSON payload: {exc}",
            )

    def _parse_txt(
        self,
        path: Path,
        doc_id: str,
        file_hash: str,
        file_size: int,
        raw_bytes: bytes,
    ) -> ExtractedDocument:
        """Extract plain text documents."""
        text = raw_bytes.decode("utf-8", errors="replace").strip()
        page = PageExtraction(
            page_number=1,
            text=text,
            char_count=len(text),
            image_count=0,
            has_images=False,
            is_scanned_page=False,
        )
        return ExtractedDocument(
            document_id=doc_id,
            source_file_path=str(path),
            file_name=path.name,
            file_size_bytes=file_size,
            file_hash_sha256=file_hash,
            mime_type="text/plain",
            total_pages=1,
            extracted_text=text,
            pages=[page],
            status=ExtractionStatus.SUCCESS,
            is_scanned=False,
        )

    def _parse_image(
        self,
        path: Path,
        doc_id: str,
        file_hash: str,
        file_size: int,
        mime_type: str,
        raw_bytes: bytes,
    ) -> ExtractedDocument:
        """Handle standalone raster images (PNG, JPG)."""
        page = PageExtraction(
            page_number=1,
            text="",
            char_count=0,
            image_count=1,
            has_images=True,
            is_scanned_page=True,
        )

        is_avail, ocr_message, details = self.ocr_service.check_availability()
        if not is_avail:
            return ExtractedDocument(
                document_id=doc_id,
                source_file_path=str(path),
                file_name=path.name,
                file_size_bytes=file_size,
                file_hash_sha256=file_hash,
                mime_type=mime_type,
                total_pages=1,
                extracted_text="",
                pages=[page],
                status=ExtractionStatus.OCR_UNAVAILABLE,
                is_scanned=True,
                ocr_applied=False,
                error_message=f"Image file '{path.name}' requires local OCR, but OCR engine is unavailable.",
                warnings=[ocr_message],
            )

        success, text, err = self.ocr_service.extract_text_from_image_bytes(raw_bytes)
        if success and text:
            page.text = text
            page.char_count = len(text)
            return ExtractedDocument(
                document_id=doc_id,
                source_file_path=str(path),
                file_name=path.name,
                file_size_bytes=file_size,
                file_hash_sha256=file_hash,
                mime_type=mime_type,
                total_pages=1,
                extracted_text=text,
                pages=[page],
                status=ExtractionStatus.SUCCESS,
                is_scanned=True,
                ocr_applied=True,
            )
        else:
            return ExtractedDocument(
                document_id=doc_id,
                source_file_path=str(path),
                file_name=path.name,
                file_size_bytes=file_size,
                file_hash_sha256=file_hash,
                mime_type=mime_type,
                total_pages=1,
                extracted_text="",
                pages=[page],
                status=ExtractionStatus.EXTRACTION_FAILED,
                is_scanned=True,
                ocr_applied=True,
                error_message=err or "OCR engine failed to recognize text.",
            )


_pdf_parser: Optional[PDFParser] = None


def get_pdf_parser() -> PDFParser:
    """Singleton getter for PDFParser."""
    global _pdf_parser
    if _pdf_parser is None:
        _pdf_parser = PDFParser()
    return _pdf_parser

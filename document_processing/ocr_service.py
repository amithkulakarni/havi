"""
Local OCR Service and Fallback Abstraction.

Responsible for detecting local OCR engine availability (Tesseract / EasyOCR),
executing optical character recognition when configured, and providing explicit,
actionable setup instructions when no OCR provider is installed on the host system.
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import Settings, get_settings

logger = logging.getLogger("havi.document_processing.ocr")


class OCRService:
    """Manages local OCR execution and provides explicit diagnostics."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()

    def check_availability(self) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Check whether a local OCR engine is configured and executable.

        Returns:
            (is_available, diagnostic_message, details_dict)
        """
        # 1. Check custom path from settings/env
        custom_tesseract = self.settings.tesseract_cmd
        if custom_tesseract and Path(custom_tesseract).is_file():
            return True, f"Tesseract found at configured path: {custom_tesseract}", {
                "engine": "tesseract",
                "executable": custom_tesseract,
                "configured": True,
            }

        # 2. Check standard system PATH for 'tesseract' binary
        system_tesseract = shutil.which("tesseract")
        if system_tesseract:
            return True, f"Tesseract CLI found on system PATH: {system_tesseract}", {
                "engine": "tesseract",
                "executable": system_tesseract,
                "configured": False,
            }

        # 3. Check well-known Windows install paths
        default_windows_paths = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
        ]
        for candidate in default_windows_paths:
            if candidate and Path(candidate).is_file():
                return True, f"Tesseract found at standard location: {candidate}", {
                    "engine": "tesseract",
                    "executable": candidate,
                    "configured": False,
                }

        # 4. Check Azure AI Vision (gpt-4.1-mini multimodal vision)
        from adapters.model_client import get_model_client_manager
        mgr = get_model_client_manager()
        if mgr.is_configured():
            model_name = self.settings.azure_ai_model_deployment_name or "gpt-4.1-mini"
            return True, f"Azure AI Vision ({model_name}) multimodal OCR engine active", {
                "engine": "azure_ai_vision",
                "executable": None,
                "model": model_name,
                "configured": True,
            }

        instructions = (
            "Local OCR engine is not installed and Azure AI endpoint is not configured.\n"
            "To enable local OCR for scanned PDFs and image files on Windows:\n"
            "  1. Install Tesseract OCR: Run in CMD/PowerShell:\n"
            "       winget install UB-Mannheim.TesseractOCR\n"
            "  2. Or download installer directly from:\n"
            "       https://github.com/UB-Mannheim/tesseract/wiki\n"
            "  3. Configure path in .env file:\n"
            '       TESSERACT_CMD="C:\\Program Files\\Tesseract-OCR\\tesseract.exe"\n'
            "  4. Ensure pytesseract package is installed:\n"
            "       pip install pytesseract"
        )

        return False, instructions, {
            "engine": None,
            "executable": None,
            "pytesseract_installed": shutil.which("tesseract") is not None,
            "configured": False,
        }

    def extract_text_from_image_bytes(self, image_bytes: bytes) -> Tuple[bool, str, Optional[str]]:
        """
        Attempt OCR on raw image bytes using Azure AI Vision or local Tesseract.

        Returns:
            (success, text, error_detail)
        """
        is_avail, message, details = self.check_availability()
        if not is_avail:
            logger.warning("[OCR_UNAVAILABLE] Scanned image received but OCR engine is unavailable.")
            return False, "", message

        engine = details.get("engine")
        if engine == "azure_ai_vision":
            from adapters.model_client import get_model_client_manager
            mgr = get_model_client_manager()
            transcription = mgr.complete_vision_sync(
                image_bytes,
                prompt="Transcribe all text, numbers, codes, dates, tables, items, remarks, and details from this document image accurately verbatim without commentary.",
            )
            if transcription and transcription.strip():
                logger.info("[AZURE_VISION_SUCCESS] Transcribed %d characters from image.", len(transcription))
                return True, transcription.strip(), None
            return False, "", "Azure AI Vision transcription returned empty response."

        tesseract_bin = details.get("executable")

        try:
            from PIL import Image

            image = Image.open(io.BytesIO(image_bytes))

            # If pytesseract is available, prefer it with the detected binary
            try:
                import pytesseract  # type: ignore # pyrefly: ignore [missing-import]

                if tesseract_bin:
                    pytesseract.pytesseract.tesseract_cmd = tesseract_bin
                text = pytesseract.image_to_string(image)
                return True, text.strip(), None
            except ImportError:
                # Direct subprocess fallback using CLI if pytesseract library is missing
                if tesseract_bin:
                    temp_img_path = self.settings.output_dir / "_temp_ocr.png"
                    image.save(temp_img_path, format="PNG")
                    cmd = [tesseract_bin, str(temp_img_path), "stdout", "--oem", "1", "-l", "eng"]
                    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
                    if temp_img_path.exists():
                        temp_img_path.unlink()
                    return True, proc.stdout.strip(), None

        except Exception as exc:
            err_msg = f"OCR execution failed on image: {exc}"
            logger.error("[OCR_FAILED] %s", err_msg)
            return False, "", err_msg

        return False, "", "No suitable OCR execution strategy available."


_ocr_service: Optional[OCRService] = None


def get_ocr_service() -> OCRService:
    """Singleton getter for OCRService."""
    global _ocr_service
    if _ocr_service is None:
        _ocr_service = OCRService()
    return _ocr_service

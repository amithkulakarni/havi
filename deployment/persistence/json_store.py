"""
Local JSON File Store & Output Manager.

Handles persistence of:
  - Final execution results: output/results/<document_id>_result.json
  - Proposed CargoWise payloads: output/payloads/<document_id>_cw1_payload.json
  - Human review cases: output/reviews/<document_id>_review.json
  - Workflow logs: output/logs/<document_id>_workflow.log

Operates with zero cloud storage / Azure Blob dependencies.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import Settings, get_settings
from schemas.models import HITLReviewPackage, ProcessingResult

logger = logging.getLogger("havi.persistence.json_store")


class JSONStore:
    """Manages file storage for all outputs in local output directories."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.settings.ensure_directories()

    def save_result(self, result: ProcessingResult) -> Path:
        """Save complete processing result to output/results/<doc_id>_result.json."""
        file_path = self.settings.output_results_dir / f"{result.document_id}_result.json"
        content_str = result.model_dump_json(indent=2)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content_str)
        logger.info("[RESULT_SAVED] Persisted execution result: %s", file_path)
        return file_path

    def save_payload(self, document_id: str, payload_data: Dict[str, Any]) -> Path:
        """Save proposed CargoWise payload to output/payloads/<doc_id>_cw1_payload.json."""
        file_path = self.settings.output_payloads_dir / f"{document_id}_cw1_payload.json"
        content_str = json.dumps(payload_data, indent=2)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content_str)
        logger.info("[PAYLOAD_SAVED] Persisted CW1 payload: %s", file_path)
        return file_path

    def save_review(self, review: HITLReviewPackage) -> Path:
        """Save HITL review case to output/reviews/<doc_id>_review.json."""
        file_path = self.settings.output_reviews_dir / f"{review.document_id}_review.json"
        content_str = review.model_dump_json(indent=2)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content_str)
        logger.info("[REVIEW_SAVED] Persisted HITL review case: %s", file_path)
        return file_path

    def load_review(self, document_id: str) -> Optional[HITLReviewPackage]:
        """Load an existing HITL review package by document ID from disk."""
        file_path = self.settings.output_reviews_dir / f"{document_id}_review.json"
        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return HITLReviewPackage(**data)
        return None

    def list_reviews(self, status_filter: Optional[str] = None) -> List[HITLReviewPackage]:
        """List review cases from local disk."""
        reviews: List[HITLReviewPackage] = []
        if self.settings.output_reviews_dir.exists():
            for review_file in self.settings.output_reviews_dir.glob("*_review.json"):
                try:
                    with open(review_file, "r", encoding="utf-8") as f:
                        pkg = HITLReviewPackage(**json.load(f))
                        if status_filter is None or pkg.status == status_filter:
                            reviews.append(pkg)
                except Exception as exc:
                    logger.warning("[REVIEW_LOAD_ERR] Failed reading %s: %s", review_file, exc)
        return reviews

    def list_results(self) -> List[Dict[str, Any]]:
        """List all processed results from output/results/ ordered newest first."""
        results: List[Dict[str, Any]] = []
        if self.settings.output_results_dir.exists():
            files = sorted(
                self.settings.output_results_dir.glob("*_result.json"),
                key=os.path.getmtime,
                reverse=True,
            )
            for res_file in files:
                try:
                    with open(res_file, "r", encoding="utf-8") as f:
                        results.append(json.load(f))
                except Exception as exc:
                    logger.warning("[RESULT_LOAD_ERR] Failed reading %s: %s", res_file, exc)
        return results

    def log_workflow_event(self, document_id: str, message: str) -> Path:
        """Append an event to output/logs/<doc_id>_workflow.log."""
        file_path = self.settings.output_logs_dir / f"{document_id}_workflow.log"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(f"[{now}] {message}\n")
        return file_path


_json_store: Optional[JSONStore] = None


def get_json_store() -> JSONStore:
    """Singleton getter for JSONStore."""
    global _json_store
    if _json_store is None:
        _json_store = JSONStore()
    return _json_store

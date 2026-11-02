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
        """Save complete processing result to output_results_dir/<doc_id>_result.json."""
        file_path = self.settings.output_results_dir / f"{result.document_id}_result.json"
        content_str = result.model_dump_json(indent=2)
        try:
            self.settings.output_results_dir.mkdir(parents=True, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content_str)
            logger.info("[RESULT_SAVED] Persisted execution result: %s", file_path)
            return file_path
        except OSError as exc:
            logger.error("[RESULT_SAVE_ERR] Failed writing to %s: %s. Attempting temp fallback.", file_path, exc)
            fallback_path = self.settings.temp_dir / f"{result.document_id}_result.json"
            try:
                self.settings.temp_dir.mkdir(parents=True, exist_ok=True)
                with open(fallback_path, "w", encoding="utf-8") as f:
                    f.write(content_str)
                logger.info("[RESULT_SAVED_FALLBACK] Persisted result to fallback path: %s", fallback_path)
                return fallback_path
            except OSError as fb_exc:
                logger.critical("[RESULT_SAVE_CRITICAL] Emergency fallback also failed for %s: %s", fallback_path, fb_exc)
                return file_path

    def save_payload(self, document_id: str, payload_data: Dict[str, Any]) -> Path:
        """Save proposed CargoWise payload to output_payloads_dir/<doc_id>_cw1_payload.json."""
        file_path = self.settings.output_payloads_dir / f"{document_id}_cw1_payload.json"
        content_str = json.dumps(payload_data, indent=2)
        try:
            self.settings.output_payloads_dir.mkdir(parents=True, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content_str)
            logger.info("[PAYLOAD_SAVED] Persisted CW1 payload: %s", file_path)
            return file_path
        except OSError as exc:
            logger.error("[PAYLOAD_SAVE_ERR] Failed writing to %s: %s. Attempting temp fallback.", file_path, exc)
            fallback_path = self.settings.temp_dir / f"{document_id}_cw1_payload.json"
            try:
                self.settings.temp_dir.mkdir(parents=True, exist_ok=True)
                with open(fallback_path, "w", encoding="utf-8") as f:
                    f.write(content_str)
                logger.info("[PAYLOAD_SAVED_FALLBACK] Persisted CW1 payload to fallback path: %s", fallback_path)
                return fallback_path
            except OSError as fb_exc:
                logger.critical("[PAYLOAD_SAVE_CRITICAL] Emergency fallback failed for %s: %s", fallback_path, fb_exc)
                return file_path

    def save_review(self, review: HITLReviewPackage) -> Path:
        """Save HITL review case to output_reviews_dir/<doc_id>_review.json."""
        file_path = self.settings.output_reviews_dir / f"{review.document_id}_review.json"
        content_str = review.model_dump_json(indent=2)
        try:
            self.settings.output_reviews_dir.mkdir(parents=True, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content_str)
            logger.info("[REVIEW_SAVED] Persisted HITL review case: %s", file_path)
            return file_path
        except OSError as exc:
            logger.error("[REVIEW_SAVE_ERR] Failed writing to %s: %s. Attempting temp fallback.", file_path, exc)
            fallback_path = self.settings.temp_dir / f"{review.document_id}_review.json"
            try:
                self.settings.temp_dir.mkdir(parents=True, exist_ok=True)
                with open(fallback_path, "w", encoding="utf-8") as f:
                    f.write(content_str)
                logger.info("[REVIEW_SAVED_FALLBACK] Persisted HITL review case to fallback: %s", fallback_path)
                return fallback_path
            except OSError as fb_exc:
                logger.critical("[REVIEW_SAVE_CRITICAL] Emergency fallback failed for %s: %s", fallback_path, fb_exc)
                return file_path

    def load_review(self, document_id: str) -> Optional[HITLReviewPackage]:
        """Load an existing HITL review package by document ID from disk."""
        file_path = self.settings.output_reviews_dir / f"{document_id}_review.json"
        if not file_path.exists():
            fallback_path = self.settings.temp_dir / f"{document_id}_review.json"
            if fallback_path.exists():
                file_path = fallback_path

        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return HITLReviewPackage(**data)
            except Exception as exc:
                logger.error("[REVIEW_LOAD_ERR] Failed parsing review from %s: %s", file_path, exc)
        return None

    def list_reviews(self, status_filter: Optional[str] = None) -> List[HITLReviewPackage]:
        """List review cases from local disk."""
        reviews: List[HITLReviewPackage] = []
        search_dirs = [self.settings.output_reviews_dir, self.settings.temp_dir]
        seen_ids = set()
        for sdir in search_dirs:
            if sdir.exists():
                for review_file in sdir.glob("*_review.json"):
                    try:
                        with open(review_file, "r", encoding="utf-8") as f:
                            pkg = HITLReviewPackage(**json.load(f))
                            if pkg.document_id not in seen_ids:
                                seen_ids.add(pkg.document_id)
                                if status_filter is None or pkg.status == status_filter:
                                    reviews.append(pkg)
                    except Exception as exc:
                        logger.warning("[REVIEW_LOAD_ERR] Failed reading %s: %s", review_file, exc)
        return reviews

    def list_results(self) -> List[Dict[str, Any]]:
        """List all processed results from output/results/ ordered newest first."""
        results: List[Dict[str, Any]] = []
        search_dirs = [self.settings.output_results_dir, self.settings.temp_dir]
        seen_ids = set()
        all_files = []
        for sdir in search_dirs:
            if sdir.exists():
                all_files.extend(list(sdir.glob("*_result.json")))

        files = sorted(
            all_files,
            key=lambda p: os.path.getmtime(p) if p.exists() else 0,
            reverse=True,
        )
        for res_file in files:
            try:
                with open(res_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    doc_id = data.get("document_id")
                    if doc_id and doc_id not in seen_ids:
                        seen_ids.add(doc_id)
                        results.append(data)
            except Exception as exc:
                logger.warning("[RESULT_LOAD_ERR] Failed reading %s: %s", res_file, exc)
        return results

    def log_workflow_event(self, document_id: str, message: str) -> Path:
        """Append an event to output_logs_dir/<doc_id>_workflow.log."""
        file_path = self.settings.output_logs_dir / f"{document_id}_workflow.log"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        line = f"[{now}] {message}\n"
        try:
            self.settings.output_logs_dir.mkdir(parents=True, exist_ok=True)
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(line)
            return file_path
        except OSError:
            fallback_path = self.settings.temp_dir / f"{document_id}_workflow.log"
            try:
                self.settings.temp_dir.mkdir(parents=True, exist_ok=True)
                with open(fallback_path, "a", encoding="utf-8") as f:
                    f.write(line)
                return fallback_path
            except OSError:
                return file_path


_json_store: Optional[JSONStore] = None


def get_json_store() -> JSONStore:
    """Singleton getter for JSONStore."""
    global _json_store
    if _json_store is None:
        _json_store = JSONStore()
    return _json_store

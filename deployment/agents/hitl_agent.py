"""
Agent 6: Human-in-the-Loop (HITL) Agent.

Captures validation failures, missing mandatory fields, low-confidence extractions,
and unmatched order references into a durable review package.
Saves output/reviews/<document_id>_review.json with status HITL_PENDING,
and provides programmatic/CLI workflow resumption after human corrections pass revalidation.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from agent_framework import Executor, WorkflowContext, handler

from adapters.notification_adapter import get_notification_adapter
from persistence.duplicate_store import get_duplicate_store
from persistence.json_store import get_json_store
from schemas.models import (
    DocumentType,
    ExtractedDocument,
    ExtractionStatus,
    HITLReviewPackage,
    ProcessingResult,
    WorkflowStatus,
)
from validation.validators import get_validator

logger = logging.getLogger("havi.agents.hitl")


class HITLAgent(Executor):
    """MAF-based Human-in-the-Loop Agent."""

    def __init__(self, id: str = "HITLAgent") -> None:
        super().__init__(id=id)
        self.validator = get_validator()
        self.json_store = get_json_store()
        self.duplicate_store = get_duplicate_store()
        self.notifier = get_notification_adapter()

    def build_review_package(self, state: Dict[str, Any]) -> HITLReviewPackage:
        """Construct durable review package from current workflow state."""
        doc_id = state.get("document_id", "DOC-UNKNOWN")
        file_name = state.get("file_name", "unknown.pdf")
        history = state.get("history", [])
        last_agent = history[-1].get("agent", "UnknownAgent") if history else "Pipeline"

        val = state.get("validation", {})
        missing = val.get("missing_mandatory_fields", [])
        low_conf = val.get("low_confidence_fields", [])
        errors = list(val.get("validation_errors", []))

        err_msg = state.get("error_message")
        if err_msg and err_msg not in errors:
            errors.append(err_msg)

        original_fields = dict(state.get("extracted_fields", {}))

        return HITLReviewPackage(
            document_id=doc_id,
            source_filename=file_name,
            raised_by_agent=last_agent,
            status="HITL_PENDING",
            missing_fields=missing,
            low_confidence_fields=low_conf,
            validation_errors=errors,
            original_extracted_values=original_fields,
            human_corrections={},
        )

    @handler
    async def process(
        self,
        state: Dict[str, Any],
        ctx: WorkflowContext[Dict[str, Any], Dict[str, Any]],
    ) -> None:
        """MAF handler method invoked by the workflow engine when routing to HITL."""
        doc_id = state.get("document_id", "DOC-UNKNOWN")
        logger.info("[HITL_AGENT_INVOKED] Packaging human review for %s", doc_id)
        self.json_store.log_workflow_event(doc_id, "[HITLAgent] Creating human review package.")

        pkg = self.build_review_package(state)
        self.json_store.save_review(pkg)

        # Dispatch alert via notification adapter (dummy by default, live if configured)
        val = state.get("validation", {})
        conf = float(val.get("overall_confidence", 1.0))
        notification_res = self.notifier.send_hitl_alert(
            document_id=doc_id,
            source_filename=state.get("file_name", "unknown.pdf"),
            reasons=pkg.missing_fields + pkg.validation_errors,
            extracted_fields=dict(state.get("extracted_fields", {})),
            confidence_score=conf,
        )

        state["workflow_status"] = "HITL_PENDING"
        state["hitl_details"] = pkg.model_dump()
        state["notification_dispatch"] = notification_res
        state.setdefault("history", []).append({
            "agent": "HITLAgent",
            "action": "REVIEW_PACKAGE_SAVED",
            "review_file": f"output/reviews/{doc_id}_review.json",
            "notification_mode": notification_res.get("mode"),
        })

        self.json_store.log_workflow_event(
            doc_id,
            f"[HITLAgent] Status marked HITL_PENDING. Notification ({notification_res.get('mode')}): {notification_res.get('status')}",
        )

        # Yield output as terminal event for this branch
        await ctx.yield_output(state)

    def list_pending_reviews(self) -> list[HITLReviewPackage]:
        """List all currently pending human review cases from durable store."""
        return self.json_store.list_reviews(status_filter="HITL_PENDING")

    def resume_workflow(
        self,
        document_id: str,
        corrections: Dict[str, Any],
        reviewer_notes: Optional[str] = None,
    ) -> Optional[ProcessingResult]:
        """Resume a pending HITL review case with corrections."""
        try:
            if reviewer_notes:
                self.json_store.log_workflow_event(
                    document_id,
                    f"[HITLAgent] Human reviewer notes: {reviewer_notes}",
                )
            return self.apply_corrections_and_resume(document_id, corrections)
        except Exception as exc:
            logger.error("[HITL_RESUME_ERROR] Could not resume review for %s: %s", document_id, exc)
            return None

    def apply_corrections_and_resume(
        self,
        document_id: str,
        human_corrections: Dict[str, Any],
    ) -> ProcessingResult:
        """
        Apply human corrections, revalidate, check duplicates, and resume workflow.

        Args:
            document_id: Identifier of pending document.
            human_corrections: Dictionary of corrected fields.

        Returns:
            Updated ProcessingResult.
        """
        review = self.json_store.load_review(document_id)
        if not review:
            raise ValueError(f"No review package found for document {document_id}")

        logger.info("[HITL_RESUME] Applying corrections to %s: %s", document_id, human_corrections)
        self.json_store.log_workflow_event(
            document_id,
            f"[HITLAgent] Applying human corrections: {human_corrections}",
        )

        # 1. Update review package preserving original values
        review.human_corrections.update(human_corrections)
        review.updated_at = datetime.now(timezone.utc).isoformat()

        # Build working fields: original merged with human corrections
        working_fields = dict(review.original_extracted_values)
        working_fields.update(review.human_corrections)

        # 2. Re-run validation (assuming 1.00 confidence for user-corrected fields)
        confidences = {k: 1.00 for k in working_fields.keys()}
        val_result = self.validator.validate_order(working_fields, confidences)

        if not val_result.is_valid:
            logger.warning("[HITL_REVALIDATION_FAILED] Document %s still fails validation after corrections.", document_id)
            review.status = "HITL_PENDING"
            review.missing_fields = val_result.missing_mandatory_fields
            review.validation_errors = val_result.validation_errors
            self.json_store.save_review(review)
            self.json_store.log_workflow_event(
                document_id,
                f"[HITLAgent] Revalidation failed: {val_result.validation_errors}. Status remains HITL_PENDING.",
            )

            # Return updated pending result
            return ProcessingResult(
                document_id=document_id,
                source_filename=review.source_filename,
                file_hash_sha256="",
                document_type=DocumentType.ORDER,
                extraction=ExtractedDocument(
                    document_id=document_id,
                    source_file_path=review.source_filename,
                    file_name=review.source_filename,
                    file_size_bytes=0,
                    file_hash_sha256="",
                    mime_type="application/pdf",
                    status=ExtractionStatus.SUCCESS,
                ),
                extracted_fields=working_fields,
                field_confidence=confidences,
                validation=val_result,
                workflow_status=WorkflowStatus.HITL_PENDING,
                hitl_details=review,
                errors=val_result.validation_errors,
            )

        # 3. Validation Passed -> Mark review RESOLVED
        review.status = "RESOLVED"
        review.missing_fields = []
        review.validation_errors = []
        self.json_store.save_review(review)

        self.json_store.log_workflow_event(
            document_id,
            "[HITLAgent] Revalidation passed! Resuming downstream workflow to CW1CreateAgent.",
        )

        # 4. Resume Downstream: CW1 Create Execution
        from agents.cw1_create_agent import CW1CreateAgent
        create_agent = CW1CreateAgent()
        create_payload = create_agent.build_create_payload(document_id, working_fields)
        self.json_store.save_payload(document_id, create_payload)

        # Check duplicate
        po = working_fields.get("po_number")
        cust = working_fields.get("customer")
        dup = self.duplicate_store.check_business_order_duplicate(po, cust)
        if dup:
            mock_res = create_agent.cw1_adapter.create_order(create_payload, document_id)
            mock_res.status = "DUPLICATE_DETECTED"
            mock_res.message = f"Duplicate order detected for PO '{po}'. Existing ref: {dup.get('cw1_reference')}."
            wf_status = WorkflowStatus.DUPLICATE_DETECTED
        else:
            mock_res = create_agent.cw1_adapter.create_order(create_payload, document_id)
            self.duplicate_store.record_created_order(
                po_number=po,
                customer=cust,
                order_number=working_fields.get("order_number"),
                document_id=document_id,
                cw1_reference=mock_res.cw1_reference or "",
            )
            wf_status = WorkflowStatus.MOCK_CREATED

        final_res = ProcessingResult(
            document_id=document_id,
            source_filename=review.source_filename,
            file_hash_sha256="",
            document_type=DocumentType.ORDER,
            extraction=ExtractedDocument(
                document_id=document_id,
                source_file_path=review.source_filename,
                file_name=review.source_filename,
                file_size_bytes=0,
                file_hash_sha256="",
                mime_type="application/pdf",
                status=ExtractionStatus.SUCCESS,
            ),
            extracted_fields=working_fields,
            field_confidence=confidences,
            validation=val_result,
            duplicate_status="DUPLICATE_DETECTED" if dup else "UNIQUE_RECORD",
            workflow_status=wf_status,
            hitl_details=review,
            mock_result=mock_res,
            timestamps={
                "resolved_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        self.json_store.save_result(final_res)
        return final_res


_hitl_agent: Optional[HITLAgent] = None


def get_hitl_agent() -> HITLAgent:
    """Singleton getter for HITLAgent."""
    global _hitl_agent
    if _hitl_agent is None:
        _hitl_agent = HITLAgent()
    return _hitl_agent

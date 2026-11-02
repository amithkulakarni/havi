"""
Microsoft Agent Framework (MAF) Orchestration Engine.

Constructs and compiles the declarative multi-agent workflow graph
connecting the six logical MAF Executors:
  1. SupervisorAgent (Document classification & routing)
  2. OrderExtractionAgent (PO field extraction & confidence scoring)
  3. CarrierDocumentExtractionAgent (Carrier/BOL field extraction)
  4. CW1CreateAgent (Duplicate check & mock CW1 create operation)
  5. CW1UpdateAgent (Order matching & mock CW1 update operation)
  6. HITLAgent (Human review packaging & workflow resumption)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from agent_framework import Workflow, WorkflowBuilder

from agents.carrier_document_extraction_agent import CarrierDocumentExtractionAgent
from agents.cw1_create_agent import CW1CreateAgent
from agents.cw1_update_agent import CW1UpdateAgent
from agents.hitl_agent import HITLAgent
from agents.order_extraction_agent import OrderExtractionAgent
from agents.supervisor_agent import SupervisorAgent
from persistence.json_store import get_json_store
from schemas.models import (
    CW1OperationResult,
    DocumentType,
    ExtractedDocument,
    HITLReviewPackage,
    ProcessingResult,
    ValidationResult,
    WorkflowStatus,
)

logger = logging.getLogger("havi.orchestration.workflow")


class FreightOrchestrator:
    """Enterprise multi-agent orchestrator built with Microsoft Agent Framework."""

    def __init__(self) -> None:
        self.json_store = get_json_store()
        self._init_agents()
        self.workflow = self._build_workflow()

    def _init_agents(self) -> None:
        """Instantiate the 6 logical MAF Executors."""
        self.supervisor = SupervisorAgent()
        self.order_extractor = OrderExtractionAgent()
        self.carrier_extractor = CarrierDocumentExtractionAgent()
        self.cw1_create = CW1CreateAgent()
        self.cw1_update = CW1UpdateAgent()
        self.hitl = HITLAgent()

    def _build_workflow(self) -> Workflow:
        """Construct the declarative MAF workflow graph with conditional transitions."""
        logger.info("[BUILD_MAF_WORKFLOW] Assembling declarative MAF workflow graph...")

        builder = (
            WorkflowBuilder(start_executor=self.supervisor)
            # Supervisor -> Order Extraction
            .add_edge(
                self.supervisor,
                self.order_extractor,
                condition=lambda s: s.get("route_to") == "OrderExtractionAgent",
            )
            # Supervisor -> Carrier Extraction
            .add_edge(
                self.supervisor,
                self.carrier_extractor,
                condition=lambda s: s.get("route_to") == "CarrierDocumentExtractionAgent",
            )
            # Supervisor -> HITL (unreadable, scanned without OCR, ambiguous)
            .add_edge(
                self.supervisor,
                self.hitl,
                condition=lambda s: s.get("route_to") == "HITLAgent",
            )
            # Order Extraction -> CW1 Create (validation passed)
            .add_edge(
                self.order_extractor,
                self.cw1_create,
                condition=lambda s: s.get("route_to") == "CW1CreateAgent",
            )
            # Order Extraction -> CW1 Update (order update validation passed)
            .add_edge(
                self.order_extractor,
                self.cw1_update,
                condition=lambda s: s.get("route_to") == "CW1UpdateAgent",
            )
            # Order Extraction -> HITL (missing mandatory fields or low confidence)
            .add_edge(
                self.order_extractor,
                self.hitl,
                condition=lambda s: s.get("route_to") == "HITLAgent",
            )
            # Carrier Extraction -> CW1 Update (validation passed)
            .add_edge(
                self.carrier_extractor,
                self.cw1_update,
                condition=lambda s: s.get("route_to") == "CW1UpdateAgent",
            )
            # Carrier Extraction -> HITL (missing mandatory carrier fields)
            .add_edge(
                self.carrier_extractor,
                self.hitl,
                condition=lambda s: s.get("route_to") == "HITLAgent",
            )
            # CW1 Update -> HITL (unmatched order reference)
            .add_edge(
                self.cw1_update,
                self.hitl,
                condition=lambda s: s.get("route_to") == "HITLAgent",
            )
        )

        wf = builder.build()
        logger.info("[BUILD_MAF_WORKFLOW_DONE] Compiled MAF workflow graph successfully.")
        return wf

    async def execute_async(self, doc: ExtractedDocument) -> ProcessingResult:
        """
        Execute the MAF workflow asynchronously against an extracted document.

        Args:
            doc: ExtractedDocument instance from PDF parser.

        Returns:
            Comprehensive ProcessingResult.
        """
        doc_id = doc.document_id
        start_time = datetime.now(timezone.utc).isoformat()
        self.json_store.log_workflow_event(doc_id, f"[Workflow] Starting MAF execution for {doc.file_name}.")

        initial_state: Dict[str, Any] = {
            "document_id": doc.document_id,
            "source_file_path": doc.source_file_path,
            "file_name": doc.file_name,
            "file_size_bytes": doc.file_size_bytes,
            "file_hash_sha256": doc.file_hash_sha256,
            "mime_type": doc.mime_type,
            "total_pages": doc.total_pages,
            "extracted_text": doc.extracted_text,
            "status": doc.status.value,
            "is_scanned": doc.is_scanned,
            "error_message": doc.error_message,
            "history": [],
            "route_to": None,
        }

        logger.info("[MAF_EXECUTE_START] Running MAF workflow for %s...", doc_id)
        events = await self.workflow.run(initial_state)
        logger.info("[MAF_EXECUTE_COMPLETE] MAF workflow produced %d events for %s.", len(events), doc_id)

        # Retrieve final output state from workflow events
        final_state = initial_state
        for event in events:
            if getattr(event, "type", None) == "output" and hasattr(event, "data"):
                if isinstance(event.data, dict):
                    final_state = event.data

        end_time = datetime.now(timezone.utc).isoformat()

        # Build typed ProcessingResult
        doc_type_str = final_state.get("document_type", "UNKNOWN")
        try:
            doc_type = DocumentType(doc_type_str)
        except ValueError:
            doc_type = DocumentType.UNKNOWN

        wf_status_str = final_state.get("workflow_status", "FAILED")
        try:
            wf_status = WorkflowStatus(wf_status_str)
        except ValueError:
            wf_status = WorkflowStatus.FAILED

        val_dict = final_state.get("validation")
        val_obj = ValidationResult(**val_dict) if val_dict else None

        mock_dict = final_state.get("mock_result")
        mock_obj = CW1OperationResult(**mock_dict) if mock_dict else None

        hitl_dict = final_state.get("hitl_details")
        hitl_obj = HITLReviewPackage(**hitl_dict) if hitl_dict else None

        errors_list: List[str] = []
        if doc.error_message:
            errors_list.append(doc.error_message)
        if val_obj and val_obj.validation_errors:
            errors_list.extend(val_obj.validation_errors)

        result = ProcessingResult(
            document_id=doc.document_id,
            source_filename=doc.file_name,
            file_hash_sha256=doc.file_hash_sha256,
            document_type=doc_type,
            extraction=doc,
            extracted_fields=final_state.get("extracted_fields", {}),
            field_confidence=final_state.get("field_confidence", {}),
            validation=val_obj,
            duplicate_status=final_state.get("duplicate_status"),
            workflow_status=wf_status,
            route_to=final_state.get("route_to"),
            hitl_details=hitl_obj,
            mock_result=mock_obj,
            timestamps={
                "started_at": start_time,
                "completed_at": end_time,
            },
            errors=list(dict.fromkeys(errors_list)),  # Deduplicate errors
            history=final_state.get("history", []),
        )

        # Save result JSON to output/results/<doc_id>_result.json
        self.json_store.save_result(result)
        self.json_store.log_workflow_event(
            doc_id,
            f"[Workflow] Finished execution. Final status: {result.workflow_status.value}.",
        )

        return result

    def execute(self, doc: ExtractedDocument) -> ProcessingResult:
        """Synchronous runner wrapper for MAF workflow."""
        try:
            loop = asyncio.get_running_loop()
            import nest_asyncio
            nest_asyncio.apply()
            return loop.run_until_complete(self.execute_async(doc))
        except RuntimeError:
            return asyncio.run(self.execute_async(doc))


_orchestrator: Optional[FreightOrchestrator] = None


def get_orchestrator() -> FreightOrchestrator:
    """Singleton getter for FreightOrchestrator."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = FreightOrchestrator()
    return _orchestrator

"""
Agent 5: CW1 Update Agent.

Matches incoming carrier updates to existing business orders in the local store,
constructs update payloads, routes unmatched records to HITL,
and calls the mock CW1 adapter to record simulated updates.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from agent_framework import Executor, WorkflowContext, handler

from adapters.cw1_mock_adapter import get_cw1_adapter
from persistence.duplicate_store import get_duplicate_store
from persistence.json_store import get_json_store
from schemas.models import CW1OperationResult

logger = logging.getLogger("havi.agents.cw1_update")


class CW1UpdateAgent(Executor):
    """MAF-based CW1 Update Agent."""

    def __init__(self, id: str = "CW1UpdateAgent") -> None:
        super().__init__(id=id)
        self.duplicate_store = get_duplicate_store()
        self.cw1_adapter = get_cw1_adapter()
        self.json_store = get_json_store()

    def build_update_payload(
        self,
        doc_id: str,
        matched_order: Dict[str, Any],
        fields: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Construct structured CargoWise One order update payload."""
        return {
            "integration_source": "LOCAL_CMD_AUTOMATION",
            "document_id": doc_id,
            "target_cw1_reference": matched_order.get("cw1_reference"),
            "matched_order_context": {
                "po_number": matched_order.get("po_number"),
                "customer": matched_order.get("customer"),
                "order_number": matched_order.get("order_number"),
            },
            "carrier_milestones": {
                "booking_id": fields.get("booking_id"),
                "carrier_name": fields.get("carrier_name"),
                "container_number": fields.get("container_number"),
                "vessel_or_flight": fields.get("vessel_or_flight"),
                "estimated_departure": fields.get("estimated_departure"),
                "estimated_arrival": fields.get("estimated_arrival"),
            },
        }

    @handler
    async def process(
        self,
        state: Dict[str, Any],
        ctx: WorkflowContext[Dict[str, Any], Dict[str, Any]],
    ) -> None:
        """MAF handler method invoked by the workflow engine."""
        doc_id = state.get("document_id", "DOC-UNKNOWN")
        file_hash = state.get("file_hash_sha256", "")
        file_name = state.get("file_name", "unknown.pdf")
        fields = state.get("extracted_fields", {})
        reference = (
            fields.get("related_order_reference")
            or fields.get("booking_id")
            or fields.get("po_number")
        )

        logger.info("[CW1_UPDATE_INVOKED] Matching document %s with ref '%s'", doc_id, reference)
        self.json_store.log_workflow_event(doc_id, f"[CW1UpdateAgent] Attempting order match for reference '{reference}'.")

        # 1. Attempt match against existing business orders
        matched_order = self.duplicate_store.find_order_by_reference(reference)

        if not matched_order:
            # Unmatched carrier document -> Route to HITL
            logger.warning("[UNMATCHED_ORDER] Reference '%s' not found in store. Routing to HITLAgent.", reference)
            state["route_to"] = "HITLAgent"
            state["workflow_status"] = "HITL_PENDING"
            state["error_message"] = (
                f"Carrier reference '{reference}' could not be matched to an existing order in the local store."
            )
            val = state.get("validation", {})
            val.setdefault("validation_errors", []).append(state["error_message"])
            val["requires_hitl"] = True
            state["validation"] = val

            self.json_store.log_workflow_event(
                doc_id,
                f"[CW1UpdateAgent] Unmatched reference '{reference}'. Routed to HITLAgent.",
            )

            # Pass along to HITLAgent
            await ctx.send_message(state)
            return

        # 2. Build update payload
        cw1_ref = matched_order.get("cw1_reference", "UNKNOWN-REF")
        update_payload = self.build_update_payload(doc_id, matched_order, fields)
        self.json_store.save_payload(doc_id, update_payload)

        # 3. Call Mock CW1 Adapter
        mock_result = self.cw1_adapter.update_order(cw1_ref, update_payload, doc_id)
        state["mock_result"] = mock_result.model_dump()
        state["workflow_status"] = mock_result.status

        # 4. Record carrier update
        self.duplicate_store.record_carrier_update(
            booking_id=fields.get("booking_id"),
            carrier_name=fields.get("carrier_name"),
            related_order_reference=reference,
            document_id=doc_id,
            cw1_reference=cw1_ref,
        )
        self.duplicate_store.record_document(
            file_hash=file_hash,
            document_id=doc_id,
            file_name=file_name,
            document_type="CARRIER_DOCUMENT",
            workflow_status=mock_result.status,
            cw1_reference=cw1_ref,
        )

        self.json_store.log_workflow_event(
            doc_id,
            f"[CW1UpdateAgent] Mock update recorded for reference {cw1_ref}.",
        )

        state.setdefault("history", []).append({
            "agent": "CW1UpdateAgent",
            "matched_order": matched_order,
            "mock_result": mock_result.model_dump(),
        })

        await ctx.yield_output(state)

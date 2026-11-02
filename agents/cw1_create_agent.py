"""
Agent 4: CW1 Create Agent.

Accepts validated purchase order data, executes duplicate protection checks
against the local SQLite store, constructs the CargoWise One create payload,
invokes the local Mock CW1 adapter, and persists the proposed payload.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from agent_framework import Executor, WorkflowContext, handler

from adapters.cw1_mock_adapter import get_cw1_adapter
from persistence.duplicate_store import get_duplicate_store
from persistence.json_store import get_json_store
from schemas.models import CW1OperationResult

logger = logging.getLogger("havi.agents.cw1_create")


class CW1CreateAgent(Executor):
    """MAF-based CW1 Create Agent."""

    def __init__(self, id: str = "CW1CreateAgent") -> None:
        super().__init__(id=id)
        self.duplicate_store = get_duplicate_store()
        self.cw1_adapter = get_cw1_adapter()
        self.json_store = get_json_store()

    def build_create_payload(self, doc_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Construct structured CargoWise One order creation schema."""
        return {
            "integration_source": "LOCAL_CMD_AUTOMATION",
            "document_id": doc_id,
            "order_header": {
                "po_number": fields.get("po_number"),
                "customer": fields.get("customer"),
                "order_number": fields.get("order_number"),
                "incoterms": fields.get("incoterms") or "DDP",
            },
            "line_items": [
                {
                    "item_number": 1,
                    "material_description": fields.get("material"),
                    "quantity": fields.get("quantity"),
                    "unit_of_measure": fields.get("uom") or "Cases",
                }
            ],
            "routing": {
                "origin_location": fields.get("pickup_location"),
                "destination_location": fields.get("delivery_location"),
                "requested_pickup_date": fields.get("requested_pickup_date"),
                "requested_delivery_date": fields.get("requested_delivery_date"),
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
        po_number = fields.get("po_number")
        customer = fields.get("customer")

        logger.info("[CW1_CREATE_INVOKED] Preparing CW1 create operation for %s", doc_id)
        self.json_store.log_workflow_event(doc_id, "[CW1CreateAgent] Duplicate check initiated.")

        # 1. Tier A Duplicate Check: Exact File Hash
        dup_file = self.duplicate_store.check_document_duplicate(file_hash)
        if dup_file and dup_file.get("workflow_status") in ("MOCK_CREATED", "MOCK_UPDATED"):
            prev_ref = dup_file.get("cw1_reference")
            logger.warning("[DUPLICATE_FILE] Document %s (hash %s) was already processed as %s", doc_id, file_hash, prev_ref)
            state["duplicate_status"] = "DUPLICATE_FILE_DETECTED"
            state["workflow_status"] = "DUPLICATE_DETECTED"
            mock_res = CW1OperationResult(
                operation_type="CREATE",
                status="DUPLICATE_DETECTED",
                cw1_reference=prev_ref,
                message=f"Duplicate physical file detected. Previously processed under reference {prev_ref}.",
            )
            state["mock_result"] = mock_res.model_dump()
            self.json_store.log_workflow_event(
                doc_id,
                f"[CW1CreateAgent] Duplicate file detected. Skipped creation (existing ref: {prev_ref}).",
            )
            await ctx.yield_output(state)
            return

        # 2. Tier C Duplicate Check: Business Key (PO Number + Customer)
        dup_order = self.duplicate_store.check_business_order_duplicate(po_number, customer)
        if dup_order:
            prev_ref = dup_order.get("cw1_reference")
            logger.warning("[DUPLICATE_ORDER] Order PO %s for %s already exists with ref %s", po_number, customer, prev_ref)
            state["duplicate_status"] = "DUPLICATE_ORDER_DETECTED"
            state["workflow_status"] = "DUPLICATE_DETECTED"
            mock_res = CW1OperationResult(
                operation_type="CREATE",
                status="DUPLICATE_DETECTED",
                cw1_reference=prev_ref,
                message=f"Duplicate order detected for PO '{po_number}' and Customer '{customer}'. Existing ref: {prev_ref}.",
            )
            state["mock_result"] = mock_res.model_dump()
            self.json_store.log_workflow_event(
                doc_id,
                f"[CW1CreateAgent] Duplicate business order detected (PO: {po_number}). Skipped creation.",
            )
            await ctx.yield_output(state)
            return

        # 3. Construct and save proposed payload
        cw1_payload = self.build_create_payload(doc_id, fields)
        self.json_store.save_payload(doc_id, cw1_payload)

        # 4. Invoke Mock CW1 Adapter
        mock_result = self.cw1_adapter.create_order(cw1_payload, doc_id)
        state["mock_result"] = mock_result.model_dump()
        state["workflow_status"] = mock_result.status
        state["duplicate_status"] = "UNIQUE_RECORD"

        # 5. Persist to duplicate store
        self.duplicate_store.record_created_order(
            po_number=po_number,
            customer=customer,
            order_number=fields.get("order_number"),
            document_id=doc_id,
            cw1_reference=mock_result.cw1_reference or "",
        )
        self.duplicate_store.record_document(
            file_hash=file_hash,
            document_id=doc_id,
            file_name=file_name,
            document_type="ORDER",
            workflow_status=mock_result.status,
            cw1_reference=mock_result.cw1_reference,
        )

        self.json_store.log_workflow_event(
            doc_id,
            f"[CW1CreateAgent] Mock order created. Ref: {mock_result.cw1_reference}.",
        )

        state.setdefault("history", []).append({
            "agent": "CW1CreateAgent",
            "mock_result": mock_result.model_dump(),
        })

        # Yield output as terminal event for this branch
        await ctx.yield_output(state)

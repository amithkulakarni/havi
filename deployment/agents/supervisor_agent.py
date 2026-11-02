"""
Agent 1: Supervisor Agent.

Accepts extracted document content, classifies the business document type
(ORDER, CARRIER_DOCUMENT, UNKNOWN), determines the next agent route,
and outputs a structured routing result for MAF orchestration.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict

from agent_framework import Executor, WorkflowContext, handler

from persistence.json_store import get_json_store
from schemas.models import DocumentType, ExtractionStatus, SupervisorRoutingResult

logger = logging.getLogger("havi.agents.supervisor")

# Regex patterns for business classification
_ORDER_KEYWORDS = [
    r"\bPURCHASE\s+ORDER\b",
    r"\bPO\s*(NUMBER|#|NO|\-)\b",
    r"\bNEW_ORDER\b",
    r"\bCUSTOMER\s+NAME\b",
    r"\bORDER\s+NUMBER\b",
    r"\bMATERIAL\b",
    r"\bQUANTITY\b",
    r"\bDELIVERY\s+LOCATION\b",
    r"\bPICKUP\s+LOCATION\b",
    r"\bINCOTERMS\b",
]

_CARRIER_KEYWORDS = [
    r"\bBILL\s+OF\s+LADING\b",
    r"\bB/?L\b",
    r"\bCARRIER\s+(CONFIRMATION|NAME|BOOKING)\b",
    r"\bBOOKING\s+(NUMBER|#|ID)\b",
    r"\bVESSEL\b",
    r"\bVOYAGE\b",
    r"\bCONTAINER\s+(NUMBER|#|ID)\b",
    r"\bPORT\s+OF\s+(LOADING|DISCHARGE)\b",
    r"\bPROOF\s+OF\s+DELIVERY\b",
    r"\bAIR\s*WAYBILL\b",
]

_ORDER_UPDATE_KEYWORDS = [
    r"\bORDER\s+UPDATE\b",
    r"\bUPDATE\s+ORDER\b",
    r"\bAMENDMENT\b",
    r"\bPO\s*(REVISION|AMENDMENT)\b",
    r"\bREVISION\b",
    r"\bMODIFY\s+ORDER\b",
    r"\bCHANGE\s+ORDER\b",
]


class SupervisorAgent(Executor):
    """MAF-based Supervisor Agent for document classification and routing."""

    def __init__(self, id: str = "SupervisorAgent") -> None:
        super().__init__(id=id)
        self.json_store = get_json_store()

    def classify(self, doc_data: Dict[str, Any]) -> SupervisorRoutingResult:
        """Analyze text and structural features to classify document."""
        doc_id = doc_data.get("document_id", "UNKNOWN-DOC")
        text = (doc_data.get("extracted_text") or "").upper()
        status = doc_data.get("status", "")

        # 1. Unreadable, empty, or un-OCRed scanned documents
        if not text.strip() or status in (
            ExtractionStatus.OCR_UNAVAILABLE.value,
            ExtractionStatus.SCANNED_NEEDS_OCR.value,
            ExtractionStatus.CORRUPTED.value,
        ):
            return SupervisorRoutingResult(
                document_id=doc_id,
                document_type=DocumentType.UNKNOWN,
                route_to="HITLAgent",
                confidence=0.0,
                status="UNREADABLE_ROUTED_TO_HITL",
                reasoning=(
                    "Document has no extractable text or is a scanned document without OCR. "
                    "Routing directly to HITLAgent for human review."
                ),
            )

        # 2. Check JSON payload hints
        if '"DOCUMENT_TYPE": "ORDER_UPDATE"' in text:
            return SupervisorRoutingResult(
                document_id=doc_id,
                document_type=DocumentType.ORDER_UPDATE,
                route_to="OrderExtractionAgent",
                confidence=0.99,
                status="CLASSIFIED",
                reasoning="Explicit document_type ORDER_UPDATE tag found in structured content.",
            )

        if '"DOCUMENT_TYPE": "ORDER"' in text or '"DOCUMENT_TYPE": "NEW_ORDER"' in text:
            return SupervisorRoutingResult(
                document_id=doc_id,
                document_type=DocumentType.ORDER,
                route_to="OrderExtractionAgent",
                confidence=0.99,
                status="CLASSIFIED",
                reasoning="Explicit document_type ORDER tag found in structured document content.",
            )

        if (
            '"DOCUMENT_TYPE": "CARRIER"' in text
            or '"DOCUMENT_TYPE": "CARRIER_DOCUMENT"' in text
            or '"DOCUMENT_TYPE": "CARRIER_CONFIRMATION"' in text
        ):
            return SupervisorRoutingResult(
                document_id=doc_id,
                document_type=DocumentType.CARRIER_DOCUMENT,
                route_to="CarrierDocumentExtractionAgent",
                confidence=0.99,
                status="CLASSIFIED",
                reasoning="Explicit document_type CARRIER tag found in structured content.",
            )

        # 3. Dynamic Azure AI LLM Classification (gpt-4.1-mini)
        import json
        from adapters.model_client import get_model_client_manager
        mgr = get_model_client_manager()
        raw_text = doc_data.get("extracted_text") or ""
        if mgr.is_configured() and raw_text.strip():
            ai_prompt = f"""You are the Supervisor Classification Agent for a freight and supply chain logistics platform.
Classify the following document into exactly one of these types:
- NEW_ORDER: Purchase order, sales order, food/goods delivery receipt, shipment order receipt containing ordered items, quantities, or materials.
- ORDER_UPDATE: Modification, amendment, revision, or change to an existing purchase order or order line items.
- CARRIER_CONFIRMATION: Bill of lading (BOL), air waybill (AWB), carrier confirmation, vessel/voyage manifest, or carrier booking.
- UNKNOWN: Completely unreadable or irrelevant document.

Document Content:
\"\"\"{raw_text[:3500]}\"\"\"

Return ONLY a JSON object:
{{"document_type": "NEW_ORDER" or "ORDER_UPDATE" or "CARRIER_CONFIRMATION" or "UNKNOWN", "confidence": 0.95, "reasoning": "1-sentence explanation"}}"""

            ai_resp = mgr.complete_sync(ai_prompt)
            if ai_resp:
                try:
                    cleaned = ai_resp.strip()
                    if "```json" in cleaned:
                        cleaned = cleaned.split("```json")[1].split("```")[0].strip()
                    elif "```" in cleaned:
                        cleaned = cleaned.split("```")[1].split("```")[0].strip()
                    parsed = json.loads(cleaned)
                    dtype = parsed.get("document_type", "").upper()
                    conf = float(parsed.get("confidence", 0.90))
                    reasoning = parsed.get("reasoning", "Classified dynamically by Azure AI gpt-4.1-mini.")

                    # HITL Gate: threshold 0.80
                    if conf < 0.80 or "UNKNOWN" in dtype:
                        effective_conf = min(conf, 0.40) if "UNKNOWN" in dtype else conf
                        return SupervisorRoutingResult(
                            document_id=doc_id,
                            document_type=DocumentType.UNKNOWN,
                            route_to="HITLAgent",
                            confidence=effective_conf,
                            status="LOW_CONFIDENCE_ROUTED_TO_HITL",
                            reasoning=f"AI confidence {conf:.2f} below 0.80 threshold or classified as UNKNOWN. Routed to HITL.",
                        )

                    if "UPDATE" in dtype or "ORDER_UPDATE" in dtype:
                        return SupervisorRoutingResult(
                            document_id=doc_id,
                            document_type=DocumentType.ORDER_UPDATE,
                            route_to="OrderExtractionAgent",
                            confidence=conf,
                            status="CLASSIFIED",
                            reasoning=reasoning,
                        )
                    elif "ORDER" in dtype or "NEW_ORDER" in dtype:
                        return SupervisorRoutingResult(
                            document_id=doc_id,
                            document_type=DocumentType.ORDER,
                            route_to="OrderExtractionAgent",
                            confidence=conf,
                            status="CLASSIFIED",
                            reasoning=reasoning,
                        )
                    elif "CARRIER" in dtype:
                        return SupervisorRoutingResult(
                            document_id=doc_id,
                            document_type=DocumentType.CARRIER_DOCUMENT,
                            route_to="CarrierDocumentExtractionAgent",
                            confidence=conf,
                            status="CLASSIFIED",
                            reasoning=reasoning,
                        )
                except Exception as exc:
                    logger.warning("[SUPERVISOR_AI_PARSE_WARN] Failed parsing AI response: %s", exc)

        # 4. Fallback: Score against domain keyword heuristics
        order_score = sum(1 for pat in _ORDER_KEYWORDS if re.search(pat, text))
        carrier_score = sum(1 for pat in _CARRIER_KEYWORDS if re.search(pat, text))
        update_score = sum(1 for pat in _ORDER_UPDATE_KEYWORDS if re.search(pat, text))

        logger.info(
            "[SUPERVISOR_EVAL] Doc: %s -> Order score: %d, Carrier score: %d, Update score: %d",
            doc_id,
            order_score,
            carrier_score,
            update_score,
        )

        if order_score >= 2 and order_score >= carrier_score:
            confidence = min(0.70 + (order_score * 0.06), 0.98)
            # Check if this is an ORDER_UPDATE
            if update_score >= 1:
                doc_type = DocumentType.ORDER_UPDATE
                reason = f"Matched {order_score} order patterns and {update_score} update pattern(s)."
            else:
                doc_type = DocumentType.ORDER
                reason = f"Matched {order_score} purchase order keyword patterns."

            if confidence < 0.80:
                return SupervisorRoutingResult(
                    document_id=doc_id,
                    document_type=DocumentType.UNKNOWN,
                    route_to="HITLAgent",
                    confidence=round(confidence, 2),
                    status="LOW_CONFIDENCE_ROUTED_TO_HITL",
                    reasoning=f"Confidence {confidence:.2f} is below required 0.80 threshold.",
                )

            return SupervisorRoutingResult(
                document_id=doc_id,
                document_type=doc_type,
                route_to="OrderExtractionAgent",
                confidence=round(confidence, 2),
                status="CLASSIFIED",
                reasoning=reason,
            )
        elif carrier_score >= 2 and carrier_score > order_score:
            confidence = min(0.70 + (carrier_score * 0.06), 0.98)
            if confidence < 0.80:
                return SupervisorRoutingResult(
                    document_id=doc_id,
                    document_type=DocumentType.UNKNOWN,
                    route_to="HITLAgent",
                    confidence=round(confidence, 2),
                    status="LOW_CONFIDENCE_ROUTED_TO_HITL",
                    reasoning=f"Carrier confidence {confidence:.2f} is below required 0.80 threshold.",
                )
            return SupervisorRoutingResult(
                document_id=doc_id,
                document_type=DocumentType.CARRIER_DOCUMENT,
                route_to="CarrierDocumentExtractionAgent",
                confidence=round(confidence, 2),
                status="CLASSIFIED",
                reasoning=f"Matched {carrier_score} carrier/BOL keyword patterns.",
            )
        else:
            return SupervisorRoutingResult(
                document_id=doc_id,
                document_type=DocumentType.UNKNOWN,
                route_to="HITLAgent",
                confidence=0.40,
                status="AMBIGUOUS_ROUTED_TO_HITL",
                reasoning="Content did not match minimum confidence patterns (>= 0.80) for Order or Carrier document.",
            )

    @handler
    async def process(
        self,
        state: Dict[str, Any],
        ctx: WorkflowContext[Dict[str, Any], Dict[str, Any]],
    ) -> None:
        """MAF handler method invoked by the workflow engine."""
        doc_id = state.get("document_id", "DOC-UNKNOWN")
        logger.info("[SUPERVISOR_AGENT_INVOKED] Classifying document: %s", doc_id)
        self.json_store.log_workflow_event(doc_id, "[SupervisorAgent] Received document for classification.")

        routing = self.classify(state)

        # Update shared state
        state["document_type"] = routing.document_type.value
        state["route_to"] = routing.route_to
        state["classification_confidence"] = routing.confidence
        state["routing_result"] = routing.model_dump()
        state.setdefault("history", []).append({
            "agent": "SupervisorAgent",
            "decision": routing.model_dump(),
        })

        self.json_store.log_workflow_event(
            doc_id,
            f"[SupervisorAgent] Classified as {routing.document_type.value} -> Route to {routing.route_to} (conf: {routing.confidence})",
        )

        # Send to downstream agents in MAF graph
        await ctx.send_message(state)

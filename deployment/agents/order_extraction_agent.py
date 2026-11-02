"""
Agent 2: Order Extraction Agent.

Specialized in extracting purchase order fields from document text or structured JSON,
assigning field-level confidence scores, preserving verbatim extracted values,
and evaluating against mandatory business validation rules.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

from agent_framework import Executor, WorkflowContext, handler

from persistence.json_store import get_json_store
from schemas.models import DocumentType, FieldExtraction
from validation.validators import get_validator

logger = logging.getLogger("havi.agents.order_extraction")

# Field extraction regex patterns for purchase orders
_PATTERNS = {
    "po_number": [
        r"(?:PO\s*(?:Number|#|No|\-)?\s*[:\s]+)([A-Z0-9\-_]+)",
        r"(?:Purchase\s+Order\s*(?:Number|#|No)?\s*[:\s]+)([A-Z0-9\-_]+)",
    ],
    "customer": [
        r"(?:Customer\s*(?:Name)?\s*[:\s]+)([^\n\r]+)",
        r"(?:Client\s*(?:Name)?\s*[:\s]+)([^\n\r]+)",
    ],
    "order_number": [
        r"(?:Order\s*(?:Number|#|No|\-)?\s*[:\s]+)([A-Z0-9\-_]+)",
        r"(?:Sales\s+Order\s*[:\s]+)([A-Z0-9\-_]+)",
    ],
    "material": [
        r"(?:Material\s*(?:/\s*Product)?\s*[:\s]+)([^\n\r]+)",
        r"(?:Item\s*(?:Description)?\s*[:\s]+)([^\n\r]+)",
        r"(?:Product\s*[:\s]+)([^\n\r]+)",
    ],
    "quantity": [
        r"(?:Quantity\s*[:\s]+)([0-9,.]+)",
        r"(?:Qty\s*[:\s]+)([0-9,.]+)",
    ],
    "uom": [
        r"(?:Unit\s*of\s*Measure\s*[:\s]+)([A-Za-z]+)",
        r"(?:UOM\s*[:\s]+)([A-Za-z]+)",
    ],
    "pickup_location": [
        r"(?:Pickup\s*Location\s*[:\s]+)([^\n\r]+)",
        r"(?:Ship\s*From\s*[:\s]+)([^\n\r]+)",
        r"(?:Origin\s*[:\s]+)([^\n\r]+)",
    ],
    "delivery_location": [
        r"(?:Delivery\s*Location\s*[:\s]+)([^\n\r]+)",
        r"(?:Ship\s*To\s*[:\s]+)([^\n\r]+)",
        r"(?:Destination\s*[:\s]+)([^\n\r]+)",
    ],
    "requested_pickup_date": [
        r"(?:Requested\s*Pickup\s*Date\s*[:\s]+)([0-9]{4}[-/][0-9]{2}[-/][0-9]{2})",
        r"(?:Pickup\s*Date\s*[:\s]+)([0-9]{4}[-/][0-9]{2}[-/][0-9]{2})",
    ],
    "requested_delivery_date": [
        r"(?:Requested\s*Delivery\s*Date\s*[:\s]+)([0-9]{4}[-/][0-9]{2}[-/][0-9]{2})",
        r"(?:Delivery\s*Date\s*[:\s]+)([0-9]{4}[-/][0-9]{2}[-/][0-9]{2})",
    ],
    "incoterms": [
        r"(?:Incoterms\s*[:\s]+)([A-Z]{3})",
        r"\b(DDP|FOB|EXW|CIF|CFR|DAP|CIP)\b",
    ],
}


class OrderExtractionAgent(Executor):
    """MAF-based Order Extraction Agent."""

    def __init__(self, id: str = "OrderExtractionAgent") -> None:
        super().__init__(id=id)
        self.validator = get_validator()
        self.json_store = get_json_store()

    def extract_fields(self, text: str) -> Tuple[Dict[str, Any], Dict[str, float], Dict[str, FieldExtraction]]:
        """Extract order fields and assign field-level confidences."""
        extracted_dict: Dict[str, Any] = {}
        confidences: Dict[str, float] = {}
        field_models: Dict[str, FieldExtraction] = {}

        # If text is valid JSON, parse directly
        try:
            json_data = json.loads(text)
            if isinstance(json_data, dict):
                # Map JSON fields
                key_map = {
                    "po_number": ["po_number", "ponumber", "po"],
                    "customer": ["customer_name", "customer", "client"],
                    "order_number": ["order_number", "ordernumber", "order_id"],
                    "material": ["material", "product", "item_description"],
                    "quantity": ["quantity", "qty"],
                    "uom": ["unit_of_measure", "uom"],
                    "pickup_location": ["pickup_location", "origin", "ship_from"],
                    "delivery_location": ["delivery_location", "destination", "ship_to"],
                    "requested_pickup_date": ["requested_pickup_date", "pickup_date"],
                    "requested_delivery_date": ["requested_delivery_date", "delivery_date"],
                    "incoterms": ["incoterms"],
                }
                for std_key, aliases in key_map.items():
                    val = None
                    for alias in aliases:
                        if alias in json_data and json_data[alias] is not None:
                            val = json_data[alias]
                            break

                    if val is not None:
                        extracted_dict[std_key] = val
                        confidences[std_key] = 0.99
                        field_models[std_key] = FieldExtraction(
                            value=val,
                            raw_value=str(val),
                            confidence=0.99,
                            is_missing=False,
                            is_uncertain=False,
                        )
                    else:
                        extracted_dict[std_key] = None
                        confidences[std_key] = 0.0
                        field_models[std_key] = FieldExtraction(
                            value=None,
                            raw_value=None,
                            confidence=0.0,
                            is_missing=True,
                            is_uncertain=True,
                        )
                return extracted_dict, confidences, field_models
        except Exception:
            pass

        # Dynamic Azure AI LLM Extraction (gpt-4.1-mini)
        from adapters.model_client import get_model_client_manager
        mgr = get_model_client_manager()
        if mgr.is_configured() and text.strip():
            ai_prompt = f"""You are the Order Extraction Agent for a freight logistics automation system.
Extract all business order and delivery fields from the document text below.
Fields to extract:
- po_number: string or null (Purchase order number, Order ID, Booking ID, or primary transaction reference)
- customer: string or null (Customer or ordering entity name, e.g. "Domino's Taiwan", "HAVI Logistics", "McDonald's")
- order_number: string or null (Sales order or secondary order number)
- material: string or null (Description of items, materials, or products ordered)
- quantity: number or integer (Total quantity of items/materials ordered, sum or primary item count, e.g. 10300. Must be a single numeric value, do not return comma-separated lists)
- uom: string or null (Unit of measure, e.g. packs, kg, cases, pieces, boxes)
- pickup_location: string or null (Origin, pickup location, airport, or address)
- delivery_location: string or null (Destination, delivery location, airport, or address)
- requested_pickup_date: string or null (Date in YYYY-MM-DD or as stated in document)
- requested_delivery_date: string or null (Date in YYYY-MM-DD or as stated in document)
- incoterms: string or null (Incoterm rule like FOB, DDP, CIF, EXW, or delivery mode)

Document Text:
\"\"\"{text[:4000]}\"\"\"

Return ONLY a valid JSON object matching this schema:
{{
  "po_number": {{"value": "...", "confidence": 0.95}},
  "customer": {{"value": "...", "confidence": 0.95}},
  "order_number": {{"value": "...", "confidence": 0.90}},
  "material": {{"value": "...", "confidence": 0.95}},
  "quantity": {{"value": 1000, "confidence": 0.95}},
  "uom": {{"value": "packs", "confidence": 0.95}},
  "pickup_location": {{"value": "...", "confidence": 0.95}},
  "delivery_location": {{"value": "...", "confidence": 0.95}},
  "requested_pickup_date": {{"value": "...", "confidence": 0.90}},
  "requested_delivery_date": {{"value": "...", "confidence": 0.90}},
  "incoterms": {{"value": "...", "confidence": 0.85}}
}}"""
            ai_resp = mgr.complete_sync(ai_prompt)
            if ai_resp:
                try:
                    cleaned = ai_resp.strip()
                    if "```json" in cleaned:
                        cleaned = cleaned.split("```json")[1].split("```")[0].strip()
                    elif "```" in cleaned:
                        cleaned = cleaned.split("```")[1].split("```")[0].strip()
                    data = json.loads(cleaned)
                    if isinstance(data, dict):
                        expected_keys = [
                            "po_number", "customer", "order_number", "material",
                            "quantity", "uom", "pickup_location", "delivery_location",
                            "requested_pickup_date", "requested_delivery_date", "incoterms"
                        ]
                        for k in expected_keys:
                            field_obj = data.get(k)
                            val = None
                            conf = 0.0
                            if isinstance(field_obj, dict):
                                val = field_obj.get("value")
                                conf = float(field_obj.get("confidence", 0.90))
                            elif field_obj is not None:
                                val = field_obj
                                conf = 0.90

                            if val is not None and str(val).strip() and str(val).lower() != "null":
                                if k == "quantity":
                                    try:
                                        if isinstance(val, (int, float)):
                                            val = float(val) if isinstance(val, float) else int(val)
                                        else:
                                            nums = re.findall(r"\b\d+(?:\.\d+)?\b", str(val).replace(",", ""))
                                            if nums:
                                                val = sum(float(n) if "." in n else int(n) for n in nums)
                                    except Exception:
                                        pass
                                extracted_dict[k] = val
                                confidences[k] = conf
                                field_models[k] = FieldExtraction(
                                    value=val,
                                    raw_value=str(val),
                                    confidence=conf,
                                    is_missing=False,
                                    is_uncertain=conf < self.validator.confidence_threshold,
                                )
                            else:
                                extracted_dict[k] = None
                                confidences[k] = 0.0
                                field_models[k] = FieldExtraction(
                                    value=None,
                                    raw_value=None,
                                    confidence=0.0,
                                    is_missing=True,
                                    is_uncertain=True,
                                )
                        if extracted_dict.get("po_number") or extracted_dict.get("customer"):
                            return extracted_dict, confidences, field_models
                except Exception as parse_exc:
                    logger.warning("[ORDER_AI_PARSE_WARN] Failed parsing AI extraction (%s); falling back to regex.", parse_exc)

        # Fallback: Text-based regex extraction
        for field_name, patterns in _PATTERNS.items():
            matched_val: Optional[str] = None
            conf = 0.0

            for pat in patterns:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    matched_val = m.group(1).strip()
                    conf = 0.95
                    break

            # Type conversion for quantity
            final_val: Any = matched_val
            if field_name == "quantity" and matched_val:
                try:
                    cleaned = matched_val.replace(",", "")
                    final_val = float(cleaned) if "." in cleaned else int(cleaned)
                except ValueError:
                    conf = 0.60  # Decreased confidence if number format invalid

            if matched_val:
                extracted_dict[field_name] = final_val
                confidences[field_name] = conf
                field_models[field_name] = FieldExtraction(
                    value=final_val,
                    raw_value=matched_val,
                    confidence=conf,
                    is_missing=False,
                    is_uncertain=conf < self.validator.confidence_threshold,
                )
            else:
                extracted_dict[field_name] = None
                confidences[field_name] = 0.0
                field_models[field_name] = FieldExtraction(
                    value=None,
                    raw_value=None,
                    confidence=0.0,
                    is_missing=True,
                    is_uncertain=True,
                )

        return extracted_dict, confidences, field_models

    @handler
    async def process(
        self,
        state: Dict[str, Any],
        ctx: WorkflowContext[Dict[str, Any], Dict[str, Any]],
    ) -> None:
        """MAF handler method invoked by the workflow engine."""
        doc_id = state.get("document_id", "DOC-UNKNOWN")
        logger.info("[ORDER_EXTRACTION_INVOKED] Extracting order fields for %s", doc_id)
        self.json_store.log_workflow_event(doc_id, "[OrderExtractionAgent] Extraction started.")

        text = state.get("extracted_text", "")
        fields, confs, field_models = self.extract_fields(text)

        # Run validation
        val_result = self.validator.validate_order(fields, confs)

        # Update state
        state["extracted_fields"] = fields
        state["field_confidence"] = confs
        state["validation"] = val_result.model_dump()

        if val_result.requires_hitl:
            state["route_to"] = "HITLAgent"
            state["workflow_status"] = "HITL_PENDING"
            logger.warning(
                "[ORDER_VALIDATION_FAILED] Doc %s has %d missing mandatory fields and %d low confidence fields. Routing to HITLAgent.",
                doc_id,
                len(val_result.missing_mandatory_fields),
                len(val_result.low_confidence_fields),
            )
            self.json_store.log_workflow_event(
                doc_id,
                f"[OrderExtractionAgent] Validation failed: missing {val_result.missing_mandatory_fields}. Routed to HITLAgent.",
            )
        else:
            if state.get("document_type") in (DocumentType.ORDER_UPDATE.value, "ORDER_UPDATE"):
                state["route_to"] = "CW1UpdateAgent"
                state["workflow_status"] = "VALIDATED"
                logger.info("[ORDER_UPDATE_VALIDATED] Doc %s validated successfully (conf: %.2f). Routed to CW1UpdateAgent.", doc_id, val_result.overall_confidence)
                self.json_store.log_workflow_event(
                    doc_id,
                    f"[OrderExtractionAgent] Order update validated (conf: {val_result.overall_confidence:.2f}). Routed to CW1UpdateAgent.",
                )
            else:
                state["route_to"] = "CW1CreateAgent"
                state["workflow_status"] = "VALIDATED"
                logger.info("[ORDER_VALIDATION_PASSED] Doc %s validated successfully (conf: %.2f). Routed to CW1CreateAgent.", doc_id, val_result.overall_confidence)
                self.json_store.log_workflow_event(
                    doc_id,
                    f"[OrderExtractionAgent] Validation passed (conf: {val_result.overall_confidence:.2f}). Routed to CW1CreateAgent.",
                )

        state.setdefault("history", []).append({
            "agent": "OrderExtractionAgent",
            "extracted_fields": fields,
            "validation": val_result.model_dump(),
            "route_to": state["route_to"],
        })

        await ctx.send_message(state)

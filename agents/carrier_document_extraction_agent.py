"""
Agent 3: Carrier Document Extraction Agent.

Specialized in extracting bill of lading, booking confirmation, and carrier milestone fields.
Identifies related purchase order or booking references for matching,
computes field-level confidence scores, and invokes carrier validation.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

from agent_framework import Executor, WorkflowContext, handler

from persistence.json_store import get_json_store
from schemas.models import FieldExtraction
from validation.validators import get_validator

logger = logging.getLogger("havi.agents.carrier_extraction")

# Field extraction patterns for carrier documents & Bills of Lading
_CARRIER_PATTERNS = {
    "booking_id": [
        r"(?:Booking\s+(?:Number|#|No|ID)\s*[:\s]+|Booking\s*:\s*)([A-Z0-9\-_]+)",
        r"(?:BKG\s*(?:#|No|ID)?\s*[:\s]+)([A-Z0-9\-_]+)",
        r"(?:BOL\s*(?:#|No|ID)?\s*[:\s]+)([A-Z0-9\-_]+)",
        r"(?:Bill\s+of\s+Lading\s*(?:Number|#|No|ID)?\s*[:\s]+)([A-Z0-9\-_]+)",
    ],
    "carrier_name": [
        r"(?:Carrier\s+Name\s*[:\s]+|Carrier\s*:\s*)([^\n\r]+)",
        r"(?:Shipping\s+Line\s*[:\s]+)([^\n\r]+)",
        r"\b(MAERSK|MSC|CMA\s+CGM|HAPAG-LLOYD|ONE|EVERGREEN|COSCO)\b",
    ],
    "vessel_or_flight": [
        r"(?:Vessel\s*(?:/\s*Voyage)?\s*[:\s]+)([^\n\r]+)",
        r"(?:Flight\s*(?:Number|#)?\s*[:\s]+)([^\n\r]+)",
    ],
    "container_number": [
        r"(?:Container\s*(?:Number|#|ID)?\s*[:\s]+)([A-Z]{4}[0-9]{7})",
        r"\b([A-Z]{4}[0-9]{7})\b",
    ],
    "estimated_departure": [
        r"(?:ETD|Estimated\s+Departure|Departure\s+Date)\s*[:\s]+([0-9]{4}[-/][0-9]{2}[-/][0-9]{2})",
    ],
    "estimated_arrival": [
        r"(?:ETA|Estimated\s+Arrival|Arrival\s+Date)\s*[:\s]+([0-9]{4}[-/][0-9]{2}[-/][0-9]{2})",
    ],
    "related_order_reference": [
        r"(?:Related\s+Order\s*(?:Reference|PO|#)?\s*[:\s]+)([A-Z0-9\-_]+)",
        r"(?:Customer\s+PO\s*(?:#|Number)?\s*[:\s]+)([A-Z0-9\-_]+)",
        r"(?:PO\s*(?:Number|#|No)?\s*[:\s]+)([A-Z0-9\-_]+)",
        r"(?:Ref\s*(?:Number|#)?\s*[:\s]+)([A-Z0-9\-_]+)",
    ],
}


class CarrierDocumentExtractionAgent(Executor):
    """MAF-based Carrier Document Extraction Agent."""

    def __init__(self, id: str = "CarrierDocumentExtractionAgent") -> None:
        super().__init__(id=id)
        self.validator = get_validator()
        self.json_store = get_json_store()

    def extract_carrier_fields(
        self, text: str
    ) -> Tuple[Dict[str, Any], Dict[str, float], Dict[str, FieldExtraction]]:
        """Extract carrier confirmation fields from text or JSON."""
        extracted: Dict[str, Any] = {}
        confs: Dict[str, float] = {}
        field_models: Dict[str, FieldExtraction] = {}

        # Check JSON input
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                aliases = {
                    "booking_id": ["booking_id", "booking_number", "bol_number"],
                    "carrier_name": ["carrier_name", "carrier", "shipping_line"],
                    "vessel_or_flight": ["vessel_or_flight", "vessel", "flight"],
                    "container_number": ["container_number", "container_id"],
                    "estimated_departure": ["estimated_departure", "etd"],
                    "estimated_arrival": ["estimated_arrival", "eta"],
                    "related_order_reference": ["related_order_reference", "po_number", "order_reference"],
                }
                for key, keys in aliases.items():
                    val = next((data[k] for k in keys if k in data and data[k] is not None), None)
                    if val is not None:
                        extracted[key] = val
                        confs[key] = 0.99
                        field_models[key] = FieldExtraction(
                            value=val,
                            raw_value=str(val),
                            confidence=0.99,
                            is_missing=False,
                            is_uncertain=False,
                        )
                    else:
                        extracted[key] = None
                        confs[key] = 0.0
                        field_models[key] = FieldExtraction(
                            value=None,
                            raw_value=None,
                            confidence=0.0,
                            is_missing=True,
                            is_uncertain=True,
                        )
                return extracted, confs, field_models
        except Exception:
            pass

        # Dynamic Azure AI LLM Extraction (gpt-4.1-mini)
        from adapters.model_client import get_model_client_manager
        mgr = get_model_client_manager()
        if mgr.is_configured() and text.strip():
            ai_prompt = f"""You are the Carrier Document Extraction Agent for a freight logistics automation platform.
Extract carrier confirmation, bill of lading, and milestone tracking fields from the document text below.
Fields to extract:
- booking_id: string or null (Carrier booking number, Bill of Lading BOL number, or Air Waybill AWB number)
- carrier_name: string or null (Ocean/air shipping line, airline, or carrier name, e.g. "EVA Air", "Maersk", "MSC", "Evergreen")
- vessel_or_flight: string or null (Vessel name, voyage number, flight number, or aircraft identifier)
- container_number: string or null (Standard 4-letter + 7-digit ISO container number, e.g. MSKU1234567, or ULD/pallet identifier)
- estimated_departure: string or null (Estimated departure date ETD in YYYY-MM-DD or as stated in document)
- estimated_arrival: string or null (Estimated arrival date ETA in YYYY-MM-DD or as stated in document)
- related_order_reference: string or null (Associated Purchase Order, PO Number, Order ID, or customer reference number)

Document Text:
\"\"\"{text[:4000]}\"\"\"

Return ONLY a valid JSON object matching this schema:
{{
  "booking_id": {{"value": "...", "confidence": 0.95}},
  "carrier_name": {{"value": "...", "confidence": 0.95}},
  "vessel_or_flight": {{"value": "...", "confidence": 0.90}},
  "container_number": {{"value": "...", "confidence": 0.90}},
  "estimated_departure": {{"value": "...", "confidence": 0.90}},
  "estimated_arrival": {{"value": "...", "confidence": 0.90}},
  "related_order_reference": {{"value": "...", "confidence": 0.95}}
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
                            "booking_id", "carrier_name", "vessel_or_flight",
                            "container_number", "estimated_departure", "estimated_arrival",
                            "related_order_reference"
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
                                extracted[k] = val
                                confs[k] = conf
                                field_models[k] = FieldExtraction(
                                    value=val,
                                    raw_value=str(val),
                                    confidence=conf,
                                    is_missing=False,
                                    is_uncertain=conf < self.validator.confidence_threshold,
                                )
                            else:
                                extracted[k] = None
                                confs[k] = 0.0
                                field_models[k] = FieldExtraction(
                                    value=None,
                                    raw_value=None,
                                    confidence=0.0,
                                    is_missing=True,
                                    is_uncertain=True,
                                )
                        if extracted.get("booking_id") or extracted.get("carrier_name"):
                            return extracted, confs, field_models
                except Exception as parse_exc:
                    logger.warning("[CARRIER_AI_PARSE_WARN] Failed parsing AI extraction (%s); falling back to regex.", parse_exc)

        # Regex extraction
        for field, patterns in _CARRIER_PATTERNS.items():
            matched: Optional[str] = None
            conf = 0.0
            for pat in patterns:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    matched = m.group(1).strip()
                    conf = 0.93
                    break

            if matched:
                extracted[field] = matched
                confs[field] = conf
                field_models[field] = FieldExtraction(
                    value=matched,
                    raw_value=matched,
                    confidence=conf,
                    is_missing=False,
                    is_uncertain=conf < self.validator.confidence_threshold,
                )
            else:
                extracted[field] = None
                confs[field] = 0.0
                field_models[field] = FieldExtraction(
                    value=None,
                    raw_value=None,
                    confidence=0.0,
                    is_missing=True,
                    is_uncertain=True,
                )

        return extracted, confs, field_models

    @handler
    async def process(
        self,
        state: Dict[str, Any],
        ctx: WorkflowContext[Dict[str, Any], Dict[str, Any]],
    ) -> None:
        """MAF handler method invoked by the workflow engine."""
        doc_id = state.get("document_id", "DOC-UNKNOWN")
        logger.info("[CARRIER_EXTRACTION_INVOKED] Extracting carrier fields for %s", doc_id)
        self.json_store.log_workflow_event(doc_id, "[CarrierDocumentExtractionAgent] Extraction started.")

        text = state.get("extracted_text", "")
        fields, confs, field_models = self.extract_carrier_fields(text)

        val_result = self.validator.validate_carrier_document(fields, confs)

        state["extracted_fields"] = fields
        state["field_confidence"] = confs
        state["validation"] = val_result.model_dump()

        if val_result.requires_hitl:
            state["route_to"] = "HITLAgent"
            state["workflow_status"] = "HITL_PENDING"
            logger.warning("[CARRIER_VALIDATION_FAILED] Missing mandatory fields: %s", val_result.missing_mandatory_fields)
            self.json_store.log_workflow_event(
                doc_id,
                f"[CarrierDocumentExtractionAgent] Validation failed: missing {val_result.missing_mandatory_fields}. Routed to HITLAgent.",
            )
        else:
            state["route_to"] = "CW1UpdateAgent"
            state["workflow_status"] = "VALIDATED"
            logger.info("[CARRIER_VALIDATION_PASSED] Carrier document validated successfully.")
            self.json_store.log_workflow_event(
                doc_id,
                "[CarrierDocumentExtractionAgent] Validation passed. Routed to CW1UpdateAgent.",
            )

        state.setdefault("history", []).append({
            "agent": "CarrierDocumentExtractionAgent",
            "extracted_fields": fields,
            "validation": val_result.model_dump(),
            "route_to": state["route_to"],
        })

        await ctx.send_message(state)

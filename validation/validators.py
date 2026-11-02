"""
Logistics Field Validators and Confidence Policy Engine.

Enforces:
  - Configurable confidence threshold (0.80).
  - Mandatory field checks for Purchase Orders and Carrier documents.
  - Distinction between missing mandatory vs. missing optional fields.
  - Overall confidence calculation (cannot override missing mandatory fields).
  - Revalidation logic for Human-in-the-Loop (HITL) corrections.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from config import Settings, get_settings
from schemas.models import ValidationResult

logger = logging.getLogger("havi.validation")

# Configurable schemas for mandatory fields
MANDATORY_ORDER_FIELDS: Set[str] = {
    "po_number",
    "customer",
    "material",
    "quantity",
    "pickup_location",
    "delivery_location",
}

OPTIONAL_ORDER_FIELDS: Set[str] = {
    "order_number",
    "uom",
    "requested_pickup_date",
    "requested_delivery_date",
    "incoterms",
}

MANDATORY_CARRIER_FIELDS: Set[str] = {
    "booking_id",
    "carrier_name",
    "related_order_reference",
}


class DocumentValidator:
    """Enforces validation policies, confidence gates, and HITL routing rules."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()

    @property
    def confidence_threshold(self) -> float:
        return self.settings.confidence_threshold

    def validate_order(
        self,
        fields: Dict[str, Any],
        confidence_scores: Dict[str, float],
    ) -> ValidationResult:
        """
        Validate extracted Purchase Order data.

        Rules:
          - Any missing mandatory field triggers HITL.
          - Any extracted field with confidence < 0.80 triggers HITL.
          - Missing optional fields do NOT trigger HITL.
        """
        missing_mandatory: List[str] = []
        low_confidence: List[str] = []
        validation_errors: List[str] = []

        # 1. Check mandatory fields
        for req in MANDATORY_ORDER_FIELDS:
            val = fields.get(req)
            if val is None or str(val).strip() == "" or str(val).strip().upper() == "NULL":
                missing_mandatory.append(req)
                validation_errors.append(f"Mandatory field '{req}' is missing or empty.")

        # 2. Check field confidences (only for mandatory or extracted optional fields)
        all_confidences: List[float] = []
        for f_name, score in confidence_scores.items():
            val = fields.get(f_name)
            # If an optional field is absent (None), do not fail validation on 0.0 confidence
            if val is None and f_name in OPTIONAL_ORDER_FIELDS:
                continue
            all_confidences.append(score)
            if score < self.confidence_threshold:
                low_confidence.append(f_name)
                validation_errors.append(
                    f"Field '{f_name}' confidence {score:.2f} is below threshold {self.confidence_threshold:.2f}."
                )

        # 3. Numeric validation for quantity if present
        qty = fields.get("quantity")
        if qty is not None:
            try:
                import re
                if isinstance(qty, (int, float)):
                    float_qty = float(qty)
                else:
                    cleaned_qty = str(qty).replace(",", "").strip()
                    try:
                        float_qty = float(cleaned_qty)
                    except ValueError:
                        # Extract all numbers from multi-item quantity strings (e.g., '1000, 1500, 2000')
                        nums = re.findall(r"\b\d+(?:\.\d+)?\b", str(qty).replace(",", ""))
                        if nums:
                            float_qty = sum(float(n) for n in nums)
                        else:
                            raise ValueError(f"No numeric quantity found in '{qty}'")
                if float_qty <= 0:
                    validation_errors.append(f"Quantity must be greater than zero, found: {qty}")
            except Exception:
                validation_errors.append(f"Quantity must be a valid number, found: {qty}")

        # Compute overall confidence
        overall_conf = (
            sum(all_confidences) / len(all_confidences) if all_confidences else 0.0
        )

        requires_hitl = (
            len(missing_mandatory) > 0
            or len(low_confidence) > 0
            or len(validation_errors) > 0
        )

        is_valid = not requires_hitl

        return ValidationResult(
            is_valid=is_valid,
            overall_confidence=round(overall_conf, 2),
            missing_mandatory_fields=missing_mandatory,
            low_confidence_fields=low_confidence,
            validation_errors=validation_errors,
            requires_hitl=requires_hitl,
        )

    def validate_carrier_document(
        self,
        fields: Dict[str, Any],
        confidence_scores: Dict[str, float],
    ) -> ValidationResult:
        """Validate extracted carrier confirmation or BOL document."""
        missing_mandatory: List[str] = []
        low_confidence: List[str] = []
        validation_errors: List[str] = []

        for req in MANDATORY_CARRIER_FIELDS:
            val = fields.get(req)
            if val is None or str(val).strip() == "":
                missing_mandatory.append(req)
                validation_errors.append(f"Carrier mandatory field '{req}' is missing.")

        all_confidences: List[float] = []
        for f_name, score in confidence_scores.items():
            val = fields.get(f_name)
            if val is None and f_name not in MANDATORY_CARRIER_FIELDS:
                continue
            all_confidences.append(score)
            if score < self.confidence_threshold:
                low_confidence.append(f_name)
                validation_errors.append(
                    f"Carrier field '{f_name}' confidence {score:.2f} below threshold {self.confidence_threshold:.2f}."
                )

        overall_conf = (
            sum(all_confidences) / len(all_confidences) if all_confidences else 0.0
        )

        requires_hitl = len(missing_mandatory) > 0 or len(low_confidence) > 0 or len(validation_errors) > 0
        is_valid = not requires_hitl

        return ValidationResult(
            is_valid=is_valid,
            overall_confidence=round(overall_conf, 2),
            missing_mandatory_fields=missing_mandatory,
            low_confidence_fields=low_confidence,
            validation_errors=validation_errors,
            requires_hitl=requires_hitl,
        )


_validator: Optional[DocumentValidator] = None


def get_validator() -> DocumentValidator:
    """Singleton getter for DocumentValidator."""
    global _validator
    if _validator is None:
        _validator = DocumentValidator()
    return _validator

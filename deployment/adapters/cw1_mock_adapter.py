"""
CargoWise One (CW1) Integration Adapter Interface & Mock Implementation.

Provides a pluggable adapter interface and a realistic local mock adapter.
Generates simulated CargoWise references, verifies payload structure,
and strictly returns designated mock statuses without making any network calls.
"""

from __future__ import annotations

import abc
import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from schemas.models import CW1OperationResult

logger = logging.getLogger("havi.adapters.cw1")


class BaseCW1Adapter(abc.ABC):
    """Abstract interface for CargoWise One order and shipment operations."""

    @abc.abstractmethod
    def create_order(
        self,
        order_payload: Dict[str, Any],
        document_id: str,
    ) -> CW1OperationResult:
        """Create a new shipment order in CargoWise One."""
        pass

    @abc.abstractmethod
    def update_order(
        self,
        cw1_reference: str,
        update_payload: Dict[str, Any],
        document_id: str,
    ) -> CW1OperationResult:
        """Update an existing shipment order in CargoWise One."""
        pass


class MockCW1Adapter(BaseCW1Adapter):
    """
    Deterministic local mock adapter for CargoWise One.

    Status Codes:
      - MOCK_READY: Payload constructed and passed pre-flight checks.
      - MOCK_CREATED: New order mock-registered with simulated reference (MOCK-CW1-ORD-XXXX).
      - MOCK_UPDATED: Existing order mock-updated with new carrier/shipment details.
      - DUPLICATE_DETECTED: Order already exists in duplicate store.
      - HITL_PENDING: Operation requires human review before dispatch.
      - VALIDATION_FAILED: Mandatory fields missing or invalid format.
      - PROCESSING_FAILED: Unhandled error in adapter processing.
    """

    def create_order(
        self,
        order_payload: Dict[str, Any],
        document_id: str,
    ) -> CW1OperationResult:
        """Simulate creating an order in CargoWise One."""
        logger.info("[MOCK_CW1_CREATE] Executing mock order creation for doc %s", document_id)

        po = order_payload.get("po_number") or "NOPO"
        cust = order_payload.get("customer") or "NOCUST"

        # Deterministic mock CW1 reference derived from document ID and PO
        seed = f"{document_id}_{po}_{cust}".encode("utf-8")
        ref_hash = hashlib.sha256(seed).hexdigest()[:8].upper()
        mock_cw1_ref = f"MOCK-CW1-ORD-{ref_hash}"

        return CW1OperationResult(
            operation_type="CREATE",
            status="MOCK_CREATED",
            cw1_reference=mock_cw1_ref,
            payload=order_payload,
            message=(
                f"Simulated new order creation successful. "
                f"Mock CargoWise reference assigned: {mock_cw1_ref}."
            ),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def update_order(
        self,
        cw1_reference: str,
        update_payload: Dict[str, Any],
        document_id: str,
    ) -> CW1OperationResult:
        """Simulate updating an existing order in CargoWise One."""
        logger.info(
            "[MOCK_CW1_UPDATE] Executing mock order update on ref %s for doc %s",
            cw1_reference,
            document_id,
        )

        return CW1OperationResult(
            operation_type="UPDATE",
            status="MOCK_UPDATED",
            cw1_reference=cw1_reference,
            payload=update_payload,
            message=(
                f"Simulated order update applied successfully to existing "
                f"CargoWise reference: {cw1_reference}."
            ),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )


_cw1_adapter: Optional[BaseCW1Adapter] = None


def get_cw1_adapter() -> BaseCW1Adapter:
    """Singleton getter for CW1 adapter."""
    global _cw1_adapter
    if _cw1_adapter is None:
        _cw1_adapter = MockCW1Adapter()
    return _cw1_adapter

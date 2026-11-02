"""
In-Memory Duplicate & Idempotency Store (Development / Test-Only).

IMPORTANT ARCHITECTURAL NOTICE:
================================
This store provides thread-safe in-memory deduplication for local workflow
execution, testing, and single-process hosted agent evaluation.

LIMITATIONS & PRODUCTION CONSTRAINTS:
1. NON-PERSISTENT ACROSS RESTARTS: All tracked document hashes and business
   order keys reside in process memory and are reset whenever the worker/container
   restarts.
2. NO CROSS-REPLICA SYNCHRONIZATION: If multiple worker instances or container
   replicas run concurrently, each replica maintains its own isolated in-memory
   state. It does not provide distributed synchronization across multiple pods.
3. FOR TEST & DEVELOPMENT ONLY: Do not claim production-grade persistent duplicate
   detection using this in-memory registry. For production, persistent distributed
   idempotency requires the enterprise integration layer (e.g. Logic Apps state
   or CargoWise primary key constraints).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("havi.persistence.duplicate_store")


class DuplicateStore:
    """
    Thread-safe in-memory store for local deduplication and state tracking.
    Zero external database or cloud storage dependencies.
    """

    def __init__(self, **kwargs: Any) -> None:
        self._lock = threading.Lock()
        # Tier A: file_hash -> document record dict
        self._document_records: Dict[str, Dict[str, Any]] = {}
        # Tier C: (normalized_po, normalized_customer) -> business order dict
        self._business_orders: Dict[tuple[str, str], Dict[str, Any]] = {}
        # Carrier updates history: list of carrier update dicts
        self._carrier_updates: List[Dict[str, Any]] = []

    def reset(self) -> None:
        """Clear all in-memory records (used for test isolation)."""
        with self._lock:
            self._document_records.clear()
            self._business_orders.clear()
            self._carrier_updates.clear()

    def check_document_duplicate(self, file_hash: str) -> Optional[Dict[str, Any]]:
        """
        Tier A: Check if this exact physical document has already been processed in memory.

        Returns:
            Dictionary with existing record details if duplicate, None otherwise.
        """
        if not file_hash:
            return None
        with self._lock:
            record = self._document_records.get(file_hash)
            return dict(record) if record else None

    def check_business_order_duplicate(
        self, po_number: str, customer: str
    ) -> Optional[Dict[str, Any]]:
        """
        Tier C: Check if a business order with this PO and Customer already exists in memory.

        Returns:
            Dictionary with existing order record if duplicate, None otherwise.
        """
        if not po_number or not customer:
            return None

        norm_po = po_number.strip().upper()
        norm_cust = customer.strip().upper()
        key = (norm_po, norm_cust)

        with self._lock:
            record = self._business_orders.get(key)
            return dict(record) if record else None

    def record_document(
        self,
        file_hash: str,
        document_id: str,
        file_name: str,
        document_type: str,
        workflow_status: str,
        cw1_reference: Optional[str] = None,
    ) -> None:
        """Persist or update document processing state in memory."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            if file_hash in self._document_records:
                self._document_records[file_hash]["workflow_status"] = workflow_status
                if cw1_reference:
                    self._document_records[file_hash]["cw1_reference"] = cw1_reference
                self._document_records[file_hash]["updated_at"] = now
            else:
                self._document_records[file_hash] = {
                    "file_hash": file_hash,
                    "document_id": document_id,
                    "file_name": file_name,
                    "document_type": document_type,
                    "workflow_status": workflow_status,
                    "cw1_reference": cw1_reference,
                    "created_at": now,
                    "updated_at": now,
                }

    def claim_document_processing(self, file_hash: str, document_id: str) -> bool:
        """
        Check if document hash can be claimed for processing.
        Returns True if not previously processed; False if duplicate.
        """
        return self.check_document_duplicate(file_hash) is None

    def claim_order_creation(self, po_number: str, customer: str, document_id: str) -> bool:
        """
        Check if order creation can proceed without violating uniqueness.
        Returns True if not duplicate; False if already exists.
        """
        return self.check_business_order_duplicate(po_number, customer) is None

    def record_created_order(
        self,
        po_number: str,
        customer: str,
        order_number: Optional[str],
        document_id: str,
        cw1_reference: str,
    ) -> bool:
        """
        Record a newly created business order in memory.

        Returns:
            True if inserted, False if duplicate constraint was violated.
        """
        if not po_number or not customer:
            return False

        norm_po = po_number.strip().upper()
        norm_cust = customer.strip().upper()
        key = (norm_po, norm_cust)
        now = datetime.now(timezone.utc).isoformat()

        with self._lock:
            if key in self._business_orders:
                logger.warning(
                    "[DUPLICATE_ORDER_SKIPPED] PO %s for Customer %s already recorded in memory.",
                    po_number,
                    customer,
                )
                return False

            self._business_orders[key] = {
                "po_number": po_number.strip(),
                "customer": customer.strip(),
                "order_number": order_number,
                "document_id": document_id,
                "cw1_reference": cw1_reference,
                "created_at": now,
            }
            return True

    def find_order_by_reference(self, reference: str) -> Optional[Dict[str, Any]]:
        """
        Find an existing order by PO number, order number, or CW1 reference.
        Used by CW1 Update Agent to match carrier documents to existing shipments.
        """
        if not reference:
            return None
        cleaned = reference.strip().upper()

        with self._lock:
            for record in self._business_orders.values():
                po = (record.get("po_number") or "").upper()
                order_num = (record.get("order_number") or "").upper()
                cw1 = (record.get("cw1_reference") or "").upper()
                if cleaned in (po, order_num, cw1):
                    return dict(record)
        return None

    def record_carrier_update(
        self,
        booking_id: Optional[str],
        carrier_name: Optional[str],
        related_order_reference: Optional[str],
        document_id: str,
        cw1_reference: Optional[str],
    ) -> None:
        """Record carrier update history in memory."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._carrier_updates.append({
                "booking_id": booking_id,
                "carrier_name": carrier_name,
                "related_order_reference": related_order_reference,
                "document_id": document_id,
                "cw1_reference": cw1_reference,
                "created_at": now,
            })


_duplicate_store: Optional[DuplicateStore] = None


def get_duplicate_store() -> DuplicateStore:
    """Singleton getter for in-memory DuplicateStore."""
    global _duplicate_store
    if _duplicate_store is None:
        _duplicate_store = DuplicateStore()
    return _duplicate_store

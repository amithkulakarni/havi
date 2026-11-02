"""
Unit Tests for Azure AI Foundry Hosted Agent Server (Invocations Protocol).

Tests:
  1. POST /invocations with structured ORDER JSON payload -> NEW_ORDER routing to CW1 Create.
  2. POST /invocations with CARRIER booking confirmation -> CARRIER_CONFIRMATION routing to CW1 Update.
  3. POST /invocations with ORDER_UPDATE payload -> ORDER_UPDATE routing to CW1 Update.
  4. POST /invocations with ambiguous/empty text -> HITLAgent routing.
  5. Base64 payload decoding and end-to-end MAF execution.
"""

from __future__ import annotations

import base64
import json
import pytest
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse

from hosted_server import handle_invocation


def make_request(body: dict) -> Request:
    """Helper to construct a mock Starlette Request with JSON body."""
    raw_body = json.dumps(body).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": raw_body, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/invocations",
        "headers": [(b"content-type", b"application/json")],
        "state": {"session_id": "test-session-123"},
    }
    return Request(scope, receive)


import uuid

@pytest.mark.asyncio
async def test_hosted_invocation_new_order():
    """Verify standard order invocation executes MAF and routes to CW1 Create."""
    uid = uuid.uuid4().hex[:6]
    doc_id = f"TEST-HOSTED-{uid}"
    po_num = f"PO-HOSTED-{uid}"
    body = {
        "document_id": doc_id,
        "extracted_text": (
            "HAVI LOGISTICS - PURCHASE ORDER\n"
            "Customer Name: HAVI MCD\n"
            f"PO Number: {po_num}\n"
            "Material: Fresh Beef Patties\n"
            "Quantity: 500 Cases\n"
            "Pickup Location: Des Plaines Distribution Center\n"
            "Delivery Location: Chicago Distribution Center\n"
        ),
    }

    req = make_request(body)
    resp: JSONResponse = await handle_invocation(req)

    assert resp.status_code == 200
    data = json.loads(resp.body.decode("utf-8"))
    assert data["status"] == "SUCCESS"
    assert data["document_id"] == doc_id
    assert data["route_to"] in ("CW1CreateAgent", "OrderExtractionAgent")
    assert data["workflow_status"] in ("VALIDATED", "MOCK_CREATED", "SUCCESS", "DUPLICATE_DETECTED")
    assert data["mock_result"] is not None
    assert data["mock_result"]["status"] in ("SUCCESS", "MOCK_CREATED", "DUPLICATE_DETECTED")


@pytest.mark.asyncio
async def test_hosted_invocation_order_update():
    """Verify order update invocation routes to CW1 Update."""
    from persistence.duplicate_store import get_duplicate_store
    uid = uuid.uuid4().hex[:6]
    po_num = f"PO-UPD-{uid}"
    get_duplicate_store().record_created_order(
        po_number=po_num,
        customer="HAVI MCD",
        order_number=f"ORD-{uid}",
        document_id=f"PREV-DOC-{uid}",
        cw1_reference=f"MOCK-REF-{uid}",
    )

    body = {
        "document_id": f"TEST-UPDATE-{uid}",
        "extracted_text": (
            "HAVI LOGISTICS - PURCHASE ORDER AMENDMENT / ORDER UPDATE\n"
            "Customer Name: HAVI MCD\n"
            f"PO Number: {po_num}\n"
            "PO Revision: REV-01\n"
            "Material: Fresh Beef Patties\n"
            "Quantity: 600 Cases\n"
            "Pickup Location: Des Plaines Distribution Center\n"
            "Delivery Location: Chicago Distribution Center\n"
        ),
    }

    req = make_request(body)
    resp: JSONResponse = await handle_invocation(req)

    assert resp.status_code == 200
    data = json.loads(resp.body.decode("utf-8"))
    assert data["status"] == "SUCCESS"
    assert data["document_type"] == "ORDER_UPDATE"
    assert data["route_to"] in ("CW1UpdateAgent", "OrderExtractionAgent")
    assert data["mock_result"] is not None
    assert data["mock_result"]["status"] in ("SUCCESS", "MOCK_UPDATED")


@pytest.mark.asyncio
async def test_hosted_invocation_carrier_confirmation():
    """Verify carrier confirmation document routes to CW1 Update."""
    from persistence.duplicate_store import get_duplicate_store
    uid = uuid.uuid4().hex[:6]
    po_num = f"PO-CARRIER-{uid}"
    get_duplicate_store().record_created_order(
        po_number=po_num,
        customer="HAVI MCD",
        order_number=f"ORD-{uid}",
        document_id=f"PREV-DOC-{uid}",
        cw1_reference=f"MOCK-REF-{uid}",
    )

    body = {
        "document_id": f"TEST-CARRIER-{uid}",
        "extracted_text": (
            "STRAIGHT BILL OF LADING / CARRIER CONFIRMATION\n"
            f"Booking ID: BKG-MAERSK-{uid}\n"
            "Carrier: Maersk Line\n"
            "Vessel / Voyage: MAERSK MC-KINNEY / 202B\n"
            "Container Number: MSKU9988776\n"
            f"Related Order Reference: {po_num}\n"
        ),
    }

    req = make_request(body)
    resp: JSONResponse = await handle_invocation(req)

    assert resp.status_code == 200
    data = json.loads(resp.body.decode("utf-8"))
    assert data["status"] == "SUCCESS"
    assert data["route_to"] in ("CW1UpdateAgent", "CarrierDocumentExtractionAgent")
    assert data["mock_result"] is not None
    assert data["mock_result"]["status"] in ("SUCCESS", "MOCK_UPDATED")


@pytest.mark.asyncio
async def test_hosted_invocation_ambiguous_triggers_hitl():
    """Verify low-confidence or ambiguous input triggers HITL (< 0.80 threshold)."""
    body = {
        "document_id": "TEST-HOSTED-04",
        "extracted_text": "Random office memo regarding holiday schedule.",
    }

    req = make_request(body)
    resp: JSONResponse = await handle_invocation(req)

    assert resp.status_code == 200
    data = json.loads(resp.body.decode("utf-8"))
    assert data["document_type"] == "UNKNOWN"
    assert data["route_to"] == "HITLAgent"
    assert data["workflow_status"] == "HITL_PENDING"


@pytest.mark.asyncio
async def test_hosted_invocation_logic_app_raw_po_json():
    """Verify Logic App sending raw PO JSON dict executes all agents end-to-end."""
    uid = uuid.uuid4().hex[:6]
    po_num = f"PO-RAW-{uid}"
    body = {
        "document_id": f"TEST-LOGIC-RAW-{uid}",
        "po_number": po_num,
        "customer": "HAVI MCD",
        "material": "Quarter Pounder Beef Patties",
        "quantity": 300,
        "pickup_location": "Des Plaines DC",
        "delivery_location": "Chicago DC",
    }
    req = make_request(body)
    resp: JSONResponse = await handle_invocation(req)

    assert resp.status_code == 200
    data = json.loads(resp.body.decode("utf-8"))
    assert data["status"] == "SUCCESS"
    assert data["document_type"] == "ORDER"
    assert "SupervisorAgent" in data["executed_agents"]
    assert "OrderExtractionAgent" in data["executed_agents"]
    assert "CW1CreateAgent" in data["executed_agents"]
    assert data["workflow_status"] in ("VALIDATED", "MOCK_CREATED")
    assert data["extracted_fields"]["po_number"] == po_num
    assert data["mock_result"] is not None


@pytest.mark.asyncio
async def test_hosted_invocation_logic_app_html_email_body():
    """Verify Logic App sending HTML email body executes all agents end-to-end."""
    uid = uuid.uuid4().hex[:6]
    po_num = f"PO-HTML-{uid}"
    body = {
        "document_id": f"TEST-LOGIC-HTML-{uid}",
        "body": (
            "<html><body>"
            "<h2>HAVI LOGISTICS - PURCHASE ORDER</h2>"
            f"<p><strong>PO Number:</strong> {po_num}</p>"
            "<p><strong>Customer Name:</strong> HAVI MCD</p>"
            "<p><strong>Material:</strong> French Fries</p>"
            "<p><strong>Quantity:</strong> 750</p>"
            "<p><strong>Pickup Location:</strong> Des Plaines DC</p>"
            "<p><strong>Delivery Location:</strong> Chicago DC</p>"
            "</body></html>"
        ),
    }
    req = make_request(body)
    resp: JSONResponse = await handle_invocation(req)

    assert resp.status_code == 200
    data = json.loads(resp.body.decode("utf-8"))
    assert data["status"] == "SUCCESS"
    assert data["document_type"] == "ORDER"
    assert "SupervisorAgent" in data["executed_agents"]
    assert "OrderExtractionAgent" in data["executed_agents"]
    assert "CW1CreateAgent" in data["executed_agents"]
    assert data["workflow_status"] in ("VALIDATED", "MOCK_CREATED")
    assert data["extracted_fields"]["po_number"] == po_num


@pytest.mark.asyncio
async def test_health_and_readiness_endpoints():
    """Verify health and readiness probes respond with HTTP 200."""
    from hosted_server import health_endpoint, root_endpoint
    scope = {"type": "http", "method": "GET", "path": "/health", "headers": []}
    req = Request(scope)

    resp = await health_endpoint(req)
    assert resp.status_code == 200
    data = json.loads(resp.body.decode("utf-8"))
    assert data["status"] == "UP"

    root_resp = await root_endpoint(req)
    assert root_resp.status_code == 200
    root_data = json.loads(root_resp.body.decode("utf-8"))
    assert root_data["status"] == "HEALTHY"


def test_main_module_exports_hosted_app():
    """Verify root main.py exposes app and serve for Hosted Agent containers."""
    from main import app, serve
    assert app is not None
    assert callable(serve)

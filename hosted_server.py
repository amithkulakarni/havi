"""
Azure AI Foundry Hosted Agent Server for MAF Freight Automation Platform.

Implements the official Microsoft Agent Server protocols:
  1. Invocations Protocol (/invocations POST endpoint) for Logic Apps & standard HTTP JSON callers.
  2. Responses Protocol (/responses POST endpoint) for OpenAI/Foundry conversational streaming callers.

Hosted natively inside Azure AI Foundry without external web app or API gateways.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from azure.ai.agentserver.invocations import InvocationAgentServerHost
from azure.ai.agentserver.responses import (
    CreateResponse,
    ResponseContext,
    ResponsesAgentServerHost,
)
from azure.ai.agentserver.responses.aio import ResponseEventStream
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from config import get_settings
from document_processing.pdf_parser import get_pdf_parser
from orchestration.workflow import get_orchestrator
from schemas.models import ExtractedDocument, ExtractionStatus, PageExtraction

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("havi.hosted_server")


class HostedFreightAgentApp(InvocationAgentServerHost, ResponsesAgentServerHost):
    """Multi-protocol Hosted Agent application for Azure AI Foundry."""
    pass


app = HostedFreightAgentApp()


def _clean_html_text(html_text: str) -> str:
    """Strip HTML tags and convert basic break lines into clean text."""
    if not html_text:
        return ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", html_text)
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"(?i)</tr>", "\n", text)
    text = re.sub(r"(?i)</td>", "\t", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _build_document_from_payload(payload: Any) -> ExtractedDocument:
    """Extract ExtractedDocument from incoming payload (supports text, JSON, base64, URLs, Logic Apps)."""
    settings = get_settings()
    settings.ensure_directories()
    parser = get_pdf_parser()

    # 0. Normalize list or envelope wrappers (common in Logic Apps / Power Automate)
    if isinstance(payload, list) and len(payload) > 0:
        if isinstance(payload[0], dict):
            payload = payload[0]
        else:
            payload = {"extracted_text": str(payload)}
    elif not isinstance(payload, dict):
        payload = {"extracted_text": str(payload)}

    # If payload contains an attachments array (Logic App Outlook connector)
    attachments = payload.get("attachments") or payload.get("Attachments")
    if isinstance(attachments, list) and len(attachments) > 0 and isinstance(attachments[0], dict):
        att = attachments[0]
        for k in ("contentBytes", "$content", "content", "name", "fileName", "contentType"):
            if k in att and k not in payload:
                payload[k] = att[k]

    doc_id = str(payload.get("document_id") or payload.get("id") or f"DOC-HOSTED-{os.urandom(4).hex()}")
    filename = (
        payload.get("filename")
        or payload.get("source_filename")
        or payload.get("name")
        or payload.get("fileName")
        or payload.get("FileName")
        or payload.get("originalFileName")
        or "payload_document.txt"
    )

    # 1. Base64 encoded file payload (PDF, image, or text attachment)
    b64_content = (
        payload.get("file_content_base64")
        or payload.get("file_base64")
        or payload.get("content_base64")
        or payload.get("contentBytes")
        or payload.get("$content")
        or payload.get("fileContent")
        or payload.get("attachmentData")
    )
    text = ""
    if b64_content and isinstance(b64_content, str):
        # Remove data URI header if present
        if "," in b64_content and b64_content.startswith("data:"):
            b64_content = b64_content.split(",", 1)[1]

        file_bytes = None
        try:
            file_bytes = base64.b64decode(b64_content)
        except Exception as b64_err:
            logger.warning("[BASE64_DECODE_WARN] Failed base64 decode: %s", b64_err)

        if file_bytes:
            ext = Path(filename).suffix.lower()
            if not ext or ext not in (".pdf", ".png", ".jpg", ".jpeg", ".tiff"):
                if file_bytes.startswith(b"%PDF"):
                    ext = ".pdf"
                else:
                    try:
                        decoded_str = file_bytes.decode("utf-8")
                        if decoded_str.strip():
                            text = decoded_str
                            b64_content = None  # Process as text below
                    except UnicodeDecodeError:
                        ext = ".pdf"

            if b64_content:
                settings.temp_dir.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=str(settings.temp_dir), suffix=ext, delete=False) as tmp_file:
                    tmp_file.write(file_bytes)
                    tmp_path = tmp_file.name

                try:
                    doc = parser.parse_document(tmp_path)
                    doc.document_id = doc_id
                    doc.file_name = filename
                    doc.source_filename = filename
                    return doc
                finally:
                    if os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except OSError:
                            pass

    # 2. Remote Blob Storage / HTTP SAS URL (Azure Blob Storage connector / Event Grid)
    file_url = (
        payload.get("blob_url")
        or payload.get("blobUrl")
        or payload.get("file_url")
        or payload.get("fileUrl")
        or payload.get("url")
        or payload.get("uri")
        or payload.get("sas_url")
        or payload.get("sasUrl")
        or payload.get("sourceUrl")
    )
    if file_url and isinstance(file_url, str):
        import urllib.request
        ext = Path(filename).suffix.lower() or ".pdf"
        settings.temp_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=str(settings.temp_dir), suffix=ext, delete=False) as tmp_file:
            req = urllib.request.Request(file_url, headers={"User-Agent": "HaviFreightAgent/1.0"})
            with urllib.request.urlopen(req, timeout=30) as response:
                tmp_file.write(response.read())
            tmp_path = tmp_file.name

        try:
            doc = parser.parse_document(tmp_path)
            doc.document_id = doc_id
            doc.file_name = filename
            doc.source_filename = filename
            return doc
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    # 3. Local file path reference (for mounted volumes / local tests)
    local_path = payload.get("file_path") or payload.get("filePath") or payload.get("path")
    if local_path and isinstance(local_path, str) and os.path.exists(local_path):
        doc = parser.parse_document(local_path)
        doc.document_id = doc_id
        return doc

    # 4. Conversational / Foundry / OpenAI Agent message format
    input_field = payload.get("input") or payload.get("messages")
    if isinstance(input_field, list):
        msg_parts = []
        for msg in input_field:
            if isinstance(msg, dict):
                c = msg.get("content") or msg.get("text") or ""
                if isinstance(c, str):
                    msg_parts.append(c)
            elif isinstance(msg, str):
                msg_parts.append(msg)
        if msg_parts:
            text = "\n".join(msg_parts)
    elif isinstance(input_field, str) and input_field.strip():
        text = input_field

    # 5. Direct text or structured payload
    if not text:
        text_val = (
            payload.get("extracted_text")
            or payload.get("text")
            or payload.get("content")
            or payload.get("prompt")
            or payload.get("body")
            or payload.get("message")
            or payload.get("data")
            or payload.get("email_body")
            or payload.get("raw_text")
            or ""
        )
        if isinstance(text_val, str):
            text = text_val
        elif isinstance(text_val, dict):
            text = json.dumps(text_val, indent=2)

    # If text is HTML (common from Office 365 Outlook connector)
    if text and ("<html" in text.lower() or "<p" in text.lower() or "<br" in text.lower() or "<div" in text.lower()):
        text = _clean_html_text(text)

    # 6. Fallback: If text is still empty, serialize the entire dictionary
    if not text.strip():
        meta_keys = {"document_id", "id", "filename", "source_filename", "session_id", "headers"}
        business_keys = [k for k in payload.keys() if k not in meta_keys]
        if business_keys:
            text = json.dumps(payload, indent=2)

    has_text = bool(text.strip())
    doc = ExtractedDocument(
        document_id=doc_id,
        source_file_path=str(settings.test_data_dir / filename),
        file_name=filename,
        file_size_bytes=len(text.encode("utf-8")),
        file_hash_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest() if text else "empty_hash",
        mime_type="application/pdf" if filename.lower().endswith(".pdf") else "text/plain",
        total_pages=1 if has_text else 0,
        status=ExtractionStatus.SUCCESS if has_text else ExtractionStatus.OCR_UNAVAILABLE,
        is_scanned=False,
        ocr_applied=False,
        pages=[
            PageExtraction(
                page_number=1,
                text=text,
                char_count=len(text),
                image_count=0,
                has_images=False,
                is_scanned_page=False,
            )
        ] if has_text else [],
        extracted_text=text,
        error_message=None if has_text else "No extractable text provided in input payload.",
    )
    return doc


@app.invoke_handler
async def handle_invocation(request: Request) -> Response:
    """
    Handle POST /invocations requests from Azure Logic Apps or standard HTTP callers.
    """
    try:
        data = await request.json()
    except Exception as exc:
        logger.error("[INVOCATION_PAYLOAD_ERR] Invalid JSON: %s", exc)
        return JSONResponse(
            status_code=400,
            content={"status": "ERROR", "error": f"Invalid JSON payload: {str(exc)}"},
        )

    logger.info("[INVOCATION_RECEIVED] Invocations request received. Processing through MAF Orchestrator...")
    doc = _build_document_from_payload(data)

    orchestrator = get_orchestrator()
    try:
        result = await orchestrator.execute_async(doc)
        executed_agents = [
            h.get("agent") for h in result.history if isinstance(h, dict) and h.get("agent")
        ]
        response_dict = {
            "status": "SUCCESS",
            "document_id": result.document_id,
            "source_filename": result.source_filename,
            "document_type": result.document_type.value,
            "route_to": result.route_to,
            "executed_agents": executed_agents,
            "final_agent": executed_agents[-1] if executed_agents else (result.route_to or "SupervisorAgent"),
            "workflow_status": result.workflow_status.value,
            "duplicate_status": result.duplicate_status,
            "validation": result.validation.model_dump() if result.validation else None,
            "extracted_fields": result.extracted_fields,
            "field_confidence": result.field_confidence,
            "mock_result": result.mock_result.model_dump() if result.mock_result else None,
            "hitl_details": result.hitl_details.model_dump() if result.hitl_details else None,
            "timestamps": result.timestamps,
            "errors": result.errors,
            "history": result.history,
        }
        return JSONResponse(status_code=200, content=response_dict)
    except Exception as exc:
        logger.error("[INVOCATION_EXEC_ERR] Workflow execution failed: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "FAILED", "document_id": doc.document_id, "error": str(exc)},
        )



@app.response_handler
async def handle_response(
    request: CreateResponse,
    context: ResponseContext,
    cancellation_signal: asyncio.Event,
):
    """
    Handle POST /responses requests under OpenAI/Foundry conversational streaming protocol.
    """
    stream = ResponseEventStream(response_id=context.response_id, request=request)
    yield stream.emit_created()
    yield stream.emit_in_progress()

    input_text = getattr(request, "input", "")
    if isinstance(input_text, list):
        # Extract content from message list if formatted as chat history
        parts = []
        for msg in input_text:
            content = getattr(msg, "content", "")
            if isinstance(content, str):
                parts.append(content)
        input_text = "\n".join(parts)

    payload = {"extracted_text": str(input_text)}
    doc = _build_document_from_payload(payload)

    orchestrator = get_orchestrator()
    result = await orchestrator.execute_async(doc)

    summary = (
        f"Document ID: {result.document_id}\n"
        f"Classification: {result.document_type.value}\n"
        f"Route: {result.route_to}\n"
        f"Workflow Status: {result.workflow_status.value}\n"
        f"Extracted Fields: {json.dumps(result.extracted_fields, indent=2)}"
    )

    async for event in stream.output_item_message(summary):
        if cancellation_signal.is_set():
            break
        yield event

    yield stream.emit_completed()


async def root_endpoint(request: Request) -> Response:
    """Root health and discovery endpoint for Azure App Service."""
    settings = get_settings()
    return JSONResponse(
        content={
            "service": "HAVI MCD MAF Hosted Agent Server",
            "status": "HEALTHY",
            "version": settings.app_version,
            "runtime_root": str(settings.runtime_root),
            "endpoints": ["/invocations", "/responses", "/health", "/readiness", "/status"],
        }
    )


async def health_endpoint(request: Request) -> Response:
    """Health probe endpoint."""
    settings = get_settings()
    return JSONResponse(
        content={
            "status": "UP",
            "service": "havi-mcd-hosted-agent",
            "version": settings.app_version,
            "runtime_root": str(settings.runtime_root),
        }
    )


async def status_endpoint(request: Request) -> Response:
    """Detailed operational status endpoint."""
    settings = get_settings()
    return JSONResponse(
        content={
            "status": "OPERATIONAL",
            "service": "havi-mcd-hosted-agent",
            "version": settings.app_version,
            "runtime_root": str(settings.runtime_root),
            "output_dir": str(settings.output_dir),
            "temp_dir": str(settings.temp_dir),
            "auth_method": settings.azure_auth_method,
            "model_deployment": settings.azure_ai_model_deployment_name,
        }
    )


app.add_route("/", root_endpoint, methods=["GET"])
app.add_route("/health", health_endpoint, methods=["GET"])
app.add_route("/healthz", health_endpoint, methods=["GET"])
app.add_route("/readiness", health_endpoint, methods=["GET"])
app.add_route("/status", status_endpoint, methods=["GET"])


def serve(port: int = 8080, host: str = "0.0.0.0") -> None:
    """Run the Hosted Agent server."""
    logger.info("[HOSTED_SERVER_START] Starting Foundry Hosted Agent Server on %s:%d...", host, port)
    app.run(port=port, host=host)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", os.environ.get("AGENT_PORT", os.environ.get("WEBSITES_PORT", "8080"))))
    serve(port=port)

"""
Local PDF to End-to-End Freight Automation - Master CLI Entry Point.

Executes the complete local freight automation workflow:
  1. Validates local file path and input format.
  2. Extracts text or detects scanned document via PDFParser.
  3. Executes in-process Microsoft Agent Framework (MAF) workflow:
     - SupervisorAgent: Classifies document type and determines route.
     - OrderExtractionAgent / CarrierDocumentExtractionAgent: Extracts structured fields & confidences.
     - Validation: Evaluates mandatory fields and 0.80 confidence threshold.
     - CW1CreateAgent / CW1UpdateAgent: Enforces SQLite deduplication & calls Mock CW1 adapter.
     - HITLAgent: Captures review package and manages workflow resumption.
  4. Saves local JSON outputs to output/ (results/, payloads/, reviews/, logs/).
  5. Displays comprehensive execution report in the terminal.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from agents.hitl_agent import get_hitl_agent
from config import get_settings
from document_processing.ocr_service import get_ocr_service
from document_processing.pdf_parser import get_pdf_parser
from hosted_server import app, serve
from orchestration.workflow import get_orchestrator
from schemas.models import ExtractionStatus, ProcessingResult, WorkflowStatus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("havi.main")


def print_banner() -> None:
    """Print master terminal banner."""
    print("=" * 76)
    print("  LOCAL PDF TO END-TO-END MAF FREIGHT AUTOMATION PLATFORM")
    print("  Microsoft Agent Framework (MAF 1.21.0) Autonomous Orchestration Engine")
    print("=" * 76)


def format_field_table(fields: Dict[str, Any], confidences: Dict[str, float]) -> str:
    """Format extracted fields and confidence scores into a clean CLI table."""
    lines = []
    lines.append(f"    {'Field Name':<26} | {'Confidence':<10} | {'Extracted Value'}")
    lines.append(f"    {'-'*26} + {'-'*10} + {'-'*30}")
    for k, v in fields.items():
        conf = confidences.get(k, 0.0)
        conf_str = f"{conf:.2f}"
        val_str = str(v) if v is not None else "(MISSING / NULL)"
        lines.append(f"    {k:<26} | {conf_str:<10} | {val_str}")
    return "\n".join(lines)


def run_document_extraction(file_path: str, save_output: bool = True) -> int:
    """Backward compatibility alias for process_file_e2e."""
    return process_file_e2e(file_path)


def process_file_e2e(file_path: str) -> int:
    """
    Run end-to-end MAF processing on a local document.

    Returns:
        Exit code: 0 for success, 2 for HITL pending / scanned, 1 for errors.
    """
    settings = get_settings()
    settings.ensure_directories()
    parser = get_pdf_parser()
    orchestrator = get_orchestrator()

    print_banner()
    print(f"[*] Input File Target:  {file_path}")

    target_path = Path(file_path).resolve()
    if not target_path.exists():
        print(f"\n[!] ERROR: Input file does not exist: {file_path}")
        return 1

    # Step 1: Document Ingestion and Text Extraction
    print("\n[Stage 1] Parsing local document structure and extracting text...")
    doc = parser.parse_document(str(target_path))

    print(f"  - Document ID:       {doc.document_id}")
    print(f"  - SHA-256 Hash:      {doc.file_hash_sha256}")
    print(f"  - Parser Status:     {doc.status.value}")
    print(f"  - Scanned PDF:       {doc.is_scanned}")
    print(f"  - Extractable Chars: {len(doc.extracted_text)}")

    # Step 2: Execute MAF Workflow
    print("\n[Stage 2] Executing Microsoft Agent Framework (MAF) Orchestration Graph...")
    result = orchestrator.execute(doc)

    print("\n" + "=" * 76)
    print(f"  MAF AUTOMATION WORKFLOW EXECUTION REPORT - {result.document_id}")
    print("=" * 76)
    print(f"  Document ID:         {result.document_id}")
    print(f"  Source File:         {result.source_filename}")
    print(f"  Document Type:       {result.document_type.value}")
    print(f"  Final Route:         {result.route_to}")
    print(f"  Workflow Status:     {result.workflow_status.value}")
    print(f"  Duplicate Status:    {result.duplicate_status or 'N/A'}")

    # Validation Details
    if result.validation:
        val = result.validation
        print("\n  [Validation Gate]")
        print(f"    - Extraction Confidence: {val.overall_confidence:.2f} (Threshold: {settings.confidence_threshold:.2f} -> PASS)") if val.overall_confidence >= settings.confidence_threshold else print(f"    - Extraction Confidence: {val.overall_confidence:.2f} (Threshold: {settings.confidence_threshold:.2f} -> LOW)")
        print(f"    - Requires HITL:         {val.requires_hitl}")
        if val.requires_hitl:
            hitl_reasons = []
            if val.overall_confidence < settings.confidence_threshold:
                hitl_reasons.append(f"Confidence score ({val.overall_confidence:.2f}) < 0.80")
            if val.missing_mandatory_fields:
                hitl_reasons.append(f"Missing mandatory fields: {val.missing_mandatory_fields}")
            if val.validation_errors:
                hitl_reasons.append(f"Business rule validation: {val.validation_errors}")
            print(f"    - HITL Trigger Reason:   {'; '.join(hitl_reasons)}")
        if val.missing_mandatory_fields:
            print(f"    - Missing Mandatory:     {val.missing_mandatory_fields}")
        if val.low_confidence_fields:
            print(f"    - Low Confidence:        {val.low_confidence_fields}")
        if val.validation_errors:
            print(f"    - Validation Errors:     {val.validation_errors}")

    # Field Extractions Table
    if result.extracted_fields:
        print("\n  [Extracted Business Fields]")
        print(format_field_table(result.extracted_fields, result.field_confidence))

    # Mock CW1 Outcome
    if result.mock_result:
        mr = result.mock_result
        print("\n  [CargoWise One Mock Adapter Outcome]")
        print(f"    - Operation:           {mr.operation_type}")
        print(f"    - Status:              {mr.status}")
        print(f"    - CW1 Mock Reference:  {mr.cw1_reference or 'None'}")
        print(f"    - Simulation Message:  {mr.message}")

    # HITL Review Details
    if result.hitl_details:
        hr = result.hitl_details
        print("\n  [Human-in-the-Loop Review Case]")
        print(f"    - Raised By Agent:     {hr.raised_by_agent}")
        print(f"    - Review Status:       {hr.status}")
        print(f"    - Pending Issues:      {hr.missing_fields + hr.validation_errors}")
        print(f"    - Review Case File:    output/reviews/{result.document_id}_review.json")
        print("    * To resume this workflow after review, execute:")
        print(f'      python main.py --review {result.document_id} --corrections \'{{"po_number": "PO-XXXXX"}}\'')

    # Output file paths
    print("\n  [Local JSON Artifacts]")
    print(f"    - Result JSON:         output/results/{result.document_id}_result.json")
    if result.mock_result and result.mock_result.payload:
        print(f"    - CW1 Payload JSON:    output/payloads/{result.document_id}_cw1_payload.json")
    if result.hitl_details:
        print(f"    - Review JSON:         output/reviews/{result.document_id}_review.json")
        alert_file = f"output/notifications/{result.document_id}_alert.json"
        if os.path.exists(alert_file):
            print(f"    - Alert JSON (HITL):   {alert_file}")
    print(f"    - Execution Log:       output/logs/{result.document_id}_workflow.log")
    print("=" * 76)

    if result.workflow_status in (WorkflowStatus.MOCK_CREATED, WorkflowStatus.MOCK_UPDATED, WorkflowStatus.DUPLICATE_DETECTED):
        return 0
    elif result.workflow_status == WorkflowStatus.HITL_PENDING:
        return 2
    else:
        return 1


def handle_review_cli(doc_id: str, corrections_str: str) -> int:
    """Handle CLI submission of corrections for a pending HITL review."""
    print_banner()
    print(f"[*] Resuming HITL Review for Document ID: {doc_id}")
    try:
        corrections = json.loads(corrections_str)
    except Exception as exc:
        print(f"\n[!] ERROR: Invalid JSON in --corrections: {exc}")
        return 1

    hitl_agent = get_hitl_agent()
    try:
        updated_result = hitl_agent.apply_corrections_and_resume(doc_id, corrections)
    except Exception as exc:
        print(f"\n[!] ERROR resuming review: {exc}")
        return 1

    print("\n" + "=" * 76)
    print(f"  HITL RESUMPTION REPORT - {updated_result.document_id}")
    print("=" * 76)
    print(f"  Workflow Status:   {updated_result.workflow_status.value}")
    if updated_result.mock_result:
        print(f"  CW1 Mock Status:   {updated_result.mock_result.status}")
        print(f"  CW1 Reference:     {updated_result.mock_result.cw1_reference}")
        print(f"  Message:           {updated_result.mock_result.message}")
    if updated_result.validation and not updated_result.validation.is_valid:
        print(f"  Remaining Errors:  {updated_result.validation.validation_errors}")
    print(f"  Updated Result:    output/results/{doc_id}_result.json")
    print("=" * 76)

    return 0 if updated_result.workflow_status in (WorkflowStatus.MOCK_CREATED, WorkflowStatus.MOCK_UPDATED) else 2


def handle_interactive_review(doc_id: str) -> int:
    """Interactively prompt user in CLI to fill in missing mandatory fields."""
    print_banner()
    hitl_agent = get_hitl_agent()
    review = hitl_agent.json_store.load_review(doc_id)
    if not review:
        print(f"[!] No review package found for document {doc_id}")
        return 1

    print(f"[*] Interactive Review for Document: {doc_id} ({review.source_filename})")
    print(f"    Raised By: {review.raised_by_agent}")
    print(f"    Issues:    {review.validation_errors}")
    print("\nPlease enter human corrections for missing/invalid fields (press Enter to skip):")

    corrections: Dict[str, Any] = {}
    fields_to_prompt = list(dict.fromkeys(review.missing_fields + review.low_confidence_fields))
    if not fields_to_prompt:
        fields_to_prompt = ["po_number", "customer", "material", "quantity", "pickup_location", "delivery_location"]

    for field in fields_to_prompt:
        existing = review.original_extracted_values.get(field)
        val = input(f"  {field} (current: {existing}): ").strip()
        if val:
            if field == "quantity":
                try:
                    val = float(val) if "." in val else int(val)
                except ValueError:
                    pass
            corrections[field] = val

    if not corrections:
        print("No corrections provided. Keeping status HITL_PENDING.")
        return 2

    return handle_review_cli(doc_id, json.dumps(corrections))


def handle_status_cli() -> int:
    """Display clean summary dashboard of processed documents and pending HITL cases."""
    print_banner()
    from persistence.json_store import get_json_store
    store = get_json_store()
    results = store.list_results()
    reviews = store.list_reviews()

    print("=" * 76)
    print("  HAVI MCD FREIGHT AUTOMATION - STATUS & REVIEW DASHBOARD")
    print("=" * 76)

    # 1. Pending HITL Reviews
    pending_reviews = [r for r in reviews if r.status == "HITL_PENDING"]
    print(f"\n[!] PENDING HUMAN-IN-THE-LOOP (HITL) CASES ({len(pending_reviews)} pending):")
    if pending_reviews:
        print(f"  {'Doc ID':<16} | {'Source File':<20} | {'Raised By':<16} | Issues")
        print("  " + "-" * 72)
        for r in pending_reviews:
            issues = "; ".join(r.missing_fields + r.validation_errors)
            if len(issues) > 30:
                issues = issues[:27] + "..."
            print(f"  {r.document_id:<16} | {r.source_filename:<20} | {r.raised_by_agent:<16} | {issues}")
        print("\n  * To review a case interactively: python main.py --interactive-review <DOC_ID>")
        print("  * Review JSON files stored at:   output/reviews/<DOC_ID>_review.json")
    else:
        print("  None. All documents processed without review gates.")

    # 2. Processed Results History
    print(f"\n[*] PROCESSED DOCUMENTS HISTORY ({len(results)} total recorded):")
    if results:
        print(f"  {'Doc ID':<16} | {'Document Type':<16} | {'Status':<16} | {'CW1 Reference':<20}")
        print("  " + "-" * 72)
        for res in results[:10]:
            doc_id = res.get("document_id", "UNKNOWN")
            doc_type = res.get("document_type", "UNKNOWN")
            status = res.get("workflow_status", "UNKNOWN")
            mr = res.get("mock_result") or {}
            cw1_ref = mr.get("cw1_reference") or "-"
            print(f"  {doc_id:<16} | {doc_type:<16} | {status:<16} | {cw1_ref:<20}")
        if len(results) > 10:
            print(f"  ... and {len(results) - 10} older records in output/results/")
    else:
        print("  No documents processed yet.")

    print("\n" + "=" * 76 + "\n")
    return 0


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Local PDF to End-to-End MAF Freight Automation Runner"
    )
    parser.add_argument(
        "--file",
        type=str,
        help="Path to local document (PDF, PNG, JPG, JSON, TXT) to execute end-to-end",
    )
    parser.add_argument(
        "--review",
        type=str,
        help="Document ID of pending HITL review to resume",
    )
    parser.add_argument(
        "--corrections",
        type=str,
        help="JSON string with human field corrections e.g. '{\"po_number\": \"PO-998877\"}'",
    )
    parser.add_argument(
        "--interactive-review",
        type=str,
        help="Document ID to review interactively in the terminal",
    )
    parser.add_argument(
        "--check-ocr",
        action="store_true",
        help="Check local OCR provider installation and availability",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="View dashboard of processed documents and pending HITL cases",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Run the Azure AI Foundry Hosted Agent server on port 8088",
    )

    args = parser.parse_args()

    if args.status:
        sys.exit(handle_status_cli())

    if args.serve or len(sys.argv) == 1 or "PORT" in os.environ or "FOUNDRY_HOSTED_AGENT" in os.environ or "WEBSITES_PORT" in os.environ:
        port = int(os.environ.get("PORT", os.environ.get("AGENT_PORT", os.environ.get("WEBSITES_PORT", "8080"))))
        serve(port=port)
        sys.exit(0)

    if args.check_ocr:
        ocr_svc = get_ocr_service()
        avail, msg, details = ocr_svc.check_availability()
        print_banner()
        print(f"OCR Available: {avail}")
        print(f"Engine:        {details.get('engine') or 'None'}")
        print(f"Executable:    {details.get('executable') or 'Not Found'}")
        print(f"\nDiagnostic:\n{msg}")
        sys.exit(0 if avail else 1)

    if args.review and args.corrections:
        sys.exit(handle_review_cli(args.review, args.corrections))

    if args.interactive_review:
        sys.exit(handle_interactive_review(args.interactive_review))

    if args.file:
        sys.exit(process_file_e2e(args.file))

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()

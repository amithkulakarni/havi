# HAVI MCD - Autonomous Freight Multi-Agent Platform

## Overview
HAVI MCD Logistics Multi-Agent Automation platform built with **Microsoft Agent Framework (MAF 1.21.0)** and ready for deployment as an **Azure AI Foundry Hosted Agent**.

The platform automates the ingestion, classification, parsing, validation, and CargoWise 1 (CW1) synchronization of freight logistics documents (Purchase Orders, Order Updates, Carrier Confirmations, Rate Confirmations, and Scanned Bills of Lading).

---

## Architectural Highlights
- **Microsoft Agent Framework (MAF)**: In-process autonomous multi-agent orchestration.
  - `SupervisorAgent`: Classifies document type (`ORDER`, `ORDER_UPDATE`, `CARRIER_DOCUMENT`, `UNKNOWN`) and routes workflow.
  - `OrderExtractionAgent`: Extracts structured purchase order fields with confidence scores.
  - `CarrierDocumentExtractionAgent`: Extracts carrier confirmations and matches reference orders.
  - `CW1CreateAgent`: Enforces idempotency via SQLite hash deduplication and syncs orders to CargoWise 1.
  - `CW1UpdateAgent`: Applies amendments and carrier confirmations to existing CW1 shipments.
  - `HITLAgent`: Human-in-the-Loop review package management for low-confidence (<0.80) or scanned documents.
- **Dual Cloud Protocols**:
  - **Invocations Protocol** (`POST /invocations`): REST and Logic Apps / Power Automate integration.
  - **Responses Protocol** (`POST /responses`): Real-time streaming conversational agent protocol.
- **Enterprise Probes**:
  - `GET /health`, `GET /healthz`, `GET /readiness`, `GET /` for cloud orchestrator liveness and readiness monitoring.

---

## Directory Structure
```
havi-mcd/
├── main.py                     # Root entry point: Hosted Agent Server & CLI runner
├── hosted_server.py            # Azure AI Foundry Multi-Protocol Hosted Agent Host
├── config.py                   # Central environment settings & directories
├── requirements.txt            # Pinned runtime dependencies
├── deploy_foundry_agent.py     # Foundry agent registration script
├── adapters/                   # External system adapters (CargoWise 1 Mock, Notifications)
├── agents/                     # MAF Agents (Supervisor, Order, Carrier, CW1, HITL)
├── document_processing/        # PDF parser, image extraction, and local OCR service
├── orchestration/              # MAF routing graph, workflow manager, and pipelines
├── persistence/                # SQLite deduplication store and order database
├── schemas/                    # Pydantic models (ExtractedDocument, ProcessingResult, etc.)
├── validation/                 # Mandatory field validators & 0.80 confidence policy
├── test_data/                  # Sample logistics test documents
└── tests/                      # Pytest suite (44+ tests covering all components)
```

---

## Running Locally

### 1. Environment Setup
```bash
python -m venv venv
# Windows:
.\venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
```

### 2. Start the Hosted Agent Server (Development Mode)
```bash
python main.py
# Server starts on http://0.0.0.0:8080 with endpoints /invocations, /responses, /health
```

### 3. Run CLI Document Processing
```bash
python main.py --file test_data/sample_purchase_order.pdf
```

### 4. Run Verification Tests
```bash
python -m pytest tests/ -v
```

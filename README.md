# HAVI MCD Logistics Automation — Azure AI Foundry Hosted Agent

Production-ready autonomous logistics multi-agent system built with **Microsoft Agent Framework (MAF)** and configured for **Azure AI Foundry Hosted Agents** using `azd` (Azure Developer CLI) and source-code deployment (`python_3_13`).

---

## 1. System Architecture & Multi-Agent Orchestration

The platform orchestrates logistics workflow automation for purchase orders, carrier confirmations, rate confirmations, and amendments. It deploys as a **single Hosted Agent (`havi-deploy`)** running a centralized **Supervisor Agent** connected to **six specialized internal agents**:

```
                              +---------------------------------+
                              |   Azure AI Foundry Hosted Agent |
                              |           (havi-deploy)         |
                              +----------------+----------------+
                                               |
                                     [Invocations / Responses]
                                               v
                              +---------------------------------+
                              |         SupervisorAgent         |
                              |   (Classification & Dispatch)   |
                              +---+-------------+-------------+-+
                                  |             |             |
        NEW_ORDER / ORDER_UPDATE  |             |             | CARRIER_CONFIRMATION
                                  v             |             v
     +------------------------------+           |   +------------------------------------+
     |    OrderExtractionAgent      |           |   |  CarrierDocumentExtractionAgent    |
     |   - PO #, Customer, Dest     |           |   |  - Carrier Name, Tracking, Ref #   |
     |   - Confidence Scoring       |           |   |  - Order Reference Cross-Matching  |
     +--------------+---------------+           |   +-----------------+------------------+
                    |                           |                     |
                    v                           |                     v
     +------------------------------+           |   +------------------------------------+
     |      Validation Gate         |           |   |       Downstream CW1 Match         |
     | (Confidence >= 0.80 & Fields)|           |   +-----------------+------------------+
     +-------+--------------+-------+           |                     |
             |              |                   |                     |
   [Valid]   |              | [Missing/Low]     | [Ambiguous/Scanned] | [Matched Order]
             v              +---------------\   |   /-----------------+
+-----------------------+                    \  |  /
|     CW1CreateAgent    |                     v v v
| - SQLite Idempotency  |          +--------------------------+
| - Hash Deduplication  |          |        HITLAgent         |
| - CargoWise 1 Sync    |          | - Human Review Packages  |
+-----------------------+          | - Notification Alerts    |
            |                      +--------------------------+
            v
+-----------------------+
|     CW1UpdateAgent    |
| - Amendment Tracking  |
| - Status Synchronize  |
+-----------------------+
```

### Agent Roles

| Agent | Responsibility | Invocable Trigger / Condition |
| :--- | :--- | :--- |
| **SupervisorAgent** | Analyzes document content, determines document category, assigns routing intent. | Inbound payload via `/invocations` or `/responses`. |
| **OrderExtractionAgent** | Extracts structured PO fields (`po_number`, `customer`, `origin`, `destination`, `total_weight`, `pieces`, `cargo_ready_date`) with per-field confidence. | Inbound routing tag `ORDER` or `ORDER_UPDATE`. |
| **CarrierDocumentExtractionAgent** | Extracts carrier confirmations, BOLs, driver references, and matches against existing orders. | Inbound routing tag `CARRIER_CONFIRMATION`. |
| **CW1CreateAgent** | Calculates SHA-256 idempotency hash, verifies SQLite ledger, and provisions new CW1 shipment. | Validated order with all mandatory fields and confidence $\ge 0.80$. |
| **CW1UpdateAgent** | Resolves existing shipment via reference number and applies carrier/date/weight amendments. | Validated update payload matching an existing order. |
| **HITLAgent** | Generates human-in-the-loop review bundles, logs review files, and alerts operations. | Missing mandatory fields, confidence $< 0.80$, scanned unreadable document, or ambiguous intent. |

---

## 2. Supervisor Routing Matrix

| Inbound Classification | Validation State | Confidence | Routed Target Agent | Action Taken |
| :--- | :--- | :--- | :--- | :--- |
| `NEW_ORDER` | Complete (all fields present) | $\ge 0.80$ | `CW1CreateAgent` | Creates new shipment in CW1 with deduplication. |
| `NEW_ORDER` | Missing mandatory field(s) | Any | `HITLAgent` | Escalates to human review with missing field metadata. |
| `NEW_ORDER` | Complete | $< 0.80$ | `HITLAgent` | Escalates to human review due to low extraction confidence. |
| `ORDER_UPDATE` | Complete & references existing PO | $\ge 0.80$ | `CW1UpdateAgent` | Updates existing shipment in CW1. |
| `CARRIER_CONFIRMATION` | Carrier document parsed & matched | $\ge 0.80$ | `CW1UpdateAgent` | Attaches tracking/carrier details to shipment. |
| `CARRIER_CONFIRMATION` | Unmatched reference / missing data | Any | `HITLAgent` | Routes to manual verification. |
| `UNKNOWN` / Ambiguous | N/A | $< 0.80$ | `HITLAgent` | Generates review package for logistics supervisor. |
| Scanned / Unreadable | OCR confidence failed | $< 0.60$ | `HITLAgent` | Flagged for manual document entry. |

---

## 3. Azure Target Configuration

| Parameter | Configuration Value |
| :--- | :--- |
| **Subscription Name** | `az-rapidbuildfacto-pjnp-01nl168041` |
| **Subscription ID** | `eb12d4bb-8acb-4b2e-bf4d-6ac85e635cce` |
| **Resource Group** | `rg-rbf-stride-Havi` |
| **Azure AI Foundry Account** | `havi-mcd-automation-resource` |
| **Foundry Project Name** | `havi-mcd-automation` |
| **Region** | `eastus2` |
| **AI Model Deployment** | `gpt-4o` |
| **Project Endpoint** | `https://havi-mcd-automation-resource.services.ai.azure.com/api/projects/havi-mcd-automation` |
| **Project Resource ID** | `/subscriptions/eb12d4bb-8acb-4b2e-bf4d-6ac85e635cce/resourceGroups/rg-rbf-stride-Havi/providers/Microsoft.CognitiveServices/accounts/havi-mcd-automation-resource/projects/havi-mcd-automation` |
| **Hosted Agent Name** | `havi-deploy` |
| **Runtime** | `python_3_13` |
| **Entry Point** | `main.py` |
| **Dependency Resolution** | `remote_build` |

---

## 4. Production Runtime & Read-Only Filesystem Safety

Azure AI Foundry Hosted Agents execute inside a security-hardened container sandbox where the application root (`/app`) is **read-only**.

### Centralized Runtime Path Architecture (`runtime_paths.py`)

The platform implements an automated hierarchical path resolver:
1. **Local Development**: Writes to `./output`, `./temp`, `./uploads`, and `./data` inside the workspace root.
2. **Container / Hosted Agent**: Automatically detects container environments (`/app` present or `AZURE_CONTAINER_SERVICE_NAME` set) and routes all writes to `/tmp/havi_mcd/`.
3. **Environment Override**: Honored via `RUNTIME_ROOT=/custom/path`.
4. **Active Read-Only Probe & Automatic Fallback**: If writing to the designated root fails with `OSError(30, 'Read-only file system')`, the engine automatically re-routes dynamically to `tempfile.gettempdir()/havi_mcd`.

Directories managed dynamically:
- SQLite Idempotency DB: `{RUNTIME_ROOT}/data/order_idempotency.db`
- Durable Results: `{RUNTIME_ROOT}/output/results/`
- HITL Reviews: `{RUNTIME_ROOT}/output/reviews/`
- Notification Dispatches: `{RUNTIME_ROOT}/output/notifications/`
- OCR Temp Cache: `{RUNTIME_ROOT}/temp/`

---

## 5. Security, Identity & RBAC Matrix

In accordance with enterprise zero-trust principles:
- **No hardcoded credentials, API keys, or tenant IDs** exist in the codebase.
- Local deployment operators authenticate via **Azure CLI (`DefaultAzureCredential`)**.
- Cloud Hosted Agent authenticates to Azure OpenAI / Foundry Services via **System-Assigned or User-Assigned Managed Identity**.

### Required Azure RBAC Permissions

The Azure Administrator must grant the following role assignments:

| Identity | Target Resource / Scope | Role | Purpose |
| :--- | :--- | :--- | :--- |
| **Deployment Operator** (User / Service Principal) | Resource Group `rg-rbf-stride-Havi` or Project ID | **Azure AI Developer** or **Contributor** | Required to register and package Hosted Agents via `azd` or Azure CLI. |
| **Deployment Operator** | Storage Account (foundry storage) | **Storage Blob Data Contributor** | Uploads deployment ZIP archive during remote build. |
| **Hosted Agent Identity** (Managed Identity of `havi-deploy`) | Cognitive Services Account `havi-mcd-automation-resource` | **Cognitive Services OpenAI User** | Permits calling model deployments (`gpt-4o`) via TokenCredential without API keys. |
| **Hosted Agent Identity** | Project `havi-mcd-automation` | **Azure AI Project Contributor** | Allows recording telemetry, spans, and invocation lifecycle events. |

---

## 6. Azure Cloud Shell Deployment Guide

Execute the following exact commands in **Azure Cloud Shell** (Bash mode):

### Step 1: Set Active Azure Subscription
```bash
az account set --subscription "eb12d4bb-8acb-4b2e-bf4d-6ac85e635cce"
az account show --query "{name:name, id:id, tenantId:tenantId}" -o table
```

### Step 2: Clone or Upload the Repository
```bash
# Clone the repository
git clone https://github.com/dharm962890-a11y/havi-mcd-automation.git
cd havi-mcd-automation
```

### Step 3: Deploy via Azure Developer CLI (`azd`)
The repository is pre-configured with `azure.yaml` targeting the existing Foundry resource:

```bash
# Verify azd is installed (standard in Azure Cloud Shell)
azd version

# Initialize agent binding to existing Foundry Project (non-interactive, preserving config)
azd ai agent init \
  --no-prompt \
  --project-id "/subscriptions/eb12d4bb-8acb-4b2e-bf4d-6ac85e635cce/resourceGroups/rg-rbf-stride-Havi/providers/Microsoft.CognitiveServices/accounts/havi-mcd-automation-resource/projects/havi-mcd-automation" \
  --deploy-mode code \
  --runtime python_3_13 \
  --entry-point main.py

# Deploy to Azure AI Foundry Hosted Agent
azd deploy
```

### Alternative Step 3: Deploy via Python SDK / ZIP Direct
If deploying using the verified source ZIP package:

```bash
python deploy_foundry_agent.py
```

---

## 7. Invocation & Monitoring Verification

### 1. Invocations Protocol (`POST /invocations`)
Standard enterprise REST endpoint used by Azure Logic Apps, Power Automate, and Foundry Playground:

```bash
curl -X POST "https://havi-mcd-automation-resource.services.ai.azure.com/api/projects/havi-mcd-automation/agents/havi-deploy/invocations" \
  -H "Authorization: Bearer $(az account get-access-token --resource https://cognitiveservices.azure.com/.default --query accessToken -o tsv)" \
  -H "Content-Type: application/json" \
  -H "x-correlation-id: CORR-HAVI-TEST-001" \
  -d '{
    "messages": [
      {
        "role": "user",
        "content": "{\"po_number\": \"PO-MCD-98231\", \"customer\": \"McDonalds Corp\", \"origin\": \"Chicago, IL\", \"destination\": \"Oak Brook, IL\", \"cargo_ready_date\": \"2026-11-15\", \"pieces\": 450, \"total_weight\": 12500}"
      }
    ]
  }'
```

### 2. Streaming Responses Protocol (`POST /responses`)
Real-time Server-Sent Events (SSE) streaming protocol:

```bash
curl -N -X POST "https://havi-mcd-automation-resource.services.ai.azure.com/api/projects/havi-mcd-automation/agents/havi-deploy/responses" \
  -H "Authorization: Bearer $(az account get-access-token --resource https://cognitiveservices.azure.com/.default --query accessToken -o tsv)" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {
        "role": "user",
        "content": "Process carrier confirmation for reference PO-MCD-98231 with carrier Swift Transport, tracking SW-8841."
      }
    ]
  }'
```

### 3. Health & Liveness Probes
Hosted agent health and runtime status:

```bash
# Liveness probe
curl -s "http://localhost:8080/health"
# {"status":"HEALTHY","service":"havi-mcd-hosted-agent","version":"1.1.0","runtime_root":"/tmp/havi_mcd"}

# Deep system status
curl -s "http://localhost:8080/status"
```

---

## 8. Verification & Test Summary

The repository passes comprehensive automated test suites and production validation:

```
============================= 61 passed in 25.23s =============================
Platform: Windows / Python 3.13.7
Test Suites Executed:
  - tests/test_cw1_mock_adapter.py (3 passed)
  - tests/test_duplicate_and_idempotency.py (3 passed)
  - tests/test_end_to_end_workflow.py (4 passed)
  - tests/test_extraction_agents.py (3 passed)
  - tests/test_hosted_server.py (7 passed)
  - tests/test_model_configuration.py (6 passed)
  - tests/test_monitoring_and_tools.py (7 passed)
  - tests/test_pdf_processing.py (10 passed)
  - tests/test_runtime_paths.py (7 passed)
  - tests/test_supervisor_routing.py (6 passed)
  - tests/test_validation_and_hitl.py (4 passed)
```

Production Validation Gates (`python validate_production.py`):
- `[PASS]` [1/6] Hosted Agent Startup & Export Validation
- `[PASS]` [2/6] Centralized Runtime Paths Validation
- `[PASS]` [3/6] Health & Probes Endpoints Validation (`/health`, `/healthz`, `/readiness`, `/status`)
- `[PASS]` [4/6] Invocations Protocol Processing
- `[PASS]` [5/6] OCR Workflow & Temporary File Handling
- `[PASS]` [6/6] Durable Output Generation & Result Persistence

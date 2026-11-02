# Azure AI Foundry Hosted Agent Deployment Guide

This guide details how to deploy the **HAVI MCD Multi-Agent Framework (MAF)** application to **Azure AI Foundry Hosted Agents** using the pre-packaged source archive `havi-mcd-hosted-agent.zip`.

---

## 1. Prerequisites
- **Azure AI Foundry Project Endpoint**: Format:
  `https://<resource-name>.services.ai.azure.com/api/projects/<project-name>`
- **Deployment Archive**: `havi-mcd-hosted-agent.zip` (verified at archive root with `main.py` and `requirements.txt`).
- **Azure Role**: `Contributor` or `Azure AI Developer` on the target Resource Group (`rg-rbf-elevate`).
- **Model Deployment**: Model deployed in Azure OpenAI / Foundry (e.g., `gpt-4o` or `gpt-4.1-mini`).

---

## 2. Environment Variables Specification
Configure the following application settings in Azure AI Foundry / Azure App Configuration:

| Environment Variable | Description | Example / Default |
|---|---|---|
| `FOUNDRY_PROJECT_ENDPOINT` | Azure AI Foundry Project URL | `https://havi-mcd-automation-poc.services.ai.azure.com/api/projects/havi-mcd-automation` |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | Deployed model name | `gpt-4o` / `gpt-4.1-mini` |
| `AZURE_AUTH_METHOD` | Identity mode (`entra_id` / `service_principal` / `api_key`) | `entra_id` (uses Managed Identity / OIDC) |
| `CONFIDENCE_THRESHOLD` | Threshold for mandatory field HITL routing | `0.80` |
| `SCANNED_CHAR_THRESHOLD` | Min character count to avoid OCR / HITL flag | `20` |
| `ENABLE_OCR_FALLBACK` | Allow optical character recognition | `true` |
| `PORT` | Container binding port | `8080` (automatically set by container runtime) |

---

## 3. Upload & Deployment Steps

### Option A: Azure AI Foundry Portal (UI)
1. Navigate to **Azure AI Foundry Portal** ([ai.azure.com](https://ai.azure.com)).
2. Select your Project: `havi-mcd-automation` under Subscription `az-rapidbuildfacto-pjnp-01nl168041`.
3. Go to **Build** -> **Agents** -> **New Agent** -> Select **Hosted Agent**.
4. Choose **Upload Source Code (ZIP)**.
5. Select `havi-mcd-hosted-agent.zip`.
6. Set the entry point:
   - Module: `main` (points to `main.py` and `main:app`)
   - Startup command: `python main.py` or default container command.
7. Configure the Environment Variables listed in Section 2 above.
8. Click **Deploy**.

### Option B: Automated CI/CD (GitHub Actions + OIDC)
The repository includes an automated GitHub Actions pipeline in `.github/workflows/deploy.yml`:
1. Push code to the `main` branch.
2. The workflow logs in to Azure using OpenID Connect (OIDC) through User-Assigned Managed Identity `hav-mcd-github-deployment`.
3. Executes the full 44-test verification suite.
4. Registers and verifies the agent via `python deploy_foundry_agent.py`.

---

## 4. Verification and Health Probes

### Health & Readiness Endpoints
Once running, the container exposes the following monitoring probes:
- `GET /health` -> `{"status": "UP", "service": "havi-mcd-hosted-agent"}`
- `GET /healthz` -> `{"status": "UP", "service": "havi-mcd-hosted-agent"}`
- `GET /readiness` -> `{"status": "UP", "service": "havi-mcd-hosted-agent"}`
- `GET /` -> Returns service discovery JSON and available protocols.

### Invocations Protocol Testing (`POST /invocations`)
Send a test HTTP request via cURL or Postman:
```bash
curl -X POST https://<hosted-agent-endpoint>/invocations \
  -H "Content-Type: application/json" \
  -d '{
    "document_id": "TEST-PO-001",
    "extracted_text": "HAVI LOGISTICS - PURCHASE ORDER\nPO Number: PO-987654\nCustomer Name: HAVI MCD\nMaterial: Beef Patties\nQuantity: 500\nPickup Location: Des Plaines DC\nDelivery Location: Chicago DC"
  }'
```
Expected response: HTTP 200 with `document_type: "ORDER"`, `route_to: "CW1CreateAgent"`, `workflow_status: "MOCK_CREATED"`.

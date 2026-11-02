"""
deploy_foundry_agent.py
=======================
Connects to the Azure AI Foundry Project using azure-ai-projects SDK,
inspects existing agent definitions to avoid duplication, and prepares
the custom MAF agent registration for Azure AI Foundry Hosted Agents.

USAGE:
    python deploy_foundry_agent.py

PREREQUISITES:
    - FOUNDRY_PROJECT_ENDPOINT in .env
    - AZURE_AUTH_METHOD (service_principal or entra_id or DefaultAzureCredential)
    - AZURE_AI_MODEL_DEPLOYMENT_NAME (e.g. gpt-4o)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from azure.ai.projects import AIProjectClient
from azure.identity import ClientSecretCredential, DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("havi.deploy")

AGENT_NAME = "havi-mcd-supervisor-agent"
MODEL_DEPLOYMENT = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4o")
FOUNDRY_ENDPOINT = (
    os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    or os.environ.get("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
)


def _build_credential():
    """Build Entra ID credential for Azure AI Foundry Project access."""
    tenant = (os.environ.get("AZURE_TENANT_ID") or "").strip()
    client_id = (os.environ.get("AZURE_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("AZURE_CLIENT_SECRET") or "").strip()

    if tenant and client_id and client_secret:
        logger.info(
            "Foundry Project Auth: Service Principal (tenant=%s client=%s)",
            tenant,
            client_id[:8] + "...",
        )
        return ClientSecretCredential(
            tenant_id=tenant,
            client_id=client_id,
            client_secret=client_secret,
        )

    # Clean out empty string environment variables to prevent DefaultAzureCredential from failing
    for k in ["AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_TENANT_ID"]:
        if os.environ.get(k) == "":
            del os.environ[k]

    if client_id and not client_secret:
        logger.info("Foundry Project Auth: DefaultAzureCredential (User-Assigned Managed Identity client_id=%s)", client_id)
        return DefaultAzureCredential(managed_identity_client_id=client_id)

    logger.info("Foundry Project Auth: DefaultAzureCredential (az cli / System-Assigned Managed Identity)")
    return DefaultAzureCredential()


def verify_foundry_project():
    """
    Connect to Azure AI Foundry Project and inspect existing agents.
    Returns existing agent details if present.
    """
    if not FOUNDRY_ENDPOINT or not FOUNDRY_ENDPOINT.strip():
        raise ValueError(
            "FOUNDRY_PROJECT_ENDPOINT is not set in .env.\n"
            "An Azure AI Foundry project endpoint is required for Hosted Agent deployment.\n"
            "Format: https://<resource>.services.ai.azure.com/api/projects/<project-name>"
        )

    credential = _build_credential()
    clean_endpoint = FOUNDRY_ENDPOINT.strip()
    logger.info("Connecting to Azure AI Foundry Project: %s", clean_endpoint)

    client = AIProjectClient(
        endpoint=clean_endpoint,
        credential=credential,
    )

    logger.info("Listing existing agents in the Foundry project...")
    matched_agent = None
    all_agents = []

    for agent in client.agents.list():
        all_agents.append({"id": agent.id, "name": agent.name, "model": agent.model if hasattr(agent, "model") else None})
        logger.info("  Found agent in Foundry: id=%s name=%s", agent.id, agent.name)
        if agent.name == AGENT_NAME:
            matched_agent = agent

    if matched_agent:
        logger.info("[OK] Matched existing Foundry Agent definition: %s", matched_agent.id)
    else:
        logger.info("No pre-existing agent definition matching '%s' found in project.", AGENT_NAME)

    return {
        "status": "VERIFIED",
        "foundry_endpoint": clean_endpoint,
        "model_deployment": MODEL_DEPLOYMENT,
        "matched_agent_id": matched_agent.id if matched_agent else None,
        "total_existing_agents": len(all_agents),
    }


if __name__ == "__main__":
    try:
        res = verify_foundry_project()
        print("\n" + "=" * 70)
        print("  AZURE AI FOUNDRY PROJECT VERIFICATION SUCCESSFUL")
        print("=" * 70)
        print(json.dumps(res, indent=2))
        print("=" * 70 + "\n")
    except Exception as exc:
        logger.error("[FOUNDRY_VERIFY_BLOCKED] %s", exc)
        sys.exit(1)

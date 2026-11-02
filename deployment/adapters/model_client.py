"""
Shared Model Client Adapter for Microsoft Agent Framework (MAF).

Supports two distinct authentication modes:
  1. API-Key Mode (Default/Local): Uses OpenAIChatCompletionClient directly with
     AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, and AZURE_OPENAI_API_VERSION.
     Does NOT create any Azure Credential objects or perform Entra ID / az login checks.
  2. Service Principal / Entra ID Mode: Uses ClientSecretCredential or DefaultAzureCredential
     with Azure AI Foundry project endpoint or direct endpoint.
  3. Safe Offline Fallback: Falls back to offline heuristic mode when keys/endpoints are absent.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from agent_framework.openai import OpenAIChatCompletionClient

from config import Settings, get_settings

logger = logging.getLogger("havi.adapters.model_client")


def _normalize_endpoint(endpoint: Optional[str]) -> str:
    """Normalize Azure AI / Foundry / OpenAI endpoint by removing trailing API paths like /responses or /chat/completions."""
    if not endpoint:
        return ""
    ep = endpoint.strip().rstrip("/")
    if ep.endswith("/responses"):
        ep = ep[:-len("/responses")].rstrip("/")
    if ep.endswith("/chat/completions"):
        ep = ep[:-len("/chat/completions")].rstrip("/")
    return ep


class ModelClientManager:
    """Manages shared MAF model client lifecycle, credentials, and endpoints."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self._client: Optional[OpenAIChatCompletionClient] = None
        self._init_attempted = False

    def reset(self) -> None:
        """Reset cached client to allow re-initialization (e.g. for testing)."""
        self._client = None
        self._init_attempted = False

    def get_auth_method(self) -> str:
        """Return configured authentication method ('api_key', 'service_principal', or 'entra_id')."""
        method = os.getenv("AZURE_AUTH_METHOD") or self.settings.azure_auth_method or "api_key"
        return method.strip().lower()

    def is_configured(self) -> bool:
        """Check whether Azure model endpoint is configured for the active auth method."""
        auth_method = self.get_auth_method()
        if auth_method == "api_key":
            endpoint = os.getenv("AZURE_OPENAI_ENDPOINT") or self.settings.azure_openai_endpoint
            key = os.getenv("AZURE_OPENAI_API_KEY") or self.settings.azure_openai_api_key
            return bool(endpoint and key and "your-" not in endpoint and "<" not in endpoint)
        else:
            foundry_ep = (
                os.getenv("FOUNDRY_PROJECT_ENDPOINT")
                or os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
                or self.settings.foundry_project_endpoint
                or self.settings.azure_ai_foundry_project_endpoint
            )
            direct_ep = os.getenv("AZURE_OPENAI_ENDPOINT") or self.settings.azure_openai_endpoint
            ep = foundry_ep or direct_ep
            return bool(ep and "your-" not in ep and "<" not in ep)

    def get_client(self) -> Optional[OpenAIChatCompletionClient]:
        """
        Retrieve shared MAF OpenAIChatCompletionClient instance.

        Guarantees that when in api_key mode:
          - Initializes with model, azure_endpoint, api_version, and api_key.
          - NO Azure Credential objects (DefaultAzureCredential/ClientSecretCredential) are created.
        """
        if self._client is not None:
            return self._client

        if self._init_attempted:
            return None

        self._init_attempted = True

        auth_method = self.get_auth_method()
        deployment_name = (
            os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME")
            or self.settings.azure_ai_model_deployment_name
            or "gpt-4.1-mini"
        )
        api_version = (
            os.getenv("AZURE_OPENAI_API_VERSION")
            or self.settings.azure_openai_api_version
            or "2024-08-01-preview"
        )

        try:
            # ==============================================================
            # BRANCH 1: API-Key Authentication Mode (Zero Azure Credentials)
            # ==============================================================
            if auth_method == "api_key":
                endpoint = os.getenv("AZURE_OPENAI_ENDPOINT") or self.settings.azure_openai_endpoint
                api_key = os.getenv("AZURE_OPENAI_API_KEY") or self.settings.azure_openai_api_key

                if not endpoint or "your-" in endpoint or "<" in endpoint:
                    logger.warning("[MODEL_CLIENT_NOTICE] AZURE_OPENAI_ENDPOINT not set for API-key mode. Falling back to offline mode.")
                    return None

                if not api_key or "your-" in api_key or "<" in api_key:
                    logger.warning("[MODEL_CLIENT_NOTICE] AZURE_OPENAI_API_KEY not set. Falling back to offline mode.")
                    return None

                # Determine endpoint mode: Azure AI Foundry v1 base_url vs direct Azure OpenAI azure_endpoint
                raw_ep = _normalize_endpoint(endpoint)
                is_v1_endpoint = raw_ep.endswith("/openai/v1") or raw_ep.endswith("/v1")

                # CRITICAL SECURITY RULE: NEVER PRINT OR LOG THE API KEY!
                logger.info(
                    "[MODEL_CLIENT_INIT] Initializing MAF OpenAIChatCompletionClient in API-Key mode: "
                    "endpoint='%s', is_v1=%s, deployment='%s', api_version='%s', key_configured=True, key_length=%d chars.",
                    raw_ep,
                    is_v1_endpoint,
                    deployment_name,
                    api_version,
                    len(api_key),
                )

                # Initialize directly with api_key; DO NOT pass credential parameter
                if is_v1_endpoint:
                    self._client = OpenAIChatCompletionClient(
                        model=deployment_name,
                        base_url=raw_ep,
                        api_key=api_key,
                    )
                else:
                    clean_endpoint = raw_ep.rstrip("/")
                    self._client = OpenAIChatCompletionClient(
                        model=deployment_name,
                        azure_endpoint=clean_endpoint,
                        api_version=api_version,
                        api_key=api_key,
                    )
                logger.info("[MODEL_CLIENT_READY] Shared MAF API-key model client initialized successfully (No Azure credential created).")
                return self._client

            # ==============================================================
            # BRANCH 2: Service Principal / Entra ID / Managed Identity Mode
            # ==============================================================
            elif auth_method in ("service_principal", "entra_id", "managed_identity", "system_assigned", "user_assigned"):
                from azure.identity import ClientSecretCredential, DefaultAzureCredential

                tenant_id = (os.getenv("AZURE_TENANT_ID") or self.settings.azure_tenant_id or "").strip()
                client_id = (os.getenv("AZURE_CLIENT_ID") or self.settings.azure_client_id or "").strip()
                client_secret = (os.getenv("AZURE_CLIENT_SECRET") or self.settings.azure_client_secret or "").strip()

                if auth_method == "service_principal" or (tenant_id and client_id and client_secret and auth_method != "managed_identity"):
                    logger.info(
                        "[MODEL_CLIENT_AUTH] Using Automated Headless Service Principal (tenant: %s, client_id: %s, secret_configured=True)",
                        tenant_id,
                        client_id,
                    )
                    credential = ClientSecretCredential(
                        tenant_id=tenant_id,
                        client_id=client_id,
                        client_secret=client_secret,
                    )
                elif client_id:
                    logger.info("[MODEL_CLIENT_AUTH] Using DefaultAzureCredential with User-Assigned Managed Identity (client_id: %s)", client_id)
                    credential = DefaultAzureCredential(managed_identity_client_id=client_id)
                else:
                    logger.info("[MODEL_CLIENT_AUTH] Using DefaultAzureCredential (System-Assigned Managed Identity / Environment)")
                    credential = DefaultAzureCredential()

                foundry_endpoint = (
                    os.getenv("FOUNDRY_PROJECT_ENDPOINT")
                    or os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
                    or self.settings.foundry_project_endpoint
                    or self.settings.azure_ai_foundry_project_endpoint
                )
                direct_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT") or self.settings.azure_openai_endpoint

                if foundry_endpoint and "your-" not in foundry_endpoint:
                    from azure.ai.projects.aio import AIProjectClient as AsyncAIProjectClient

                    logger.info(
                        "[MODEL_CLIENT_INIT] Initializing MAF client via Azure AI Foundry project endpoint: '%s' (deployment: '%s')",
                        foundry_endpoint,
                        deployment_name,
                    )
                    async_project_client = AsyncAIProjectClient(
                        endpoint=foundry_endpoint,
                        credential=credential,
                    )
                    async_openai = async_project_client.get_openai_client()

                    self._client = OpenAIChatCompletionClient(
                        model=deployment_name,
                        async_client=async_openai,
                    )
                    logger.info("[MODEL_CLIENT_READY] MAF Foundry model client initialized successfully.")
                    return self._client

                elif direct_endpoint and "your-" not in direct_endpoint:
                    self._client = OpenAIChatCompletionClient(
                        model=deployment_name,
                        azure_endpoint=direct_endpoint,
                        api_version=api_version,
                        credential=credential,
                    )
                    logger.info("[MODEL_CLIENT_READY] MAF direct Entra ID model client initialized successfully.")
                    return self._client

            logger.warning("[MODEL_CLIENT_NOTICE] Unsupported auth method or missing configuration. Operating in offline mode.")
            return None

        except Exception as exc:
            logger.warning(
                "[MODEL_CLIENT_NOTICE] Could not initialize cloud model client (%s). Operating in offline mode.",
                exc,
            )
            return None

    async def complete_prompt(self, prompt: str) -> Optional[str]:
        """
        Execute an asynchronous completion against the shared model client.

        Returns:
            Generated text string, or None if client unavailable or invocation fails.
        """
        client = self.get_client()
        if not client:
            return None

        try:
            response = await client.get_response(prompt)
            if response and hasattr(response, "text"):
                return str(response.text)
        except Exception as exc:
            logger.warning("[MODEL_CALL_NOTICE] Model call failed (%s). Falling back to local heuristic.", exc)

        return None

    def complete_sync(self, prompt: str, system_prompt: Optional[str] = None) -> Optional[str]:
        """Execute synchronous completion against Azure OpenAI model deployment."""
        if not self.is_configured():
            return None
        try:
            endpoint = os.getenv("AZURE_OPENAI_ENDPOINT") or self.settings.azure_openai_endpoint
            key = os.getenv("AZURE_OPENAI_API_KEY") or self.settings.azure_openai_api_key
            api_version = os.getenv("AZURE_OPENAI_API_VERSION") or self.settings.azure_openai_api_version
            deployment = (
                os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME")
                or self.settings.azure_ai_model_deployment_name
                or "gpt-4.1-mini"
            )
            raw_ep = _normalize_endpoint(endpoint)
            is_v1 = raw_ep.endswith("/openai/v1") or raw_ep.endswith("/v1")

            if is_v1:
                from openai import OpenAI
                client = OpenAI(base_url=raw_ep, api_key=key, timeout=25.0)
            else:
                from openai import AzureOpenAI
                client = AzureOpenAI(azure_endpoint=raw_ep.rstrip("/"), api_key=key, api_version=api_version, timeout=25.0)
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            resp = client.chat.completions.create(
                model=deployment,
                messages=messages,
                temperature=0.0,
                max_tokens=1500,
            )
            return resp.choices[0].message.content or ""
        except Exception as exc:
            logger.warning("[MODEL_SYNC_NOTICE] Synchronous model call failed (%s). Falling back to heuristic.", exc)
            return None

    def complete_vision_sync(self, image_bytes: bytes, prompt: str) -> Optional[str]:
        """Execute multimodal vision completion on raw image bytes."""
        if not self.is_configured():
            return None
        try:
            import base64
            from openai import AzureOpenAI
            endpoint = os.getenv("AZURE_OPENAI_ENDPOINT") or self.settings.azure_openai_endpoint
            key = os.getenv("AZURE_OPENAI_API_KEY") or self.settings.azure_openai_api_key
            api_version = os.getenv("AZURE_OPENAI_API_VERSION") or self.settings.azure_openai_api_version
            deployment = (
                os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME")
                or self.settings.azure_ai_model_deployment_name
                or "gpt-4.1-mini"
            )

            raw_ep = _normalize_endpoint(endpoint)
            is_v1 = raw_ep.endswith("/openai/v1") or raw_ep.endswith("/v1")

            if is_v1:
                from openai import OpenAI
                client = OpenAI(base_url=raw_ep, api_key=key, timeout=30.0)
            else:
                from openai import AzureOpenAI
                client = AzureOpenAI(azure_endpoint=raw_ep, api_key=key, api_version=api_version, timeout=30.0)

            b64_img = base64.b64encode(image_bytes).decode("utf-8")

            resp = client.chat.completions.create(
                model=deployment,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_img}"}},
                        ],
                    }
                ],
                max_tokens=2000,
            )
            return resp.choices[0].message.content or ""
        except Exception as exc:
            logger.warning("[MODEL_VISION_NOTICE] Vision model call failed (%s). Falling back to heuristic.", exc)
            return None


_model_client_mgr: Optional[ModelClientManager] = None


def get_model_client_manager() -> ModelClientManager:
    """Singleton getter for ModelClientManager."""
    global _model_client_mgr
    if _model_client_mgr is None:
        _model_client_mgr = ModelClientManager()
    return _model_client_mgr

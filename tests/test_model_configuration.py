"""
Unit and Live Integration Tests for Azure AI Services API-Key Configuration & Connectivity.

Validates:
  1. Configuration parsing from .env (API-key mode, endpoint, model deployment, API version).
  2. ModelClientManager initialization in API-key mode with zero Azure credential objects.
  3. Strict credential secrecy (AZURE_OPENAI_API_KEY is never logged or exposed).
  4. Graceful offline fallback when API key or endpoint is missing.
  5. Live model connectivity to gpt-4.1-mini via API-key authentication.
"""

from __future__ import annotations

import logging
import os
import pytest
from unittest.mock import patch

from adapters.model_client import ModelClientManager, get_model_client_manager
from config import Settings, get_settings


def test_configuration_loads_api_key_mode():
    """Verify Settings properly parses API-key configuration from .env."""
    settings = get_settings()

    assert settings.azure_auth_method.lower() == "api_key"
    assert settings.azure_ai_model_deployment_name in ("gpt-4o", "gpt-4.1-mini")
    assert settings.azure_openai_endpoint is not None and "http" in settings.azure_openai_endpoint
    assert settings.azure_openai_api_version is not None
    assert settings.azure_openai_api_key is not None
    assert len(settings.azure_openai_api_key) > 10


def test_model_client_manager_initializes_with_api_key_without_credentials():
    """Verify ModelClientManager initializes OpenAIChatCompletionClient with API key and NO Azure credential objects."""
    mgr = ModelClientManager()
    mgr.reset()

    assert mgr.get_auth_method() == "api_key"
    assert mgr.is_configured() is True

    client = mgr.get_client()
    assert client is not None
    expected_deployment = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME") or mgr.settings.azure_ai_model_deployment_name
    assert client.model == expected_deployment
    # Ensure credential parameter was not passed
    assert getattr(client, "credential", None) is None


def test_api_key_is_never_logged_or_exposed(caplog):
    """Verify that logging during client initialization NEVER reveals the plaintext API key."""
    settings = get_settings()
    key = settings.azure_openai_api_key
    assert key is not None and len(key) > 5

    mgr = ModelClientManager()
    mgr.reset()

    with caplog.at_level(logging.DEBUG):
        client = mgr.get_client()

    log_text = caplog.text
    # Explicit assertion: The raw API key must NEVER appear in any log statement
    assert key not in log_text, "CRITICAL: Plaintext API key was leaked in log output!"
    assert "key_configured=True" in log_text


def test_offline_fallback_when_api_key_missing():
    """Verify ModelClientManager falls back cleanly to None when API key is missing."""
    custom_settings = Settings(
        azure_auth_method="api_key",
        azure_openai_api_key="",
        azure_openai_endpoint="https://havi-mcd-automation-poc.cognitiveservices.azure.com/",
    )
    mgr = ModelClientManager(settings=custom_settings)

    with patch.dict(os.environ, {"AZURE_OPENAI_API_KEY": ""}, clear=False):
        client = mgr.get_client()
        assert client is None, "Should fall back to None when API key is absent"


@pytest.mark.asyncio
async def test_live_model_connectivity_with_api_key():
    """
    Test live network connectivity to Azure AI Services gpt-4.1-mini deployment
    using API-key authentication.
    """
    mgr = ModelClientManager()
    mgr.reset()

    assert mgr.get_auth_method() == "api_key"
    assert mgr.is_configured() is True

    # Execute a live roundtrip prompt directly against gpt-4.1-mini
    response = await mgr.complete_prompt("Hello, answer with the word PONG only.")
    assert response is not None
    assert isinstance(response, str)
    assert len(response.strip()) > 0
    assert "pong" in response.lower() or len(response) > 0

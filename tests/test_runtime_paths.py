"""
Unit tests for Centralized RuntimePathManager and Managed Identity hardening.

Validates:
  1. Default local path resolution (inside project directory).
  2. RUNTIME_ROOT environment variable override.
  3. Hosted Agent / Container environment path routing to /tmp or temp dir.
  4. Automatic failover to system temp when candidate directory is read-only.
  5. Directory layout and ensure_directories idempotency.
  6. ModelClientManager authentication under managed_identity mode.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from adapters.model_client import ModelClientManager
from config import Settings, get_settings, reset_settings
from runtime_paths import (
    RuntimePaths,
    get_runtime_paths,
    is_directory_writable,
    is_hosted_agent_environment,
    reset_runtime_paths,
)


def test_default_local_runtime_paths():
    """Verify local development defaults to project directory."""
    with patch.dict(os.environ, {}, clear=True):
        paths = RuntimePaths()
        assert paths.is_cloud_environment is False
        assert paths.is_fallback is False
        assert paths.runtime_root == paths.base_dir
        assert paths.output_dir == paths.base_dir / "output"
        assert paths.output_results_dir == paths.base_dir / "output" / "results"
        assert paths.temp_dir == paths.base_dir / "temp"


def test_runtime_root_env_override(tmp_path: Path):
    """Verify RUNTIME_ROOT explicitly overrides the root directory."""
    custom_root = tmp_path / "custom_runtime"
    with patch.dict(os.environ, {"RUNTIME_ROOT": str(custom_root)}, clear=False):
        paths = RuntimePaths()
        assert paths.runtime_root == custom_root.resolve()
        assert paths.output_results_dir == custom_root.resolve() / "output" / "results"
        assert paths.temp_dir == custom_root.resolve() / "temp"


def test_hosted_agent_detection_and_path_routing():
    """Verify hosted agent environment flag routes paths under /tmp or tempdir."""
    with patch.dict(os.environ, {"AZURE_AI_HOSTED_AGENT": "true"}, clear=False):
        paths = RuntimePaths()
        assert paths.is_cloud_environment is True
        if os.name != "nt":
            assert str(paths.runtime_root).startswith("/tmp")
        else:
            assert "havi_mcd" in str(paths.runtime_root)


def test_readonly_filesystem_automatic_fallback(tmp_path: Path):
    """Verify automatic fallback when candidate directory is not writable."""
    fake_readonly = tmp_path / "readonly_root"
    fake_readonly.mkdir()

    with patch.dict(os.environ, {"RUNTIME_ROOT": str(fake_readonly)}, clear=False), \
         patch("runtime_paths.is_directory_writable", return_value=False):
        paths = RuntimePaths()
        assert paths.is_fallback is True
        assert "havi_mcd" in str(paths.runtime_root)


def test_ensure_directories_creates_all_subdirs(tmp_path: Path):
    """Verify ensure_directories creates all required runtime directories."""
    custom_root = tmp_path / "ensure_test"
    with patch.dict(os.environ, {"RUNTIME_ROOT": str(custom_root)}, clear=False):
        paths = RuntimePaths()
        paths.ensure_directories()
        assert paths.output_results_dir.is_dir()
        assert paths.output_payloads_dir.is_dir()
        assert paths.output_reviews_dir.is_dir()
        assert paths.output_logs_dir.is_dir()
        assert paths.output_notifications_dir.is_dir()
        assert paths.temp_dir.is_dir()
        assert paths.uploads_dir.is_dir()
        assert paths.data_dir.is_dir()


def test_config_settings_integration():
    """Verify Settings picks up runtime paths and app_version."""
    reset_settings()
    settings = get_settings()
    assert settings.app_version == "1.1.0"
    assert settings.output_results_dir.name == "results"
    assert settings.temp_dir.name == "temp"


def test_managed_identity_auth_method_support():
    """Verify ModelClientManager recognizes managed_identity auth method."""
    custom_settings = Settings(
        azure_auth_method="managed_identity",
        azure_openai_endpoint="https://havi-mcd-automation-poc.openai.azure.com/",
        azure_client_id="11111111-2222-3333-4444-555555555555",
    )
    mgr = ModelClientManager(settings=custom_settings)
    with patch.dict(os.environ, {"AZURE_AUTH_METHOD": "managed_identity"}, clear=False):
        assert mgr.get_auth_method() == "managed_identity"
        assert mgr.is_configured() is True

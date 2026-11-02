"""
Health and Diagnostic Probes for Azure AI Foundry Hosted Agents.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from config import get_settings
from runtime_paths import is_directory_writable


def check_system_health() -> Dict[str, Any]:
    """Run comprehensive system health probe."""
    settings = get_settings()

    runtime_writable = is_directory_writable(settings.runtime_root / "temp")
    output_writable = is_directory_writable(settings.output_dir)

    return {
        "status": "HEALTHY" if (runtime_writable and output_writable) else "DEGRADED",
        "service": "havi-mcd-hosted-agent",
        "version": settings.app_version,
        "runtime_root": str(settings.runtime_root),
        "runtime_root_writable": runtime_writable,
        "output_writable": output_writable,
        "auth_method": settings.azure_auth_method,
        "model_deployment": settings.azure_ai_model_deployment_name,
        "foundry_endpoint_configured": bool(settings.foundry_project_endpoint or os.getenv("FOUNDRY_PROJECT_ENDPOINT")),
    }

"""
Centralized Runtime Path Manager for HAVI MCD Freight Automation Platform.

Supports:
  1. Local Windows development (writes inside project directory by default).
  2. Local Linux development.
  3. Azure AI Foundry Hosted Agents (automatically defaults to writable /tmp paths).
  4. Azure App Service Linux.
  5. RUNTIME_ROOT environment variable override.
  6. Automatic fallback to OS temp directory when the target filesystem is read-only.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("havi.runtime_paths")


def is_hosted_agent_environment() -> bool:
    """
    Detect whether the application is running inside Azure AI Foundry Hosted Agents,
    Azure App Service, Azure Container Apps, or a Docker container.
    """
    # Explicit Hosted Agent flags
    if os.getenv("AZURE_AI_HOSTED_AGENT", "").strip().lower() in ("true", "1", "yes"):
        return True
    if os.getenv("AI_HOSTED_AGENT", "").strip().lower() in ("true", "1", "yes"):
        return True

    # Azure App Service / Container Apps indicators
    if os.getenv("WEBSITE_SITE_NAME") or os.getenv("CONTAINER_APP_NAME"):
        return True

    # Linux container runtime indicators
    if os.name != "nt":
        if os.path.exists("/.dockerenv"):
            return True
        cwd = os.getcwd()
        if cwd == "/app" or cwd.startswith("/app/"):
            return True

    return False


def is_directory_writable(target_path: Path) -> bool:
    """Test if a directory can be created and written to."""
    try:
        target_path.mkdir(parents=True, exist_ok=True)
        probe_file = target_path / f".probe_{os.getpid()}_{os.urandom(4).hex()}.tmp"
        probe_file.write_text("probe", encoding="utf-8")
        if probe_file.exists():
            probe_file.unlink()
        return True
    except (OSError, PermissionError):
        return False


@dataclass
class RuntimePaths:
    """Manages all storage, output, temporary, and logging paths."""

    base_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent)
    runtime_root: Path = field(init=False)
    output_dir: Path = field(init=False)
    output_results_dir: Path = field(init=False)
    output_payloads_dir: Path = field(init=False)
    output_reviews_dir: Path = field(init=False)
    output_logs_dir: Path = field(init=False)
    output_notifications_dir: Path = field(init=False)
    temp_dir: Path = field(init=False)
    uploads_dir: Path = field(init=False)
    data_dir: Path = field(init=False)
    test_data_dir: Path = field(init=False)
    is_cloud_environment: bool = field(init=False)
    is_fallback: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self.is_cloud_environment = is_hosted_agent_environment()
        self._resolve_paths()

    def _resolve_paths(self) -> None:
        """Resolve and configure directory tree based on environment and permissions."""
        env_override = os.getenv("RUNTIME_ROOT", "").strip()

        if env_override:
            candidate_root = Path(env_override).resolve()
            source = "RUNTIME_ROOT_ENV"
        elif self.is_cloud_environment:
            # Azure AI Foundry Hosted Agents and Linux containers use /tmp
            candidate_root = Path("/tmp/havi_mcd") if os.name != "nt" else Path(tempfile.gettempdir()) / "havi_mcd"
            source = "HOSTED_AGENT_TMP"
        else:
            # Local development writes inside project directory
            candidate_root = self.base_dir
            source = "LOCAL_PROJECT_ROOT"

        # Check write capability and fallback automatically if read-only
        test_dir = candidate_root / "output"
        if not is_directory_writable(test_dir):
            self.is_fallback = True
            if os.name != "nt" and Path("/tmp").is_dir():
                self.runtime_root = Path("/tmp/havi_mcd")
            else:
                self.runtime_root = Path(tempfile.gettempdir()) / "havi_mcd"
            logger.warning(
                "[RUNTIME_PATHS_FALLBACK] Candidate root '%s' is not writable (read-only filesystem). "
                "Automatically fell back to OS temporary root: '%s'",
                candidate_root,
                self.runtime_root,
            )
        else:
            self.runtime_root = candidate_root
            self.is_fallback = False
            logger.info(
                "[RUNTIME_PATHS_INIT] Configured runtime root: '%s' (source: %s, cloud_env=%s)",
                self.runtime_root,
                source,
                self.is_cloud_environment,
            )

        # Configure subdirectories
        self.output_dir = self.runtime_root / "output"
        self.output_results_dir = self.output_dir / "results"
        self.output_payloads_dir = self.output_dir / "payloads"
        self.output_reviews_dir = self.output_dir / "reviews"
        self.output_logs_dir = self.output_dir / "logs"
        self.output_notifications_dir = self.output_dir / "notifications"
        self.temp_dir = self.runtime_root / "temp"
        self.uploads_dir = self.runtime_root / "uploads"
        self.data_dir = self.runtime_root / "data"

        # Read-only test data lookup: Check project directory first
        local_test_data = self.base_dir / "test_data"
        if local_test_data.exists():
            self.test_data_dir = local_test_data
        else:
            self.test_data_dir = self.data_dir / "test_data"

    def ensure_directories(self) -> None:
        """Create all required directories, falling back to temp directory if needed."""
        directories = [
            self.output_dir,
            self.output_results_dir,
            self.output_payloads_dir,
            self.output_reviews_dir,
            self.output_logs_dir,
            self.output_notifications_dir,
            self.temp_dir,
            self.uploads_dir,
            self.data_dir,
        ]

        for directory in directories:
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except (OSError, PermissionError) as exc:
                logger.error("[DIR_CREATE_ERROR] Failed to create '%s': %s", directory, exc)
                # Attempt emergency fallback to system temp dir
                if not self.is_fallback:
                    logger.warning("[DIR_EMERGENCY_FALLBACK] Diverting all directories to system temp dir.")
                    self.runtime_root = Path(tempfile.gettempdir()) / "havi_mcd"
                    self.is_fallback = True
                    self._resolve_paths()
                    self.ensure_directories()
                    return


_runtime_paths: Optional[RuntimePaths] = None


def get_runtime_paths() -> RuntimePaths:
    """Singleton accessor for RuntimePaths."""
    global _runtime_paths
    if _runtime_paths is None:
        _runtime_paths = RuntimePaths()
        _runtime_paths.ensure_directories()
    return _runtime_paths


def reset_runtime_paths() -> None:
    """Reset cached runtime paths (useful for test isolation)."""
    global _runtime_paths
    _runtime_paths = None

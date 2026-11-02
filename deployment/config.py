"""
Application Configuration.

Central settings for local file processing, confidence thresholds,
directory layouts, and OCR provider configuration.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Base workspace directory
    base_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parent)

    # Confidence policy threshold (default 0.80 per specification)
    confidence_threshold: float = Field(default=0.80, ge=0.0, le=1.0)

    # Scanned PDF detection heuristic: max chars per page to consider page potentially scanned
    scanned_char_threshold: int = Field(default=20, ge=0)

    # Directories
    test_data_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parent / "test_data")
    output_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parent / "output")
    output_results_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parent / "output" / "results")
    output_payloads_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parent / "output" / "payloads")
    output_reviews_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parent / "output" / "reviews")
    output_logs_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parent / "output" / "logs")

    # Supported file extensions
    supported_extensions: List[str] = Field(
        default=[".pdf", ".png", ".jpg", ".jpeg", ".json", ".txt"]
    )

    # OCR configuration
    tesseract_cmd: Optional[str] = Field(default=None)
    enable_ocr_fallback: bool = Field(default=True)

    # Azure AI / OpenAI / Foundry Configuration
    azure_auth_method: str = Field(default="api_key")
    azure_openai_api_key: Optional[str] = Field(default=None)
    azure_openai_endpoint: Optional[str] = Field(default=None)
    azure_ai_model_deployment_name: str = Field(default="gpt-4o")
    azure_openai_api_version: str = Field(default="2024-10-21")
    azure_tenant_id: Optional[str] = Field(default=None)
    azure_client_id: Optional[str] = Field(default=None)
    azure_client_secret: Optional[str] = Field(default=None)
    foundry_project_endpoint: Optional[str] = Field(default=None)
    azure_ai_foundry_project_endpoint: Optional[str] = Field(default=None)

    # API Security / Ingestion Key
    api_auth_key: Optional[str] = Field(default=None)

    def ensure_directories(self) -> None:
        """Ensure all required output directories exist on disk."""
        for path in [
            self.output_dir,
            self.output_results_dir,
            self.output_payloads_dir,
            self.output_reviews_dir,
            self.output_logs_dir,
            self.test_data_dir / "incoming",
        ]:
            path.mkdir(parents=True, exist_ok=True)


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Retrieve singleton application settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.ensure_directories()
    return _settings

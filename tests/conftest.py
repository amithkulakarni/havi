"""Pytest session configuration and fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def cleanup_telemetry():
    """Ensure OpenTelemetry metric/trace exporters are cleanly shut down at session exit."""
    yield
    try:
        from opentelemetry import metrics, trace
        meter_provider = metrics.get_meter_provider()
        if hasattr(meter_provider, "shutdown"):
            meter_provider.shutdown()
        tracer_provider = trace.get_tracer_provider()
        if hasattr(tracer_provider, "shutdown"):
            tracer_provider.shutdown()
    except Exception:
        pass

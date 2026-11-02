"""
Telemetry, Latency, and Workflow Execution Metrics for Azure AI Foundry Hosted Agents.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from monitoring.logger import get_correlation_id


@dataclass
class WorkflowTelemetrySpan:
    """Represents a measured span in an agent orchestration workflow."""

    span_name: str
    correlation_id: str = field(default_factory=get_correlation_id)
    start_time: float = field(default_factory=time.perf_counter)
    end_time: Optional[float] = None
    duration_ms: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    status: str = "IN_PROGRESS"

    def complete(self, status: str = "SUCCESS", **extra: Any) -> float:
        """Mark span as complete and calculate elapsed time."""
        self.end_time = time.perf_counter()
        self.duration_ms = round((self.end_time - self.start_time) * 1000, 2)
        self.status = status
        self.metadata.update(extra)
        return self.duration_ms

    def to_dict(self) -> Dict[str, Any]:
        return {
            "span_name": self.span_name,
            "correlation_id": self.correlation_id,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "metadata": self.metadata,
        }


class WorkflowMetricsTracker:
    """Tracks latency, agent hops, and workflow execution statistics."""

    def __init__(self) -> None:
        self.spans: List[WorkflowTelemetrySpan] = []

    def start_span(self, name: str, **metadata: Any) -> WorkflowTelemetrySpan:
        span = WorkflowTelemetrySpan(span_name=name, metadata=metadata)
        self.spans.append(span)
        return span

    def summary(self) -> Dict[str, Any]:
        total_duration = sum(s.duration_ms or 0.0 for s in self.spans)
        return {
            "total_spans": len(self.spans),
            "total_duration_ms": round(total_duration, 2),
            "spans": [s.to_dict() for s in self.spans],
        }

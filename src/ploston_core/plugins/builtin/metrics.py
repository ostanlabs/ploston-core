"""Metrics plugin for AEL.

This plugin emits OpenTelemetry metrics for workflow execution.
"""

import time
from typing import Any

from ..base import AELPlugin
from ..types import (
    RequestContext,
    ResponseContext,
    StepContext,
    StepResultContext,
)


class MetricsPlugin(AELPlugin):
    """Plugin that emits metrics for workflow execution.

    Uses OpenTelemetry metrics when available, falls back to counters.

    Configuration options:
        prefix: Metric name prefix. Default: ael_plugin
        emit_histogram: Whether to emit duration histograms. Default: True
    """

    name = "metrics"
    priority = 90  # Run late to capture accurate timing

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self._prefix = self.config.get("prefix", "ael_plugin")
        self._emit_histogram = self.config.get("emit_histogram", True)
        self._request_times: dict[str, float] = {}

        # Try to get OTEL meter
        self._meter = None
        self._request_counter = None
        self._step_counter = None
        self._step_duration = None
        self._workflow_duration = None
        self._init_metrics()

    def _init_metrics(self) -> None:
        """Initialize OpenTelemetry metrics if available."""
        try:
            from opentelemetry import metrics

            self._meter = metrics.get_meter(__name__)
            self._request_counter = self._meter.create_counter(
                f"{self._prefix}_requests_total",
                description="Total workflow requests",
            )
            self._step_counter = self._meter.create_counter(
                f"{self._prefix}_steps_total",
                description="Total step executions",
            )
            if self._emit_histogram:
                self._step_duration = self._meter.create_histogram(
                    f"{self._prefix}_step_duration_seconds",
                    description="Step execution duration",
                    unit="s",
                )
                self._workflow_duration = self._meter.create_histogram(
                    f"{self._prefix}_workflow_duration_seconds",
                    description="Workflow execution duration",
                    unit="s",
                )
        except ImportError:
            pass  # OTEL not available, metrics will be no-ops

    def on_request_received(self, context: RequestContext) -> RequestContext:
        """Record workflow request and start timing."""
        self._request_times[context.execution_id] = time.time()
        if self._request_counter:
            self._request_counter.add(1, {"workflow": context.workflow_id})
        return context

    def on_step_before(self, context: StepContext) -> StepContext:
        """Record step start time."""
        # Store step start time in metadata
        context.metadata["_metrics_start_time"] = time.time()
        return context

    def on_step_after(self, context: StepResultContext) -> StepResultContext:
        """Record step metrics."""
        if self._step_counter:
            self._step_counter.add(
                1,
                {
                    "workflow": context.workflow_id,
                    "step": context.step_id,
                    "step_type": context.step_type,
                    "status": "success" if context.success else "error",
                },
            )

        if self._step_duration and self._emit_histogram:
            duration_s = context.duration_ms / 1000.0
            self._step_duration.record(
                duration_s,
                {
                    "workflow": context.workflow_id,
                    "step": context.step_id,
                    "step_type": context.step_type,
                },
            )
        return context

    def on_response_ready(self, context: ResponseContext) -> ResponseContext:
        """Record workflow completion metrics."""
        if self._workflow_duration and self._emit_histogram:
            duration_s = context.duration_ms / 1000.0
            self._workflow_duration.record(
                duration_s,
                {
                    "workflow": context.workflow_id,
                    "status": "success" if context.success else "error",
                },
            )

        # Clean up timing data
        self._request_times.pop(context.execution_id, None)
        return context

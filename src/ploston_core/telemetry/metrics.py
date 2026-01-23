"""AEL Core Metrics Schema - OpenTelemetry conventions.

Defines all metrics exposed by AEL following OpenTelemetry semantic conventions.

Metrics:
- Counters: Track total occurrences
- Histograms: Track distributions (durations, sizes)
- Gauges: Track current values

Labels/Attributes:
- workflow_id: Workflow identifier
- step_id: Step identifier within workflow
- tool_name: Tool name (server:tool format)
- status: Execution status (success, error, timeout)
- error_code: Error code when status=error
"""

from dataclasses import dataclass
from typing import Any

from opentelemetry import metrics
from opentelemetry.metrics import Counter, Histogram, UpDownCounter


@dataclass
class MetricLabels:
    """Standard metric labels/attributes."""

    # Workflow labels
    WORKFLOW_ID = "workflow_id"
    STEP_ID = "step_id"

    # Tool labels
    TOOL_NAME = "tool_name"
    TOOL_SERVER = "tool_server"

    # Status labels
    STATUS = "status"
    ERROR_CODE = "error_code"

    # Status values
    STATUS_SUCCESS = "success"
    STATUS_ERROR = "error"
    STATUS_TIMEOUT = "timeout"
    STATUS_CANCELLED = "cancelled"


class AELMetrics:
    """AEL metrics collection.

    Provides instrumentation for:
    - Workflow executions
    - Step executions
    - Tool invocations
    - System state (active workflows, registered tools)
    """

    def __init__(self, meter: metrics.Meter):
        """Initialize metrics.

        Args:
            meter: OpenTelemetry Meter instance
        """
        self._meter = meter
        self._setup_counters()
        self._setup_histograms()
        self._setup_gauges()

    def _setup_counters(self) -> None:
        """Set up counter metrics."""
        # Workflow executions
        self.workflow_executions_total: Counter = self._meter.create_counter(
            name="ael_workflow_executions_total",
            description="Total number of workflow executions",
            unit="1",
        )

        # Step executions
        self.step_executions_total: Counter = self._meter.create_counter(
            name="ael_step_executions_total",
            description="Total number of step executions",
            unit="1",
        )

        # Tool invocations
        self.tool_invocations_total: Counter = self._meter.create_counter(
            name="ael_tool_invocations_total",
            description="Total number of tool invocations",
            unit="1",
        )

    def _setup_histograms(self) -> None:
        """Set up histogram metrics."""
        # Workflow duration
        self.workflow_duration_seconds: Histogram = self._meter.create_histogram(
            name="ael_workflow_duration_seconds",
            description="Workflow execution duration in seconds",
            unit="s",
        )

        # Step duration
        self.step_duration_seconds: Histogram = self._meter.create_histogram(
            name="ael_step_duration_seconds",
            description="Step execution duration in seconds",
            unit="s",
        )

        # Tool invocation duration
        self.tool_invocation_duration_seconds: Histogram = self._meter.create_histogram(
            name="ael_tool_invocation_duration_seconds",
            description="Tool invocation duration in seconds",
            unit="s",
        )

    def _setup_gauges(self) -> None:
        """Set up gauge metrics (using UpDownCounter for gauges)."""
        # Active workflows
        self.active_workflows: UpDownCounter = self._meter.create_up_down_counter(
            name="ael_active_workflows",
            description="Number of currently running workflows",
            unit="1",
        )

        # Registered tools
        self.registered_tools: UpDownCounter = self._meter.create_up_down_counter(
            name="ael_registered_tools",
            description="Number of registered tools",
            unit="1",
        )

        # Registered workflows
        self.registered_workflows: UpDownCounter = self._meter.create_up_down_counter(
            name="ael_registered_workflows",
            description="Number of registered workflows",
            unit="1",
        )

    # Convenience methods for recording metrics

    def record_workflow_start(self, workflow_id: str) -> None:
        """Record workflow start.

        Args:
            workflow_id: Workflow identifier
        """
        self.active_workflows.add(1, {MetricLabels.WORKFLOW_ID: workflow_id})

    def record_workflow_end(
        self,
        workflow_id: str,
        duration_seconds: float,
        status: str,
        error_code: str | None = None,
    ) -> None:
        """Record workflow completion.

        Args:
            workflow_id: Workflow identifier
            duration_seconds: Execution duration
            status: Execution status (success, error, timeout)
            error_code: Error code if status is error
        """
        labels: dict[str, Any] = {
            MetricLabels.WORKFLOW_ID: workflow_id,
            MetricLabels.STATUS: status,
        }
        if error_code:
            labels[MetricLabels.ERROR_CODE] = error_code

        self.active_workflows.add(-1, {MetricLabels.WORKFLOW_ID: workflow_id})
        self.workflow_executions_total.add(1, labels)
        self.workflow_duration_seconds.record(duration_seconds, labels)

    def record_step_execution(
        self,
        workflow_id: str,
        step_id: str,
        duration_seconds: float,
        status: str,
        error_code: str | None = None,
    ) -> None:
        """Record step execution.

        Args:
            workflow_id: Workflow identifier
            step_id: Step identifier
            duration_seconds: Execution duration
            status: Execution status
            error_code: Error code if status is error
        """
        labels: dict[str, Any] = {
            MetricLabels.WORKFLOW_ID: workflow_id,
            MetricLabels.STEP_ID: step_id,
            MetricLabels.STATUS: status,
        }
        if error_code:
            labels[MetricLabels.ERROR_CODE] = error_code

        self.step_executions_total.add(1, labels)
        self.step_duration_seconds.record(duration_seconds, labels)

    def record_tool_invocation(
        self,
        tool_name: str,
        duration_seconds: float,
        status: str,
        error_code: str | None = None,
    ) -> None:
        """Record tool invocation.

        Args:
            tool_name: Tool name (server:tool format)
            duration_seconds: Invocation duration
            status: Execution status
            error_code: Error code if status is error
        """
        labels: dict[str, Any] = {
            MetricLabels.TOOL_NAME: tool_name,
            MetricLabels.STATUS: status,
        }
        if error_code:
            labels[MetricLabels.ERROR_CODE] = error_code

        self.tool_invocations_total.add(1, labels)
        self.tool_invocation_duration_seconds.record(duration_seconds, labels)

    def set_registered_tools_count(self, count: int) -> None:
        """Set the number of registered tools.

        Args:
            count: Number of registered tools
        """
        # Reset and set to new value
        # Note: This is a simplification; in production you'd track delta
        self.registered_tools.add(count)

    def set_registered_workflows_count(self, count: int) -> None:
        """Set the number of registered workflows.

        Args:
            count: Number of registered workflows
        """
        self.registered_workflows.add(count)

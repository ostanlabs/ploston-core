"""Ploston Core Metrics Schema - OpenTelemetry conventions.

Defines all metrics exposed by Ploston following OpenTelemetry semantic conventions.

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

All metrics use the 'ploston_' prefix for consistency with Grafana dashboards.
"""

from dataclasses import dataclass
from typing import Any

from opentelemetry import metrics
from opentelemetry.metrics import Counter, Histogram, UpDownCounter

# Metric prefix for all Ploston metrics
METRIC_PREFIX = "ploston"


@dataclass
class MetricLabels:
    """Standard metric labels/attributes."""

    # Workflow labels
    WORKFLOW_ID = "workflow_id"
    WORKFLOW = "workflow"  # Alias for dashboard compatibility
    STEP_ID = "step_id"

    # Tool labels
    TOOL_NAME = "tool_name"
    TOOL_SERVER = "tool_server"
    TOOL_SOURCE = "tool_source"  # native, local, system, configured (mcp)
    FROM_TOOL = "from_tool"
    TO_TOOL = "to_tool"

    # Status labels
    STATUS = "status"
    ERROR_CODE = "error_code"

    # Status values
    STATUS_SUCCESS = "success"
    STATUS_ERROR = "error"
    STATUS_TIMEOUT = "timeout"
    STATUS_CANCELLED = "cancelled"

    # Tool source values (for dashboard display)
    SOURCE_NATIVE = "native"  # Native tools (filesystem, kafka, etc.)
    SOURCE_LOCAL = "local"  # Tools from local runners
    SOURCE_SYSTEM = "system"  # System tools (python_exec)
    SOURCE_CONFIGURED = "configured"  # MCP servers configured in CP


class PlostMetrics:
    """Ploston metrics collection.

    Provides instrumentation for:
    - Workflow executions
    - Step executions
    - Tool invocations
    - Chain detection (tool call patterns)
    - System state (active workflows, registered tools)

    All metrics use the 'ploston_' prefix for Grafana dashboard compatibility.
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

        # Track current values for UpDownCounters (to calculate deltas)
        self._current_mcp_tools = 0
        self._current_system_tools = 0
        self._current_runner_tools = 0
        self._current_native_tools = 0
        self._current_connected_runners = 0

    def _setup_counters(self) -> None:
        """Set up counter metrics."""
        # Workflow executions
        self.workflow_executions_total: Counter = self._meter.create_counter(
            name=f"{METRIC_PREFIX}_workflow_executions_total",
            description="Total number of workflow executions",
            unit="1",
        )

        # Step executions
        self.step_executions_total: Counter = self._meter.create_counter(
            name=f"{METRIC_PREFIX}_step_executions_total",
            description="Total number of step executions",
            unit="1",
        )

        # Tool invocations
        self.tool_invocations_total: Counter = self._meter.create_counter(
            name=f"{METRIC_PREFIX}_tool_invocations_total",
            description="Total number of tool invocations",
            unit="1",
        )

        # Chain links (tool call patterns)
        self.chain_links_total: Counter = self._meter.create_counter(
            name=f"{METRIC_PREFIX}_chain_links_total",
            description="Total chain links detected between tool calls",
            unit="1",
        )

    def _setup_histograms(self) -> None:
        """Set up histogram metrics."""
        # Workflow duration
        self.workflow_duration_seconds: Histogram = self._meter.create_histogram(
            name=f"{METRIC_PREFIX}_workflow_duration_seconds",
            description="Workflow execution duration in seconds",
            unit="s",
        )

        # Step duration
        self.step_duration_seconds: Histogram = self._meter.create_histogram(
            name=f"{METRIC_PREFIX}_step_duration_seconds",
            description="Step execution duration in seconds",
            unit="s",
        )

        # Tool invocation duration
        self.tool_invocation_duration_seconds: Histogram = self._meter.create_histogram(
            name=f"{METRIC_PREFIX}_tool_invocation_duration_seconds",
            description="Tool invocation duration in seconds",
            unit="s",
        )

    def _setup_gauges(self) -> None:
        """Set up gauge metrics (using UpDownCounter for gauges)."""
        # Active workflows
        self.active_workflows: UpDownCounter = self._meter.create_up_down_counter(
            name=f"{METRIC_PREFIX}_active_workflows",
            description="Number of currently running workflows",
            unit="1",
        )

        # Registered tools (legacy - total count)
        self.registered_tools: UpDownCounter = self._meter.create_up_down_counter(
            name=f"{METRIC_PREFIX}_registered_tools",
            description="Number of registered tools",
            unit="1",
        )

        # Tools by source - tracks tools from different sources
        self.tools_by_source: UpDownCounter = self._meter.create_up_down_counter(
            name=f"{METRIC_PREFIX}_tools_by_source",
            description="Number of tools by source (mcp, system, runner)",
            unit="1",
        )

        # Connected runners
        self.connected_runners: UpDownCounter = self._meter.create_up_down_counter(
            name=f"{METRIC_PREFIX}_connected_runners",
            description="Number of connected local runners",
            unit="1",
        )

        # Registered workflows
        self.registered_workflows: UpDownCounter = self._meter.create_up_down_counter(
            name=f"{METRIC_PREFIX}_registered_workflows",
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
        source: str | None = None,
    ) -> None:
        """Record tool invocation.

        Args:
            tool_name: Tool name (server:tool format)
            duration_seconds: Invocation duration
            status: Execution status
            error_code: Error code if status is error
            source: Tool source category (native, local, system, configured)
        """
        labels: dict[str, Any] = {
            MetricLabels.TOOL_NAME: tool_name,
            MetricLabels.STATUS: status,
        }
        if error_code:
            labels[MetricLabels.ERROR_CODE] = error_code
        if source:
            labels[MetricLabels.TOOL_SOURCE] = source

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

    def set_tools_by_source(
        self,
        mcp_tools: int = 0,
        system_tools: int = 0,
        runner_tools: int = 0,
        native_tools: int = 0,
    ) -> None:
        """Set the number of tools by source (legacy - adds to counter).

        Args:
            mcp_tools: Number of tools from MCP servers
            system_tools: Number of system tools (python_exec, etc.)
            runner_tools: Number of tools from local runners
            native_tools: Number of native tools (filesystem, kafka, etc.)
        """
        self.tools_by_source.add(mcp_tools, {"source": "mcp"})
        self.tools_by_source.add(system_tools, {"source": "system"})
        self.tools_by_source.add(runner_tools, {"source": "runner"})
        self.tools_by_source.add(native_tools, {"source": "native"})

    def update_tools_by_source(
        self,
        mcp_tools: int = 0,
        system_tools: int = 0,
        runner_tools: int = 0,
        native_tools: int = 0,
    ) -> None:
        """Update the number of tools by source (calculates delta).

        This method tracks the current values and only adds the delta
        to the UpDownCounter, ensuring accurate gauge-like behavior.

        Args:
            mcp_tools: New count of tools from MCP servers
            system_tools: New count of system tools (python_exec, etc.)
            runner_tools: New count of tools from local runners
            native_tools: New count of native tools (filesystem, kafka, etc.)
        """
        # Calculate deltas
        mcp_delta = mcp_tools - self._current_mcp_tools
        system_delta = system_tools - self._current_system_tools
        runner_delta = runner_tools - self._current_runner_tools
        native_delta = native_tools - self._current_native_tools

        # Update counters with deltas
        if mcp_delta != 0:
            self.tools_by_source.add(mcp_delta, {"source": "mcp"})
        if system_delta != 0:
            self.tools_by_source.add(system_delta, {"source": "system"})
        if runner_delta != 0:
            self.tools_by_source.add(runner_delta, {"source": "runner"})
        if native_delta != 0:
            self.tools_by_source.add(native_delta, {"source": "native"})

        # Update tracked values
        self._current_mcp_tools = mcp_tools
        self._current_system_tools = system_tools
        self._current_runner_tools = runner_tools
        self._current_native_tools = native_tools

    def set_connected_runners(self, count: int) -> None:
        """Set the number of connected local runners (legacy - adds to counter).

        Args:
            count: Number of connected runners
        """
        self.connected_runners.add(count)

    def update_connected_runners(self, count: int) -> None:
        """Update the number of connected local runners (calculates delta).

        This method tracks the current value and only adds the delta
        to the UpDownCounter, ensuring accurate gauge-like behavior.

        Args:
            count: New count of connected runners
        """
        delta = count - self._current_connected_runners
        if delta != 0:
            self.connected_runners.add(delta)
        self._current_connected_runners = count

    def update_runner_tools(self, runner_tools: int) -> None:
        """Update just the runner tools count (calculates delta).

        Args:
            runner_tools: New count of tools from local runners
        """
        runner_delta = runner_tools - self._current_runner_tools
        if runner_delta != 0:
            self.tools_by_source.add(runner_delta, {"source": "runner"})
        self._current_runner_tools = runner_tools

    def set_registered_workflows_count(self, count: int) -> None:
        """Set the number of registered workflows.

        Args:
            count: Number of registered workflows
        """
        self.registered_workflows.add(count)

    def record_chain_link(self, from_tool: str, to_tool: str) -> None:
        """Record a chain link between two tool calls.

        Chain links track patterns where one tool's output feeds into another.

        Args:
            from_tool: Source tool name
            to_tool: Destination tool name
        """
        self.chain_links_total.add(
            1,
            {
                MetricLabels.FROM_TOOL: from_tool,
                MetricLabels.TO_TOOL: to_tool,
            },
        )


# Backwards compatibility alias
AELMetrics = PlostMetrics

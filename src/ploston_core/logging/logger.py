"""AEL Logger - Hierarchical colored logging for workflow execution."""

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TextIO

from ploston_core.logging.colors import (
    CYAN,
    GREEN,
    LIGHT_BLUE,
    MAGENTA,
    ORANGE,
    RED,
    RESET,
    YELLOW,
)
from ploston_core.types import LogFormat, LogLevel


@dataclass
class LogConfig:
    """Logger configuration."""

    level: LogLevel = LogLevel.INFO
    format: LogFormat = LogFormat.COLORED
    show_params: bool = True
    show_results: bool = True
    truncate_at: int = 200
    components: dict[str, bool] = field(default_factory=dict)
    output: TextIO = field(default=sys.stdout)

    def __post_init__(self) -> None:
        """Initialize default components if not provided."""
        if not self.components:
            self.components = {
                "workflow": True,
                "step": True,
                "tool": True,
                "sandbox": True,
            }


class AELLogger:
    """Main logger facade. Creates component-specific loggers."""

    def __init__(self, config: LogConfig | None = None):
        """Initialize logger with configuration.

        Args:
            config: Logger configuration (defaults to LogConfig())
        """
        self.config = config or LogConfig()
        self._level_order = {
            LogLevel.DEBUG: 0,
            LogLevel.INFO: 1,
            LogLevel.WARN: 2,
            LogLevel.ERROR: 3,
        }

    def workflow(self, workflow_id: str, execution_id: str) -> "WorkflowLogger":
        """Get a logger scoped to a workflow execution.

        Args:
            workflow_id: Workflow identifier
            execution_id: Execution identifier

        Returns:
            WorkflowLogger instance
        """
        return WorkflowLogger(self, workflow_id, execution_id)

    def configure(self, config: LogConfig) -> None:
        """Update configuration (for hot-reload).

        Args:
            config: New logger configuration
        """
        self.config = config

    def _should_log(self, level: LogLevel) -> bool:
        """Check if a log level should be logged.

        Args:
            level: Log level to check

        Returns:
            True if should log, False otherwise
        """
        return self._level_order.get(level, 0) >= self._level_order.get(self.config.level, 1)

    def _log(
        self,
        level: LogLevel,
        component: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Internal logging method.

        Args:
            level: Log level
            component: Component name (workflow, step, tool, sandbox)
            message: Log message
            context: Additional context data
        """
        if not self._should_log(level):
            return

        if not self.config.components.get(component, True):
            return

        if self.config.format == LogFormat.JSON:
            self._log_json(level, component, message, context)
        else:
            self._log_colored(level, component, message, context)

    def _log_json(
        self,
        level: LogLevel,
        component: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Log in JSON format.

        Args:
            level: Log level
            component: Component name
            message: Log message
            context: Additional context data
        """
        log_entry = {
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "level": level.value,
            "component": component,
            "message": message,
        }
        if context:
            log_entry.update(context)

        print(json.dumps(log_entry), file=self.config.output)

    def _log_colored(
        self,
        level: LogLevel,
        component: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Log in colored format.

        Args:
            level: Log level
            component: Component name
            message: Log message
            context: Additional context data
        """
        # Color mapping
        level_colors = {
            LogLevel.DEBUG: LIGHT_BLUE,
            LogLevel.INFO: CYAN,
            LogLevel.WARN: YELLOW,
            LogLevel.ERROR: RED,
        }

        color = level_colors.get(level, RESET)
        component_color = {
            "workflow": MAGENTA,
            "step": CYAN,
            "tool": GREEN,
            "sandbox": ORANGE,
        }.get(component, RESET)

        # Format: [COMPONENT] message
        output = f"{component_color}[{component.upper()}]{RESET} {color}{message}{RESET}"

        if context and self.config.show_params:
            # Truncate context if needed
            context_str = str(context)
            if len(context_str) > self.config.truncate_at:
                context_str = context_str[: self.config.truncate_at] + "..."
            output += f" {LIGHT_BLUE}{context_str}{RESET}"

        print(output, file=self.config.output)


class WorkflowLogger:
    """Logger for workflow-level events."""

    def __init__(self, parent: AELLogger, workflow_id: str, execution_id: str):
        """Initialize workflow logger.

        Args:
            parent: Parent AELLogger instance
            workflow_id: Workflow identifier
            execution_id: Execution identifier
        """
        self.parent = parent
        self.workflow_id = workflow_id
        self.execution_id = execution_id

    def started(self, version: str | None = None) -> None:
        """Log workflow start.

        Args:
            version: Optional workflow version
        """
        context = {
            "workflow_id": self.workflow_id,
            "execution_id": self.execution_id,
            "event": "workflow_started",
        }
        if version:
            context["version"] = version

        message = f"Workflow '{self.workflow_id}' started"
        if version:
            message += f" (v{version})"

        self.parent._log(LogLevel.INFO, "workflow", message, context)

    def completed(self, duration_ms: int, step_count: int) -> None:
        """Log workflow completion with summary.

        Args:
            duration_ms: Execution duration in milliseconds
            step_count: Number of steps executed
        """
        context = {
            "workflow_id": self.workflow_id,
            "execution_id": self.execution_id,
            "event": "workflow_completed",
            "duration_ms": duration_ms,
            "step_count": step_count,
        }

        duration_s = duration_ms / 1000
        message = (
            f"Workflow '{self.workflow_id}' completed ({step_count} steps, {duration_s:.2f}s) ✓"
        )

        self.parent._log(LogLevel.INFO, "workflow", message, context)

    def failed(self, error: Exception, duration_ms: int) -> None:
        """Log workflow failure.

        Args:
            error: Exception that caused failure
            duration_ms: Execution duration in milliseconds
        """
        context = {
            "workflow_id": self.workflow_id,
            "execution_id": self.execution_id,
            "event": "workflow_failed",
            "duration_ms": duration_ms,
            "error": str(error),
            "error_type": type(error).__name__,
        }

        duration_s = duration_ms / 1000
        message = f"Workflow '{self.workflow_id}' failed ({duration_s:.2f}s): {error}"

        self.parent._log(LogLevel.ERROR, "workflow", message, context)

    def step(self, step_id: str) -> "StepLogger":
        """Get a logger scoped to a step.

        Args:
            step_id: Step identifier

        Returns:
            StepLogger instance
        """
        return StepLogger(self, step_id)


class StepLogger:
    """Logger for step-level events."""

    def __init__(self, parent: WorkflowLogger, step_id: str):
        """Initialize step logger.

        Args:
            parent: Parent WorkflowLogger instance
            step_id: Step identifier
        """
        self.parent = parent
        self.step_id = step_id

    def started(self, step_type: str, tool_name: str | None = None) -> None:
        """Log step start.

        Args:
            step_type: Type of step (tool, code)
            tool_name: Optional tool name for tool steps
        """
        context = {
            "workflow_id": self.parent.workflow_id,
            "execution_id": self.parent.execution_id,
            "step_id": self.step_id,
            "event": "step_started",
            "step_type": step_type,
        }
        if tool_name:
            context["tool_name"] = tool_name

        message = f"Step '{self.step_id}' started"
        if tool_name:
            message += f" (tool: {tool_name})"

        self.parent.parent._log(LogLevel.INFO, "step", message, context)

    def completed(self, duration_ms: int, result_preview: str | None = None) -> None:
        """Log step completion.

        Args:
            duration_ms: Execution duration in milliseconds
            result_preview: Optional preview of result
        """
        context = {
            "workflow_id": self.parent.workflow_id,
            "execution_id": self.parent.execution_id,
            "step_id": self.step_id,
            "event": "step_completed",
            "duration_ms": duration_ms,
        }
        if result_preview:
            context["result_preview"] = result_preview

        duration_s = duration_ms / 1000
        message = f"Step '{self.step_id}' completed ({duration_s:.2f}s) ✓"

        self.parent.parent._log(LogLevel.INFO, "step", message, context)

    def skipped(self, reason: str) -> None:
        """Log step skip.

        Args:
            reason: Reason for skipping
        """
        context = {
            "workflow_id": self.parent.workflow_id,
            "execution_id": self.parent.execution_id,
            "step_id": self.step_id,
            "event": "step_skipped",
            "reason": reason,
        }

        message = f"Step '{self.step_id}' skipped: {reason}"

        self.parent.parent._log(LogLevel.INFO, "step", message, context)

    def failed(self, error: Exception) -> None:
        """Log step failure.

        Args:
            error: Exception that caused failure
        """
        context = {
            "workflow_id": self.parent.workflow_id,
            "execution_id": self.parent.execution_id,
            "step_id": self.step_id,
            "event": "step_failed",
            "error": str(error),
            "error_type": type(error).__name__,
        }

        message = f"Step '{self.step_id}' failed: {error}"

        self.parent.parent._log(LogLevel.ERROR, "step", message, context)

    def retrying(self, attempt: int, max_attempts: int, delay_seconds: float) -> None:
        """Log retry attempt.

        Args:
            attempt: Current attempt number
            max_attempts: Maximum number of attempts
            delay_seconds: Delay before retry in seconds
        """
        context = {
            "workflow_id": self.parent.workflow_id,
            "execution_id": self.parent.execution_id,
            "step_id": self.step_id,
            "event": "step_retrying",
            "attempt": attempt,
            "max_attempts": max_attempts,
            "delay_seconds": delay_seconds,
        }

        message = (
            f"Step '{self.step_id}' retrying "
            f"(attempt {attempt}/{max_attempts}, delay: {delay_seconds}s)"
        )

        self.parent.parent._log(LogLevel.WARN, "step", message, context)

    def tool(self) -> "ToolLogger":
        """Get a logger for tool calls within this step.

        Returns:
            ToolLogger instance
        """
        return ToolLogger(self)

    def sandbox(self) -> "SandboxLogger":
        """Get a logger for sandbox events within this step.

        Returns:
            SandboxLogger instance
        """
        return SandboxLogger(self)


class ToolLogger:
    """Logger for tool call events."""

    def __init__(self, parent: StepLogger):
        """Initialize tool logger.

        Args:
            parent: Parent StepLogger instance
        """
        self.parent = parent

    def calling(self, tool_name: str, params: dict[str, Any] | None = None) -> None:
        """Log tool call start.

        Args:
            tool_name: Name of the tool being called
            params: Optional tool parameters
        """
        context: dict[str, Any] = {
            "workflow_id": self.parent.parent.workflow_id,
            "execution_id": self.parent.parent.execution_id,
            "step_id": self.parent.step_id,
            "event": "tool_calling",
            "tool_name": tool_name,
        }
        if params:
            context["params"] = params

        message = f"Calling tool '{tool_name}'"

        self.parent.parent.parent._log(LogLevel.INFO, "tool", message, context)

    def result(self, tool_name: str, result: Any, duration_ms: int) -> None:
        """Log tool call result.

        Args:
            tool_name: Name of the tool
            result: Tool execution result
            duration_ms: Execution duration in milliseconds
        """
        context = {
            "workflow_id": self.parent.parent.workflow_id,
            "execution_id": self.parent.parent.execution_id,
            "step_id": self.parent.step_id,
            "event": "tool_result",
            "tool_name": tool_name,
            "duration_ms": duration_ms,
        }

        duration_s = duration_ms / 1000
        message = f"Tool '{tool_name}' completed ({duration_s:.2f}s) ✓"

        if self.parent.parent.parent.config.show_results:
            result_str = str(result)
            if len(result_str) > self.parent.parent.parent.config.truncate_at:
                result_str = result_str[: self.parent.parent.parent.config.truncate_at] + "..."
            context["result"] = result_str

        self.parent.parent.parent._log(LogLevel.INFO, "tool", message, context)

    def error(self, tool_name: str, error: str, duration_ms: int) -> None:
        """Log tool call error.

        Args:
            tool_name: Name of the tool
            error: Error message
            duration_ms: Execution duration in milliseconds
        """
        context = {
            "workflow_id": self.parent.parent.workflow_id,
            "execution_id": self.parent.parent.execution_id,
            "step_id": self.parent.step_id,
            "event": "tool_error",
            "tool_name": tool_name,
            "duration_ms": duration_ms,
            "error": error,
        }

        duration_s = duration_ms / 1000
        message = f"Tool '{tool_name}' failed ({duration_s:.2f}s): {error}"

        self.parent.parent.parent._log(LogLevel.ERROR, "tool", message, context)


class SandboxLogger:
    """Logger for Python sandbox events."""

    def __init__(self, parent: StepLogger):
        """Initialize sandbox logger.

        Args:
            parent: Parent StepLogger instance
        """
        self.parent = parent

    def executing(self) -> None:
        """Log sandbox execution start."""
        context = {
            "workflow_id": self.parent.parent.workflow_id,
            "execution_id": self.parent.parent.execution_id,
            "step_id": self.parent.step_id,
            "event": "sandbox_executing",
        }

        message = "Executing Python code in sandbox"

        self.parent.parent.parent._log(LogLevel.INFO, "sandbox", message, context)

    def imports_validated(self) -> None:
        """Log successful import validation."""
        context = {
            "workflow_id": self.parent.parent.workflow_id,
            "execution_id": self.parent.parent.execution_id,
            "step_id": self.parent.step_id,
            "event": "sandbox_imports_validated",
        }

        message = "Imports validated ✓"

        self.parent.parent.parent._log(LogLevel.DEBUG, "sandbox", message, context)

    def completed(self, duration_ms: int, tool_calls: int = 0) -> None:
        """Log sandbox completion.

        Args:
            duration_ms: Execution duration in milliseconds
            tool_calls: Number of tool calls made
        """
        context = {
            "workflow_id": self.parent.parent.workflow_id,
            "execution_id": self.parent.parent.execution_id,
            "step_id": self.parent.step_id,
            "event": "sandbox_completed",
            "duration_ms": duration_ms,
            "tool_calls": tool_calls,
        }

        duration_s = duration_ms / 1000
        message = f"Sandbox execution completed ({duration_s:.2f}s, {tool_calls} tool calls) ✓"

        self.parent.parent.parent._log(LogLevel.INFO, "sandbox", message, context)

    def error(self, error_type: str, message_text: str) -> None:
        """Log sandbox error.

        Args:
            error_type: Type of error (e.g., SecurityError, SyntaxError)
            message_text: Error message
        """
        context = {
            "workflow_id": self.parent.parent.workflow_id,
            "execution_id": self.parent.parent.execution_id,
            "step_id": self.parent.step_id,
            "event": "sandbox_error",
            "error_type": error_type,
            "error": message_text,
        }

        message = f"Sandbox error ({error_type}): {message_text}"

        self.parent.parent.parent._log(LogLevel.ERROR, "sandbox", message, context)

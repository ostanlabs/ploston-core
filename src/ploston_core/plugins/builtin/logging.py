"""Logging plugin for AEL.

This plugin logs all hook events for debugging and auditing.
"""

import logging
import re
from typing import Any

from ..base import AELPlugin
from ..types import (
    RequestContext,
    ResponseContext,
    StepContext,
    StepResultContext,
)

# Keys whose values must never be written to logs (workflow inputs/params/outputs
# routinely carry credentials/PII). Matched case-insensitively against key names.
_SECRET_KEY_RE = re.compile(
    r"(token|secret|password|passwd|credential|api[_-]?key|apikey|access[_-]?key|"
    r"private[_-]?key|client[_-]?secret|auth)",
    re.IGNORECASE,
)
_REDACTED = "***"


def _redact(value: Any) -> Any:
    """Return a copy of ``value`` with secret-keyed entries redacted (recursively)."""
    if isinstance(value, dict):
        return {
            k: (_REDACTED if _SECRET_KEY_RE.search(str(k)) else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


class LoggingPlugin(AELPlugin):
    """Plugin that logs all workflow execution events.

    Configuration options:
        level: Log level (DEBUG, INFO, WARNING, ERROR). Default: INFO
        include_params: Whether to log step parameters. Default: True
        include_outputs: Whether to log step outputs. Default: False
        logger_name: Name of the logger to use. Default: ael.plugins.logging
    """

    name = "logging"
    priority = 10  # Run early to capture all events

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self._level = getattr(logging, self.config.get("level", "INFO").upper(), logging.INFO)
        self._include_params = self.config.get("include_params", True)
        self._include_outputs = self.config.get("include_outputs", False)
        logger_name = self.config.get("logger_name", "ael.plugins.logging")
        self._logger = logging.getLogger(logger_name)

    def on_request_received(self, context: RequestContext) -> RequestContext:
        """Log workflow execution request."""
        msg = f"[{context.execution_id}] Workflow request: {context.workflow_id}"
        if self._include_params:
            msg += f" inputs={_redact(context.inputs)}"
        self._logger.log(self._level, msg)
        return context

    def on_step_before(self, context: StepContext) -> StepContext:
        """Log step execution start."""
        msg = (
            f"[{context.execution_id}] Step {context.step_index + 1}/{context.total_steps}: "
            f"{context.step_id} ({context.step_type})"
        )
        if context.tool_name:
            msg += f" tool={context.tool_name}"
        if self._include_params:
            msg += f" params={_redact(context.params)}"
        self._logger.log(self._level, msg)
        return context

    def on_step_after(self, context: StepResultContext) -> StepResultContext:
        """Log step execution result."""
        status = "SUCCESS" if context.success else "FAILED"
        msg = f"[{context.execution_id}] Step {context.step_id}: {status} ({context.duration_ms}ms)"
        if not context.success and context.error:
            msg += f" error={context.error}"
        if self._include_outputs and context.success:
            msg += f" output={_redact(context.output)}"
        self._logger.log(self._level, msg)
        return context

    def on_response_ready(self, context: ResponseContext) -> ResponseContext:
        """Log workflow execution result."""
        status = "SUCCESS" if context.success else "FAILED"
        msg = (
            f"[{context.execution_id}] Workflow {context.workflow_id}: {status} "
            f"({context.step_count} steps, {context.duration_ms}ms)"
        )
        if not context.success and context.error:
            msg += f" error={context.error}"
        if self._include_outputs and context.success:
            msg += f" outputs={_redact(context.outputs)}"
        self._logger.log(self._level, msg)
        return context

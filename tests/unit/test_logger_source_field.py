"""Tests for source field on all logger events (T-705 Part 1).

Verifies that all WorkflowLogger, StepLogger, ToolLogger, and SandboxLogger
events include source="workflow" in their context dicts.
"""

import json
from io import StringIO

import pytest

from ploston_core.logging.logger import AELLogger, LogConfig
from ploston_core.types import LogFormat, LogLevel


@pytest.fixture
def json_logger():
    """Create an AELLogger configured for JSON output with captured output."""
    output = StringIO()
    config = LogConfig(level=LogLevel.DEBUG, format=LogFormat.JSON, output=output)
    logger = AELLogger(config)
    return logger, output


def _parse_last_log(output: StringIO) -> dict:
    """Parse the last JSON log line from output."""
    lines = output.getvalue().strip().split("\n")
    return json.loads(lines[-1])


class TestWorkflowLoggerSourceField:
    """WorkflowLogger events must have source='workflow'."""

    def test_started_has_source_workflow(self, json_logger):
        logger, output = json_logger
        wl = logger.workflow("wf-1", "exec-1")
        wl.started()
        data = _parse_last_log(output)
        assert data["source"] == "workflow"
        assert data["event"] == "workflow_started"

    def test_completed_has_source_workflow(self, json_logger):
        logger, output = json_logger
        wl = logger.workflow("wf-1", "exec-1")
        wl.completed(duration_ms=100, step_count=2)
        data = _parse_last_log(output)
        assert data["source"] == "workflow"
        assert data["event"] == "workflow_completed"

    def test_failed_has_source_workflow(self, json_logger):
        logger, output = json_logger
        wl = logger.workflow("wf-1", "exec-1")
        wl.failed(error=RuntimeError("boom"), duration_ms=50)
        data = _parse_last_log(output)
        assert data["source"] == "workflow"
        assert data["event"] == "workflow_failed"


class TestStepLoggerSourceField:
    """StepLogger events must have source='workflow'."""

    def test_started_has_source_workflow(self, json_logger):
        logger, output = json_logger
        sl = logger.workflow("wf-1", "exec-1").step("step-1")
        sl.started("tool", tool_name="http_request")
        data = _parse_last_log(output)
        assert data["source"] == "workflow"
        assert data["event"] == "step_started"

    def test_completed_has_source_workflow(self, json_logger):
        logger, output = json_logger
        sl = logger.workflow("wf-1", "exec-1").step("step-1")
        sl.completed(duration_ms=200)
        data = _parse_last_log(output)
        assert data["source"] == "workflow"

    def test_skipped_has_source_workflow(self, json_logger):
        logger, output = json_logger
        sl = logger.workflow("wf-1", "exec-1").step("step-1")
        sl.skipped("condition not met")
        data = _parse_last_log(output)
        assert data["source"] == "workflow"

    def test_failed_has_source_workflow(self, json_logger):
        logger, output = json_logger
        sl = logger.workflow("wf-1", "exec-1").step("step-1")
        sl.failed(RuntimeError("step error"))
        data = _parse_last_log(output)
        assert data["source"] == "workflow"
        assert data["event"] == "step_failed"

    def test_retrying_has_source_workflow(self, json_logger):
        logger, output = json_logger
        sl = logger.workflow("wf-1", "exec-1").step("step-1")
        sl.retrying(attempt=1, max_attempts=3, delay_seconds=1.0)
        data = _parse_last_log(output)
        assert data["source"] == "workflow"


class TestToolLoggerSourceField:
    """ToolLogger (within step) events must have source='workflow'."""

    def test_calling_has_source_workflow(self, json_logger):
        logger, output = json_logger
        tl = logger.workflow("wf-1", "exec-1").step("step-1").tool()
        tl.calling("http_request")
        data = _parse_last_log(output)
        assert data["source"] == "workflow"
        assert data["event"] == "tool_calling"

    def test_result_has_source_workflow(self, json_logger):
        logger, output = json_logger
        tl = logger.workflow("wf-1", "exec-1").step("step-1").tool()
        tl.result("http_request", {"status": 200}, duration_ms=150)
        data = _parse_last_log(output)
        assert data["source"] == "workflow"

    def test_error_has_source_workflow(self, json_logger):
        logger, output = json_logger
        tl = logger.workflow("wf-1", "exec-1").step("step-1").tool()
        tl.error("http_request", "Connection refused", duration_ms=50)
        data = _parse_last_log(output)
        assert data["source"] == "workflow"
        assert data["event"] == "tool_error"


class TestSandboxLoggerSourceField:
    """SandboxLogger events must have source='workflow'."""

    def test_executing_has_source_workflow(self, json_logger):
        logger, output = json_logger
        sb = logger.workflow("wf-1", "exec-1").step("step-1").sandbox()
        sb.executing()
        data = _parse_last_log(output)
        assert data["source"] == "workflow"

    def test_imports_validated_has_source_workflow(self, json_logger):
        logger, output = json_logger
        sb = logger.workflow("wf-1", "exec-1").step("step-1").sandbox()
        sb.imports_validated()
        data = _parse_last_log(output)
        assert data["source"] == "workflow"

    def test_completed_has_source_workflow(self, json_logger):
        logger, output = json_logger
        sb = logger.workflow("wf-1", "exec-1").step("step-1").sandbox()
        sb.completed(duration_ms=300, tool_calls=2)
        data = _parse_last_log(output)
        assert data["source"] == "workflow"

    def test_error_has_source_workflow(self, json_logger):
        logger, output = json_logger
        sb = logger.workflow("wf-1", "exec-1").step("step-1").sandbox()
        sb.error("SecurityError", "Forbidden import")
        data = _parse_last_log(output)
        assert data["source"] == "workflow"
        assert data["event"] == "sandbox_error"

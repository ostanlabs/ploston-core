"""Specification tests for the builtin LoggingPlugin.

Asserts the plugin emits correct, well-formed log records for each hook, honors
its config toggles, and behaves as an observe-only plugin (returns the context
unchanged). Also includes a SECURITY spec test for the prior-audit finding that
the logging plugin may log secrets.

Where the implementation violates the intended contract, the test is allowed to
fail (RED) and is reported as a finding.
"""

import logging

import pytest

from ploston_core.plugins.builtin.logging import LoggingPlugin
from ploston_core.plugins.types import (
    RequestContext,
    ResponseContext,
    StepContext,
    StepResultContext,
)

LOGGER_NAME = "ael.plugins.logging.spec"


def make_plugin(**config):
    config.setdefault("logger_name", LOGGER_NAME)
    config.setdefault("level", "INFO")
    return LoggingPlugin(config)


# ---------------------------------------------------------------------------
# Construction / config
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_defaults(self):
        p = LoggingPlugin()
        assert p._level == logging.INFO
        assert p._include_params is True
        assert p._include_outputs is False

    def test_level_parsing_case_insensitive(self):
        p = LoggingPlugin({"level": "debug"})
        assert p._level == logging.DEBUG

    def test_invalid_level_falls_back_to_info(self):
        p = LoggingPlugin({"level": "NOPE"})
        assert p._level == logging.INFO

    def test_class_priority_runs_early(self):
        # Documented: priority 10 so logging captures all events early.
        assert LoggingPlugin.priority == 10


# ---------------------------------------------------------------------------
# Emission correctness
# ---------------------------------------------------------------------------


class TestRequestLogging:
    def test_logs_request_at_configured_level(self, caplog):
        p = make_plugin(level="INFO")
        ctx = RequestContext(workflow_id="wf-1", inputs={"x": 1}, execution_id="exec-7")
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            ret = p.on_request_received(ctx)
        assert ret is ctx  # observe-only
        rec = [r for r in caplog.records if r.name == LOGGER_NAME]
        assert len(rec) == 1
        assert rec[0].levelno == logging.INFO
        assert "exec-7" in rec[0].getMessage()
        assert "wf-1" in rec[0].getMessage()

    def test_include_params_false_omits_inputs(self, caplog):
        p = make_plugin(include_params=False)
        ctx = RequestContext(workflow_id="wf", inputs={"x": 1}, execution_id="e")
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            p.on_request_received(ctx)
        msg = caplog.records[-1].getMessage()
        assert "inputs=" not in msg


class TestStepLogging:
    def test_step_before_logs_index_and_type(self, caplog):
        p = make_plugin()
        ctx = StepContext(
            workflow_id="wf",
            execution_id="e",
            step_id="s1",
            step_type="tool",
            tool_name="search",
            params={"q": "hi"},
            step_index=0,
            total_steps=3,
        )
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            ret = p.on_step_before(ctx)
        assert ret is ctx
        msg = caplog.records[-1].getMessage()
        assert "1/3" in msg  # human-readable 1-based index
        assert "s1" in msg
        assert "tool" in msg
        assert "search" in msg

    def test_step_after_success_status(self, caplog):
        p = make_plugin()
        ctx = StepResultContext(
            workflow_id="wf",
            execution_id="e",
            step_id="s1",
            step_type="tool",
            tool_name="t",
            params={},
            output={"r": 1},
            success=True,
            duration_ms=42,
        )
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            p.on_step_after(ctx)
        msg = caplog.records[-1].getMessage()
        assert "SUCCESS" in msg
        assert "42ms" in msg

    def test_step_after_failure_includes_error(self, caplog):
        p = make_plugin()
        ctx = StepResultContext(
            workflow_id="wf",
            execution_id="e",
            step_id="s1",
            step_type="tool",
            tool_name="t",
            params={},
            output=None,
            success=False,
            error=ValueError("kaboom"),
            duration_ms=5,
        )
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            p.on_step_after(ctx)
        msg = caplog.records[-1].getMessage()
        assert "FAILED" in msg
        assert "kaboom" in msg

    def test_step_after_outputs_suppressed_by_default(self, caplog):
        p = make_plugin()  # include_outputs default False
        ctx = StepResultContext(
            workflow_id="wf",
            execution_id="e",
            step_id="s1",
            step_type="tool",
            tool_name="t",
            params={},
            output={"secret_output": "leak"},
            success=True,
        )
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            p.on_step_after(ctx)
        assert "secret_output" not in caplog.records[-1].getMessage()


class TestIncludeOutputs:
    def test_step_after_includes_output_when_enabled(self, caplog):
        p = make_plugin(include_outputs=True)
        ctx = StepResultContext(
            workflow_id="wf",
            execution_id="e",
            step_id="s1",
            step_type="tool",
            tool_name="t",
            params={},
            output={"answer": 42},
            success=True,
        )
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            p.on_step_after(ctx)
        assert "answer" in caplog.records[-1].getMessage()

    def test_response_includes_outputs_when_enabled(self, caplog):
        p = make_plugin(include_outputs=True)
        ctx = ResponseContext(
            workflow_id="wf",
            execution_id="e",
            inputs={},
            outputs={"final": "value"},
            success=True,
        )
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            p.on_response_ready(ctx)
        assert "final" in caplog.records[-1].getMessage()

    def test_response_failure_includes_error(self, caplog):
        p = make_plugin()
        ctx = ResponseContext(
            workflow_id="wf",
            execution_id="e",
            inputs={},
            outputs={},
            success=False,
            error=RuntimeError("workflow blew up"),
        )
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            p.on_response_ready(ctx)
        msg = caplog.records[-1].getMessage()
        assert "FAILED" in msg
        assert "workflow blew up" in msg


class TestResponseLogging:
    def test_response_success(self, caplog):
        p = make_plugin()
        ctx = ResponseContext(
            workflow_id="wf",
            execution_id="e",
            inputs={},
            outputs={"o": 1},
            success=True,
            step_count=4,
            duration_ms=100,
        )
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            ret = p.on_response_ready(ctx)
        assert ret is ctx
        msg = caplog.records[-1].getMessage()
        assert "SUCCESS" in msg
        assert "4 steps" in msg
        assert "100ms" in msg


# ---------------------------------------------------------------------------
# SECURITY — secrets must not be logged
# ---------------------------------------------------------------------------


class TestSecretRedaction:
    """Prior audit flag: the logging plugin may log secrets.

    Intended contract for an audit/observability plugin: sensitive values in
    inputs/params (api keys, passwords, tokens) MUST be redacted before being
    written to logs. These tests assert redaction; if the plugin logs raw
    secrets they will FAIL and surface the defect.
    """

    SECRET = "sk-supersecret-DEADBEEF"

    def test_request_inputs_secret_is_redacted(self, caplog):
        p = make_plugin(include_params=True)
        ctx = RequestContext(
            workflow_id="wf",
            inputs={"api_key": self.SECRET, "user": "alice"},
            execution_id="e",
        )
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            p.on_request_received(ctx)
        msg = caplog.records[-1].getMessage()
        assert self.SECRET not in msg, (
            "Logging plugin leaked a secret from request inputs into logs. "
            "Sensitive keys must be redacted."
        )

    def test_step_params_secret_is_redacted(self, caplog):
        p = make_plugin(include_params=True)
        ctx = StepContext(
            workflow_id="wf",
            execution_id="e",
            step_id="s1",
            step_type="tool",
            tool_name="t",
            params={"password": self.SECRET},
        )
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            p.on_step_before(ctx)
        msg = caplog.records[-1].getMessage()
        assert self.SECRET not in msg, "Logging plugin leaked a secret from step params into logs."


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))

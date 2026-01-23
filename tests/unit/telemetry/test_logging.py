"""Tests for AEL structured logging with trace context."""

import json
import logging
from io import StringIO

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from ploston_core.telemetry.logging import (
    AELLogger,
    StructuredLogFormatter,
    get_logger,
    reset_loggers,
)


@pytest.fixture(autouse=True)
def reset_logger_state():
    """Reset logger state before and after each test."""
    reset_loggers()
    yield
    reset_loggers()


class TestStructuredLogFormatter:
    """Tests for StructuredLogFormatter."""

    def test_formats_as_json(self):
        """Test log record is formatted as JSON."""
        formatter = StructuredLogFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["message"] == "Test message"
        assert data["level"] == "INFO"
        assert data["component"] == "test"

    def test_includes_timestamp(self):
        """Test log includes ISO timestamp."""
        formatter = StructuredLogFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert "timestamp" in data
        # ISO format check
        assert "T" in data["timestamp"]

    def test_includes_extra_fields(self):
        """Test extra fields are included in output."""
        formatter = StructuredLogFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )
        record.workflow_id = "wf-123"
        record.step_id = "step-1"
        output = formatter.format(record)
        data = json.loads(output)
        assert data["workflow_id"] == "wf-123"
        assert data["step_id"] == "step-1"

    def test_excludes_standard_log_fields(self):
        """Test standard log fields are not duplicated."""
        formatter = StructuredLogFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        # These should not be in output
        assert "lineno" not in data
        assert "pathname" not in data
        assert "funcName" not in data


class TestAELLogger:
    """Tests for AELLogger."""

    def test_creates_logger_with_ael_prefix(self):
        """Test logger name has ael. prefix."""
        logger = AELLogger("test_component")
        assert logger._logger.name == "ael.test_component"

    def _capture_log(self, logger_name: str, log_func, *args, **kwargs):
        """Helper to capture log output."""
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(StructuredLogFormatter())

        py_logger = logging.getLogger(f"ael.{logger_name}")
        py_logger.handlers = [handler]
        py_logger.setLevel(logging.DEBUG)

        log_func(*args, **kwargs)

        return stream.getvalue()

    def test_info_logs_at_info_level(self):
        """Test info() logs at INFO level."""
        logger = AELLogger("test_info")
        output = self._capture_log("test_info", logger.info, "Test message")
        data = json.loads(output)
        assert data["level"] == "INFO"
        assert data["message"] == "Test message"

    def test_error_logs_at_error_level(self):
        """Test error() logs at ERROR level."""
        logger = AELLogger("test_error")
        output = self._capture_log("test_error", logger.error, "Error message")
        data = json.loads(output)
        assert data["level"] == "ERROR"

    def test_warning_logs_at_warning_level(self):
        """Test warning() logs at WARNING level."""
        logger = AELLogger("test_warning")
        output = self._capture_log("test_warning", logger.warning, "Warning message")
        data = json.loads(output)
        assert data["level"] == "WARNING"

    def test_debug_logs_at_debug_level(self):
        """Test debug() logs at DEBUG level when enabled."""
        logger = AELLogger("test_debug", level=logging.DEBUG)
        output = self._capture_log("test_debug", logger.debug, "Debug message")
        data = json.loads(output)
        assert data["level"] == "DEBUG"

    def test_extra_kwargs_included(self):
        """Test extra kwargs are included in log."""
        logger = AELLogger("test_extra")
        output = self._capture_log(
            "test_extra", lambda: logger.info("Test", workflow_id="wf-123", tool_name="read_file")
        )
        data = json.loads(output)
        assert data["workflow_id"] == "wf-123"
        assert data["tool_name"] == "read_file"


class TestGetLogger:
    """Tests for get_logger factory function."""

    def test_returns_ael_logger(self):
        """Test get_logger returns AELLogger instance."""
        logger = get_logger("test")
        assert isinstance(logger, AELLogger)

    def test_caches_loggers(self):
        """Test same logger is returned for same name."""
        logger1 = get_logger("test")
        logger2 = get_logger("test")
        assert logger1 is logger2

    def test_different_names_different_loggers(self):
        """Test different names return different loggers."""
        logger1 = get_logger("test1")
        logger2 = get_logger("test2")
        assert logger1 is not logger2


class TestTraceContextInjection:
    """Tests for trace context injection in logs."""

    def test_no_trace_context_when_no_span(self):
        """Test no trace_id/span_id when no active span."""
        formatter = StructuredLogFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert "trace_id" not in data
        assert "span_id" not in data

    def test_trace_context_injected_with_active_span(self):
        """Test trace_id/span_id injected when span is active."""
        # Set up tracer provider
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
        tracer = trace.get_tracer("test")

        formatter = StructuredLogFormatter()

        with tracer.start_as_current_span("test_span"):
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="test.py",
                lineno=1,
                msg="Test with trace",
                args=(),
                exc_info=None,
            )
            output = formatter.format(record)

        data = json.loads(output)
        assert "trace_id" in data
        assert "span_id" in data
        # Verify format (32 hex chars for trace_id, 16 for span_id)
        assert len(data["trace_id"]) == 32
        assert len(data["span_id"]) == 16

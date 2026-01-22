"""Unit tests for AEL telemetry metrics."""

import pytest
from unittest.mock import MagicMock, patch

from ploston_core.telemetry import (
    AELMetrics,
    MetricLabels,
    TelemetryConfig,
    get_telemetry,
    reset_telemetry,
    setup_telemetry,
)


class TestTelemetryConfig:
    """Tests for TelemetryConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = TelemetryConfig()
        assert config.enabled is True
        assert config.service_name == "ael"
        assert config.service_version == "1.0.0"
        assert config.metrics_enabled is True
        assert config.traces_enabled is False

    def test_custom_config(self):
        """Test custom configuration values."""
        config = TelemetryConfig(
            enabled=True,
            service_name="custom-ael",
            service_version="2.0.0",
            metrics_enabled=True,
            traces_enabled=True,
        )
        assert config.service_name == "custom-ael"
        assert config.service_version == "2.0.0"
        assert config.traces_enabled is True


class TestTelemetrySetup:
    """Tests for telemetry setup."""

    def setup_method(self):
        """Reset telemetry before each test."""
        reset_telemetry()

    def teardown_method(self):
        """Reset telemetry after each test."""
        reset_telemetry()

    def test_setup_returns_telemetry_dict(self):
        """Test that setup_telemetry returns a dictionary."""
        telemetry = setup_telemetry()
        assert isinstance(telemetry, dict)
        assert "meter" in telemetry
        assert "tracer" in telemetry
        assert "metrics" in telemetry
        assert "config" in telemetry

    def test_setup_creates_ael_metrics(self):
        """Test that setup creates AELMetrics instance."""
        telemetry = setup_telemetry()
        assert isinstance(telemetry["metrics"], AELMetrics)

    def test_setup_is_idempotent(self):
        """Test that calling setup multiple times returns same instance."""
        telemetry1 = setup_telemetry()
        telemetry2 = setup_telemetry()
        assert telemetry1 is telemetry2

    def test_get_telemetry_returns_none_before_setup(self):
        """Test that get_telemetry returns None before setup."""
        assert get_telemetry() is None

    def test_get_telemetry_returns_instance_after_setup(self):
        """Test that get_telemetry returns instance after setup."""
        setup_telemetry()
        assert get_telemetry() is not None

    def test_disabled_telemetry_returns_none_metrics(self):
        """Test that disabled telemetry returns None for metrics."""
        config = TelemetryConfig(enabled=False)
        telemetry = setup_telemetry(config)
        assert telemetry["metrics"] is None


class TestAELMetrics:
    """Tests for AELMetrics class."""

    def setup_method(self):
        """Reset telemetry and create metrics instance."""
        reset_telemetry()
        self.telemetry = setup_telemetry()
        self.metrics = self.telemetry["metrics"]

    def teardown_method(self):
        """Reset telemetry after each test."""
        reset_telemetry()

    def test_metrics_has_workflow_counter(self):
        """Test that metrics has workflow executions counter."""
        assert hasattr(self.metrics, "workflow_executions_total")

    def test_metrics_has_step_counter(self):
        """Test that metrics has step executions counter."""
        assert hasattr(self.metrics, "step_executions_total")

    def test_metrics_has_tool_counter(self):
        """Test that metrics has tool invocations counter."""
        assert hasattr(self.metrics, "tool_invocations_total")

    def test_metrics_has_workflow_histogram(self):
        """Test that metrics has workflow duration histogram."""
        assert hasattr(self.metrics, "workflow_duration_seconds")

    def test_metrics_has_step_histogram(self):
        """Test that metrics has step duration histogram."""
        assert hasattr(self.metrics, "step_duration_seconds")

    def test_metrics_has_tool_histogram(self):
        """Test that metrics has tool invocation duration histogram."""
        assert hasattr(self.metrics, "tool_invocation_duration_seconds")

    def test_metrics_has_active_workflows_gauge(self):
        """Test that metrics has active workflows gauge."""
        assert hasattr(self.metrics, "active_workflows")

    def test_metrics_has_registered_tools_gauge(self):
        """Test that metrics has registered tools gauge."""
        assert hasattr(self.metrics, "registered_tools")

    def test_record_workflow_start(self):
        """Test recording workflow start."""
        # Should not raise
        self.metrics.record_workflow_start("test-workflow")

    def test_record_workflow_end_success(self):
        """Test recording successful workflow end."""
        # Should not raise
        self.metrics.record_workflow_end(
            workflow_id="test-workflow",
            duration_seconds=1.5,
            status=MetricLabels.STATUS_SUCCESS,
        )

    def test_record_workflow_end_error(self):
        """Test recording failed workflow end."""
        # Should not raise
        self.metrics.record_workflow_end(
            workflow_id="test-workflow",
            duration_seconds=0.5,
            status=MetricLabels.STATUS_ERROR,
            error_code="TestError",
        )

    def test_record_step_execution(self):
        """Test recording step execution."""
        # Should not raise
        self.metrics.record_step_execution(
            workflow_id="test-workflow",
            step_id="step-1",
            duration_seconds=0.1,
            status=MetricLabels.STATUS_SUCCESS,
        )

    def test_record_tool_invocation(self):
        """Test recording tool invocation."""
        # Should not raise
        self.metrics.record_tool_invocation(
            tool_name="test-tool",
            duration_seconds=0.05,
            status=MetricLabels.STATUS_SUCCESS,
        )


class TestMetricLabels:
    """Tests for MetricLabels constants."""

    def test_workflow_labels(self):
        """Test workflow label constants."""
        assert MetricLabels.WORKFLOW_ID == "workflow_id"
        assert MetricLabels.STEP_ID == "step_id"

    def test_tool_labels(self):
        """Test tool label constants."""
        assert MetricLabels.TOOL_NAME == "tool_name"
        assert MetricLabels.TOOL_SERVER == "tool_server"

    def test_status_labels(self):
        """Test status label constants."""
        assert MetricLabels.STATUS == "status"
        assert MetricLabels.ERROR_CODE == "error_code"

    def test_status_values(self):
        """Test status value constants."""
        assert MetricLabels.STATUS_SUCCESS == "success"
        assert MetricLabels.STATUS_ERROR == "error"
        assert MetricLabels.STATUS_TIMEOUT == "timeout"
        assert MetricLabels.STATUS_CANCELLED == "cancelled"

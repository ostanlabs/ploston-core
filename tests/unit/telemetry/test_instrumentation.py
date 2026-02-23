"""Unit tests for AEL telemetry instrumentation."""

import pytest

from ploston_core.telemetry import (
    MetricLabels,
    instrument_step,
    instrument_tool_call,
    instrument_workflow,
    record_tool_result,
    reset_telemetry,
    setup_telemetry,
)


class TestInstrumentWorkflow:
    """Tests for instrument_workflow context manager."""

    def setup_method(self):
        """Reset telemetry before each test."""
        reset_telemetry()
        setup_telemetry()

    def teardown_method(self):
        """Reset telemetry after each test."""
        reset_telemetry()

    @pytest.mark.asyncio
    async def test_instrument_workflow_yields_result_dict(self):
        """Test that instrument_workflow yields a result dictionary."""
        async with instrument_workflow("test-workflow") as result:
            assert isinstance(result, dict)
            assert "status" in result
            assert result["status"] == MetricLabels.STATUS_SUCCESS

    @pytest.mark.asyncio
    async def test_instrument_workflow_success(self):
        """Test successful workflow instrumentation."""
        async with instrument_workflow("test-workflow") as result:
            # Simulate successful execution
            pass
        # Result should still be success
        assert result["status"] == MetricLabels.STATUS_SUCCESS

    @pytest.mark.asyncio
    async def test_instrument_workflow_error(self):
        """Test workflow instrumentation on error."""
        with pytest.raises(ValueError):
            async with instrument_workflow("test-workflow") as result:
                raise ValueError("Test error")
        # Result should be error
        assert result["status"] == MetricLabels.STATUS_ERROR
        assert result["error_code"] == "ValueError"


class TestInstrumentStep:
    """Tests for instrument_step context manager."""

    def setup_method(self):
        """Reset telemetry before each test."""
        reset_telemetry()
        setup_telemetry()

    def teardown_method(self):
        """Reset telemetry after each test."""
        reset_telemetry()

    @pytest.mark.asyncio
    async def test_instrument_step_yields_result_dict(self):
        """Test that instrument_step yields a result dictionary."""
        async with instrument_step("test-workflow", "step-1") as result:
            assert isinstance(result, dict)
            assert "status" in result

    @pytest.mark.asyncio
    async def test_instrument_step_success(self):
        """Test successful step instrumentation."""
        async with instrument_step("test-workflow", "step-1") as result:
            pass
        assert result["status"] == MetricLabels.STATUS_SUCCESS

    @pytest.mark.asyncio
    async def test_instrument_step_error(self):
        """Test step instrumentation on error."""
        with pytest.raises(RuntimeError):
            async with instrument_step("test-workflow", "step-1") as result:
                raise RuntimeError("Step failed")
        assert result["status"] == MetricLabels.STATUS_ERROR
        assert result["error_code"] == "RuntimeError"


class TestInstrumentToolCall:
    """Tests for instrument_tool_call context manager."""

    def setup_method(self):
        """Reset telemetry before each test."""
        reset_telemetry()
        setup_telemetry()

    def teardown_method(self):
        """Reset telemetry after each test."""
        reset_telemetry()

    @pytest.mark.asyncio
    async def test_instrument_tool_call_yields_result_dict(self):
        """Test that instrument_tool_call yields a result dictionary."""
        async with instrument_tool_call("test-tool") as result:
            assert isinstance(result, dict)
            assert "status" in result

    @pytest.mark.asyncio
    async def test_instrument_tool_call_success(self):
        """Test successful tool call instrumentation."""
        async with instrument_tool_call("test-tool") as result:
            pass
        assert result["status"] == MetricLabels.STATUS_SUCCESS

    @pytest.mark.asyncio
    async def test_instrument_tool_call_error(self):
        """Test tool call instrumentation on error."""
        with pytest.raises(TimeoutError):
            async with instrument_tool_call("test-tool") as result:
                raise TimeoutError("Tool timed out")
        assert result["status"] == MetricLabels.STATUS_ERROR
        assert result["error_code"] == "TimeoutError"

    @pytest.mark.asyncio
    async def test_instrument_tool_call_with_source(self):
        """Test tool call instrumentation with source parameter."""
        async with instrument_tool_call("test-tool", source="native") as result:
            pass
        assert result["status"] == MetricLabels.STATUS_SUCCESS
        assert result.get("source") == "native"

    @pytest.mark.asyncio
    async def test_instrument_tool_call_with_all_source_types(self):
        """Test tool call instrumentation with all source types."""
        for source in ["native", "local", "system", "configured"]:
            async with instrument_tool_call(f"test-tool-{source}", source=source) as result:
                pass
            assert result["status"] == MetricLabels.STATUS_SUCCESS
            assert result.get("source") == source

    @pytest.mark.asyncio
    async def test_instrument_tool_call_source_on_error(self):
        """Test that source is preserved on error."""
        with pytest.raises(ValueError):
            async with instrument_tool_call("test-tool", source="system") as result:
                raise ValueError("Test error")
        assert result["status"] == MetricLabels.STATUS_ERROR
        assert result.get("source") == "system"


class TestRecordToolResult:
    """Tests for record_tool_result helper."""

    def test_record_success(self):
        """Test recording successful result."""
        result = {"status": MetricLabels.STATUS_ERROR, "error_code": "SomeError"}
        record_tool_result(result, success=True)
        assert result["status"] == MetricLabels.STATUS_SUCCESS

    def test_record_error(self):
        """Test recording error result."""
        result = {"status": MetricLabels.STATUS_SUCCESS, "error_code": None}
        record_tool_result(result, success=False, error_code="TestError")
        assert result["status"] == MetricLabels.STATUS_ERROR
        assert result["error_code"] == "TestError"


class TestInstrumentationWithoutTelemetry:
    """Tests for instrumentation when telemetry is not initialized."""

    def setup_method(self):
        """Reset telemetry before each test."""
        reset_telemetry()

    def teardown_method(self):
        """Reset telemetry after each test."""
        reset_telemetry()

    @pytest.mark.asyncio
    async def test_instrument_workflow_without_telemetry(self):
        """Test that instrument_workflow works without telemetry."""
        # Should not raise even without telemetry
        async with instrument_workflow("test-workflow") as result:
            pass
        assert result["status"] == MetricLabels.STATUS_SUCCESS

    @pytest.mark.asyncio
    async def test_instrument_step_without_telemetry(self):
        """Test that instrument_step works without telemetry."""
        async with instrument_step("test-workflow", "step-1") as result:
            pass
        assert result["status"] == MetricLabels.STATUS_SUCCESS

    @pytest.mark.asyncio
    async def test_instrument_tool_call_without_telemetry(self):
        """Test that instrument_tool_call works without telemetry."""
        async with instrument_tool_call("test-tool") as result:
            pass
        assert result["status"] == MetricLabels.STATUS_SUCCESS

"""AEL Telemetry Instrumentation - decorators and helpers.

Provides instrumentation helpers for:
- Workflow execution
- Step execution
- Tool invocations

Uses OpenTelemetry context propagation for distributed tracing.
"""

import time
from contextlib import asynccontextmanager
from typing import Any

from opentelemetry.trace import Status, StatusCode

from .metrics import MetricLabels
from .setup import get_telemetry


@asynccontextmanager
async def instrument_workflow(workflow_id: str):
    """Context manager for instrumenting workflow execution.

    Records:
    - Workflow start/end metrics
    - Workflow duration histogram
    - Active workflow gauge
    - Trace span for workflow

    Args:
        workflow_id: Workflow identifier

    Yields:
        Dictionary to store execution status
    """
    telemetry = get_telemetry()
    start_time = time.time()
    result = {"status": MetricLabels.STATUS_SUCCESS, "error_code": None}

    # Get tracer
    tracer = telemetry["tracer"] if telemetry else None
    metrics = telemetry["metrics"] if telemetry else None

    # Start span
    span = None
    if tracer:
        span = tracer.start_span(f"workflow:{workflow_id}")
        span.set_attribute("workflow.id", workflow_id)

    # Record workflow start
    if metrics:
        metrics.record_workflow_start(workflow_id)

    try:
        yield result
    except Exception as e:
        result["status"] = MetricLabels.STATUS_ERROR
        result["error_code"] = type(e).__name__
        if span:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
        raise
    finally:
        duration = time.time() - start_time

        # Record workflow end
        if metrics:
            metrics.record_workflow_end(
                workflow_id=workflow_id,
                duration_seconds=duration,
                status=result["status"],
                error_code=result.get("error_code"),
            )

        # End span
        if span:
            if result["status"] == MetricLabels.STATUS_SUCCESS:
                span.set_status(Status(StatusCode.OK))
            span.end()


@asynccontextmanager
async def instrument_step(workflow_id: str, step_id: str):
    """Context manager for instrumenting step execution.

    Records:
    - Step execution counter
    - Step duration histogram
    - Trace span for step

    Args:
        workflow_id: Workflow identifier
        step_id: Step identifier

    Yields:
        Dictionary to store execution status
    """
    telemetry = get_telemetry()
    start_time = time.time()
    result = {"status": MetricLabels.STATUS_SUCCESS, "error_code": None}

    # Get tracer
    tracer = telemetry["tracer"] if telemetry else None
    metrics = telemetry["metrics"] if telemetry else None

    # Start span
    span = None
    if tracer:
        span = tracer.start_span(f"step:{step_id}")
        span.set_attribute("workflow.id", workflow_id)
        span.set_attribute("step.id", step_id)

    try:
        yield result
    except Exception as e:
        result["status"] = MetricLabels.STATUS_ERROR
        result["error_code"] = type(e).__name__
        if span:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
        raise
    finally:
        duration = time.time() - start_time

        # Record step execution
        if metrics:
            metrics.record_step_execution(
                workflow_id=workflow_id,
                step_id=step_id,
                duration_seconds=duration,
                status=result["status"],
                error_code=result.get("error_code"),
            )

        # End span
        if span:
            if result["status"] == MetricLabels.STATUS_SUCCESS:
                span.set_status(Status(StatusCode.OK))
            span.end()


@asynccontextmanager
async def instrument_tool_call(tool_name: str, source: str | None = None):
    """Context manager for instrumenting tool invocations.

    Records:
    - Tool invocation counter
    - Tool duration histogram
    - Trace span for tool call

    Args:
        tool_name: Tool name (server:tool format)
        source: Tool source category (native, local, system, configured)

    Yields:
        Dictionary to store execution status and source
    """
    telemetry = get_telemetry()
    start_time = time.time()
    result = {"status": MetricLabels.STATUS_SUCCESS, "error_code": None, "source": source}

    # Get tracer
    tracer = telemetry["tracer"] if telemetry else None
    metrics = telemetry["metrics"] if telemetry else None

    # Start span
    span = None
    if tracer:
        span = tracer.start_span(f"tool:{tool_name}")
        span.set_attribute("tool.name", tool_name)
        if source:
            span.set_attribute("tool.source", source)

    try:
        yield result
    except Exception as e:
        result["status"] = MetricLabels.STATUS_ERROR
        result["error_code"] = type(e).__name__
        if span:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
        raise
    finally:
        duration = time.time() - start_time

        # Record tool invocation
        if metrics:
            metrics.record_tool_invocation(
                tool_name=tool_name,
                duration_seconds=duration,
                status=result["status"],
                error_code=result.get("error_code"),
                source=result.get("source"),
            )

        # End span
        if span:
            if result["status"] == MetricLabels.STATUS_SUCCESS:
                span.set_status(Status(StatusCode.OK))
            span.end()


def record_tool_result(
    result: dict[str, Any], success: bool, error_code: str | None = None
) -> None:
    """Update result dictionary with execution status.

    Args:
        result: Result dictionary from context manager
        success: Whether execution succeeded
        error_code: Error code if failed
    """
    if success:
        result["status"] = MetricLabels.STATUS_SUCCESS
    else:
        result["status"] = MetricLabels.STATUS_ERROR
        result["error_code"] = error_code

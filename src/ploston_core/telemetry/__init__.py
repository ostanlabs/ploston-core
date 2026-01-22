"""AEL Telemetry - OpenTelemetry-based observability."""

from .metrics import AELMetrics, MetricLabels
from .setup import (
    TelemetryConfig,
    OTLPExporterConfig,
    setup_telemetry,
    get_telemetry,
    reset_telemetry,
)
from .instrumentation import (
    instrument_workflow,
    instrument_step,
    instrument_tool_call,
    record_tool_result,
)
from .logging import (
    AELLogger,
    StructuredLogFormatter,
    get_logger,
    reset_loggers,
)

__all__ = [
    # Metrics
    "AELMetrics",
    "MetricLabels",
    # Setup
    "TelemetryConfig",
    "OTLPExporterConfig",
    "setup_telemetry",
    "get_telemetry",
    "reset_telemetry",
    # Instrumentation
    "instrument_workflow",
    "instrument_step",
    "instrument_tool_call",
    "record_tool_result",
    # Logging
    "AELLogger",
    "StructuredLogFormatter",
    "get_logger",
    "reset_loggers",
]

"""AEL Telemetry - OpenTelemetry-based observability."""

from .chain_detector import ChainDetector, ChainLink, InMemoryChainCache
from .instrumentation import (
    instrument_step,
    instrument_tool_call,
    instrument_workflow,
    record_tool_result,
)
from .logging import (
    AELLogger,
    StructuredLogFormatter,
    get_logger,
    reset_loggers,
)
from .metrics import AELMetrics, MetricLabels
from .setup import (
    OTLPExporterConfig,
    TelemetryConfig,
    get_telemetry,
    reset_telemetry,
    setup_telemetry,
)
from .token_estimator import (
    DEFAULT_PRICING,
    TokenEstimationConfig,
    TokenEstimator,
    TokenSavingsResult,
)

__all__ = [
    # Metrics
    "AELMetrics",
    "MetricLabels",
    # Token Estimation
    "TokenEstimator",
    "TokenEstimationConfig",
    "TokenSavingsResult",
    "DEFAULT_PRICING",
    # Chain Detection
    "ChainDetector",
    "ChainLink",
    "InMemoryChainCache",
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

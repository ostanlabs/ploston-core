"""AEL Telemetry Setup - OpenTelemetry initialization.

Configures OpenTelemetry SDK with:
- MeterProvider with PrometheusMetricReader
- TracerProvider with optional OTLP exporter
- LoggerProvider with optional OTLP exporter

Supports:
- Prometheus metrics export (/metrics endpoint)
- OTLP trace export to Tempo
- OTLP log export to Loki
"""

from dataclasses import dataclass, field
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from .metrics import AELMetrics


@dataclass
class OTLPExporterConfig:
    """OTLP exporter configuration.

    Attributes:
        enabled: Whether OTLP export is enabled
        endpoint: OTLP collector endpoint
        insecure: Whether to use insecure connection
        protocol: Protocol (grpc or http)
        headers: Additional headers
    """

    enabled: bool = False
    endpoint: str = "http://localhost:4317"
    insecure: bool = True
    protocol: str = "grpc"  # grpc | http
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class TelemetryConfig:
    """Telemetry configuration.

    Attributes:
        enabled: Whether telemetry is enabled
        service_name: Service name for telemetry
        service_version: Service version
        metrics_port: Port for /metrics endpoint (Prometheus)
        metrics_enabled: Whether metrics are enabled
        traces_enabled: Whether traces are enabled
        logs_enabled: Whether logs are enabled
        otlp: OTLP exporter configuration
    """

    enabled: bool = True
    service_name: str = "ael"
    service_version: str = "1.0.0"
    metrics_port: int = 9090
    metrics_enabled: bool = True
    traces_enabled: bool = False
    traces_sample_rate: float = 1.0
    logs_enabled: bool = False
    otlp: OTLPExporterConfig = field(default_factory=OTLPExporterConfig)

    # Additional attributes for OTEL resource
    attributes: dict[str, str] = field(default_factory=dict)


# Global telemetry state
_telemetry: dict[str, Any] | None = None


def _create_otlp_span_exporter(otlp_config: OTLPExporterConfig):
    """Create OTLP span exporter based on configuration.

    Args:
        otlp_config: OTLP exporter configuration

    Returns:
        OTLP span exporter instance
    """
    if otlp_config.protocol == "http":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        return OTLPSpanExporter(
            endpoint=f"{otlp_config.endpoint}/v1/traces",
            headers=otlp_config.headers or None,
        )
    else:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        return OTLPSpanExporter(
            endpoint=otlp_config.endpoint,
            insecure=otlp_config.insecure,
            headers=otlp_config.headers or None,
        )


def _create_otlp_log_exporter(otlp_config: OTLPExporterConfig):
    """Create OTLP log exporter based on configuration.

    Args:
        otlp_config: OTLP exporter configuration

    Returns:
        OTLP log exporter instance
    """
    if otlp_config.protocol == "http":
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

        return OTLPLogExporter(
            endpoint=f"{otlp_config.endpoint}/v1/logs",
            headers=otlp_config.headers or None,
        )
    else:
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter

        return OTLPLogExporter(
            endpoint=otlp_config.endpoint,
            insecure=otlp_config.insecure,
            headers=otlp_config.headers or None,
        )


def setup_telemetry(config: TelemetryConfig | None = None) -> dict[str, Any]:
    """Set up OpenTelemetry instrumentation.

    Initializes:
    - MeterProvider with PrometheusMetricReader
    - TracerProvider with optional OTLP exporter
    - LoggerProvider with optional OTLP exporter
    - AELMetrics instance

    Args:
        config: Telemetry configuration (uses defaults if None)

    Returns:
        Dictionary with meter, tracer, logger, and metrics instances
    """
    global _telemetry

    if _telemetry is not None:
        return _telemetry

    config = config or TelemetryConfig()

    if not config.enabled:
        # Return no-op instances
        _telemetry = {
            "meter": None,
            "tracer": None,
            "logger": None,
            "metrics": None,
            "config": config,
        }
        return _telemetry

    # Create resource with service info
    resource = Resource.create(
        {
            SERVICE_NAME: config.service_name,
            SERVICE_VERSION: config.service_version,
            **config.attributes,
        }
    )

    # Set up metrics with Prometheus exporter
    if config.metrics_enabled:
        reader = PrometheusMetricReader()
        meter_provider = MeterProvider(
            metric_readers=[reader],
            resource=resource,
        )
        metrics.set_meter_provider(meter_provider)

    # Set up traces with optional OTLP exporter
    tracer_provider = TracerProvider(resource=resource)

    if config.traces_enabled and config.otlp.enabled:
        # Add OTLP span exporter for Tempo
        span_exporter = _create_otlp_span_exporter(config.otlp)
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))

    trace.set_tracer_provider(tracer_provider)

    # Set up logs with optional OTLP exporter
    logger_provider = None
    otel_logger = None
    if config.logs_enabled and config.otlp.enabled:
        from opentelemetry._logs import set_logger_provider
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

        logger_provider = LoggerProvider(resource=resource)
        log_exporter = _create_otlp_log_exporter(config.otlp)
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
        set_logger_provider(logger_provider)

        # Create OTEL logger for AEL
        otel_logger = logger_provider.get_logger(config.service_name, config.service_version)

    # Get meter and tracer
    meter = metrics.get_meter(
        name=config.service_name,
        version=config.service_version,
    )
    tracer = trace.get_tracer(
        instrumenting_module_name=config.service_name,
        instrumenting_library_version=config.service_version,
    )

    # Create AEL metrics
    ael_metrics = AELMetrics(meter)

    # Initialize metrics with zero values so they appear in Prometheus
    # OTEL metrics only appear after first recording
    if config.metrics_enabled:
        _initialize_metrics(ael_metrics, meter)

    _telemetry = {
        "meter": meter,
        "tracer": tracer,
        "logger": otel_logger,
        "logger_provider": logger_provider,
        "metrics": ael_metrics,
        "config": config,
        "tracer_provider": tracer_provider,
    }

    return _telemetry


def _initialize_metrics(ael_metrics: AELMetrics, meter: metrics.Meter) -> None:
    """Initialize metrics with zero values so they appear in Prometheus.

    OTEL metrics only appear in Prometheus after they've been recorded
    at least once. This function records initial zero values for key
    metrics so dashboards can display them immediately.
    """
    # Initialize counters with 0 (using a dummy label to create the metric)
    # Note: We use add(0) which creates the metric but doesn't change the value
    ael_metrics.workflow_executions_total.add(0, {"workflow_id": "_init", "status": "init"})
    ael_metrics.step_executions_total.add(
        0, {"workflow_id": "_init", "step_id": "_init", "status": "init"}
    )
    ael_metrics.tool_invocations_total.add(0, {"tool_name": "_init", "status": "init"})
    ael_metrics.chain_links_total.add(0, {"from_tool": "_init", "to_tool": "_init"})

    # Initialize gauges
    ael_metrics.active_workflows.add(0, {"workflow_id": "_init"})
    ael_metrics.registered_tools.add(0)
    ael_metrics.registered_workflows.add(0)

    # Initialize tools by source metrics
    ael_metrics.tools_by_source.add(0, {"source": "mcp"})
    ael_metrics.tools_by_source.add(0, {"source": "system"})
    ael_metrics.tools_by_source.add(0, {"source": "runner"})

    # Initialize connected runners
    ael_metrics.connected_runners.add(0)

    # Initialize token estimator metrics
    # These are created by TokenEstimator but we need to initialize them here
    # so they appear in Prometheus before any workflows are executed
    from .token_estimator import TokenEstimator

    token_estimator = TokenEstimator(meter=meter)
    # Record initial zero values to make metrics appear
    token_estimator._tokens_saved_total.add(0, {"workflow_name": "_init"})
    token_estimator._cost_saved_cents_total.add(0, {"workflow_name": "_init", "model": "_init"})
    token_estimator._raw_mcp_estimate_total.add(0, {"workflow_name": "_init"})
    token_estimator._tokens_saved_per_execution.record(0, {"workflow_name": "_init"})


def get_telemetry() -> dict[str, Any] | None:
    """Get the current telemetry instance.

    Returns:
        Telemetry dictionary or None if not initialized
    """
    return _telemetry


def reset_telemetry() -> None:
    """Reset telemetry state (for testing)."""
    global _telemetry
    _telemetry = None

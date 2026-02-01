"""AEL Configuration data models."""

from dataclasses import dataclass, field
from typing import Any

from ploston_core.types import (
    LogFormat,
    LogLevel,
    MCPTransport,
    PackageProfile,
    RetryConfig,
)


@dataclass
class ServerConfig:
    """Server configuration."""

    host: str = "0.0.0.0"
    port: int = 8080
    workers: int = 0  # 0 = auto-detect


@dataclass
class MCPExposureConfig:
    """MCP exposure configuration."""

    workflows: bool = True
    tools: bool = True


@dataclass
class MCPTLSConfig:
    """MCP TLS configuration for HTTP transport."""

    enabled: bool = False
    cert_file: str = ""
    key_file: str = ""


@dataclass
class MCPHTTPConfig:
    """MCP HTTP transport configuration."""

    host: str = "0.0.0.0"
    port: int = 8080
    cors_origins: list[str] = field(default_factory=lambda: ["*"])
    tls: MCPTLSConfig = field(default_factory=MCPTLSConfig)


@dataclass
class MCPConfig:
    """MCP configuration."""

    enabled: bool = True
    transport: MCPTransport = MCPTransport.STDIO
    exposure: MCPExposureConfig = field(default_factory=MCPExposureConfig)
    http: MCPHTTPConfig = field(default_factory=MCPHTTPConfig)


@dataclass
class MCPServerDefinition:
    """Definition of an MCP server to connect to."""

    command: str | None = None  # For stdio: command to spawn
    url: str | None = None  # For http: server URL
    transport: MCPTransport = MCPTransport.STDIO
    env: dict[str, str] = field(default_factory=dict)
    timeout: int = 30


@dataclass
class SystemToolsConfig:
    """System tools configuration."""

    python_exec_enabled: bool = True


@dataclass
class ToolsConfig:
    """Tools configuration."""

    mcp_servers: dict[str, MCPServerDefinition] = field(default_factory=dict)
    system_tools: SystemToolsConfig = field(default_factory=SystemToolsConfig)


@dataclass
class WorkflowsConfig:
    """Workflows configuration."""

    directory: str = "./workflows"
    watch: bool = True


@dataclass
class ExecutionConfig:
    """Execution configuration."""

    default_timeout: int = 300
    step_timeout: int = 30
    max_steps: int = 100
    retry: RetryConfig = field(default_factory=RetryConfig)


@dataclass
class PythonExecPackagesConfig:
    """Python execution packages configuration."""

    default_profile: PackageProfile = PackageProfile.STANDARD


@dataclass
class PythonExecConfig:
    """Python execution configuration."""

    timeout: int = 30
    max_tool_calls: int = 10
    default_imports: list[str] = field(default_factory=lambda: ["json", "re", "datetime"])
    packages: PythonExecPackagesConfig = field(default_factory=PythonExecPackagesConfig)


@dataclass
class LoggingComponentsConfig:
    """Logging components configuration."""

    workflow: bool = True
    step: bool = True
    tool: bool = True
    sandbox: bool = True


@dataclass
class LoggingOptionsConfig:
    """Logging options configuration."""

    show_params: bool = True
    show_results: bool = True
    truncate_at: int = 200


@dataclass
class LoggingConfig:
    """Logging configuration."""

    level: LogLevel = LogLevel.INFO
    format: LogFormat = LogFormat.COLORED
    components: LoggingComponentsConfig = field(default_factory=LoggingComponentsConfig)
    options: LoggingOptionsConfig = field(default_factory=LoggingOptionsConfig)


@dataclass
class PluginDefinition:
    """Plugin definition."""

    name: str
    type: str = "builtin"  # builtin | file | package
    path: str | None = None  # For file type
    package: str | None = None  # For package type
    priority: int = 50
    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class APIKeyDefinition:
    """API key definition."""

    name: str
    key: str
    scopes: list[str] = field(default_factory=lambda: ["read", "write", "execute"])


@dataclass
class SecurityConfig:
    """Security configuration."""

    require_auth: bool = False
    api_keys: list[APIKeyDefinition] = field(default_factory=list)


@dataclass
class TelemetryStorageConfig:
    """Telemetry storage configuration."""

    type: str = "memory"  # memory | file | postgres


@dataclass
class TelemetryOTLPConfig:
    """Telemetry OTLP exporter configuration.

    Attributes:
        enabled: Whether OTLP export is enabled
        endpoint: OTLP collector endpoint (e.g., http://otel-collector:4317)
        insecure: Whether to use insecure connection (no TLS)
        protocol: Protocol to use (grpc or http)
        headers: Additional headers for authentication
    """

    enabled: bool = False
    endpoint: str = "http://localhost:4317"
    insecure: bool = True
    protocol: str = "grpc"  # grpc | http
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class TelemetryExportConfig:
    """Telemetry export configuration."""

    otlp: TelemetryOTLPConfig = field(default_factory=TelemetryOTLPConfig)


@dataclass
class TelemetryMetricsConfig:
    """Telemetry metrics configuration (OpenTelemetry)."""

    enabled: bool = True
    prometheus_enabled: bool = True  # Expose /metrics endpoint


@dataclass
class TelemetryTracingConfig:
    """Telemetry tracing configuration (OpenTelemetry).

    Attributes:
        enabled: Whether tracing is enabled
        sample_rate: Sampling rate (0.0 to 1.0, 1.0 = sample all)
        export_to_otlp: Whether to export traces to OTLP collector
    """

    enabled: bool = False
    sample_rate: float = 1.0
    export_to_otlp: bool = False


@dataclass
class TelemetryLoggingConfig:
    """Telemetry logging configuration (OpenTelemetry).

    Attributes:
        enabled: Whether OTEL logging is enabled
        export_to_otlp: Whether to export logs to OTLP collector (Loki)
        include_trace_context: Whether to include trace_id/span_id in logs
    """

    enabled: bool = False
    export_to_otlp: bool = False
    include_trace_context: bool = True


@dataclass
class TelemetryConfig:
    """Telemetry configuration."""

    enabled: bool = True
    service_name: str = "ael"
    service_version: str = "1.0.0"
    storage: TelemetryStorageConfig = field(default_factory=TelemetryStorageConfig)
    export: TelemetryExportConfig = field(default_factory=TelemetryExportConfig)
    metrics: TelemetryMetricsConfig = field(default_factory=TelemetryMetricsConfig)
    tracing: TelemetryTracingConfig = field(default_factory=TelemetryTracingConfig)
    logging: TelemetryLoggingConfig = field(default_factory=TelemetryLoggingConfig)


@dataclass
class RunnerMCPServerDefinition:
    """MCP server definition for runners.

    Similar to MCPServerDefinition but with args list for stdio transport.
    Aligned with tools.mcp_servers schema.
    """

    command: str | None = None  # For stdio: command to spawn
    args: list[str] = field(default_factory=list)  # Command arguments
    url: str | None = None  # For http: server URL
    transport: MCPTransport = MCPTransport.STDIO
    env: dict[str, str] = field(default_factory=dict)
    timeout: int = 30


@dataclass
class RunnerDefinition:
    """Definition of a pre-configured runner.

    Allows pre-configuring runners with MCP servers in the config file.
    When a runner with this name connects, these MCPs are pushed to it.
    """

    mcp_servers: dict[str, RunnerMCPServerDefinition] = field(default_factory=dict)


@dataclass
class AELConfig:
    """Root configuration object."""

    server: ServerConfig = field(default_factory=ServerConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    workflows: WorkflowsConfig = field(default_factory=WorkflowsConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    python_exec: PythonExecConfig = field(default_factory=PythonExecConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    plugins: list[PluginDefinition] = field(default_factory=list)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    runners: dict[str, RunnerDefinition] = field(default_factory=dict)

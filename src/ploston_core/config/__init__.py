"""AEL Configuration - Config loading and management."""

from .loader import (
    ConfigLoader,
    deep_merge,
    get_config_loader,
    load_config,
    resolve_env_vars,
)
from .models import (
    AELConfig,
    APIKeyDefinition,
    ExecutionConfig,
    LoggingComponentsConfig,
    LoggingConfig,
    LoggingOptionsConfig,
    MCPConfig,
    MCPExposureConfig,
    MCPHTTPConfig,
    MCPServerDefinition,
    MCPTLSConfig,
    PluginDefinition,
    PythonExecConfig,
    PythonExecPackagesConfig,
    SecurityConfig,
    ServerConfig,
    SystemToolsConfig,
    TelemetryConfig,
    TelemetryExportConfig,
    TelemetryMetricsConfig,
    TelemetryOTLPConfig,
    TelemetryStorageConfig,
    TelemetryTracingConfig,
    ToolsConfig,
    WorkflowsConfig,
)

__all__ = [
    # Config models
    "AELConfig",
    "ServerConfig",
    "MCPConfig",
    "MCPExposureConfig",
    "MCPHTTPConfig",
    "MCPTLSConfig",
    "MCPServerDefinition",
    "ToolsConfig",
    "SystemToolsConfig",
    "WorkflowsConfig",
    "ExecutionConfig",
    "PythonExecConfig",
    "PythonExecPackagesConfig",
    "LoggingConfig",
    "LoggingComponentsConfig",
    "LoggingOptionsConfig",
    "PluginDefinition",
    "APIKeyDefinition",
    "SecurityConfig",
    "TelemetryConfig",
    "TelemetryStorageConfig",
    "TelemetryOTLPConfig",
    "TelemetryExportConfig",
    "TelemetryMetricsConfig",
    "TelemetryTracingConfig",
    # Loader
    "ConfigLoader",
    "get_config_loader",
    "load_config",
    # Utilities
    "resolve_env_vars",
    "deep_merge",
]

# Mode management
from .mode_manager import Mode, ModeManager

__all__ += [
    "Mode",
    "ModeManager",
]

# Staged config
from .staged_config import StagedConfig  # noqa: E402

__all__ += [
    "StagedConfig",
]

# Config tools
from .tools import (  # noqa: E402
    CONFIG_TOOL_SCHEMAS,
    CONFIGURE_TOOL_SCHEMA,
    ConfigToolRegistry,
)

__all__ += [
    "ConfigToolRegistry",
    "CONFIG_TOOL_SCHEMAS",
    "CONFIGURE_TOOL_SCHEMA",
]

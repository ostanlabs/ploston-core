"""Shared enumerations for AEL."""

from enum import Enum


class LogLevel(str, Enum):
    """Log verbosity level."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


class LogFormat(str, Enum):
    """Log output format."""

    COLORED = "colored"
    JSON = "json"


class BackoffType(str, Enum):
    """Retry backoff strategy."""

    FIXED = "fixed"
    EXPONENTIAL = "exponential"


class OnError(str, Enum):
    """Step error handling strategy."""

    FAIL = "fail"
    SKIP = "skip"
    RETRY = "retry"


class PackageProfile(str, Enum):
    """Python sandbox package profile."""

    STANDARD = "standard"
    COMMON = "common"


class MCPTransport(str, Enum):
    """MCP connection transport type."""

    STDIO = "stdio"
    HTTP = "http"


class StepType(str, Enum):
    """Workflow step type."""

    TOOL = "tool"
    CODE = "code"


class ExecutionStatus(str, Enum):
    """Workflow execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    """Individual step status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ToolSource(str, Enum):
    """Where a tool comes from."""

    MCP = "mcp"
    HTTP = "http"
    SYSTEM = "system"
    NATIVE = "native"  # Native tools (filesystem, kafka, etc.)


class ToolStatus(str, Enum):
    """Tool availability status."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class ConnectionStatus(str, Enum):
    """MCP server connection status."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    CONNECTING = "connecting"

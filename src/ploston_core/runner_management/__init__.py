"""Runner management module for Control Plane.

This module provides CP-side components for managing Local Runners:
- RunnerRegistry: Data model and CRUD operations for runners
- WorkflowRouter: Routing decisions for CP vs Runner execution
- EmbeddedCA: TLS certificate management
- RunnerWebSocketServer: WebSocket server for runner connections

Per DEC-125, all runner management is OSS (in ploston-core).
"""

from ploston_core.runner_management.registry import (
    Runner,
    RunnerRegistry,
    RunnerStatus,
    generate_runner_id,
    generate_runner_token,
    hash_token,
    validate_token_format,
)
from ploston_core.runner_management.router import (
    RoutingDecision,
    RoutingTarget,
    RunnerUnavailableError,
    ToolUnavailableError,
    WorkflowRouter,
    extract_tools_from_workflow,
    parse_tool_prefix,
)
from ploston_core.runner_management.websocket_server import (
    RunnerConnection,
    RunnerWebSocketServer,
)

__all__ = [
    # Registry
    "Runner",
    "RunnerRegistry",
    "RunnerStatus",
    "generate_runner_id",
    "generate_runner_token",
    "hash_token",
    "validate_token_format",
    # Router
    "RoutingDecision",
    "RoutingTarget",
    "RunnerUnavailableError",
    "ToolUnavailableError",
    "WorkflowRouter",
    "extract_tools_from_workflow",
    "parse_tool_prefix",
    # WebSocket Server
    "RunnerConnection",
    "RunnerWebSocketServer",
]

# Optional: EmbeddedCA (requires cryptography)
try:
    from ploston_core.runner_management.embedded_ca import (
        CertificateInfo,
        EmbeddedCA,
    )
    __all__.extend(["CertificateInfo", "EmbeddedCA"])
except ImportError:
    pass  # cryptography not installed

"""Internal types for ploston-core.

These types are used internally and may include additional values
not exposed in the public API.
"""

from enum import Enum


class InternalToolSource(str, Enum):
    """Internal tool source enum with all possible sources.

    This extends the public ToolSource enum with additional internal sources
    like RUNNER that are used for filtering but not exposed in the public API.
    """

    MCP = "mcp"
    HTTP = "http"
    SYSTEM = "system"
    NATIVE = "native"
    RUNNER = "runner"  # Tools from connected runners

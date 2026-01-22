"""Sandbox factory for breaking circular dependencies."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ploston_core.logging import AELLogger
    from ploston_core.sandbox import PythonExecSandbox, SandboxConfig


class SandboxFactory:
    """Factory for creating sandbox instances.

    This breaks the circular dependency:
    - ToolInvoker needs to create sandboxes for python_exec
    - Sandbox needs ToolCallerProtocol for nested tool calls

    By using a factory, ToolInvoker doesn't hold a sandbox reference,
    and sandbox doesn't depend on ToolInvoker directly.
    """

    def __init__(
        self,
        default_config: "SandboxConfig | None" = None,
        logger: "AELLogger | None" = None,
    ):
        """Initialize sandbox factory.

        Args:
            default_config: Default sandbox configuration
            logger: Optional logger
        """
        self._default_config = default_config
        self._logger = logger

    def create(self) -> "PythonExecSandbox":
        """Create a new sandbox instance.

        Returns:
            New PythonExecSandbox instance
        """
        from ploston_core.sandbox import PythonExecSandbox

        # PythonExecSandbox doesn't use default_config or logger in __init__
        # It takes tool_caller, allowed_imports, timeout, max_output_size
        timeout = self._default_config.timeout if self._default_config else 30
        allowed_imports = (
            set(self._default_config.allowed_imports)
            if self._default_config and self._default_config.allowed_imports
            else None
        )

        return PythonExecSandbox(
            tool_caller=None,  # Will be set by workflow engine
            allowed_imports=allowed_imports,
            timeout=timeout,
        )

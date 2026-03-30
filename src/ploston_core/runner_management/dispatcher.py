"""Concrete RunnerDispatcher implementation.

Routes tool calls to connected runners via WebSocket, resolving
runner_name → runner_id via RunnerRegistry.

Implements T-729 from UNIFIED_TOOL_RESOLVER_SPEC.
"""

from __future__ import annotations

from typing import Any

from ploston_core.errors import create_error

from .registry import RunnerRegistry


class RunnerToolDispatcher:
    """Concrete RunnerDispatcher that routes via WebSocket to connected runners.

    Resolves runner_name to runner_id via RunnerRegistry, then delegates
    to send_tool_call_to_runner (in api/routers/runner_static.py).
    """

    def __init__(self, runner_registry: RunnerRegistry) -> None:
        self._registry = runner_registry

    async def dispatch(
        self,
        runner_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float = 60.0,
    ) -> Any:
        """Dispatch a tool call to a runner.

        Args:
            runner_name: Human-readable runner name
            tool_name: Bare tool name (mcp__tool, no runner prefix)
            arguments: Tool call arguments
            timeout: Timeout in seconds

        Returns:
            Tool call result from the runner

        Raises:
            AELError(TOOL_UNAVAILABLE): If runner not found or not connected
        """
        runner = self._registry.get_by_name(runner_name)
        if not runner:
            raise create_error(
                "TOOL_UNAVAILABLE",
                tool_name=tool_name,
                reason=f"Runner '{runner_name}' not found in registry",
            )

        if runner.status.value != "connected":
            raise create_error(
                "TOOL_UNAVAILABLE",
                tool_name=tool_name,
                reason=f"Runner '{runner_name}' is not connected",
            )

        # Import here to avoid circular dependency — runner_static.py is in
        # the api layer which depends on runner_management.
        from ploston_core.api.routers.runner_static import send_tool_call_to_runner

        try:
            result = await send_tool_call_to_runner(
                runner_id=runner.id,
                tool_name=tool_name,
                arguments=arguments,
                timeout=timeout,
            )
        except TimeoutError:
            raise create_error(
                "TOOL_UNAVAILABLE",
                tool_name=tool_name,
                reason=f"Runner '{runner_name}' timed out after {timeout}s",
            )
        except ValueError as e:
            raise create_error(
                "TOOL_UNAVAILABLE",
                tool_name=tool_name,
                reason=str(e),
            ) from e

        return result

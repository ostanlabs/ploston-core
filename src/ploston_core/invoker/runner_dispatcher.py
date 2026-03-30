"""RunnerDispatcher protocol for routing tool calls to connected runners.

Implements DEC-161: RunnerDispatcher protocol injected into ToolInvoker.
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RunnerDispatcher(Protocol):
    """Dispatch a tool call to a connected runner.

    Protocol that decouples ToolInvoker from the WebSocket transport layer.
    The concrete implementation (RunnerToolDispatcher) resolves runner_name
    to runner_id via RunnerRegistry and calls send_tool_call_to_runner.
    """

    async def dispatch(
        self,
        runner_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float = 60.0,
    ) -> Any:
        """Dispatch a tool call to a runner.

        Args:
            runner_name: Human-readable runner name (e.g. "macbook-pro-local")
            tool_name: Bare tool name without runner prefix (e.g. "github__actions_list")
            arguments: Tool call arguments
            timeout: Timeout in seconds

        Returns:
            Tool call result from the runner

        Raises:
            AELError(TOOL_UNAVAILABLE): If runner not found, not connected, or call fails
        """
        ...

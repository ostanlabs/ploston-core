"""Unit tests for RunnerToolDispatcher.

Tests U-01 through U-05 from UNIFIED_TOOL_RESOLVER_SPEC.
"""

from unittest.mock import AsyncMock, patch

import pytest

from ploston_core.errors import AELError
from ploston_core.runner_management.dispatcher import RunnerToolDispatcher
from ploston_core.runner_management.registry import RunnerRegistry


@pytest.fixture
def registry() -> RunnerRegistry:
    """Create a RunnerRegistry with a connected runner."""
    reg = RunnerRegistry()
    runner, _ = reg.create("macbook-pro-local")
    reg.set_connected(runner.id)
    return reg


@pytest.fixture
def dispatcher(registry: RunnerRegistry) -> RunnerToolDispatcher:
    return RunnerToolDispatcher(registry)


class TestRunnerToolDispatcher:
    """U-01 through U-05: RunnerToolDispatcher tests."""

    @pytest.mark.asyncio
    async def test_u01_dispatch_resolves_name_to_id(self, dispatcher, registry):
        """U-01: dispatch() resolves runner name to ID and calls send_tool_call_to_runner."""
        runner = registry.get_by_name("macbook-pro-local")
        expected_result = {"output": "ok"}

        with patch(
            "ploston_core.api.routers.runner_static.send_tool_call_to_runner",
            new_callable=AsyncMock,
            return_value=expected_result,
        ) as mock_send:
            result = await dispatcher.dispatch(
                runner_name="macbook-pro-local",
                tool_name="github__actions_list",
                arguments={"repo": "test"},
            )

            mock_send.assert_called_once_with(
                runner_id=runner.id,
                tool_name="github__actions_list",
                arguments={"repo": "test"},
                timeout=60.0,
            )
            assert result == expected_result

    @pytest.mark.asyncio
    async def test_u02_dispatch_raises_when_runner_not_found(self, dispatcher):
        """U-02: dispatch() raises TOOL_UNAVAILABLE when runner not in registry."""
        with pytest.raises(AELError) as exc_info:
            await dispatcher.dispatch(
                runner_name="nonexistent-runner",
                tool_name="github__actions_list",
                arguments={},
            )
        assert exc_info.value.code == "TOOL_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_u03_dispatch_raises_when_runner_disconnected(self, dispatcher, registry):
        """U-03: dispatch() raises TOOL_UNAVAILABLE when runner is disconnected."""
        runner = registry.get_by_name("macbook-pro-local")
        registry.set_disconnected(runner.id)

        with pytest.raises(AELError) as exc_info:
            await dispatcher.dispatch(
                runner_name="macbook-pro-local",
                tool_name="github__actions_list",
                arguments={},
            )
        assert exc_info.value.code == "TOOL_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_u04_dispatch_wraps_value_error(self, dispatcher):
        """U-04: dispatch() raises TOOL_UNAVAILABLE wrapping ValueError from send_tool_call_to_runner."""
        with patch(
            "ploston_core.api.routers.runner_static.send_tool_call_to_runner",
            new_callable=AsyncMock,
            side_effect=ValueError("Runner macbook-pro-local not connected"),
        ):
            with pytest.raises(AELError) as exc_info:
                await dispatcher.dispatch(
                    runner_name="macbook-pro-local",
                    tool_name="github__actions_list",
                    arguments={},
                )
            assert exc_info.value.code == "TOOL_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_u05_dispatch_propagates_timeout(self, dispatcher, registry):
        """U-05: dispatch() propagates timeout to underlying call."""
        with patch(
            "ploston_core.api.routers.runner_static.send_tool_call_to_runner",
            new_callable=AsyncMock,
            return_value={"output": "ok"},
        ) as mock_send:
            await dispatcher.dispatch(
                runner_name="macbook-pro-local",
                tool_name="github__actions_list",
                arguments={},
                timeout=120.0,
            )
            assert mock_send.call_args.kwargs["timeout"] == 120.0

    @pytest.mark.asyncio
    async def test_dispatch_wraps_timeout_error(self, dispatcher):
        """dispatch() raises TOOL_UNAVAILABLE on timeout."""
        with patch(
            "ploston_core.api.routers.runner_static.send_tool_call_to_runner",
            new_callable=AsyncMock,
            side_effect=TimeoutError(),
        ):
            with pytest.raises(AELError) as exc_info:
                await dispatcher.dispatch(
                    runner_name="macbook-pro-local",
                    tool_name="github__actions_list",
                    arguments={},
                    timeout=5.0,
                )
            assert exc_info.value.code == "TOOL_UNAVAILABLE"

"""Tests for MockRunner test utility.

Implements S-189: Test Infrastructure
- UT-114: MockRunner initialization
- UT-115: MockRunner message methods
- UT-116: MockRunner context manager
"""

from unittest.mock import AsyncMock, patch

import pytest

from tests.mocks.mock_runner import MockRunner


class TestMockRunnerInit:
    """Tests for MockRunner initialization (UT-114)."""

    def test_init_stores_params(self):
        """Test that init stores connection parameters."""
        runner = MockRunner("ws://localhost:8443", "token123", "test-runner")

        assert runner.cp_url == "ws://localhost:8443"
        assert runner.token == "token123"
        assert runner.name == "test-runner"
        assert runner.ws is None
        assert runner.received_configs == []
        assert runner.received_workflows == []

    def test_init_message_id_starts_at_zero(self):
        """Test that message ID counter starts at zero."""
        runner = MockRunner("ws://localhost:8443", "token123", "test-runner")
        assert runner._message_id == 0

    def test_next_id_increments(self):
        """Test that _next_id increments the counter."""
        runner = MockRunner("ws://localhost:8443", "token123", "test-runner")

        assert runner._next_id() == 1
        assert runner._next_id() == 2
        assert runner._next_id() == 3


class TestMockRunnerMessages:
    """Tests for MockRunner message methods (UT-115)."""

    @pytest.mark.asyncio
    async def test_send_requires_connection(self):
        """Test that send raises if not connected."""
        runner = MockRunner("ws://localhost:8443", "token123", "test-runner")

        with pytest.raises(RuntimeError, match="Not connected"):
            await runner.send({"test": "message"})

    @pytest.mark.asyncio
    async def test_receive_requires_connection(self):
        """Test that receive raises if not connected."""
        runner = MockRunner("ws://localhost:8443", "token123", "test-runner")

        with pytest.raises(RuntimeError, match="Not connected"):
            await runner.receive()

    @pytest.mark.asyncio
    async def test_send_serializes_json(self):
        """Test that send serializes message to JSON."""
        runner = MockRunner("ws://localhost:8443", "token123", "test-runner")
        runner.ws = AsyncMock()

        await runner.send({"test": "message"})

        runner.ws.send.assert_called_once_with('{"test": "message"}')

    @pytest.mark.asyncio
    async def test_receive_deserializes_json(self):
        """Test that receive deserializes JSON response."""
        runner = MockRunner("ws://localhost:8443", "token123", "test-runner")
        runner.ws = AsyncMock()
        runner.ws.recv.return_value = '{"result": "ok"}'

        result = await runner.receive()

        assert result == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_register_sends_correct_message(self):
        """Test that register sends correct JSON-RPC message."""
        runner = MockRunner("ws://localhost:8443", "token123", "test-runner")
        runner.ws = AsyncMock()
        runner.ws.recv.return_value = '{"jsonrpc": "2.0", "id": 1, "result": {"status": "ok"}}'

        result = await runner.register()

        # Check the sent message
        call_args = runner.ws.send.call_args[0][0]
        import json
        sent = json.loads(call_args)
        assert sent["jsonrpc"] == "2.0"
        assert sent["id"] == 1
        assert sent["method"] == "runner/register"
        assert sent["params"]["token"] == "token123"
        assert sent["params"]["name"] == "test-runner"

        # Check the result
        assert result["result"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_send_availability(self):
        """Test that send_availability sends correct message."""
        runner = MockRunner("ws://localhost:8443", "token123", "test-runner")
        runner.ws = AsyncMock()

        await runner.send_availability(["tool1", "tool2"], ["tool3"])

        call_args = runner.ws.send.call_args[0][0]
        import json
        sent = json.loads(call_args)
        assert sent["method"] == "runner/availability"
        assert sent["params"]["available"] == ["tool1", "tool2"]
        assert sent["params"]["unavailable"] == ["tool3"]

    @pytest.mark.asyncio
    async def test_send_heartbeat(self):
        """Test that send_heartbeat sends correct message."""
        runner = MockRunner("ws://localhost:8443", "token123", "test-runner")
        runner.ws = AsyncMock()

        await runner.send_heartbeat()

        call_args = runner.ws.send.call_args[0][0]
        import json
        sent = json.loads(call_args)
        assert sent["method"] == "runner/heartbeat"
        assert "timestamp" in sent["params"]

    @pytest.mark.asyncio
    async def test_send_workflow_result(self):
        """Test that send_workflow_result sends correct message."""
        runner = MockRunner("ws://localhost:8443", "token123", "test-runner")
        runner.ws = AsyncMock()

        await runner.send_workflow_result(42, {"output": "done"})

        call_args = runner.ws.send.call_args[0][0]
        import json
        sent = json.loads(call_args)
        assert sent["jsonrpc"] == "2.0"
        assert sent["id"] == 42
        assert sent["result"] == {"output": "done"}

    @pytest.mark.asyncio
    async def test_context_manager_connects_and_disconnects(self):
        """Test that context manager handles connection lifecycle."""
        with patch("tests.mocks.mock_runner.MockRunner.connect") as mock_connect, \
             patch("tests.mocks.mock_runner.MockRunner.disconnect") as mock_disconnect:
            mock_connect.return_value = None
            mock_disconnect.return_value = None

            async with MockRunner("ws://localhost:8443", "token123", "test-runner") as runner:
                mock_connect.assert_called_once()
                assert isinstance(runner, MockRunner)

            mock_disconnect.assert_called_once()

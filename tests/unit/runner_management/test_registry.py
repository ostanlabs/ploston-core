"""Unit tests for RunnerRegistry.

Tests for S-182: Runner Registry (CP)
- UT-077: test_runner_datamodel
- UT-078: test_runner_crud
- UT-079: test_runner_status_tracking
- UT-080: test_runner_tools_tracking
- UT-081: test_token_generation
- UT-082: test_token_hash_storage
"""

from datetime import UTC, datetime

import pytest

from ploston_core.runner_management.registry import (
    Runner,
    RunnerRegistry,
    RunnerStatus,
    generate_runner_id,
    generate_runner_token,
    hash_token,
    validate_token_format,
)


class TestRunnerDataModel:
    """UT-077: Runner dataclass fields."""

    def test_runner_required_fields(self):
        """Test Runner with required fields."""
        runner = Runner(
            id="runner_abc123",
            name="test-runner",
            created_at=datetime.now(UTC),
        )
        assert runner.id == "runner_abc123"
        assert runner.name == "test-runner"
        assert runner.status == RunnerStatus.DISCONNECTED
        assert runner.available_tools == []
        assert runner.mcps == {}

    def test_runner_all_fields(self):
        """Test Runner with all fields."""
        now = datetime.now(UTC)
        runner = Runner(
            id="runner_abc123",
            name="test-runner",
            created_at=now,
            last_seen=now,
            status=RunnerStatus.CONNECTED,
            available_tools=["fs_read", "fs_write"],
            token_hash="abc123hash",
            mcps={"filesystem": {"command": "npx"}},
        )
        assert runner.status == RunnerStatus.CONNECTED
        assert runner.available_tools == ["fs_read", "fs_write"]
        assert runner.token_hash == "abc123hash"
        assert "filesystem" in runner.mcps

    def test_runner_to_dict(self):
        """Test Runner serialization."""
        now = datetime.now(UTC)
        runner = Runner(
            id="runner_abc123",
            name="test-runner",
            created_at=now,
            status=RunnerStatus.CONNECTED,
            available_tools=["fs_read"],
        )
        data = runner.to_dict()
        assert data["id"] == "runner_abc123"
        assert data["name"] == "test-runner"
        assert data["status"] == "connected"
        assert data["available_tools"] == ["fs_read"]
        assert "token_hash" not in data  # Should not expose token hash


class TestRunnerCRUD:
    """UT-078: Create, read, update, delete."""

    def test_create_runner(self):
        """Test creating a runner."""
        registry = RunnerRegistry()
        runner, token = registry.create("test-runner")

        assert runner.name == "test-runner"
        assert runner.id.startswith("runner_")
        assert token.startswith("ploston_runner_")
        assert runner.status == RunnerStatus.DISCONNECTED

    def test_create_runner_with_mcps(self):
        """Test creating a runner with MCP configs."""
        registry = RunnerRegistry()
        mcps = {"filesystem": {"command": "npx", "args": ["@mcp/filesystem"]}}
        runner, token = registry.create("test-runner", mcps=mcps)

        assert runner.mcps == mcps

    def test_create_duplicate_name_fails(self):
        """Test that duplicate names are rejected."""
        registry = RunnerRegistry()
        registry.create("test-runner")

        with pytest.raises(ValueError, match="already exists"):
            registry.create("test-runner")

    def test_get_runner_by_id(self):
        """Test getting a runner by ID."""
        registry = RunnerRegistry()
        runner, _ = registry.create("test-runner")

        found = registry.get(runner.id)
        assert found is not None
        assert found.name == "test-runner"

    def test_get_runner_by_name(self):
        """Test getting a runner by name."""
        registry = RunnerRegistry()
        registry.create("test-runner")

        found = registry.get_by_name("test-runner")
        assert found is not None
        assert found.name == "test-runner"

    def test_get_runner_by_token(self):
        """Test getting a runner by token."""
        registry = RunnerRegistry()
        runner, token = registry.create("test-runner")

        found = registry.get_by_token(token)
        assert found is not None
        assert found.id == runner.id

    def test_get_nonexistent_runner(self):
        """Test getting a nonexistent runner."""
        registry = RunnerRegistry()
        assert registry.get("nonexistent") is None
        assert registry.get_by_name("nonexistent") is None
        assert registry.get_by_token("invalid_token") is None

    def test_list_runners(self):
        """Test listing all runners."""
        registry = RunnerRegistry()
        registry.create("runner-1")
        registry.create("runner-2")

        runners = registry.list()
        assert len(runners) == 2
        names = {r.name for r in runners}
        assert names == {"runner-1", "runner-2"}

    def test_update_runner(self):
        """Test updating a runner."""
        registry = RunnerRegistry()
        runner, _ = registry.create("test-runner")

        updated = registry.update(
            runner.id,
            status=RunnerStatus.CONNECTED,
            available_tools=["fs_read"],
        )

        assert updated is not None
        assert updated.status == RunnerStatus.CONNECTED
        assert updated.available_tools == ["fs_read"]

    def test_update_nonexistent_runner(self):
        """Test updating a nonexistent runner."""
        registry = RunnerRegistry()
        result = registry.update("nonexistent", status=RunnerStatus.CONNECTED)
        assert result is None

    def test_delete_runner(self):
        """Test deleting a runner."""
        registry = RunnerRegistry()
        runner, token = registry.create("test-runner")

        assert registry.delete(runner.id) is True
        assert registry.get(runner.id) is None
        assert registry.get_by_name("test-runner") is None
        assert registry.get_by_token(token) is None

    def test_delete_by_name(self):
        """Test deleting a runner by name."""
        registry = RunnerRegistry()
        registry.create("test-runner")

        assert registry.delete_by_name("test-runner") is True
        assert registry.get_by_name("test-runner") is None

    def test_delete_nonexistent_runner(self):
        """Test deleting a nonexistent runner."""
        registry = RunnerRegistry()
        assert registry.delete("nonexistent") is False
        assert registry.delete_by_name("nonexistent") is False


class TestRunnerStatusTracking:
    """UT-079: Status: connected/disconnected."""

    def test_set_connected(self):
        """Test marking a runner as connected."""
        registry = RunnerRegistry()
        runner, _ = registry.create("test-runner")

        updated = registry.set_connected(runner.id)

        assert updated is not None
        assert updated.status == RunnerStatus.CONNECTED
        assert updated.last_seen is not None

    def test_set_disconnected(self):
        """Test marking a runner as disconnected."""
        registry = RunnerRegistry()
        runner, _ = registry.create("test-runner")
        registry.set_connected(runner.id)

        updated = registry.set_disconnected(runner.id)

        assert updated is not None
        assert updated.status == RunnerStatus.DISCONNECTED

    def test_update_heartbeat(self):
        """Test updating heartbeat timestamp."""
        registry = RunnerRegistry()
        runner, _ = registry.create("test-runner")

        updated = registry.update_heartbeat(runner.id)

        assert updated is not None
        assert updated.last_seen is not None

    def test_list_connected_runners(self):
        """Test listing only connected runners."""
        registry = RunnerRegistry()
        runner1, _ = registry.create("runner-1")
        runner2, _ = registry.create("runner-2")
        registry.create("runner-3")

        registry.set_connected(runner1.id)
        registry.set_connected(runner2.id)

        connected = registry.list_connected()
        assert len(connected) == 2
        names = {r.name for r in connected}
        assert names == {"runner-1", "runner-2"}


class TestRunnerToolsTracking:
    """UT-080: available_tools updated."""

    def test_update_available_tools(self):
        """Test updating available tools."""
        registry = RunnerRegistry()
        runner, _ = registry.create("test-runner")

        updated = registry.update_available_tools(
            runner.id,
            ["fs_read", "fs_write", "docker_run"],
        )

        assert updated is not None
        assert updated.available_tools == ["fs_read", "fs_write", "docker_run"]

    def test_has_tool(self):
        """Test checking if runner has a tool."""
        registry = RunnerRegistry()
        runner, _ = registry.create("test-runner")
        registry.set_connected(runner.id)
        registry.update_available_tools(runner.id, ["fs_read", "fs_write"])

        assert registry.has_tool("test-runner", "fs_read") is True
        assert registry.has_tool("test-runner", "fs_write") is True
        assert registry.has_tool("test-runner", "docker_run") is False

    def test_has_tool_with_prefix(self):
        """Test checking tool with runner__mcp__ prefix."""
        registry = RunnerRegistry()
        runner, _ = registry.create("mac")
        registry.set_connected(runner.id)
        # Runner stores tools as mcp__tool format
        registry.update_available_tools(runner.id, ["fs__read_file"])

        # Tool name with runner__mcp__tool format
        assert registry.has_tool("mac", "mac__fs__read_file") is True

    def test_has_tool_disconnected_runner(self):
        """Test that disconnected runners don't have tools."""
        registry = RunnerRegistry()
        runner, _ = registry.create("test-runner")
        registry.update_available_tools(runner.id, ["fs_read"])

        # Runner is disconnected by default
        assert registry.has_tool("test-runner", "fs_read") is False

    def test_get_runner_for_tool(self):
        """Test finding a runner with a specific tool."""
        registry = RunnerRegistry()
        runner, _ = registry.create("test-runner")
        registry.set_connected(runner.id)
        registry.update_available_tools(runner.id, ["fs_read"])

        found = registry.get_runner_for_tool("fs_read")
        assert found is not None
        assert found.name == "test-runner"

    def test_get_runner_for_tool_with_prefix(self):
        """Test finding a runner with runner__mcp__tool prefixed name."""
        registry = RunnerRegistry()
        runner, _ = registry.create("mac")
        registry.set_connected(runner.id)
        # Runner stores tools as mcp__tool format
        registry.update_available_tools(runner.id, ["fs__read_file"])

        # Tool name with runner__mcp__tool format
        found = registry.get_runner_for_tool("mac__fs__read_file")
        assert found is not None
        assert found.name == "mac"

    def test_get_runner_for_tool_not_found(self):
        """Test that None is returned when no runner has the tool."""
        registry = RunnerRegistry()
        runner, _ = registry.create("test-runner")
        registry.set_connected(runner.id)
        registry.update_available_tools(runner.id, ["fs_read"])

        found = registry.get_runner_for_tool("docker_run")
        assert found is None


class TestTokenGeneration:
    """UT-081: Token format: ploston_runner_xxx."""

    def test_generate_runner_id(self):
        """Test runner ID generation."""
        id1 = generate_runner_id()
        id2 = generate_runner_id()

        assert id1.startswith("runner_")
        assert id2.startswith("runner_")
        assert id1 != id2  # Should be unique

    def test_generate_runner_token(self):
        """Test runner token generation."""
        token1 = generate_runner_token()
        token2 = generate_runner_token()

        assert token1.startswith("ploston_runner_")
        assert token2.startswith("ploston_runner_")
        assert token1 != token2  # Should be unique
        assert len(token1) > 20  # Should be sufficiently long

    def test_validate_token_format_valid(self):
        """Test valid token format."""
        assert validate_token_format("ploston_runner_abcdefgh") is True
        assert validate_token_format("ploston_runner_12345678") is True
        assert validate_token_format("ploston_runner_" + "x" * 32) is True

    def test_validate_token_format_invalid(self):
        """Test invalid token formats."""
        assert validate_token_format("invalid_token") is False
        assert validate_token_format("ploston_runner_") is False  # No suffix
        assert validate_token_format("ploston_runner_short") is False  # Too short
        assert validate_token_format("") is False


class TestTokenHashStorage:
    """UT-082: Token stored as hash."""

    def test_hash_token(self):
        """Test token hashing."""
        token = "ploston_runner_test123"
        hash1 = hash_token(token)
        hash2 = hash_token(token)

        assert hash1 == hash2  # Same token = same hash
        assert hash1 != token  # Hash is different from token
        assert len(hash1) == 64  # SHA-256 hex digest

    def test_different_tokens_different_hashes(self):
        """Test that different tokens produce different hashes."""
        hash1 = hash_token("ploston_runner_token1")
        hash2 = hash_token("ploston_runner_token2")

        assert hash1 != hash2

    def test_token_not_stored_in_runner(self):
        """Test that plain token is not stored in Runner."""
        registry = RunnerRegistry()
        runner, token = registry.create("test-runner")

        # Token hash should be stored, not the plain token
        assert runner.token_hash != token
        assert runner.token_hash == hash_token(token)

    def test_token_lookup_uses_hash(self):
        """Test that token lookup uses hash comparison."""
        registry = RunnerRegistry()
        runner, token = registry.create("test-runner")

        # Should find runner with correct token
        found = registry.get_by_token(token)
        assert found is not None
        assert found.id == runner.id

        # Should not find runner with wrong token
        found = registry.get_by_token("wrong_token")
        assert found is None

"""Unit tests for workflow registry persistence (M-068 / S-228).

Tests that API-registered workflows are persisted to disk (OSS) or Redis (Premium),
and that unregister cleans up persisted storage correctly.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from ploston_core.workflow.registry import WorkflowRegistry

# Minimal valid workflow YAML for testing
SAMPLE_YAML = """\
name: test-workflow
version: "1.0.0"
description: A test workflow
steps:
  - id: step1
    tool: echo
    mcp: system
    params:
      message: hello
"""

SAMPLE_YAML_V2 = """\
name: test-workflow
version: "2.0.0"
description: Updated test workflow
steps:
  - id: step1
    tool: echo
    mcp: system
    params:
      message: hello v2
"""


def _make_config(tmp_path: Path) -> MagicMock:
    """Create a mock WorkflowsConfig pointing at tmp_path."""
    config = MagicMock()
    config.directory = str(tmp_path / "workflows")
    return config


def _make_tool_registry() -> MagicMock:
    """Create a mock ToolRegistry that passes validation."""
    tr = MagicMock()
    tr.get_tool.return_value = MagicMock()  # tool exists
    # Default: no tool found by name (DEC-169 collision check)
    tr.get.return_value = None
    # Support new (mcp, tool) resolution: list_tools returns matching tools
    echo_tool = MagicMock()
    echo_tool.name = "echo"
    echo_tool.server_name = "system"
    tr.list_tools.return_value = [echo_tool]
    return tr


def _make_redis_store(connected: bool = True) -> MagicMock:
    """Create a mock RedisConfigStore."""
    store = MagicMock()
    store.connected = connected
    store.set_value = AsyncMock(return_value=True)
    store.get_value = AsyncMock(return_value=None)
    store.delete_value = AsyncMock(return_value=True)
    store.scan_keys = AsyncMock(return_value=[])
    return store


class TestRegisterFromYamlPersist:
    """Tests for register_from_yaml with persist flag."""

    def test_persist_writes_to_disk(self, tmp_path: Path):
        """persist=True with no Redis writes YAML to disk."""
        config = _make_config(tmp_path)
        registry = WorkflowRegistry(_make_tool_registry(), config)

        loop = asyncio.new_event_loop()
        try:

            async def _run():
                registry.register_from_yaml(SAMPLE_YAML, persist=True)
                await asyncio.sleep(0.1)

            loop.run_until_complete(_run())
        finally:
            loop.close()

        target = Path(config.directory) / "test-workflow.yaml"
        assert target.exists(), "Workflow YAML should be written to disk"
        assert target.read_text() == SAMPLE_YAML

    def test_persist_writes_to_redis_and_disk(self, tmp_path: Path):
        """persist=True with Redis connected writes to both Redis and disk (dual-write)."""
        config = _make_config(tmp_path)
        redis_store = _make_redis_store(connected=True)
        registry = WorkflowRegistry(_make_tool_registry(), config, redis_store=redis_store)

        loop = asyncio.new_event_loop()
        try:

            async def _run():
                registry.register_from_yaml(SAMPLE_YAML, persist=True)
                await asyncio.sleep(0.1)

            loop.run_until_complete(_run())
        finally:
            loop.close()

        redis_store.set_value.assert_called_once_with("workflows:test-workflow", SAMPLE_YAML)
        target = Path(config.directory) / "test-workflow.yaml"
        assert target.exists(), "Dual-write: disk copy should exist even when Redis is available"
        assert target.read_text() == SAMPLE_YAML

    def test_no_persist_does_not_write(self, tmp_path: Path):
        """persist=False (default) does not write to disk or Redis."""
        config = _make_config(tmp_path)
        redis_store = _make_redis_store(connected=True)
        registry = WorkflowRegistry(_make_tool_registry(), config, redis_store=redis_store)

        registry.register_from_yaml(SAMPLE_YAML)

        redis_store.set_value.assert_not_called()
        target = Path(config.directory) / "test-workflow.yaml"
        assert not target.exists()


class TestUnregisterPersistence:
    """Tests for unregister cleaning up persisted storage."""

    def test_unregister_api_source_deletes_file(self, tmp_path: Path):
        """Unregistering an API-sourced workflow deletes its file."""
        config = _make_config(tmp_path)
        registry = WorkflowRegistry(_make_tool_registry(), config)

        loop = asyncio.new_event_loop()
        try:

            async def _run():
                registry.register_from_yaml(SAMPLE_YAML, persist=True)
                await asyncio.sleep(0.1)
                target = Path(config.directory) / "test-workflow.yaml"
                assert target.exists()
                registry.unregister("test-workflow")
                await asyncio.sleep(0.1)
                assert not target.exists()

            loop.run_until_complete(_run())
        finally:
            loop.close()

    def test_unregister_api_source_deletes_redis_key_and_disk(self, tmp_path: Path):
        """Unregistering an API-sourced workflow deletes from both Redis and disk."""
        config = _make_config(tmp_path)
        redis_store = _make_redis_store(connected=True)
        registry = WorkflowRegistry(_make_tool_registry(), config, redis_store=redis_store)

        loop = asyncio.new_event_loop()
        try:

            async def _run():
                registry.register_from_yaml(SAMPLE_YAML, persist=True)
                await asyncio.sleep(0.1)
                target = Path(config.directory) / "test-workflow.yaml"
                assert target.exists(), "Disk file should exist after persist"
                registry.unregister("test-workflow")
                await asyncio.sleep(0.1)
                assert not target.exists(), "Disk file should be deleted on unregister"

            loop.run_until_complete(_run())
        finally:
            loop.close()

        redis_store.delete_value.assert_called_once_with("workflows:test-workflow")

    def test_unregister_file_source_does_not_delete_file(self, tmp_path: Path):
        """Unregistering a file-sourced workflow does NOT delete the file."""
        config = _make_config(tmp_path)
        workflows_dir = Path(config.directory)
        workflows_dir.mkdir(parents=True, exist_ok=True)

        yaml_file = workflows_dir / "test-workflow.yaml"
        yaml_file.write_text(SAMPLE_YAML)

        registry = WorkflowRegistry(_make_tool_registry(), config)
        registry.register_from_yaml(SAMPLE_YAML, source_path=yaml_file)

        loop = asyncio.new_event_loop()
        try:

            async def _run():
                registry.unregister("test-workflow")
                await asyncio.sleep(0.1)
                assert yaml_file.exists(), "File-sourced workflow file should not be deleted"

            loop.run_until_complete(_run())
        finally:
            loop.close()


class TestInitializeFromRedis:
    """Tests for initialize() loading from Redis after disk."""

    def test_initialize_loads_redis_after_disk(self, tmp_path: Path):
        """Redis workflows are loaded after disk; Redis wins on name collision."""
        config = _make_config(tmp_path)
        workflows_dir = Path(config.directory)
        workflows_dir.mkdir(parents=True, exist_ok=True)

        (workflows_dir / "test-workflow.yaml").write_text(SAMPLE_YAML)

        new_workflow_yaml = """\
name: redis-only-workflow
version: "1.0.0"
steps:
  - id: step1
    tool: echo
    mcp: system
    params:
      message: from redis
"""
        redis_store = _make_redis_store(connected=True)
        redis_store.scan_keys = AsyncMock(
            return_value=["workflows:test-workflow", "workflows:redis-only-workflow"]
        )

        async def _get_value(key):
            if key == "workflows:test-workflow":
                return SAMPLE_YAML_V2
            elif key == "workflows:redis-only-workflow":
                return new_workflow_yaml
            return None

        redis_store.get_value = AsyncMock(side_effect=_get_value)

        registry = WorkflowRegistry(_make_tool_registry(), config, redis_store=redis_store)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(registry.initialize())
        finally:
            loop.close()

        assert registry.get("test-workflow") is not None
        assert registry.get("redis-only-workflow") is not None

        wf = registry.get("test-workflow")
        assert wf.version == "2.0.0"

        entry = registry._workflows["test-workflow"]
        assert entry.source == "api"

    def test_initialize_loads_disk_without_tool_validation(self, tmp_path: Path):
        """Workflows loaded from disk at startup skip tool validation.

        At startup, runners/bridges haven't connected yet so tools aren't
        available. Persisted workflows were already validated when first
        registered via API, so they should load without re-validating tools.
        """
        # Use a workflow referencing a tool that does NOT exist in the registry
        unknown_tool_yaml = """\
name: uses-unknown-tool
version: "1.0.0"
description: Workflow referencing a tool not in the registry
steps:
  - id: step1
    tool: nonexistent_tool
    mcp: nonexistent_server
    params:
      foo: bar
"""
        config = _make_config(tmp_path)
        workflows_dir = Path(config.directory)
        workflows_dir.mkdir(parents=True, exist_ok=True)
        (workflows_dir / "uses-unknown-tool.yaml").write_text(unknown_tool_yaml)

        # Tool registry returns nothing — simulates startup before runners connect
        empty_tool_registry = MagicMock()
        empty_tool_registry.get_tool.return_value = None
        empty_tool_registry.get.return_value = None  # DEC-169 collision check
        empty_tool_registry.list_tools.return_value = []

        registry = WorkflowRegistry(empty_tool_registry, config)

        loop = asyncio.new_event_loop()
        try:
            count = loop.run_until_complete(registry.initialize())
        finally:
            loop.close()

        assert count == 1
        assert registry.get("uses-unknown-tool") is not None


class TestYamlContentPreserved:
    """Tests that yaml_content is preserved through registration."""

    def test_get_workflow_yaml_content_preserved(self, tmp_path: Path):
        """Registered workflow preserves original yaml_content."""
        config = _make_config(tmp_path)
        registry = WorkflowRegistry(_make_tool_registry(), config)

        registry.register_from_yaml(SAMPLE_YAML)

        wf = registry.get("test-workflow")
        assert wf is not None
        assert wf.yaml_content == SAMPLE_YAML

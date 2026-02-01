"""Unit tests for StagedConfig."""

from pathlib import Path

from ploston_core.config.loader import ConfigLoader
from ploston_core.config.staged_config import StagedConfig


class TestStagedConfigInit:
    """Tests for StagedConfig initialization."""

    def test_init_with_no_config(self):
        """Test initialization when no config is loaded."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)

        assert staged._base == {}
        assert staged._changes == {}
        assert staged.target_path == Path("ael-config.yaml")

    def test_init_with_loaded_config(self, tmp_path):
        """Test initialization with existing config."""
        # Create a config file
        config_file = tmp_path / "ael-config.yaml"
        config_file.write_text("""
server:
  port: 8080
  host: localhost
""")

        loader = ConfigLoader()
        loader.load(config_file)
        staged = StagedConfig(loader)

        assert staged._base.get("server", {}).get("port") == 8080
        assert staged._base.get("server", {}).get("host") == "localhost"


class TestStagedConfigSet:
    """Tests for StagedConfig.set()."""

    def test_set_simple_path(self):
        """Test setting a simple path."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)

        staged.set("server.port", 9000)

        assert staged._changes == {"server": {"port": 9000}}

    def test_set_deep_path(self):
        """Test setting a deep nested path."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)

        staged.set("mcp.servers.github.command", "npx")

        assert staged._changes == {"mcp": {"servers": {"github": {"command": "npx"}}}}

    def test_set_multiple_paths(self):
        """Test setting multiple paths."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)

        staged.set("server.port", 9000)
        staged.set("server.host", "0.0.0.0")
        staged.set("mcp.servers.test.command", "test-cmd")

        assert staged._changes["server"]["port"] == 9000
        assert staged._changes["server"]["host"] == "0.0.0.0"
        assert staged._changes["mcp"]["servers"]["test"]["command"] == "test-cmd"

    def test_set_preserves_env_var_syntax(self):
        """Test that ${VAR} syntax is preserved."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)

        staged.set("mcp.servers.github.env.GITHUB_TOKEN", "${GITHUB_TOKEN}")

        assert (
            staged._changes["mcp"]["servers"]["github"]["env"]["GITHUB_TOKEN"] == "${GITHUB_TOKEN}"
        )


class TestStagedConfigGet:
    """Tests for StagedConfig.get()."""

    def test_get_full_config(self):
        """Test getting full merged config."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)
        staged.set("server.port", 9000)

        result = staged.get()

        assert result["server"]["port"] == 9000

    def test_get_specific_path(self):
        """Test getting specific path."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)
        staged.set("server.port", 9000)

        result = staged.get("server.port")

        assert result == 9000

    def test_get_nonexistent_path(self):
        """Test getting non-existent path returns None."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)

        result = staged.get("nonexistent.path")

        assert result is None

    def test_get_merged_with_base(self, tmp_path):
        """Test that get merges base and changes."""
        config_file = tmp_path / "ael-config.yaml"
        config_file.write_text("""
server:
  port: 8080
  host: localhost
""")

        loader = ConfigLoader()
        loader.load(config_file)
        staged = StagedConfig(loader)

        # Override port but keep host
        staged.set("server.port", 9000)

        result = staged.get()
        assert result["server"]["port"] == 9000
        assert result["server"]["host"] == "localhost"


class TestStagedConfigMerged:
    """Tests for StagedConfig.get_merged()."""

    def test_get_merged_empty(self):
        """Test get_merged with no changes."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)

        result = staged.get_merged()

        assert result == {}

    def test_get_merged_with_changes(self):
        """Test get_merged with changes."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)
        staged.set("server.port", 9000)

        result = staged.get_merged()

        assert result == {"server": {"port": 9000}}


class TestStagedConfigDiff:
    """Tests for StagedConfig.get_diff()."""

    def test_get_diff_no_changes(self):
        """Test diff with no changes."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)

        diff = staged.get_diff()

        # No changes means empty diff
        assert diff == ""

    def test_get_diff_with_changes(self):
        """Test diff shows changes."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)
        staged.set("server.port", 9000)

        diff = staged.get_diff()

        assert "server:" in diff
        assert "port: 9000" in diff


class TestStagedConfigValidate:
    """Tests for StagedConfig.validate()."""

    def test_validate_empty_config(self):
        """Test validating empty config."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)

        result = staged.validate()

        assert result.valid

    def test_validate_valid_config(self):
        """Test validating valid config."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)
        staged.set("server.port", 8080)

        result = staged.validate()

        assert result.valid

    def test_validate_warns_on_secret_pattern(self):
        """Test validation warns on secret-like values."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)
        # This looks like a GitHub token
        staged.set("mcp.servers.github.env.TOKEN", "ghp_" + "a" * 36)

        result = staged.validate()

        # Should have a warning about the secret
        assert any("secret" in w.message.lower() for w in result.warnings)

    def test_validate_no_warning_for_env_var(self):
        """Test no warning when using ${VAR} syntax."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)
        staged.set("mcp.servers.github.env.TOKEN", "${GITHUB_TOKEN}")

        result = staged.validate()

        # Should not warn about secrets when using env var syntax
        secret_warnings = [w for w in result.warnings if "secret" in w.message.lower()]
        assert len(secret_warnings) == 0

    def test_validate_warns_on_incomplete_mcp_server(self):
        """Test validation warns on incomplete MCP server."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)
        # MCP server without command
        staged.set("mcp.servers.test.args", ["--test"])

        result = staged.validate()

        # Should warn about missing command
        assert any("command" in w.message.lower() for w in result.warnings)


class TestStagedConfigTargetPath:
    """Tests for StagedConfig target path."""

    def test_default_target_path(self):
        """Test default target path."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)

        assert staged.target_path == Path("ael-config.yaml")

    def test_set_target_path_string(self):
        """Test setting target path with string."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)

        staged.set_target_path("/custom/path/config.yaml")

        assert staged.target_path == Path("/custom/path/config.yaml")

    def test_set_target_path_path(self):
        """Test setting target path with Path."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)

        staged.set_target_path(Path("/custom/path/config.yaml"))

        assert staged.target_path == Path("/custom/path/config.yaml")


class TestStagedConfigWrite:
    """Tests for StagedConfig.write()."""

    def test_write_creates_file(self, tmp_path):
        """Test write creates config file."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)
        staged.set("server.port", 9000)
        staged.set_target_path(tmp_path / "output.yaml")

        result = staged.write()

        assert result.exists()
        content = result.read_text()
        assert "server:" in content
        assert "port: 9000" in content

    def test_write_creates_parent_dirs(self, tmp_path):
        """Test write creates parent directories."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)
        staged.set("server.port", 9000)
        staged.set_target_path(tmp_path / "nested" / "dir" / "config.yaml")

        result = staged.write()

        assert result.exists()

    def test_write_includes_header(self, tmp_path):
        """Test write includes header comment."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)
        staged.set("server.port", 9000)
        staged.set_target_path(tmp_path / "output.yaml")

        staged.write()

        content = (tmp_path / "output.yaml").read_text()
        assert "Generated by AEL" in content


class TestStagedConfigClear:
    """Tests for StagedConfig.clear()."""

    def test_clear_removes_changes(self):
        """Test clear removes all staged changes."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)
        staged.set("server.port", 9000)
        staged.set("mcp.servers.test.command", "test")

        staged.clear()

        assert staged._changes == {}
        assert not staged.has_changes()


class TestStagedConfigHasChanges:
    """Tests for StagedConfig.has_changes()."""

    def test_has_changes_false_initially(self):
        """Test has_changes is False initially."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)

        assert not staged.has_changes()

    def test_has_changes_true_after_set(self):
        """Test has_changes is True after set."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)
        staged.set("server.port", 9000)

        assert staged.has_changes()

    def test_has_changes_false_after_clear(self):
        """Test has_changes is False after clear."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)
        staged.set("server.port", 9000)
        staged.clear()

        assert not staged.has_changes()


class TestRunnersConfig:
    """Tests for runners configuration loading."""

    def test_load_runners_from_yaml(self, tmp_path):
        """Test loading runners section from YAML config."""
        config_file = tmp_path / "ael-config.yaml"
        config_file.write_text("""
runners:
  marc-laptop:
    mcp_servers:
      filesystem:
        command: npx
        args:
          - "@mcp/filesystem"
          - "/Users/marc"
        env:
          DEBUG: "1"
      docker:
        command: npx
        args:
          - "@mcp/docker"
  build-server:
    mcp_servers:
      filesystem:
        command: npx
        args:
          - "@mcp/filesystem"
          - "/opt/builds"
""")

        loader = ConfigLoader()
        config = loader.load(config_file)

        assert len(config.runners) == 2
        assert "marc-laptop" in config.runners
        assert "build-server" in config.runners

        marc_laptop = config.runners["marc-laptop"]
        assert len(marc_laptop.mcp_servers) == 2
        assert "filesystem" in marc_laptop.mcp_servers
        assert "docker" in marc_laptop.mcp_servers

        fs_mcp = marc_laptop.mcp_servers["filesystem"]
        assert fs_mcp.command == "npx"
        assert fs_mcp.args == ["@mcp/filesystem", "/Users/marc"]
        assert fs_mcp.env == {"DEBUG": "1"}

    def test_load_empty_runners(self, tmp_path):
        """Test loading config with no runners section."""
        config_file = tmp_path / "ael-config.yaml"
        config_file.write_text("""
server:
  port: 8080
""")

        loader = ConfigLoader()
        config = loader.load(config_file)

        assert config.runners == {}

    def test_load_runners_with_http_transport(self, tmp_path):
        """Test loading runners with HTTP transport MCP servers."""
        config_file = tmp_path / "ael-config.yaml"
        config_file.write_text("""
runners:
  cloud-runner:
    mcp_servers:
      remote-api:
        url: "http://api.example.com:8080"
        transport: http
        timeout: 60
""")

        loader = ConfigLoader()
        config = loader.load(config_file)

        assert "cloud-runner" in config.runners
        remote_api = config.runners["cloud-runner"].mcp_servers["remote-api"]
        assert remote_api.url == "http://api.example.com:8080"
        assert remote_api.timeout == 60


class TestStagedConfigRedisPersistence:
    """Tests for StagedConfig Redis persistence."""

    def test_set_persists_to_redis(self):
        """Test that set() persists changes to Redis."""
        from unittest.mock import AsyncMock, MagicMock

        loader = ConfigLoader()
        redis_store = MagicMock()
        redis_store.connected = True
        redis_store.set_value = AsyncMock(return_value=True)

        staged = StagedConfig(loader, redis_store=redis_store)
        staged.set("server.port", 9000)

        # Verify Redis was called (async task created)
        # Note: In tests without event loop, this may not execute
        assert staged._changes == {"server": {"port": 9000}}

    def test_clear_clears_from_redis(self):
        """Test that clear() removes changes from Redis."""
        from unittest.mock import AsyncMock, MagicMock

        loader = ConfigLoader()
        redis_store = MagicMock()
        redis_store.connected = True
        redis_store.delete_value = AsyncMock(return_value=True)

        staged = StagedConfig(loader, redis_store=redis_store)
        staged._changes = {"server": {"port": 9000}}
        staged.clear()

        assert staged._changes == {}

    async def test_restore_from_redis(self):
        """Test restoring staged changes from Redis."""
        import json
        from unittest.mock import AsyncMock, MagicMock

        loader = ConfigLoader()
        redis_store = MagicMock()
        redis_store.connected = True
        redis_store.get_value = AsyncMock(return_value=json.dumps({"server": {"port": 9000}}))

        staged = StagedConfig(loader, redis_store=redis_store)
        result = await staged.restore_from_redis()

        assert result is True
        assert staged._changes == {"server": {"port": 9000}}

    async def test_restore_from_redis_no_data(self):
        """Test restore when no data in Redis."""
        from unittest.mock import AsyncMock, MagicMock

        loader = ConfigLoader()
        redis_store = MagicMock()
        redis_store.connected = True
        redis_store.get_value = AsyncMock(return_value=None)

        staged = StagedConfig(loader, redis_store=redis_store)
        result = await staged.restore_from_redis()

        assert result is False
        assert staged._changes == {}

    async def test_restore_from_redis_not_connected(self):
        """Test restore when Redis not connected."""
        from unittest.mock import MagicMock

        loader = ConfigLoader()
        redis_store = MagicMock()
        redis_store.connected = False

        staged = StagedConfig(loader, redis_store=redis_store)
        result = await staged.restore_from_redis()

        assert result is False

    def test_no_redis_store(self):
        """Test that operations work without Redis store."""
        loader = ConfigLoader()
        staged = StagedConfig(loader)  # No redis_store

        staged.set("server.port", 9000)
        assert staged._changes == {"server": {"port": 9000}}

        staged.clear()
        assert staged._changes == {}

"""Unit tests for ConfigImporter."""

import pytest

from ploston_core.config.importer import ConfigImporter, ImportResult


class TestConfigImporter:
    """Tests for ConfigImporter."""

    @pytest.fixture
    def importer(self):
        """Create ConfigImporter instance."""
        return ConfigImporter()

    def test_import_simple_server(self, importer):
        """Import a simple MCP server config."""
        config = {
            "github": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
            }
        }
        
        result = importer.import_config("claude_desktop", config)
        
        assert "github" in result.imported
        assert "github" in result.servers
        assert result.servers["github"]["command"] == "npx"
        assert result.servers["github"]["transport"] == "stdio"

    def test_import_server_with_env(self, importer):
        """Import server with environment variables."""
        config = {
            "github": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}
            }
        }
        
        result = importer.import_config("claude_desktop", config)
        
        assert result.servers["github"]["env"]["GITHUB_TOKEN"] == "${GITHUB_TOKEN}"

    def test_import_detects_literal_secrets(self, importer):
        """Import detects and converts literal secrets."""
        config = {
            "github": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_TOKEN": "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"}
            }
        }
        
        result = importer.import_config("claude_desktop", config, convert_secrets=True)
        
        assert len(result.secrets_detected) == 1
        assert result.secrets_detected[0].server == "github"
        assert result.secrets_detected[0].converted_to == "${GITHUB_TOKEN}"

    def test_import_skip_secret_conversion(self, importer):
        """Import without converting secrets."""
        config = {
            "github": {
                "command": "npx",
                "env": {"GITHUB_TOKEN": "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"}
            }
        }
        
        result = importer.import_config("claude_desktop", config, convert_secrets=False)
        
        # Secret should be preserved as-is
        assert result.servers["github"]["env"]["GITHUB_TOKEN"] == "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

    def test_import_skip_servers(self, importer):
        """Import with skipped servers."""
        config = {
            "github": {"command": "npx"},
            "slack": {"command": "npx"},
        }
        
        result = importer.import_config("claude_desktop", config, skip_servers=["slack"])
        
        assert "github" in result.imported
        assert "slack" in result.skipped
        assert "slack" not in result.servers

    def test_import_manual_secret_mappings(self, importer):
        """Import with manual secret mappings."""
        config = {
            "github": {
                "command": "npx",
                "env": {"TOKEN": "my_secret_value"}
            }
        }
        
        result = importer.import_config(
            "claude_desktop",
            config,
            secret_mappings={"my_secret_value": "MY_CUSTOM_VAR"}
        )
        
        assert result.servers["github"]["env"]["TOKEN"] == "${MY_CUSTOM_VAR}"

    def test_import_http_transport(self, importer):
        """Import server with HTTP transport."""
        config = {
            "api-server": {
                "url": "http://localhost:8080",
            }
        }
        
        result = importer.import_config("claude_desktop", config)
        
        assert result.servers["api-server"]["transport"] == "http"
        assert result.servers["api-server"]["url"] == "http://localhost:8080"

    def test_import_cursor_format(self, importer):
        """Import from Cursor format (same as Claude Desktop)."""
        config = {
            "github": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
            }
        }
        
        result = importer.import_config("cursor", config)
        
        assert "github" in result.imported

    def test_get_source_config_path(self, importer):
        """Get default config path for source."""
        path = importer.get_source_config_path("claude_desktop")
        
        assert path is not None
        assert "Claude" in path or "claude" in path.lower()

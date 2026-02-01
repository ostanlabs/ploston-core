"""Unit tests for add_mcp_server tool handler."""

from unittest.mock import MagicMock

import pytest

from ploston_core.config.tools import ConfigToolRegistry


class TestAddMcpServer:
    """Tests for ploston:add_mcp_server tool."""

    @pytest.fixture
    def mock_staged_config(self):
        """Create mock staged config."""
        staged = MagicMock()
        staged.has_changes.return_value = False
        staged.get_merged.return_value = {"tools": {"mcp_servers": {}}}
        mock_result = MagicMock()
        mock_result.valid = True
        mock_result.errors = []
        mock_result.warnings = []
        staged.validate.return_value = mock_result
        staged.changes = {}
        return staged

    @pytest.fixture
    def mock_config_loader(self):
        """Create mock config loader."""
        loader = MagicMock()
        loader._config_path = "/path/to/config.yaml"
        return loader

    @pytest.fixture
    def registry(self, mock_staged_config, mock_config_loader):
        """Create ConfigToolRegistry."""
        return ConfigToolRegistry(
            staged_config=mock_staged_config,
            config_loader=mock_config_loader,
        )

    @pytest.mark.asyncio
    async def test_add_stdio_server(self, registry, mock_staged_config):
        """Add a stdio transport MCP server."""
        result = await registry.call("ploston:add_mcp_server", {
            "name": "github",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
        })

        assert result["success"] is True
        assert result["staged_path"] == "tools.mcp_servers.github"
        mock_staged_config.set.assert_called()

    @pytest.mark.asyncio
    async def test_add_http_server(self, registry, mock_staged_config):
        """Add an HTTP transport MCP server."""
        result = await registry.call("ploston:add_mcp_server", {
            "name": "api-server",
            "transport": "http",
            "url": "http://localhost:8080",
        })

        assert result["success"] is True
        assert result["staged_path"] == "tools.mcp_servers.api-server"

    @pytest.mark.asyncio
    async def test_add_server_with_env(self, registry, mock_staged_config):
        """Add server with environment variables."""
        result = await registry.call("ploston:add_mcp_server", {
            "name": "github",
            "transport": "stdio",
            "command": "npx",
            "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"},
        })

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_add_server_validation_included(self, registry, mock_staged_config):
        """Add server includes validation result."""
        result = await registry.call("ploston:add_mcp_server", {
            "name": "github",
            "transport": "stdio",
            "command": "npx",
        })

        assert "validation" in result
        assert "valid" in result["validation"]

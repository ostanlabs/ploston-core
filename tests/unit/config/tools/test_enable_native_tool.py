"""Unit tests for enable_native_tool tool handler."""

from unittest.mock import MagicMock

import pytest

from ploston_core.config.tools import ConfigToolRegistry


class TestEnableNativeTool:
    """Tests for ploston:enable_native_tool tool."""

    @pytest.fixture
    def mock_staged_config(self):
        """Create mock staged config."""
        staged = MagicMock()
        staged.has_changes.return_value = False
        staged.get_merged.return_value = {"tools": {"native_tools": {}}}
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
    async def test_enable_kafka(self, registry, mock_staged_config):
        """Enable Kafka native tool."""
        result = await registry.call(
            "ploston:enable_native_tool",
            {
                "tool": "kafka",
                "config": {
                    "bootstrap_servers": "localhost:9092",
                },
            },
        )

        assert result["success"] is True
        assert result["staged_path"] == "tools.native_tools.kafka"
        mock_staged_config.set.assert_called()

    @pytest.mark.asyncio
    async def test_enable_firecrawl(self, registry, mock_staged_config):
        """Enable Firecrawl native tool."""
        result = await registry.call(
            "ploston:enable_native_tool",
            {
                "tool": "firecrawl",
                "config": {
                    "api_key": "${FIRECRAWL_API_KEY}",
                },
            },
        )

        assert result["success"] is True
        assert result["staged_path"] == "tools.native_tools.firecrawl"

    @pytest.mark.asyncio
    async def test_enable_ollama(self, registry, mock_staged_config):
        """Enable Ollama native tool."""
        result = await registry.call(
            "ploston:enable_native_tool",
            {
                "tool": "ollama",
                "config": {
                    "host": "http://localhost:11434",
                    "default_model": "llama2",
                },
            },
        )

        assert result["success"] is True
        assert result["staged_path"] == "tools.native_tools.ollama"

    @pytest.mark.asyncio
    async def test_enable_filesystem(self, registry, mock_staged_config):
        """Enable filesystem native tool."""
        result = await registry.call(
            "ploston:enable_native_tool",
            {
                "tool": "filesystem",
                "config": {
                    "workspace_dir": "/home/user/workspace",
                },
            },
        )

        assert result["success"] is True
        assert result["staged_path"] == "tools.native_tools.filesystem"

    @pytest.mark.asyncio
    async def test_enable_unknown_tool(self, registry):
        """Enable unknown tool fails."""
        result = await registry.call(
            "ploston:enable_native_tool",
            {
                "tool": "unknown_tool",
                "config": {},
            },
        )

        assert result["success"] is False
        assert "error" in result
        assert "unknown" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_enable_tool_validation_included(self, registry, mock_staged_config):
        """Enable tool includes validation result."""
        result = await registry.call(
            "ploston:enable_native_tool",
            {
                "tool": "kafka",
                "config": {"bootstrap_servers": "localhost:9092"},
            },
        )

        assert "validation" in result
        assert "valid" in result["validation"]

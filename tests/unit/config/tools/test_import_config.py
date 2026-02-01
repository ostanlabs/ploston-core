"""Unit tests for import_config tool handler."""

from unittest.mock import MagicMock, patch

import pytest

from ploston_core.config.tools import ConfigToolRegistry


class TestImportConfig:
    """Tests for ploston:import_config tool."""

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
    async def test_import_from_claude_desktop(self, registry, mock_staged_config):
        """Import from Claude Desktop config."""
        # Mock the importer to not read from real file
        with patch("ploston_core.config.tools.import_config.ConfigImporter") as mock_importer_class:
            mock_importer = MagicMock()
            mock_result = MagicMock()
            mock_result.imported = ["github"]
            mock_result.skipped = []
            mock_result.servers = {"github": {"command": "npx", "transport": "stdio"}}
            mock_result.secrets_detected = []
            mock_result.errors = []
            mock_importer.import_config.return_value = mock_result
            mock_importer_class.return_value = mock_importer

            result = await registry.call(
                "ploston:import_config",
                {
                    "source": "claude_desktop",
                    "servers": {
                        "github": {
                            "command": "npx",
                            "args": ["-y", "@modelcontextprotocol/server-github"],
                        }
                    },
                },
            )

            assert result["success"] is True
            assert "github" in result["imported"]

    @pytest.mark.asyncio
    async def test_import_from_cursor(self, registry, mock_staged_config):
        """Import from Cursor config."""
        with patch("ploston_core.config.tools.import_config.ConfigImporter") as mock_importer_class:
            mock_importer = MagicMock()
            mock_result = MagicMock()
            mock_result.imported = ["github"]
            mock_result.skipped = []
            mock_result.servers = {"github": {"command": "npx", "transport": "stdio"}}
            mock_result.secrets_detected = []
            mock_result.errors = []
            mock_importer.import_config.return_value = mock_result
            mock_importer_class.return_value = mock_importer

            result = await registry.call(
                "ploston:import_config",
                {
                    "source": "cursor",
                    "servers": {
                        "github": {
                            "command": "npx",
                        }
                    },
                },
            )

            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_import_with_skip_servers(self, registry, mock_staged_config):
        """Import with skipped servers."""
        with patch("ploston_core.config.tools.import_config.ConfigImporter") as mock_importer_class:
            mock_importer = MagicMock()
            mock_result = MagicMock()
            mock_result.imported = ["github"]
            mock_result.skipped = ["slack"]
            mock_result.servers = {"github": {"command": "npx", "transport": "stdio"}}
            mock_result.secrets_detected = []
            mock_result.errors = []
            mock_importer.import_config.return_value = mock_result
            mock_importer_class.return_value = mock_importer

            result = await registry.call(
                "ploston:import_config",
                {
                    "source": "claude_desktop",
                    "servers": {
                        "github": {"command": "npx"},
                        "slack": {"command": "npx"},
                    },
                    "skip_servers": ["slack"],
                },
            )

            assert result["success"] is True
            assert "github" in result["imported"]
            assert "slack" in result["skipped"]

    @pytest.mark.asyncio
    async def test_import_with_convert_secrets(self, registry, mock_staged_config):
        """Import with secret conversion."""
        with patch("ploston_core.config.tools.import_config.ConfigImporter") as mock_importer_class:
            mock_importer = MagicMock()
            mock_result = MagicMock()
            mock_result.imported = ["github"]
            mock_result.skipped = []
            mock_result.servers = {"github": {"command": "npx", "transport": "stdio"}}
            mock_result.secrets_detected = [
                MagicMock(
                    server="github",
                    field="env.GITHUB_TOKEN",
                    original="ghp_***",
                    converted_to="${GITHUB_TOKEN}",
                    action_required="Set GITHUB_TOKEN",
                )
            ]
            mock_result.errors = []
            mock_importer.import_config.return_value = mock_result
            mock_importer_class.return_value = mock_importer

            result = await registry.call(
                "ploston:import_config",
                {
                    "source": "claude_desktop",
                    "servers": {
                        "github": {
                            "command": "npx",
                            "env": {"GITHUB_TOKEN": "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"},
                        }
                    },
                    "convert_secrets": True,
                },
            )

            assert result["success"] is True
            assert len(result.get("secrets_detected", [])) > 0

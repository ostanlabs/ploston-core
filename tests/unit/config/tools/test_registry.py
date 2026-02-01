"""Unit tests for ConfigToolRegistry."""

from unittest.mock import MagicMock

import pytest

from ploston_core.config.tools import (
    CONFIG_TOOL_SCHEMAS,
    CONFIGURE_TOOL_SCHEMA,
    ConfigToolRegistry,
)


class TestConfigToolRegistry:
    """Tests for ConfigToolRegistry."""

    @pytest.fixture
    def mock_staged_config(self):
        """Create mock staged config."""
        staged = MagicMock()
        staged.has_changes.return_value = False
        staged.get_merged.return_value = {"logging": {"level": "INFO"}}
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

    def test_get_for_mcp_exposure(self, registry):
        """Get config tools for MCP exposure."""
        tools = registry.get_for_mcp_exposure()

        assert len(tools) == 6
        tool_names = [t["name"] for t in tools]
        assert "ael:config_get" in tool_names
        assert "ael:config_set" in tool_names
        assert "ael:config_validate" in tool_names
        assert "ael:config_schema" in tool_names
        assert "ael:config_location" in tool_names
        assert "ael:config_done" in tool_names

    def test_get_configure_tool_for_mcp_exposure(self, registry):
        """Get configure tool for running mode."""
        tool = registry.get_configure_tool_for_mcp_exposure()

        assert tool["name"] == "ael:configure"

    @pytest.mark.asyncio
    async def test_call_config_get(self, registry):
        """Call config_get through registry."""
        result = await registry.call("ael:config_get", {})

        assert "value" in result
        assert "source" in result

    @pytest.mark.asyncio
    async def test_call_config_set(self, registry):
        """Call config_set through registry."""
        result = await registry.call(
            "ael:config_set",
            {"path": "logging.level", "value": "DEBUG"},
        )

        assert result["staged"] is True

    @pytest.mark.asyncio
    async def test_call_config_validate(self, registry):
        """Call config_validate through registry."""
        result = await registry.call("ael:config_validate", {})

        assert "valid" in result

    @pytest.mark.asyncio
    async def test_call_config_schema(self, registry):
        """Call config_schema through registry."""
        result = await registry.call("ael:config_schema", {})

        assert "sections" in result

    @pytest.mark.asyncio
    async def test_call_unknown_tool(self, registry):
        """Call unknown tool raises error."""
        with pytest.raises(Exception):
            await registry.call("ael:unknown", {})


class TestConfigToolSchemas:
    """Tests for tool schemas."""

    def test_all_schemas_have_name(self):
        """All schemas have name."""
        for schema in CONFIG_TOOL_SCHEMAS:
            assert "name" in schema
            assert schema["name"].startswith("ael:config_")

    def test_all_schemas_have_description(self):
        """All schemas have description."""
        for schema in CONFIG_TOOL_SCHEMAS:
            assert "description" in schema
            assert len(schema["description"]) > 0

    def test_all_schemas_have_input_schema(self):
        """All schemas have inputSchema."""
        for schema in CONFIG_TOOL_SCHEMAS:
            assert "inputSchema" in schema
            assert schema["inputSchema"]["type"] == "object"

    def test_configure_tool_schema(self):
        """Configure tool schema is valid."""
        assert CONFIGURE_TOOL_SCHEMA["name"] == "ael:configure"
        assert "description" in CONFIGURE_TOOL_SCHEMA
        assert "inputSchema" in CONFIGURE_TOOL_SCHEMA


class TestPlostontoolSchemas:
    """Tests for ploston: prefixed tool schemas."""

    def test_ploston_tool_schemas_count(self):
        """Verify correct number of ploston: tools."""
        from ploston_core.config.tools import PLOSTON_TOOL_SCHEMAS
        
        # 8 new tools + 4 renamed tools = 12 total
        assert len(PLOSTON_TOOL_SCHEMAS) == 12

    def test_all_ploston_schemas_have_name(self):
        """All ploston schemas have name."""
        from ploston_core.config.tools import PLOSTON_TOOL_SCHEMAS
        
        for schema in PLOSTON_TOOL_SCHEMAS:
            assert "name" in schema
            assert schema["name"].startswith("ploston:")

    def test_all_ploston_schemas_have_description(self):
        """All ploston schemas have description."""
        from ploston_core.config.tools import PLOSTON_TOOL_SCHEMAS
        
        for schema in PLOSTON_TOOL_SCHEMAS:
            assert "description" in schema
            assert len(schema["description"]) > 0

    def test_all_ploston_schemas_have_input_schema(self):
        """All ploston schemas have inputSchema."""
        from ploston_core.config.tools import PLOSTON_TOOL_SCHEMAS
        
        for schema in PLOSTON_TOOL_SCHEMAS:
            assert "inputSchema" in schema
            assert schema["inputSchema"]["type"] == "object"

    def test_get_for_mcp_exposure_with_ploston_prefix(self):
        """Get ploston: prefixed tools for MCP exposure."""
        from unittest.mock import MagicMock
        from ploston_core.config.tools import ConfigToolRegistry
        
        staged = MagicMock()
        loader = MagicMock()
        registry = ConfigToolRegistry(staged, loader)
        
        tools = registry.get_for_mcp_exposure(use_ploston_prefix=True)
        
        assert len(tools) == 12
        tool_names = [t["name"] for t in tools]
        assert "ploston:get_setup_context" in tool_names
        assert "ploston:add_mcp_server" in tool_names
        assert "ploston:enable_native_tool" in tool_names
        assert "ploston:import_config" in tool_names
        assert "ploston:remove_mcp_server" in tool_names
        assert "ploston:disable_native_tool" in tool_names
        assert "ploston:config_diff" in tool_names
        assert "ploston:config_reset" in tool_names
        assert "ploston:config_get" in tool_names
        assert "ploston:config_set" in tool_names
        assert "ploston:config_done" in tool_names
        assert "ploston:configure" in tool_names

    def test_get_configure_tool_with_ploston_prefix(self):
        """Get ploston:configure tool for running mode."""
        from unittest.mock import MagicMock
        from ploston_core.config.tools import ConfigToolRegistry
        
        staged = MagicMock()
        loader = MagicMock()
        registry = ConfigToolRegistry(staged, loader)
        
        tool = registry.get_configure_tool_for_mcp_exposure(use_ploston_prefix=True)
        
        assert tool["name"] == "ploston:configure"

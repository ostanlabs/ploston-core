"""Unit tests for config_schema tool."""

import pytest

from ploston_core.config.tools.config_schema import CONFIG_SCHEMA, handle_config_schema


class TestHandleConfigSchema:
    """Tests for handle_config_schema."""

    @pytest.mark.asyncio
    async def test_get_full_schema(self):
        """Get full schema when no section specified."""
        result = await handle_config_schema({})

        assert "sections" in result
        assert "schema" in result
        assert "mcp" in result["sections"]
        assert "logging" in result["sections"]

    @pytest.mark.asyncio
    async def test_get_specific_section(self):
        """Get specific section schema."""
        result = await handle_config_schema({"section": "logging"})

        assert result["section"] == "logging"
        assert "schema" in result
        assert "fields" in result["schema"]
        assert "level" in result["schema"]["fields"]

    @pytest.mark.asyncio
    async def test_get_unknown_section(self):
        """Get unknown section returns error."""
        result = await handle_config_schema({"section": "nonexistent"})

        assert "error" in result
        assert "Unknown section" in result["error"]
        assert "available_sections" in result

    @pytest.mark.asyncio
    async def test_mcp_section_has_servers(self):
        """MCP section has servers field."""
        result = await handle_config_schema({"section": "mcp"})

        assert "servers" in result["schema"]["fields"]

    @pytest.mark.asyncio
    async def test_all_sections_have_description(self):
        """All sections have description."""
        for section_name, section in CONFIG_SCHEMA.items():
            assert "description" in section, f"{section_name} missing description"
            assert "fields" in section, f"{section_name} missing fields"

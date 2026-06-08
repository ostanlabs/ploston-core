"""Specification tests for registry CLI formatters.

These tests assert the CORRECT/intended formatted output per the docstrings
and sensible CLI conventions, not whatever the current implementation happens
to emit. Where the implementation diverges from the contract, the test is
written against the contract so the divergence is surfaced as a failure.
"""

import json
from datetime import UTC, datetime

import pytest

from ploston_core.registry.formatters import format_tool_detail, format_tool_list
from ploston_core.registry.types import ToolDefinition
from ploston_core.types import ToolSource, ToolStatus


def make_tool(**overrides):
    """Build a ToolDefinition with sensible defaults, overridable per-test."""
    params = {
        "name": "echo",
        "description": "Echo back the input.",
        "source": ToolSource.MCP,
        "status": ToolStatus.AVAILABLE,
    }
    params.update(overrides)
    return ToolDefinition(**params)


class TestFormatToolList:
    def test_empty_list_returns_no_tools_message(self):
        assert format_tool_list([]) == "No tools found."

    def test_header_reports_correct_count(self):
        tools = [make_tool(name="a"), make_tool(name="b"), make_tool(name="c")]
        out = format_tool_list(tools)
        # Count must match number of tools, with pluralization marker.
        assert "Found 3 tool(s):" in out

    def test_single_tool_count(self):
        out = format_tool_list([make_tool(name="solo")])
        assert "Found 1 tool(s):" in out

    def test_available_tool_uses_check_icon(self):
        out = format_tool_list([make_tool(status=ToolStatus.AVAILABLE)])
        # Available tools are marked with the check icon, not the cross.
        assert "✓" in out  # ✓
        assert "✗" not in out  # ✗

    def test_unavailable_tool_uses_cross_icon(self):
        out = format_tool_list([make_tool(status=ToolStatus.UNAVAILABLE)])
        assert "✗" in out  # ✗

    def test_unknown_status_is_not_marked_available(self):
        # UNKNOWN is not "available"; per the contract it must use the
        # non-available (cross) icon rather than the success check.
        out = format_tool_list([make_tool(status=ToolStatus.UNKNOWN)])
        assert "✗" in out
        assert "✓" not in out

    def test_source_label_without_server(self):
        out = format_tool_list([make_tool(source=ToolSource.NATIVE, server_name=None)])
        assert "[native]" in out
        # No stray server delimiter when there is no server.
        assert "[native:" not in out

    def test_source_label_with_server(self):
        out = format_tool_list([make_tool(source=ToolSource.MCP, server_name="github")])
        assert "[mcp:github]" in out

    def test_name_and_description_present_per_line(self):
        out = format_tool_list([make_tool(name="my_tool", description="Does a thing.")])
        assert "my_tool" in out
        assert "Does a thing." in out

    def test_one_body_line_per_tool(self):
        tools = [make_tool(name=f"t{i}") for i in range(4)]
        out = format_tool_list(tools)
        body_lines = [ln for ln in out.splitlines() if ln.strip().startswith(("✓", "✗"))]
        assert len(body_lines) == 4

    def test_empty_description_does_not_crash(self):
        out = format_tool_list([make_tool(description="")])
        assert "Found 1 tool(s):" in out


class TestFormatToolDetail:
    def test_header_contains_name(self):
        out = format_tool_detail(make_tool(name="fancy_tool"))
        assert "Tool: fancy_tool" in out

    def test_includes_core_fields(self):
        tool = make_tool(
            name="t",
            description="A description.",
            source=ToolSource.MCP,
            status=ToolStatus.AVAILABLE,
        )
        out = format_tool_detail(tool)
        assert "Description: A description." in out
        assert "mcp" in out  # source value
        assert "available" in out  # status value

    def test_server_name_shown_when_present(self):
        out = format_tool_detail(make_tool(server_name="github"))
        assert "Server:" in out
        assert "github" in out

    def test_server_name_omitted_when_absent(self):
        out = format_tool_detail(make_tool(server_name=None))
        assert "Server:" not in out

    def test_last_seen_rendered_as_isoformat(self):
        ts = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
        out = format_tool_detail(make_tool(last_seen=ts))
        assert ts.isoformat() in out

    def test_last_seen_omitted_when_none(self):
        out = format_tool_detail(make_tool(last_seen=None))
        assert "Last Seen:" not in out

    def test_error_shown_when_present(self):
        out = format_tool_detail(make_tool(error="boom: connection refused"))
        assert "Error:" in out
        assert "boom: connection refused" in out

    def test_error_omitted_when_none(self):
        out = format_tool_detail(make_tool(error=None))
        assert "Error:" not in out

    def test_input_schema_rendered_as_json(self):
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        out = format_tool_detail(make_tool(input_schema=schema))
        assert "Input Schema:" in out
        # The exact JSON serialization (indent=2) must appear.
        assert json.dumps(schema, indent=2) in out

    def test_empty_input_schema_still_labeled(self):
        out = format_tool_detail(make_tool(input_schema={}))
        assert "Input Schema:" in out
        assert json.dumps({}, indent=2) in out

    def test_output_schema_rendered_when_present(self):
        schema = {"type": "string"}
        out = format_tool_detail(make_tool(output_schema=schema))
        assert "Output Schema:" in out
        assert json.dumps(schema, indent=2) in out

    def test_output_schema_section_omitted_when_none(self):
        out = format_tool_detail(make_tool(output_schema=None))
        assert "Output Schema:" not in out


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""S-272 T-863: response_hint on WorkflowToolsProvider._handle_tool_schema.

All three branches (CP-direct, runner-hosted, not-found) must include a
response_hint pointing agents at workflow_run + context.log() because MCP
tool definitions don't carry output schemas.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ploston_core.workflow.tools import WorkflowToolsProvider


def _provider(tool_registry=None, runner_registry=None):
    # workflow_registry and workflow_engine aren't exercised in these paths.
    return WorkflowToolsProvider(
        workflow_registry=MagicMock(),
        tool_registry=tool_registry,
        runner_registry=runner_registry,
    )


def _cp_registry_with(tool_name, server_name, description="desc", input_schema=None):
    reg = MagicMock()
    tool_def = SimpleNamespace(
        name=tool_name,
        description=description,
        input_schema=input_schema or {"type": "object"},
    )
    reg.list_tools = MagicMock(return_value=[tool_def])
    return reg


def _runner_registry_with(runner_name, canonical_tool_name, description=None):
    reg = MagicMock()
    runner = SimpleNamespace(
        name=runner_name,
        available_tools=[
            {
                "name": canonical_tool_name,
                "description": description,
                "inputSchema": {"type": "object"},
            }
        ],
    )
    reg.list = MagicMock(return_value=[runner])
    return reg


# ── WS-04: CP-direct branch includes response_hint ──


@pytest.mark.asyncio
async def test_ws04_cp_direct_includes_response_hint():
    provider = _provider(
        tool_registry=_cp_registry_with("python_exec", "system"),
    )
    resp = await provider._handle_tool_schema({"mcp": "system", "tool": "python_exec"})
    assert resp["source"] == "cp"
    assert "response_hint" in resp
    assert "workflow_run" in resp["response_hint"]
    assert "normalized" in resp["response_hint"]


# ── WS-05: runner-hosted branch includes response_hint ──


@pytest.mark.asyncio
async def test_ws05_runner_hosted_includes_response_hint():
    provider = _provider(
        runner_registry=_runner_registry_with("my_runner", "github__list_commits", "lc"),
    )
    resp = await provider._handle_tool_schema({"mcp": "github", "tool": "list_commits"})
    assert resp["source"] == "runner"
    assert resp["runner"] == "my_runner"
    assert "response_hint" in resp
    assert "workflow_run" in resp["response_hint"]


@pytest.mark.asyncio
async def test_ws05b_runner_string_entry_includes_response_hint():
    """Runner entries listed as bare strings also include response_hint."""
    reg = MagicMock()
    runner = SimpleNamespace(
        name="r1",
        available_tools=["github__list_commits"],
    )
    reg.list = MagicMock(return_value=[runner])

    provider = _provider(runner_registry=reg)
    resp = await provider._handle_tool_schema({"mcp": "github", "tool": "list_commits"})
    assert resp["source"] == "runner"
    assert "response_hint" in resp


# ── WS-06: not-found branch includes response_hint ──


@pytest.mark.asyncio
async def test_ws06_not_found_includes_response_hint():
    # Empty registries on both paths force the not-found branch.
    empty_tr = MagicMock()
    empty_tr.list_tools = MagicMock(return_value=[])
    empty_rr = MagicMock()
    empty_rr.list = MagicMock(return_value=[])

    provider = _provider(tool_registry=empty_tr, runner_registry=empty_rr)
    resp = await provider._handle_tool_schema({"mcp": "github", "tool": "ghost_tool"})
    assert resp["found"] is False
    assert "response_hint" in resp
    assert "workflow_run" in resp["response_hint"]

"""F-088 · T-892 · TSO-01..TSO-04 -- output_schema + suggested_output_schema surfacing.

Covers both CP-direct and runner-hosted branches of
``WorkflowToolsProvider._resolve_tool_schema`` and the batch dispatcher in
``_handle_tool_schema``.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ploston_core.schema import (
    InMemorySchemaBackend,
    ResponsePatternExtractor,
    ToolOutputSchemaStore,
)
from ploston_core.workflow.tools import WorkflowToolsProvider


def _cp_registry(tool_name: str, server: str, output_schema=None):
    reg = MagicMock()
    tool_def = SimpleNamespace(
        name=tool_name,
        description="desc",
        input_schema={"type": "object"},
        output_schema=output_schema,
    )
    reg.list_tools = MagicMock(return_value=[tool_def])
    return reg


def _runner_registry(runner_name: str, canonical: str, output_schema=None):
    reg = MagicMock()
    tool_entry = {
        "name": canonical,
        "description": "d",
        "inputSchema": {"type": "object"},
    }
    if output_schema is not None:
        tool_entry["outputSchema"] = output_schema
    runner = SimpleNamespace(name=runner_name, available_tools=[tool_entry])
    reg.list = MagicMock(return_value=[runner])
    return reg


@pytest.fixture
async def schema_store() -> ToolOutputSchemaStore:
    store = ToolOutputSchemaStore(
        backend=InMemorySchemaBackend(), extractor=ResponsePatternExtractor()
    )
    await store.observe("get_repo", "github", {"id": 7, "private": False})
    await store.observe("get_repo", "github", {"id": 8, "private": True})
    return store


@pytest.mark.asyncio
async def test_cp_direct_includes_declared_and_suggested_schemas(schema_store):
    # TSO-01: CP branch returns declared output_schema + suggested_output_schema.
    declared = {"type": "object", "properties": {"id": {"type": "integer"}}}
    provider = WorkflowToolsProvider(
        workflow_registry=MagicMock(),
        tool_registry=_cp_registry("get_repo", "github", output_schema=declared),
        schema_store=schema_store,
    )
    result = provider._resolve_tool_schema("github", "get_repo")

    assert result["source"] == "cp"
    assert result["output_schema"] == declared
    suggested = result["suggested_output_schema"]
    assert suggested["x-schema_source"] == "learned"
    assert "id" in suggested["properties"]


@pytest.mark.asyncio
async def test_runner_branch_includes_declared_and_suggested_schemas(schema_store):
    # TSO-02: runner branch picks up entry.get("outputSchema") + learned schema.
    declared = {"type": "object", "properties": {"actions": {"type": "array"}}}
    runner_store = ToolOutputSchemaStore(
        backend=InMemorySchemaBackend(), extractor=ResponsePatternExtractor()
    )
    await runner_store.observe("actions_list", "github", {"actions": []})

    provider = WorkflowToolsProvider(
        workflow_registry=MagicMock(),
        runner_registry=_runner_registry("macbook", "github__actions_list", output_schema=declared),
        schema_store=runner_store,
    )
    result = provider._resolve_tool_schema("github", "actions_list")

    assert result["source"] == "runner"
    assert result["output_schema"] == declared
    assert result["suggested_output_schema"]["x-schema_source"] == "learned"


def test_missing_schema_store_omits_suggested_but_keeps_declared():
    # TSO-03: no schema_store wired -> suggested_output_schema omitted, declared kept.
    declared = {"type": "object"}
    provider = WorkflowToolsProvider(
        workflow_registry=MagicMock(),
        tool_registry=_cp_registry("plain", "svc", output_schema=declared),
        schema_store=None,
    )
    result = provider._resolve_tool_schema("svc", "plain")
    assert result["output_schema"] == declared
    assert "suggested_output_schema" not in result


@pytest.mark.asyncio
async def test_batch_dispatcher_surfaces_learned_schemas_per_entry(schema_store):
    # TSO-04: batch mode (S-279) preserves per-entry surfacing through the
    # shared _resolve_tool_schema helper.
    provider = WorkflowToolsProvider(
        workflow_registry=MagicMock(),
        tool_registry=_cp_registry("get_repo", "github"),
        schema_store=schema_store,
    )
    response = await provider._handle_tool_schema(
        {
            "tools": [
                {"mcp": "github", "tool": "get_repo"},
                {"mcp": "github", "tool": "nonexistent"},
            ]
        }
    )

    assert "results" in response
    assert len(response["results"]) == 2
    first, second = response["results"]
    assert first["source"] == "cp"
    assert first["suggested_output_schema"]["x-schema_source"] == "learned"
    # Not-found entry doesn't crash the batch and omits suggested schema.
    assert second.get("found") is False
    assert "suggested_output_schema" not in second


def test_missing_learned_schema_sets_suggested_to_none_omitted(
    tmp_path,
):
    # TSO-05: when a tool has no learned schema, suggested_output_schema is
    # omitted entirely -- the key is present only when helpful.
    store = ToolOutputSchemaStore(backend=InMemorySchemaBackend())
    provider = WorkflowToolsProvider(
        workflow_registry=MagicMock(),
        tool_registry=_cp_registry("fresh", "svc", output_schema=None),
        schema_store=store,
    )
    result = provider._resolve_tool_schema("svc", "fresh")
    assert result["output_schema"] is None
    assert "suggested_output_schema" not in result


@pytest.mark.asyncio
async def test_low_confidence_learned_schema_is_surfaced_via_tool_schema():
    """Inspector Schema Visibility (M-074): ``workflow_tool_schema`` is a
    pull-based surface and must show learned schemas regardless of the
    agent-facing 0.8 injection floor. A single observation -> low confidence
    -> still surfaced, with ``x-confidence`` and ``x-observation_count``
    embedded so consumers can self-gate."""
    store = ToolOutputSchemaStore(
        backend=InMemorySchemaBackend(), extractor=ResponsePatternExtractor()
    )
    await store.observe("get_repo", "github", {"id": 1, "private": False})

    entry = store.get("github", "get_repo")
    assert entry is not None and entry.confidence < 0.8, (
        "fixture must produce a low-confidence learned schema"
    )

    provider = WorkflowToolsProvider(
        workflow_registry=MagicMock(),
        tool_registry=_cp_registry("get_repo", "github"),
        schema_store=store,
    )
    result = provider._resolve_tool_schema("github", "get_repo")

    suggested = result["suggested_output_schema"]
    assert suggested["x-schema_source"] == "learned"
    assert suggested["x-confidence"] < 0.8
    assert suggested["x-observation_count"] == 1


@pytest.mark.asyncio
async def test_response_hint_warns_about_low_confidence_suggested_schemas(
    schema_store,
):
    """The shared response hint must explicitly tell agents to check
    ``x-confidence`` / ``x-observation_count`` before relying on a learned
    schema, otherwise the agent has no way to know it's provisional."""
    provider = WorkflowToolsProvider(
        workflow_registry=MagicMock(),
        tool_registry=_cp_registry("get_repo", "github"),
        schema_store=schema_store,
    )
    result = provider._resolve_tool_schema("github", "get_repo")

    hint = result["response_hint"]
    assert "x-confidence" in hint
    assert "x-observation_count" in hint
    assert "low-confidence" in hint or "low confidence" in hint

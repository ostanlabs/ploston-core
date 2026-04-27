"""F-088 inspector delta: REST exposes learned output schemas.

Covers:
- GET /api/v1/tools/{name}.suggested_output_schema populated when the
  registry has a confident learned schema and no declared one.
- GET /api/v1/tools/{name}.suggested_output_schema is None when the tool
  has a declared schema (declared wins) or no learned schema.
- GET /api/v1/tools lists carry ``has_learned_output_schema`` derived
  from the registry lookup, independently per tool.
"""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ploston_core.api.routers.tools import tool_router
from ploston_core.registry.types import ToolDefinition
from ploston_core.types import ToolSource, ToolStatus


def _tool(name: str, *, output_schema: dict | None = None) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"desc of {name}",
        source=ToolSource.MCP,
        server_name="srv",
        input_schema={"type": "object", "properties": {}},
        output_schema=output_schema,
        tags=set(),
        status=ToolStatus.AVAILABLE,
    )


def _learned_schema() -> dict:
    return {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "x-schema_source": "learned",
        "x-confidence": 0.9,
        "x-observation_count": 12,
    }


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(tool_router, prefix="/api/v1")
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _wire_registry(app: FastAPI, tools, suggested_for=None) -> MagicMock:
    reg = MagicMock()
    reg.list_tools = MagicMock(return_value=list(tools))
    by_name = {t.name: t for t in tools}
    reg.get = MagicMock(side_effect=lambda n: by_name.get(n))
    suggested_for = suggested_for or {}
    # Inspector REST passes ``min_confidence=0.0`` so the mock must accept it.
    reg.get_suggested_schema = MagicMock(side_effect=lambda n, **_: suggested_for.get(n))
    app.state.tool_registry = reg
    # Router reads runner_registry when include_runner=True default; stub empty.
    app.state.runner_registry = None
    return reg


def test_detail_returns_suggested_when_no_declared_schema(app, client):
    learned = _learned_schema()
    _wire_registry(app, [_tool("get_repo")], suggested_for={"get_repo": learned})

    resp = client.get("/api/v1/tools/get_repo")
    assert resp.status_code == 200
    body = resp.json()
    assert body["output_schema"] is None
    assert body["suggested_output_schema"] == learned


def test_detail_omits_suggested_when_declared_present(app, client):
    declared = {"type": "object", "properties": {"v": {"type": "integer"}}}
    # Registry.get_suggested_schema would already return None for declared
    # tools (T-897 gate), but the handler should not fill it regardless.
    _wire_registry(
        app,
        [_tool("post_message", output_schema=declared)],
        suggested_for={"post_message": None},
    )

    resp = client.get("/api/v1/tools/post_message")
    assert resp.status_code == 200
    body = resp.json()
    assert body["output_schema"] == declared
    assert body["suggested_output_schema"] is None


def test_detail_omits_suggested_when_no_learned(app, client):
    _wire_registry(app, [_tool("ping")], suggested_for={"ping": None})

    resp = client.get("/api/v1/tools/ping")
    assert resp.status_code == 200
    body = resp.json()
    assert body["output_schema"] is None
    assert body["suggested_output_schema"] is None


def test_list_flags_has_learned_output_schema_per_tool(app, client):
    declared = {"type": "object"}
    _wire_registry(
        app,
        [
            _tool("get_repo"),  # learned below
            _tool("post_message", output_schema=declared),  # declared
            _tool("ping"),  # nothing
        ],
        suggested_for={"get_repo": _learned_schema()},
    )

    resp = client.get("/api/v1/tools?include_runner=false")
    assert resp.status_code == 200
    by_name = {t["name"]: t for t in resp.json()["tools"]}
    assert by_name["get_repo"]["has_learned_output_schema"] is True
    assert by_name["post_message"]["has_learned_output_schema"] is False
    assert by_name["ping"]["has_learned_output_schema"] is False


def test_detail_passes_min_confidence_zero_to_registry(app, client):
    """REST surface lifts the confidence floor so operators see learned
    schemas before they're agent-eligible (Inspector Schema Visibility spec)."""
    learned = _learned_schema()
    reg = _wire_registry(app, [_tool("get_repo")], suggested_for={"get_repo": learned})

    resp = client.get("/api/v1/tools/get_repo")
    assert resp.status_code == 200
    reg.get_suggested_schema.assert_called_with("get_repo", min_confidence=0.0)


def test_list_passes_min_confidence_zero_to_registry(app, client):
    """List handler must also pass ``min_confidence=0.0`` so the
    ``has_learned_output_schema`` flag reflects every learned schema."""
    reg = _wire_registry(app, [_tool("get_repo")], suggested_for={"get_repo": _learned_schema()})

    resp = client.get("/api/v1/tools?include_runner=false")
    assert resp.status_code == 200
    reg.get_suggested_schema.assert_any_call("get_repo", min_confidence=0.0)


# --------------------------------------------------------------------------- #
# List summary now carries input_schema + output_schema directly so the
# inspector can render them without a per-tool detail round-trip. Bug:
# previously every runner-hosted tool surfaced ``input_schema={}`` because
# the summary had no schema fields at all.
# --------------------------------------------------------------------------- #


def test_list_summary_includes_input_and_output_schema_for_cp_tools(app, client):
    declared_in = {"type": "object", "properties": {"q": {"type": "string"}}}
    declared_out = {"type": "object", "properties": {"hits": {"type": "integer"}}}
    t = ToolDefinition(
        name="search",
        description="d",
        source=ToolSource.MCP,
        server_name="srv",
        input_schema=declared_in,
        output_schema=declared_out,
        tags=set(),
        status=ToolStatus.AVAILABLE,
    )
    _wire_registry(app, [t], suggested_for={})

    resp = client.get("/api/v1/tools?include_runner=false")
    assert resp.status_code == 200
    [row] = resp.json()["tools"]
    assert row["input_schema"] == declared_in
    assert row["output_schema"] == declared_out


# --------------------------------------------------------------------------- #
# Inspector Schema Visibility (Phase 2) — runner-hosted tools must surface
# learned schemas through the same paths CP-direct tools use. Pre-fix the
# REST list omitted ``has_learned_output_schema`` for the runner branch and
# the detail endpoint 404'd for tools that only existed on a runner.
# --------------------------------------------------------------------------- #


def _runner_registry_with(tool_entries):
    from datetime import datetime

    from ploston_core.runner_management import Runner, RunnerRegistry, RunnerStatus

    reg = RunnerRegistry()
    runner = Runner(
        id="runner_1",
        name="laptop",
        created_at=datetime.now(),
        status=RunnerStatus.CONNECTED,
        available_tools=tool_entries,
    )
    reg._runners[runner.id] = runner
    return reg


def test_list_runner_tool_flags_has_learned_output_schema(app, client):
    """When a runner-hosted tool has a learned schema in the store, the list
    summary must reflect it. The router asks the registry by canonical name
    so the lookup decodes the bare ``(server, tool)`` key."""
    _wire_registry(
        app,
        [],
        suggested_for={"github__search_code": _learned_schema()},
    )
    app.state.runner_registry = _runner_registry_with(
        [
            {
                "name": "github__search_code",
                "description": "Search GH code",
                "inputSchema": {"type": "object"},
            }
        ]
    )

    resp = client.get("/api/v1/tools")
    assert resp.status_code == 200
    [row] = resp.json()["tools"]
    assert row["name"] == "github__search_code"
    assert row["has_learned_output_schema"] is True


def test_list_runner_tool_with_declared_output_skips_has_learned_flag(app, client):
    """Declared schema wins — even when a learned schema exists, the
    ``has_learned_output_schema`` flag must stay false because the
    inspector won't surface a learned overlay over a declared schema."""
    _wire_registry(app, [], suggested_for={})
    app.state.runner_registry = _runner_registry_with(
        [
            {
                "name": "github__list_repos",
                "description": "List repos",
                "outputSchema": {"type": "object"},
            }
        ]
    )

    resp = client.get("/api/v1/tools")
    assert resp.status_code == 200
    [row] = resp.json()["tools"]
    assert row["has_learned_output_schema"] is False


def test_detail_endpoint_resolves_runner_only_tool(app, client):
    """Pre-fix the detail endpoint 404'd for runner-hosted tools because
    they aren't in ``ToolRegistry``. The handler must fall back to the
    runner registry and still surface the learned schema."""
    learned = _learned_schema()
    _wire_registry(app, [], suggested_for={"github__search_code": learned})
    app.state.runner_registry = _runner_registry_with(
        [
            {
                "name": "github__search_code",
                "description": "Search GH code",
                "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
            }
        ]
    )

    resp = client.get("/api/v1/tools/github__search_code")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "github__search_code"
    assert body["server"] == "laptop"
    assert body["source"] == "runner"
    assert body["input_schema"] == {
        "type": "object",
        "properties": {"q": {"type": "string"}},
    }
    assert body["suggested_output_schema"] == learned


def test_detail_endpoint_returns_404_for_unknown_tool(app, client):
    """Sanity: unknown tool names still 404 even after the runner-fallback
    path is added."""
    _wire_registry(app, [], suggested_for={})
    app.state.runner_registry = _runner_registry_with([])

    resp = client.get("/api/v1/tools/nonexistent")
    assert resp.status_code == 404


def test_list_summary_carries_runner_dict_inputSchema(app, client):  # noqa: N802
    """Runner-surfaced tools mirror upstream MCP shape (camelCase key).
    The list endpoint translates ``inputSchema`` -> ``input_schema`` on the
    summary so downstream clients (inspector) get the schema without a
    detail round-trip — and without 404ing because runner tools are not
    in the CP registry."""
    from datetime import datetime

    from ploston_core.runner_management import Runner, RunnerRegistry, RunnerStatus

    _wire_registry(app, [], suggested_for={})  # no CP tools
    reg = RunnerRegistry()
    runner = Runner(
        id="runner_1",
        name="laptop",
        created_at=datetime.now(),
        status=RunnerStatus.CONNECTED,
        available_tools=[
            {
                "name": "github__get_me",
                "description": "Get me",
                "inputSchema": {
                    "type": "object",
                    "properties": {"reason": {"type": "string"}},
                },
                "outputSchema": {"type": "object"},
            },
            "github__bare_string_tool",  # legacy/string-only entry
        ],
    )
    reg._runners[runner.id] = runner
    app.state.runner_registry = reg

    resp = client.get("/api/v1/tools")
    assert resp.status_code == 200
    by_name = {t["name"]: t for t in resp.json()["tools"]}
    assert by_name["github__get_me"]["input_schema"] == {
        "type": "object",
        "properties": {"reason": {"type": "string"}},
    }
    assert by_name["github__get_me"]["output_schema"] == {"type": "object"}
    # Bare-string entries have no schema info — surface the safe defaults
    # rather than fabricating one.
    assert by_name["github__bare_string_tool"]["input_schema"] == {}
    assert by_name["github__bare_string_tool"]["output_schema"] is None

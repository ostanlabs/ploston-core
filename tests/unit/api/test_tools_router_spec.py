"""Spec tests for the tools REST router.

Targets the list filters, detail (CP-tool) path, and the direct
``call_tool`` endpoint (success / tool-failure / AELError contracts) which
the existing suite leaves largely uncovered. Collaborators (tool registry,
invoker, telemetry collector) are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ploston_core.api.routers.tools import tool_router
from ploston_core.errors import AELError, ErrorCategory
from ploston_core.invoker.types import ToolCallResult
from ploston_core.registry.types import ToolDefinition
from ploston_core.types import ToolSource, ToolStatus


def _tool(
    name: str,
    *,
    source: ToolSource = ToolSource.MCP,
    server: str | None = "srv",
    description: str | None = None,
    status: ToolStatus = ToolStatus.AVAILABLE,
    tags: set[str] | None = None,
    input_schema: dict | None = None,
    output_schema: dict | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description if description is not None else f"desc of {name}",
        source=source,
        server_name=server,
        input_schema=input_schema if input_schema is not None else {},
        output_schema=output_schema,
        tags=tags or set(),
        status=status,
    )


@pytest.fixture
def app() -> FastAPI:
    application = FastAPI()
    application.include_router(tool_router, prefix="/api/v1")
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _wire_registry(app: FastAPI, tools: list[ToolDefinition]) -> MagicMock:
    reg = MagicMock()
    reg.list_tools = MagicMock(return_value=list(tools))
    by_name = {t.name: t for t in tools}
    reg.get = MagicMock(side_effect=lambda n: by_name.get(n))
    reg.get_suggested_schema = MagicMock(return_value=None)
    app.state.tool_registry = reg
    app.state.runner_registry = None
    return reg


# ---------------------------------------------------------------------------
# GET /tools (list)
# ---------------------------------------------------------------------------


class TestListTools:
    def test_list_returns_total_and_summaries(self, app: FastAPI, client: TestClient) -> None:
        _wire_registry(app, [_tool("a"), _tool("b")])

        resp = client.get("/api/v1/tools?include_runner=false")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert {t["name"] for t in body["tools"]} == {"a", "b"}

    def test_summary_maps_source_and_status_and_sorts_tags(
        self, app: FastAPI, client: TestClient
    ) -> None:
        _wire_registry(
            app,
            [
                _tool(
                    "sys_tool",
                    source=ToolSource.SYSTEM,
                    status=ToolStatus.UNAVAILABLE,
                    tags={"zeta", "alpha"},
                )
            ],
        )

        resp = client.get("/api/v1/tools?include_runner=false")

        [row] = resp.json()["tools"]
        assert row["source"] == "system"
        assert row["status"] == "unavailable"
        # tags must be sorted by the router.
        assert row["tags"] == ["alpha", "zeta"]

    def test_unknown_status_maps_to_unavailable(self, app: FastAPI, client: TestClient) -> None:
        # Contract in _convert_status: only AVAILABLE -> available; everything
        # else (incl. UNKNOWN) -> unavailable.
        _wire_registry(app, [_tool("t", status=ToolStatus.UNKNOWN)])

        resp = client.get("/api/v1/tools?include_runner=false")

        [row] = resp.json()["tools"]
        assert row["status"] == "unavailable"

    def test_filter_by_source(self, app: FastAPI, client: TestClient) -> None:
        _wire_registry(
            app,
            [
                _tool("mcp_tool", source=ToolSource.MCP),
                _tool("native_tool", source=ToolSource.NATIVE),
            ],
        )

        resp = client.get("/api/v1/tools?include_runner=false&source=native")

        body = resp.json()
        assert body["total"] == 1
        assert body["tools"][0]["name"] == "native_tool"

    def test_filter_by_server(self, app: FastAPI, client: TestClient) -> None:
        _wire_registry(
            app,
            [
                _tool("keep", server="github"),
                _tool("drop", server="filesystem"),
            ],
        )

        resp = client.get("/api/v1/tools?include_runner=false&server=github")

        body = resp.json()
        assert body["total"] == 1
        assert body["tools"][0]["name"] == "keep"

    def test_filter_by_search_matches_name_or_description(
        self, app: FastAPI, client: TestClient
    ) -> None:
        _wire_registry(
            app,
            [
                _tool("search_code", description=None),
                _tool("other", description="performs a SEARCH over docs"),
                _tool("unrelated", description="nope"),
            ],
        )

        resp = client.get("/api/v1/tools?include_runner=false&search=search")

        names = {t["name"] for t in resp.json()["tools"]}
        assert names == {"search_code", "other"}

    def test_invalid_source_enum_returns_422(self, app: FastAPI, client: TestClient) -> None:
        _wire_registry(app, [])

        resp = client.get("/api/v1/tools?source=bogus")

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /tools/{name} (detail) — CP tool path
# ---------------------------------------------------------------------------


class TestGetTool:
    def test_get_cp_tool_returns_detail(self, app: FastAPI, client: TestClient) -> None:
        in_schema = {"type": "object", "properties": {"q": {"type": "string"}}}
        out_schema = {"type": "object"}
        _wire_registry(
            app,
            [
                _tool(
                    "search_code",
                    source=ToolSource.MCP,
                    server="github",
                    description="Search code",
                    input_schema=in_schema,
                    output_schema=out_schema,
                )
            ],
        )

        resp = client.get("/api/v1/tools/search_code")

        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "search_code"
        assert body["source"] == "mcp"
        assert body["server"] == "github"
        assert body["input_schema"] == in_schema
        assert body["output_schema"] == out_schema

    def test_get_unknown_tool_returns_404(self, app: FastAPI, client: TestClient) -> None:
        _wire_registry(app, [])

        resp = client.get("/api/v1/tools/ghost")

        assert resp.status_code == 404
        assert "ghost" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# POST /tools/{name}/call — direct invocation
# ---------------------------------------------------------------------------


def _wire_call(
    app: FastAPI,
    *,
    result: ToolCallResult,
    collector: object | None = None,
) -> MagicMock:
    invoker = MagicMock()
    invoker.invoke = AsyncMock(return_value=result)
    app.state.tool_invoker = invoker
    app.state.telemetry_collector = collector
    return invoker


class TestCallTool:
    def test_successful_call_returns_result_envelope(
        self, app: FastAPI, client: TestClient
    ) -> None:
        invoker = _wire_call(
            app,
            result=ToolCallResult(
                success=True,
                output={"answer": 42},
                duration_ms=15,
                tool_name="calc",
            ),
        )

        resp = client.post("/api/v1/tools/calc/call", json={"params": {"x": 1}})

        assert resp.status_code == 200
        body = resp.json()
        assert body["tool_name"] == "calc"
        assert body["duration_ms"] == 15
        assert body["result"] == {"answer": 42}
        invoker.invoke.assert_awaited_once_with("calc", {"x": 1})

    def test_call_with_default_empty_params(self, app: FastAPI, client: TestClient) -> None:
        invoker = _wire_call(
            app,
            result=ToolCallResult(success=True, output=None, duration_ms=1, tool_name="ping"),
        )

        resp = client.post("/api/v1/tools/ping/call", json={})

        assert resp.status_code == 200
        invoker.invoke.assert_awaited_once_with("ping", {})

    def test_failed_tool_call_returns_502_with_error_dict(
        self, app: FastAPI, client: TestClient
    ) -> None:
        err = AELError(
            code="TOOL_FAILED",
            category=ErrorCategory.TOOL,
            message="upstream said no",
            http_status=400,
        )
        _wire_call(
            app,
            result=ToolCallResult(
                success=False,
                output=None,
                duration_ms=5,
                tool_name="flaky",
                error=err,
            ),
        )

        resp = client.post("/api/v1/tools/flaky/call", json={"params": {}})

        # Contract: a tool that returns success=False is a 502 Bad Gateway
        # (the gateway reached the tool but the tool failed), with the error
        # serialized via to_dict().
        assert resp.status_code == 502
        detail = resp.json()["detail"]
        assert detail["code"] == "TOOL_FAILED"
        assert detail["message"] == "upstream said no"

    def test_failed_call_without_error_object_returns_502(
        self, app: FastAPI, client: TestClient
    ) -> None:
        _wire_call(
            app,
            result=ToolCallResult(
                success=False,
                output=None,
                duration_ms=5,
                tool_name="flaky",
                error=None,
            ),
        )

        resp = client.post("/api/v1/tools/flaky/call", json={"params": {}})

        assert resp.status_code == 502
        assert resp.json()["detail"] == "Tool call failed"

    def test_invoker_raising_aelerror_maps_to_http_status(
        self, app: FastAPI, client: TestClient
    ) -> None:
        invoker = MagicMock()
        invoker.invoke = AsyncMock(
            side_effect=AELError(
                code="TOOL_UNAVAILABLE",
                category=ErrorCategory.TOOL,
                message="not connected",
                http_status=503,
            )
        )
        app.state.tool_invoker = invoker
        app.state.telemetry_collector = None

        resp = client.post("/api/v1/tools/down/call", json={"params": {}})

        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["code"] == "TOOL_UNAVAILABLE"
        assert detail["category"] == "TOOL"

    def test_successful_call_records_telemetry_lifecycle(
        self, app: FastAPI, client: TestClient
    ) -> None:
        collector = MagicMock()
        collector.start_execution = AsyncMock(return_value="exec-9")
        collector.end_execution = AsyncMock()
        _wire_call(
            app,
            result=ToolCallResult(success=True, output="ok", duration_ms=2, tool_name="calc"),
            collector=collector,
        )

        resp = client.post("/api/v1/tools/calc/call", json={"params": {}})

        assert resp.status_code == 200
        collector.start_execution.assert_awaited_once()
        collector.end_execution.assert_awaited_once()

    def test_telemetry_start_failure_does_not_block_call(
        self, app: FastAPI, client: TestClient
    ) -> None:
        # Telemetry must never block the user's call (per the router's
        # swallowing of collector errors).
        collector = MagicMock()
        collector.start_execution = AsyncMock(side_effect=RuntimeError("telemetry down"))
        collector.end_execution = AsyncMock()
        _wire_call(
            app,
            result=ToolCallResult(success=True, output="ok", duration_ms=2, tool_name="calc"),
            collector=collector,
        )

        resp = client.post("/api/v1/tools/calc/call", json={"params": {}})

        assert resp.status_code == 200
        assert resp.json()["result"] == "ok"

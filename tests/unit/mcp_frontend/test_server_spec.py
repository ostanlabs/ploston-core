"""Spec-style coverage for the MCP frontend server (server.py).

These tests assert the *intended* contract of ``MCPFrontend`` — the JSON-RPC
dispatch in ``_handle_message``, the 6-step ``tools/call`` resolver, mode
gating, ``tools/list`` aggregation + tag filtering, error/JSON-RPC mapping,
and the execution paths (CP tool, workflow, workflow-mgmt, runner).

Collaborators (registry, workflow registry, invoker, mode_manager, runner
registry, workflow-tools provider) are mocked at the boundary; the unit under
test (``MCPFrontend``) is exercised directly. Telemetry is left unconfigured
(None) so the telemetry wrappers no-op — the routing/response contracts are
what we assert.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ploston_core.config import Mode, ModeManager
from ploston_core.errors import AELError
from ploston_core.errors.errors import ErrorCategory
from ploston_core.mcp_frontend.server import MCPFrontend, _split_tool_name
from ploston_core.types import ExecutionStatus

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def make_tool_result(success=True, output=None, error=None, structured_content=None):
    """Mimic the ToolCallResult shape consumed by _execute_tool."""
    return SimpleNamespace(
        success=success,
        output=output,
        error=error,
        structured_content=structured_content,
    )


def make_frontend(
    *,
    mode=Mode.RUNNING,
    tool_registry=None,
    workflow_registry=None,
    tool_invoker=None,
    config_tool_registry=None,
    workflow_tools_provider=None,
    runner_registry=None,
    workflow_engine=None,
):
    """Construct an MCPFrontend with mocked collaborators."""
    tr = tool_registry or MagicMock()
    if tool_registry is None:
        tr.get_for_mcp_exposure.return_value = []
        tr.get.return_value = None
    wr = workflow_registry or MagicMock()
    if workflow_registry is None:
        wr.get_for_mcp_exposure.return_value = []
        wr.get.return_value = None
    return MCPFrontend(
        workflow_engine=workflow_engine or MagicMock(),
        tool_registry=tr,
        workflow_registry=wr,
        tool_invoker=tool_invoker or MagicMock(),
        mode_manager=ModeManager(initial_mode=mode),
        config_tool_registry=config_tool_registry,
        workflow_tools_provider=workflow_tools_provider,
        runner_registry=runner_registry,
    )


# ===========================================================================
# _split_tool_name
# ===========================================================================


class TestSplitToolName:
    def test_qualified_name_splits_on_first_delimiter(self):
        assert _split_tool_name("obsidian-mcp__read_docs") == ("obsidian-mcp", "read_docs")

    def test_unqualified_name_has_empty_bridge(self):
        assert _split_tool_name("slack_post") == ("", "slack_post")

    def test_multiple_delimiters_split_on_first_only(self):
        # partition splits on the first "__"; the remainder is the tool name.
        assert _split_tool_name("a__b__c") == ("a", "b__c")


# ===========================================================================
# _handle_message — JSON-RPC dispatch & error mapping
# ===========================================================================


class TestHandleMessageDispatch:
    @pytest.fixture
    def frontend(self):
        return make_frontend()

    async def test_initialize_returns_server_info_and_capabilities(self, frontend):
        resp = await frontend._handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 1
        result = resp["result"]
        assert result["protocolVersion"] == "2024-11-05"
        assert result["capabilities"]["tools"]["listChanged"] is True
        assert "name" in result["serverInfo"]
        assert "version" in result["serverInfo"]

    async def test_ping_returns_pong(self, frontend):
        resp = await frontend._handle_message({"id": 7, "method": "ping"})
        assert resp == {"jsonrpc": "2.0", "id": 7, "result": {"pong": True}}

    async def test_tools_list_dispatched(self, frontend):
        resp = await frontend._handle_message({"id": 2, "method": "tools/list", "params": {}})
        assert resp["id"] == 2
        assert "tools" in resp["result"]

    async def test_unknown_method_for_request_returns_method_not_found(self, frontend):
        resp = await frontend._handle_message({"id": 9, "method": "no_such_method"})
        assert resp["error"]["code"] == -32601
        assert "no_such_method" in resp["error"]["message"]

    async def test_unknown_method_for_notification_returns_none(self, frontend):
        # No "id" => notification => no response even for unknown method.
        resp = await frontend._handle_message({"method": "no_such_method"})
        assert resp is None

    async def test_notifications_namespace_returns_none(self, frontend):
        resp = await frontend._handle_message(
            {"id": 5, "method": "notifications/initialized", "params": {}}
        )
        assert resp is None

    async def test_request_with_explicit_none_id_still_responds(self, frontend):
        # "id" present (even null) => it's a request, must get a response.
        resp = await frontend._handle_message({"id": None, "method": "ping"})
        assert resp is not None
        assert resp["id"] is None
        assert resp["result"] == {"pong": True}


class TestHandleMessageErrorMapping:
    async def test_tools_call_aelerror_maps_to_iserror_result_not_jsonrpc_error(self):
        """Per MCP spec + docstring: tool-call business errors are returned as
        isError:true inside result, NOT as a JSON-RPC protocol error."""
        frontend = make_frontend()  # empty registries -> TOOL_UNAVAILABLE
        resp = await frontend._handle_message(
            {"id": 3, "method": "tools/call", "params": {"name": "nope", "arguments": {}}}
        )
        # Must be a success envelope carrying isError, not an {"error": ...}.
        assert "error" not in resp
        result = resp["result"]
        assert result["isError"] is True
        payload = json.loads(result["content"][0]["text"])
        assert payload["code"] == "TOOL_UNAVAILABLE"
        assert "not found" in payload["message"]
        assert payload["retryable"] is False

    async def test_non_tools_call_aelerror_maps_to_jsonrpc_protocol_error(self):
        """AELError raised by a non-tools/call method becomes a JSON-RPC error
        with http_status as the code and structured data."""
        frontend = make_frontend()

        async def boom(_params):
            raise AELError(
                code="PARAM_INVALID",
                category=ErrorCategory.VALIDATION,
                message="bad params",
                detail="more detail",
                http_status=400,
            )

        with patch.object(frontend, "_handle_tools_list", side_effect=boom):
            resp = await frontend._handle_message({"id": 4, "method": "tools/list", "params": {}})
        assert "result" not in resp
        assert resp["error"]["code"] == 400
        assert resp["error"]["message"] == "bad params"
        assert resp["error"]["data"]["code"] == "PARAM_INVALID"
        assert resp["error"]["data"]["detail"] == "more detail"

    async def test_tools_call_unexpected_exception_maps_to_internal_error_result(self):
        """An unexpected (non-AELError) exception on tools/call is surfaced as
        isError:true with code INTERNAL_ERROR inside result."""
        frontend = make_frontend()
        with patch.object(frontend, "_handle_tools_call", side_effect=RuntimeError("kaboom")):
            resp = await frontend._handle_message(
                {"id": 6, "method": "tools/call", "params": {"name": "x"}}
            )
        assert "error" not in resp
        payload = json.loads(resp["result"]["content"][0]["text"])
        assert payload["code"] == "INTERNAL_ERROR"
        assert "kaboom" in payload["message"]
        assert payload["retryable"] is False
        assert "traceback_tail" in payload

    async def test_non_tools_call_unexpected_exception_maps_to_jsonrpc_500(self):
        frontend = make_frontend()
        with patch.object(frontend, "_handle_tools_list", side_effect=RuntimeError("explode")):
            resp = await frontend._handle_message({"id": 8, "method": "tools/list", "params": {}})
        assert resp["error"]["code"] == 500
        assert resp["error"]["data"]["code"] == "INTERNAL_ERROR"
        assert "explode" in resp["error"]["message"]

    async def test_notification_swallows_aelerror_and_returns_none(self):
        """A notification (no id) that raises AELError must not produce a
        response."""
        frontend = make_frontend()

        async def boom(_params):
            raise AELError(code="X", category=ErrorCategory.SYSTEM, message="m", http_status=500)

        with patch.object(frontend, "_handle_tools_call", side_effect=boom):
            resp = await frontend._handle_message({"method": "tools/call", "params": {"name": "x"}})
        assert resp is None

    async def test_notification_swallows_unexpected_exception_and_returns_none(self):
        frontend = make_frontend()
        with patch.object(frontend, "_handle_tools_call", side_effect=RuntimeError("boom")):
            resp = await frontend._handle_message({"method": "tools/call", "params": {"name": "x"}})
        assert resp is None


# ===========================================================================
# tools/list aggregation + tag filtering
# ===========================================================================


class TestToolsListAggregation:
    def _registry_with_tags(self, tools, tags_by_name):
        reg = MagicMock()
        reg.get_for_mcp_exposure.return_value = [dict(t) for t in tools]

        def _get(name):
            if name in tags_by_name:
                return SimpleNamespace(tags=tags_by_name[name])
            return None

        reg.get.side_effect = _get
        return reg

    async def test_running_mode_aggregates_cp_workflow_and_configure(self):
        tr = self._registry_with_tags(
            [{"name": "alpha", "description": "A"}],
            {"alpha": ["source:native"]},
        )
        wr = MagicMock()
        wr.get_for_mcp_exposure.return_value = [{"name": "wf_x", "description": "W"}]
        ctr = MagicMock()
        ctr.get_configure_tool_for_mcp_exposure.return_value = {
            "name": "configure",
            "description": "cfg",
        }
        frontend = make_frontend(tool_registry=tr, workflow_registry=wr, config_tool_registry=ctr)
        result = await frontend._handle_tools_list({})
        names = [t["name"] for t in result["tools"]]
        assert "alpha" in names
        assert "wf_x" in names
        assert "configure" in names
        # _ploston_tags must be stripped before serialization.
        assert all("_ploston_tags" not in t for t in result["tools"])

    async def test_tag_filter_match_all_semantics(self):
        tr = self._registry_with_tags(
            [
                {"name": "a", "description": ""},
                {"name": "b", "description": ""},
            ],
            {"a": ["source:native", "kind:x"], "b": ["source:native"]},
        )
        frontend = make_frontend(tool_registry=tr)
        # Filter requires BOTH tags -> only "a" qualifies.
        result = await frontend._handle_tools_list({"tags": ["source:native", "kind:x"]})
        names = [t["name"] for t in result["tools"]]
        assert names == ["a"]

    async def test_legacy_source_filter_maps_to_source_tag(self):
        tr = self._registry_with_tags(
            [
                {"name": "a", "description": ""},
                {"name": "b", "description": ""},
            ],
            {"a": ["source:mcp"], "b": ["source:native"]},
        )
        frontend = make_frontend(tool_registry=tr)
        result = await frontend._handle_tools_list({"sources": ["mcp"]})
        names = [t["name"] for t in result["tools"]]
        assert names == ["a"]

    async def test_config_mode_lists_only_config_tools(self):
        ctr = MagicMock()
        ctr.get_for_mcp_exposure.return_value = [{"name": "ael:config_get", "description": ""}]
        tr = MagicMock()
        tr.get_for_mcp_exposure.return_value = [{"name": "alpha", "description": ""}]
        frontend = make_frontend(
            mode=Mode.CONFIGURATION, tool_registry=tr, config_tool_registry=ctr
        )
        result = await frontend._handle_tools_list({})
        names = [t["name"] for t in result["tools"]]
        assert names == ["ael:config_get"]
        # CP tool registry should not be consulted for exposure in config mode.
        tr.get_for_mcp_exposure.assert_not_called()

    async def test_expose_tools_false_omits_cp_tools(self):
        from ploston_core.mcp_frontend.types import MCPServerConfig

        tr = self._registry_with_tags([{"name": "alpha", "description": ""}], {"alpha": []})
        frontend = MCPFrontend(
            workflow_engine=MagicMock(),
            tool_registry=tr,
            workflow_registry=MagicMock(get_for_mcp_exposure=MagicMock(return_value=[])),
            tool_invoker=MagicMock(),
            mode_manager=ModeManager(initial_mode=Mode.RUNNING),
            config=MCPServerConfig(expose_tools=False),
        )
        result = await frontend._handle_tools_list({})
        names = [t["name"] for t in result["tools"]]
        assert "alpha" not in names

    async def test_workflow_mgmt_tools_included_in_list(self):
        provider = MagicMock()
        provider.get_for_mcp_exposure.return_value = [
            {"name": "workflow_list", "description": "list"}
        ]
        frontend = make_frontend(workflow_tools_provider=provider)
        result = await frontend._handle_tools_list({})
        names = [t["name"] for t in result["tools"]]
        assert "workflow_list" in names


# ===========================================================================
# tools/call — the 6-step resolver
# ===========================================================================


class TestToolsCallResolver:
    async def test_missing_name_raises_param_invalid(self):
        frontend = make_frontend()
        with pytest.raises(AELError) as exc:
            await frontend._handle_tools_call({"arguments": {}})
        assert exc.value.code == "PARAM_INVALID"

    async def test_step1_config_namespace_ael_prefix_routed_to_config(self):
        ctr = MagicMock()
        ctr.call = AsyncMock(return_value={"content": [], "isError": False})
        frontend = make_frontend(mode=Mode.CONFIGURATION, config_tool_registry=ctr)
        await frontend._handle_tools_call({"name": "ael:foo", "arguments": {"a": 1}})
        ctr.call.assert_awaited_once_with("ael:foo", {"a": 1})

    async def test_step1_ploston_prefix_routed_to_config(self):
        ctr = MagicMock()
        ctr.call = AsyncMock(return_value={"content": [], "isError": False})
        # ploston:configure is allowed in running mode.
        frontend = make_frontend(mode=Mode.RUNNING, config_tool_registry=ctr)
        await frontend._handle_tools_call({"name": "ploston:configure", "arguments": {}})
        ctr.call.assert_awaited_once_with("ploston:configure", {})

    async def test_step4_cp_tool_exact_match_executes(self):
        tr = MagicMock()
        tr.get.return_value = SimpleNamespace(tags=[])
        invoker = MagicMock()
        invoker.invoke = AsyncMock(return_value=make_tool_result(success=True, output="hi"))
        frontend = make_frontend(tool_registry=tr, tool_invoker=invoker)
        resp = await frontend._handle_tools_call({"name": "mytool", "arguments": {"x": 1}})
        invoker.invoke.assert_awaited_once_with("mytool", {"x": 1})
        assert resp["isError"] is False
        assert resp["content"][0]["text"] == "hi"

    async def test_step5_workflow_bare_name_executes(self):
        wr = MagicMock()
        wr.get.return_value = SimpleNamespace(id="wf1")
        engine = MagicMock()
        engine.execute = AsyncMock(
            return_value=SimpleNamespace(
                status=ExecutionStatus.COMPLETED, outputs={"r": 1}, error=None
            )
        )
        tr = MagicMock()
        tr.get.return_value = None
        frontend = make_frontend(tool_registry=tr, workflow_registry=wr, workflow_engine=engine)
        resp = await frontend._handle_tools_call({"name": "wf1", "arguments": {"in": 2}})
        engine.execute.assert_awaited_once()
        assert resp["isError"] is False
        assert json.loads(resp["content"][0]["text"]) == {"r": 1}

    async def test_step6_not_found_raises_tool_unavailable_404(self):
        frontend = make_frontend()
        with pytest.raises(AELError) as exc:
            await frontend._handle_tools_call({"name": "ghost", "arguments": {}})
        assert exc.value.code == "TOOL_UNAVAILABLE"
        assert exc.value.http_status == 404

    async def test_step3_mgmt_tool_blocked_in_config_mode(self):
        provider = MagicMock()
        provider.call = AsyncMock()
        frontend = make_frontend(mode=Mode.CONFIGURATION, workflow_tools_provider=provider)
        with pytest.raises(AELError) as exc:
            await frontend._handle_tools_call({"name": "workflow_list", "arguments": {}})
        assert exc.value.code == "TOOL_UNAVAILABLE"
        assert exc.value.http_status == 503
        provider.call.assert_not_called()

    async def test_step3_mgmt_tool_executes_in_running_mode(self):
        provider = MagicMock()
        provider.call = AsyncMock(
            return_value={"content": [{"type": "text", "text": "[]"}], "isError": False}
        )
        frontend = make_frontend(workflow_tools_provider=provider)
        resp = await frontend._handle_tools_call({"name": "workflow_list", "arguments": {}})
        provider.call.assert_awaited_once_with("workflow_list", {})
        assert resp["isError"] is False

    async def test_cp_tool_blocked_in_config_mode(self):
        tr = MagicMock()
        tr.get.return_value = SimpleNamespace(tags=[])
        frontend = make_frontend(mode=Mode.CONFIGURATION, tool_registry=tr)
        with pytest.raises(AELError) as exc:
            await frontend._handle_tools_call({"name": "mytool", "arguments": {}})
        assert exc.value.code == "TOOL_UNAVAILABLE"
        assert exc.value.http_status == 503

    async def test_workflow_blocked_in_config_mode(self):
        wr = MagicMock()
        wr.get.return_value = SimpleNamespace(id="wf1")
        tr = MagicMock()
        tr.get.return_value = None
        frontend = make_frontend(mode=Mode.CONFIGURATION, tool_registry=tr, workflow_registry=wr)
        with pytest.raises(AELError) as exc:
            await frontend._handle_tools_call({"name": "wf1", "arguments": {}})
        assert exc.value.code == "TOOL_UNAVAILABLE"
        assert exc.value.http_status == 503


# ===========================================================================
# _handle_config_tool_call — mode gating
# ===========================================================================


class TestConfigToolCall:
    async def test_no_config_registry_raises_unavailable(self):
        frontend = make_frontend(config_tool_registry=None)
        with pytest.raises(AELError) as exc:
            await frontend._handle_config_tool_call("ael:foo", {})
        assert exc.value.code == "TOOL_UNAVAILABLE"
        assert exc.value.http_status == 503

    async def test_configure_blocked_in_config_mode(self):
        ctr = MagicMock()
        ctr.call = AsyncMock()
        frontend = make_frontend(mode=Mode.CONFIGURATION, config_tool_registry=ctr)
        with pytest.raises(AELError) as exc:
            await frontend._handle_config_tool_call("configure", {})
        assert "only available in running mode" in exc.value.message
        ctr.call.assert_not_called()

    async def test_config_tool_allowed_in_config_mode(self):
        ctr = MagicMock()
        ctr.call = AsyncMock(return_value={"content": [], "isError": False})
        frontend = make_frontend(mode=Mode.CONFIGURATION, config_tool_registry=ctr)
        await frontend._handle_config_tool_call("ael:config_get", {"p": 1})
        ctr.call.assert_awaited_once_with("ael:config_get", {"p": 1})

    async def test_non_configure_config_tool_blocked_in_running_mode(self):
        ctr = MagicMock()
        ctr.call = AsyncMock()
        frontend = make_frontend(mode=Mode.RUNNING, config_tool_registry=ctr)
        with pytest.raises(AELError) as exc:
            await frontend._handle_config_tool_call("ael:config_get", {})
        assert "only available in configuration mode" in exc.value.message
        ctr.call.assert_not_called()


# ===========================================================================
# _execute_tool
# ===========================================================================


class TestExecuteTool:
    async def test_success_serializes_dict_output_as_json(self):
        invoker = MagicMock()
        invoker.invoke = AsyncMock(return_value=make_tool_result(success=True, output={"k": "v"}))
        frontend = make_frontend(tool_invoker=invoker)
        resp = await frontend._execute_tool("t", {})
        assert resp["isError"] is False
        assert json.loads(resp["content"][0]["text"]) == {"k": "v"}

    async def test_success_passes_string_output_through(self):
        invoker = MagicMock()
        invoker.invoke = AsyncMock(
            return_value=make_tool_result(success=True, output="plain string")
        )
        frontend = make_frontend(tool_invoker=invoker)
        resp = await frontend._execute_tool("t", {})
        assert resp["content"][0]["text"] == "plain string"

    async def test_success_includes_structured_content_when_present(self):
        invoker = MagicMock()
        invoker.invoke = AsyncMock(
            return_value=make_tool_result(success=True, output="x", structured_content={"a": 1})
        )
        frontend = make_frontend(tool_invoker=invoker)
        resp = await frontend._execute_tool("t", {})
        assert resp["structuredContent"] == {"a": 1}

    async def test_failure_returns_iserror_with_error_message(self):
        invoker = MagicMock()
        invoker.invoke = AsyncMock(
            return_value=make_tool_result(
                success=False,
                error=SimpleNamespace(code="X", message="it failed"),
            )
        )
        frontend = make_frontend(tool_invoker=invoker)
        resp = await frontend._execute_tool("t", {})
        assert resp["isError"] is True
        assert resp["content"][0]["text"] == "it failed"

    async def test_failure_without_error_object_uses_default_message(self):
        invoker = MagicMock()
        invoker.invoke = AsyncMock(return_value=make_tool_result(success=False, error=None))
        frontend = make_frontend(tool_invoker=invoker)
        resp = await frontend._execute_tool("t", {})
        assert resp["isError"] is True
        assert resp["content"][0]["text"] == "Tool call failed"


# ===========================================================================
# _execute_workflow
# ===========================================================================


class TestExecuteWorkflow:
    async def test_completed_workflow_returns_outputs_json(self):
        engine = MagicMock()
        engine.execute = AsyncMock(
            return_value=SimpleNamespace(
                status=ExecutionStatus.COMPLETED, outputs={"out": 42}, error=None
            )
        )
        frontend = make_frontend(workflow_engine=engine)
        resp = await frontend._execute_workflow("wf", {"a": 1})
        assert resp["isError"] is False
        assert json.loads(resp["content"][0]["text"]) == {"out": 42}

    async def test_failed_workflow_returns_iserror_with_error_message(self):
        engine = MagicMock()
        engine.execute = AsyncMock(
            return_value=SimpleNamespace(
                status=ExecutionStatus.FAILED,
                outputs=None,
                error=SimpleNamespace(code="WF_ERR", message="workflow boom"),
            )
        )
        frontend = make_frontend(workflow_engine=engine)
        resp = await frontend._execute_workflow("wf", {})
        assert resp["isError"] is True
        assert resp["content"][0]["text"] == "workflow boom"

    async def test_failed_workflow_without_error_uses_default(self):
        engine = MagicMock()
        engine.execute = AsyncMock(
            return_value=SimpleNamespace(status=ExecutionStatus.FAILED, outputs=None, error=None)
        )
        frontend = make_frontend(workflow_engine=engine)
        resp = await frontend._execute_workflow("wf", {})
        assert resp["isError"] is True
        assert resp["content"][0]["text"] == "Workflow failed"

    async def test_engine_exception_propagates(self):
        engine = MagicMock()
        engine.execute = AsyncMock(side_effect=RuntimeError("engine down"))
        frontend = make_frontend(workflow_engine=engine)
        with pytest.raises(RuntimeError, match="engine down"):
            await frontend._execute_workflow("wf", {})


# ===========================================================================
# _execute_workflow_mgmt_tool
# ===========================================================================


class TestExecuteWorkflowMgmtTool:
    async def test_success_response_passthrough(self):
        provider = MagicMock()
        provider.call = AsyncMock(
            return_value={
                "content": [{"type": "text", "text": "ok"}],
                "isError": False,
            }
        )
        frontend = make_frontend(workflow_tools_provider=provider)
        resp = await frontend._execute_workflow_mgmt_tool("workflow_list", {"q": 1})
        provider.call.assert_awaited_once_with("workflow_list", {"q": 1})
        assert resp["isError"] is False

    async def test_error_response_passthrough(self):
        provider = MagicMock()
        provider.call = AsyncMock(
            return_value={
                "content": [{"type": "text", "text": json.dumps({"code": "BAD"})}],
                "isError": True,
            }
        )
        frontend = make_frontend(workflow_tools_provider=provider)
        resp = await frontend._execute_workflow_mgmt_tool("workflow_patch", {})
        assert resp["isError"] is True

    async def test_provider_exception_propagates(self):
        provider = MagicMock()
        provider.call = AsyncMock(side_effect=ValueError("provider broke"))
        frontend = make_frontend(workflow_tools_provider=provider)
        with pytest.raises(ValueError, match="provider broke"):
            await frontend._execute_workflow_mgmt_tool("workflow_run", {})


# ===========================================================================
# _execute_runner_tool — additional contract edges
# ===========================================================================


class TestExecuteRunnerTool:
    def _connected_runner_registry(self):
        from datetime import UTC, datetime

        from ploston_core.runner_management.registry import Runner, RunnerStatus

        reg = MagicMock()
        runner = Runner(
            id="rid",
            name="mac",
            token_hash="h",
            status=RunnerStatus.CONNECTED,
            created_at=datetime.now(UTC),
        )
        reg.get_by_name.return_value = runner
        return reg

    async def test_no_runner_registry_raises_unavailable(self):
        frontend = make_frontend(runner_registry=None)
        with pytest.raises(AELError) as exc:
            await frontend._execute_runner_tool("mac", "fs__read", {})
        assert exc.value.code == "TOOL_UNAVAILABLE"
        assert exc.value.http_status == 503
        assert "not configured" in exc.value.message

    async def test_unknown_runner_raises_404(self):
        reg = MagicMock()
        reg.get_by_name.return_value = None
        frontend = make_frontend(runner_registry=reg)
        with pytest.raises(AELError) as exc:
            await frontend._execute_runner_tool("ghost", "fs__read", {})
        assert exc.value.http_status == 404

    async def test_disconnected_runner_raises_503(self):
        reg = self._connected_runner_registry()
        frontend = make_frontend(runner_registry=reg)
        with patch("ploston_core.mcp_frontend.server.is_runner_connected", return_value=False):
            with pytest.raises(AELError) as exc:
                await frontend._execute_runner_tool("mac", "fs__read", {})
        assert exc.value.http_status == 503
        assert "not connected" in exc.value.message

    async def test_timeout_maps_to_tool_timeout_504(self):
        reg = self._connected_runner_registry()
        frontend = make_frontend(runner_registry=reg)
        with (
            patch("ploston_core.mcp_frontend.server.is_runner_connected", return_value=True),
            patch(
                "ploston_core.mcp_frontend.server.send_tool_call_to_runner",
                new_callable=AsyncMock,
                side_effect=TimeoutError(),
            ),
        ):
            with pytest.raises(AELError) as exc:
                await frontend._execute_runner_tool("mac", "fs__read", {})
        assert exc.value.code == "TOOL_TIMEOUT"
        assert exc.value.http_status == 504

    async def test_generic_exception_maps_to_execution_failed_500(self):
        reg = self._connected_runner_registry()
        frontend = make_frontend(runner_registry=reg)
        with (
            patch("ploston_core.mcp_frontend.server.is_runner_connected", return_value=True),
            patch(
                "ploston_core.mcp_frontend.server.send_tool_call_to_runner",
                new_callable=AsyncMock,
                side_effect=RuntimeError("ws dropped"),
            ),
        ):
            with pytest.raises(AELError) as exc:
                await frontend._execute_runner_tool("mac", "fs__read", {})
        assert exc.value.code == "TOOL_EXECUTION_FAILED"
        assert exc.value.http_status == 500

    async def test_output_format_result_serialized(self):
        reg = self._connected_runner_registry()
        frontend = make_frontend(runner_registry=reg)
        with (
            patch("ploston_core.mcp_frontend.server.is_runner_connected", return_value=True),
            patch(
                "ploston_core.mcp_frontend.server.send_tool_call_to_runner",
                new_callable=AsyncMock,
                return_value={"output": {"data": 1}},
            ),
        ):
            resp = await frontend._execute_runner_tool("mac", "fs__read", {})
        assert resp["isError"] is False
        assert json.loads(resp["content"][0]["text"]) == {"data": 1}


# ===========================================================================
# JSON-RPC response builders
# ===========================================================================


class TestResponseBuilders:
    def test_success_response_shape(self):
        frontend = make_frontend()
        assert frontend._success_response(1, {"x": 1}) == {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"x": 1},
        }

    def test_error_response_without_data_omits_data_field(self):
        frontend = make_frontend()
        resp = frontend._error_response(2, -32601, "nope")
        assert resp == {
            "jsonrpc": "2.0",
            "id": 2,
            "error": {"code": -32601, "message": "nope"},
        }

    def test_error_response_with_data_includes_data(self):
        frontend = make_frontend()
        resp = frontend._error_response(3, 500, "boom", {"code": "INTERNAL_ERROR"})
        assert resp["error"]["data"] == {"code": "INTERNAL_ERROR"}


# ===========================================================================
# list_changed notification path
# ===========================================================================


class TestListChangedNotification:
    async def test_stdio_notification_written_to_stdout(self):
        frontend = make_frontend()  # default stdio transport
        with patch(
            "ploston_core.mcp_frontend.server.write_message", new_callable=AsyncMock
        ) as mock_write:
            await frontend._send_tools_changed_notification()
        mock_write.assert_awaited_once()
        notif = mock_write.call_args[0][0]
        assert notif == {
            "jsonrpc": "2.0",
            "method": "notifications/tools/list_changed",
        }

    async def test_http_notification_routed_to_transport(self):
        from ploston_core.types import MCPTransport

        frontend = MCPFrontend(
            workflow_engine=MagicMock(),
            tool_registry=MagicMock(),
            workflow_registry=MagicMock(),
            tool_invoker=MagicMock(),
            mode_manager=ModeManager(initial_mode=Mode.RUNNING),
            transport=MCPTransport.HTTP,
        )
        http_transport = MagicMock()
        http_transport.send_notification = AsyncMock()
        frontend._http_transport = http_transport
        await frontend._send_tools_changed_notification()
        http_transport.send_notification.assert_awaited_once()
        notif = http_transport.send_notification.call_args[0][0]
        assert notif["method"] == "notifications/tools/list_changed"

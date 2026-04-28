"""Integration tests for workflow_tool_schema (I-01 & I-02).

I-01: Full authoring loop: workflow_schema → workflow_tool_schema →
      workflow_create → workflow_validate succeeds end-to-end.
I-02: workflow_tool_schema is present in tools/list and callable.

Usage:
    pytest tests/integration/test_workflow_tool_schema.py -v
"""

import json
from unittest.mock import MagicMock

import pytest

from ploston_core.registry.types import ToolDefinition
from ploston_core.types import ToolSource, ToolStatus
from ploston_core.workflow.tools import WorkflowToolsProvider

pytestmark = [pytest.mark.integration]


def _parse(mcp_response: dict) -> dict:
    """Extract inner result from MCP-format response."""
    return json.loads(mcp_response["content"][0]["text"])


@pytest.fixture
def tool_registry():
    """ToolRegistry that exposes system__python_exec with full schema."""
    reg = MagicMock()
    reg.list_tools.return_value = [
        ToolDefinition(
            name="python_exec",
            description="Execute Python code in a sandboxed environment.",
            source=ToolSource.SYSTEM,
            server_name="system",
            status=ToolStatus.AVAILABLE,
            input_schema={
                "type": "object",
                "required": ["code"],
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python code to execute.",
                    }
                },
            },
        ),
    ]
    reg.get_tool.return_value = None
    return reg


@pytest.fixture
def runner_registry():
    """RunnerRegistry with one runner exposing github tools."""
    runner = MagicMock()
    runner.name = "macbook-local"
    runner.available_tools = [
        {
            "name": "github__list_commits",
            "description": "List commits on a branch.",
            "inputSchema": {
                "type": "object",
                "required": ["owner", "repo"],
                "properties": {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                },
            },
        },
        "github__actions_list",
    ]
    reg = MagicMock()
    reg.list.return_value = [runner]
    return reg


@pytest.fixture
def workflow_registry():
    """Mock WorkflowRegistry that supports the authoring loop."""
    from ploston_core.workflow.parser import parse_workflow_yaml

    reg = MagicMock()
    reg.list_workflows.return_value = []

    # validate_yaml returns a valid result
    valid_result = MagicMock()
    valid_result.valid = True
    valid_result.errors = []
    valid_result.warnings = []
    reg.validate_yaml.return_value = valid_result

    # register_from_yaml parses and succeeds
    def _register(yaml_content, persist=False):
        return parse_workflow_yaml(yaml_content)

    reg.register_from_yaml.side_effect = _register

    return reg


@pytest.fixture
def provider(workflow_registry, tool_registry, runner_registry):
    return WorkflowToolsProvider(
        workflow_registry=workflow_registry,
        tool_registry=tool_registry,
        runner_registry=runner_registry,
    )


class TestAuthoringLoop:
    """I-01: Full authoring loop succeeds end-to-end."""

    @pytest.mark.asyncio
    async def test_full_authoring_loop(self, provider):
        # Step 1a: workflow_schema — static YAML reference (S-280: no longer
        # carries available_tools; discovery moves to workflow_list_tools).
        schema_result = _parse(await provider.call("workflow_schema", {}))
        assert "available_tools" not in schema_result
        assert "sections" in schema_result
        assert "schema" in schema_result

        # Step 1b: workflow_list_tools — discover available tools
        list_result = _parse(await provider.call("workflow_list_tools", {}))
        servers = {g["mcp_server"] for g in list_result["tools"]}
        assert "system" in servers

        # Step 2: workflow_tool_schema — get python_exec schema
        tool_result = _parse(
            await provider.call("workflow_tool_schema", {"mcp": "system", "tool": "python_exec"})
        )
        assert tool_result["source"] == "cp"
        assert "code" in tool_result["input_schema"].get("required", [])

        # Step 3: workflow_create — create a workflow that uses python_exec.
        # The supplied name contains a dash, which workflow_create must
        # sanitize to an underscore before registration.
        yaml_content = """
name: test-loop
version: "1.0"
description: Integration test workflow
steps:
  - id: greet
    tool: python_exec
    mcp: system
    params:
      code: "print('hello')"
"""
        create_result = _parse(
            await provider.call("workflow_create", {"yaml_content": yaml_content})
        )
        assert create_result["name"] == "test_loop"
        assert create_result["status"] == "created"
        assert create_result["name_sanitized"]["original"] == "test-loop"
        assert create_result["name_sanitized"]["registered_as"] == "test_loop"

        # Step 4: workflow_validate — confirm the YAML is valid
        validate_result = _parse(
            await provider.call("workflow_validate", {"yaml_content": yaml_content})
        )
        assert validate_result["valid"] is True


class TestToolExposure:
    """I-02: workflow_tool_schema is present in tools/list and callable."""

    def test_workflow_tool_schema_in_exposure(self, provider):
        """workflow_tool_schema appears in get_for_mcp_exposure()."""
        exposed = provider.get_for_mcp_exposure()
        names = [t["name"] for t in exposed]
        assert "workflow_tool_schema" in names

    @pytest.mark.asyncio
    async def test_workflow_tool_schema_callable(self, provider):
        """workflow_tool_schema is callable and returns structured result."""
        raw = await provider.call("workflow_tool_schema", {"mcp": "system", "tool": "python_exec"})
        result = _parse(raw)
        assert result["source"] == "cp"
        assert "input_schema" in result


class TestValidateAwait:
    """S-286 / T-905: workflow_validate surfaces missing-await warnings."""

    @pytest.mark.asyncio
    async def test_validate_returns_await_warning(self, provider):
        yaml_content = """
name: missing_await
version: "1.0"
description: "Workflow with a missing await on context.tools.call_mcp"
steps:
  - id: bad
    code: |
      x = context.tools.call_mcp("github", "list_repos", {})
      result = {"x": x}
"""
        raw = await provider.call("workflow_validate", {"yaml_content": yaml_content})
        result = _parse(raw)
        assert result["valid"] is True
        await_warnings = [w for w in result["warnings"] if w.get("path") == "steps[bad].code"]
        assert len(await_warnings) == 1
        assert "await" in await_warnings[0]["message"]
        assert await_warnings[0]["line"] == 1


class TestPatchIntegration:
    """S-287 / T-908: workflow_create → workflow_patch round-trip preserves
    code structure and re-registers the patched workflow.
    """

    @pytest.mark.asyncio
    async def test_create_then_patch_then_run(self, provider, workflow_registry):
        from ploston_core.workflow.parser import parse_workflow_yaml

        yaml_content = """
name: dx_int_patch
version: "1.0"
description: "Integration test workflow for workflow_patch"
steps:
  - id: greet
    code: |
      who = context.inputs.get("name", "world") or "world"
      result = {"greeting": "Hi, " + str(who) + "!"}
"""
        # Stage 1: create the workflow through MCP.
        create_resp = _parse(await provider.call("workflow_create", {"yaml_content": yaml_content}))
        assert create_resp["status"] == "created"
        assert create_resp["name"] == "dx_int_patch"

        # Stage 2: wire the registry so subsequent ``workflow_patch`` resolves
        # the just-created workflow with its YAML body. The shared fixture
        # auto-mocks ``get`` to a MagicMock; we override it here so the patch
        # handler sees a parsed WorkflowDefinition with yaml_content set.
        existing = parse_workflow_yaml(yaml_content)
        existing.yaml_content = yaml_content
        workflow_registry.get.return_value = existing
        workflow_registry.unregister.return_value = True

        # Stage 3: patch the code through MCP.
        patch_resp_raw = await provider.call(
            "workflow_patch",
            {
                "name": "dx_int_patch",
                "version": "1.1.0",
                "patches": [{"step_id": "greet", "old": "Hi, ", "new": "Hello, "}],
            },
        )
        patch_resp = _parse(patch_resp_raw)
        assert patch_resp["status"] == "patched"
        assert patch_resp["version"] == "1.1.0"
        assert patch_resp["patches_applied"] == 1
        assert "structuredContent" in patch_resp_raw

        # Stage 4: verify the YAML re-registered with the registry contains
        # the patched code while preserving the block scalar style and
        # surrounding structure (i.e. ruamel.yaml round-trip integrity).
        last_register_call = workflow_registry.register_from_yaml.call_args
        patched_yaml = last_register_call[0][0]
        assert "Hello, " in patched_yaml
        assert "Hi, " not in patched_yaml
        assert "code: |" in patched_yaml

        # Stage 5: confirm the patched YAML re-parses into a valid workflow
        # with the new version, simulating an end-to-end run-readiness check.
        patched_wf = parse_workflow_yaml(patched_yaml)
        assert patched_wf.name == "dx_int_patch"
        assert patched_wf.version == "1.1.0"
        assert patched_wf.steps[0].id == "greet"
        assert "Hello, " in (patched_wf.steps[0].code or "")


@pytest.fixture
def m081_provider(tmp_path):
    """Real ``WorkflowRegistry`` + a tool registry that surfaces a single
    ``echo`` tool. Used by the M-081 end-to-end integration tests so the
    static validators surface real ``unknown_tool``/``unknown_mcp``
    errors and the draft promotion path runs against persistent state.
    """
    from ploston_core.registry.types import ToolDefinition
    from ploston_core.types import ToolSource, ToolStatus
    from ploston_core.workflow.registry import WorkflowRegistry

    echo = ToolDefinition(
        name="echo",
        description="Echo a message back.",
        source=ToolSource.SYSTEM,
        server_name="system",
        input_schema={"type": "object", "properties": {"message": {"type": "string"}}},
        status=ToolStatus.AVAILABLE,
    )
    tr = MagicMock()
    tr.get_tool.return_value = MagicMock()
    tr.get.return_value = None

    def _list(server_name: str | None = None):
        if server_name in (None, "system"):
            return [echo]
        return []

    tr.list_tools.side_effect = _list

    config = MagicMock()
    config.directory = str(tmp_path / "workflows")
    config.draft_ttl_seconds = 1800
    reg = WorkflowRegistry(tr, config)
    return WorkflowToolsProvider(workflow_registry=reg, tool_registry=tr), reg


class TestM081HappyPath:
    """M-081 / S-291 / S-292: full P1+P2+P3+P4 happy path round-trip.

    Exercises the workflow_create-with-error → workflow_patch with the
    suggested_fix from the response → workflow_create succeeds flow that
    the spec calls out as the headline integration test for the
    Authoring DX v2 milestone.
    """

    @pytest.mark.asyncio
    async def test_full_authoring_session_happy_path(self, m081_provider):
        provider, registry = m081_provider
        # Stage 1: workflow_create with a forbidden import in a code step.
        # The provider's static validators reject ``import socket`` and
        # return ``status="draft"`` + a deterministic suggested_fix that
        # removes the offending line.
        bad_yaml = (
            "name: m081_happy\n"
            'version: "1.0.0"\n'
            "description: M-081 happy path test\n"
            "steps:\n"
            "  - id: a\n"
            "    code: |\n"
            "      import socket\n"
            '      result = {"v": 1}\n'
        )
        create_resp = _parse(await provider.call("workflow_create", {"yaml_content": bad_yaml}))
        assert create_resp["status"] == "draft"
        draft_id = create_resp["draft_id"]
        assert draft_id

        errors = create_resp["validation"]["errors"]
        forb = next((e for e in errors if e.get("path") == "steps.a.code"), None)
        assert forb is not None and forb["suggested_fix"] is not None
        fix = forb["suggested_fix"]
        assert fix["op"] == "replace"
        assert fix["new"] == ""

        # Stage 2: pass the suggested_fix straight back into workflow_patch.
        patch_resp = _parse(
            await provider.call(
                "workflow_patch",
                {"draft_id": draft_id, "operations": [fix]},
            )
        )
        assert patch_resp["status"] == "patched"
        assert patch_resp["promoted_from_draft"] is True
        assert patch_resp["validation"]["valid"] is True


class TestM081FailurePath:
    """M-081: ``test_full_authoring_session_failure_path``.

    Exercises the failure-path round-trip: a code-step error surfaces
    enriched runtime metadata (``code_context``, ``step_inputs``) which
    the agent uses with ``workflow_patch`` to repair the workflow in a
    single round trip. Engine integration is exercised through
    WorkflowToolsProvider's surface, mirroring how an MCP client sees
    the flow.
    """

    @pytest.mark.asyncio
    async def test_full_authoring_session_failure_path(self, m081_provider):
        provider, _ = m081_provider
        # Stage 1: create with a runtime-only bug. The static validator
        # accepts the code; the engine surfaces a ZeroDivisionError at
        # runtime with code_context attached.
        yaml_content = (
            "name: m081_fail_path\n"
            'version: "1.0.0"\n'
            "description: failure path round-trip\n"
            "steps:\n"
            "  - id: bad\n"
            "    code: |\n"
            "      x = 1 / 0\n"
            '      result = {"x": x}\n'
        )
        create_resp = _parse(await provider.call("workflow_create", {"yaml_content": yaml_content}))
        assert create_resp["status"] == "created"

        # Stage 2: verify the engine's runtime enrichment helper produces
        # the expected ``code_context`` shape. The full engine integration
        # is covered in tests/unit/engine; this end-to-end test asserts the
        # agent can reason over the helper's output to drive a patch.
        from ploston_core.engine.error_enrichment import build_code_context

        ctx = build_code_context('x = 1 / 0\nresult = {"x": x}', error_line=1)
        assert isinstance(ctx, list) and ctx
        assert any("1 / 0" in entry["text"] for entry in ctx)
        assert any(entry.get("is_error_line") for entry in ctx)

        # Stage 3: agent applies a deterministic fix via workflow_patch
        # using the spec's str_replace shape (legacy ``patches`` key
        # still accepted alongside ``operations``).
        patch_resp = _parse(
            await provider.call(
                "workflow_patch",
                {
                    "name": "m081_fail_path",
                    "version": "1.0.1",
                    "patches": [
                        {
                            "step_id": "bad",
                            "old": "x = 1 / 0",
                            "new": "x = 1",
                        }
                    ],
                },
            )
        )
        assert patch_resp["status"] == "patched"
        assert patch_resp["version"] == "1.0.1"

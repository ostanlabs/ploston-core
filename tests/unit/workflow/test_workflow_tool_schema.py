"""Unit tests for workflow_tool_schema tool (T-733).

Tests U-32 through U-42: Tool schema resolution via CP and runners.
"""

import json
from unittest.mock import MagicMock

import pytest

from ploston_core.registry.types import ToolDefinition
from ploston_core.types import ToolSource, ToolStatus
from ploston_core.workflow.tools import (
    WORKFLOW_CRUD_TOOL_NAMES,
    WORKFLOW_TOOL_SCHEMA_TOOL,
    WorkflowToolsProvider,
)


def _parse_mcp_result(mcp_response: dict) -> dict:
    """Parse MCP-format response to get the inner result dict."""
    text = mcp_response["content"][0]["text"]
    return json.loads(text)


@pytest.fixture
def mock_workflow_registry():
    """Create a mock workflow registry."""
    registry = MagicMock()
    registry.list_workflows.return_value = []
    return registry


@pytest.fixture
def mock_tool_registry():
    """Create a mock tool registry with system and MCP tools."""
    registry = MagicMock()

    python_exec = ToolDefinition(
        name="python_exec",
        description="Execute Python code in a sandbox.",
        source=ToolSource.SYSTEM,
        server_name="system",
        input_schema={
            "type": "object",
            "required": ["code"],
            "properties": {"code": {"type": "string", "description": "Python code to execute"}},
        },
        status=ToolStatus.AVAILABLE,
    )
    github_list = ToolDefinition(
        name="list_repos",
        description="List GitHub repositories.",
        source=ToolSource.MCP,
        server_name="github",
        input_schema={
            "type": "object",
            "properties": {"org": {"type": "string", "description": "Organization name"}},
        },
        status=ToolStatus.AVAILABLE,
    )

    def list_tools_side_effect(server_name=None, **kwargs):
        tools = [python_exec, github_list]
        if server_name is not None:
            return [t for t in tools if t.server_name == server_name]
        return tools

    registry.list_tools.side_effect = list_tools_side_effect
    return registry


@pytest.fixture
def mock_runner_registry():
    """Create a mock runner registry with runner-hosted tools."""
    runner = MagicMock()
    runner.name = "mac"
    runner.available_tools = [
        {
            "name": "fs__read_file",
            "description": "Read a file",
            "inputSchema": {
                "type": "object",
                "required": ["path"],
                "properties": {"path": {"type": "string"}},
            },
        },
        "docker__run_container",
    ]

    registry = MagicMock()
    registry.list.return_value = [runner]
    return registry


@pytest.fixture
def provider(mock_workflow_registry, mock_tool_registry, mock_runner_registry):
    """Create WorkflowToolsProvider with mocks."""
    return WorkflowToolsProvider(
        workflow_registry=mock_workflow_registry,
        tool_registry=mock_tool_registry,
        runner_registry=mock_runner_registry,
    )


class TestToolSchemaRegistration:
    """U-32/U-33: Tool name in CRUD set and tool list."""

    def test_tool_name_in_crud_set(self):
        """U-32: workflow_tool_schema is in WORKFLOW_CRUD_TOOL_NAMES."""
        assert "workflow_tool_schema" in WORKFLOW_CRUD_TOOL_NAMES

    def test_tool_schema_definition_exists(self):
        """U-33: WORKFLOW_TOOL_SCHEMA_TOOL has correct shape."""
        assert WORKFLOW_TOOL_SCHEMA_TOOL["name"] == "workflow_tool_schema"
        schema = WORKFLOW_TOOL_SCHEMA_TOOL["inputSchema"]
        assert "mcp" in schema["required"]
        assert "tool" in schema["required"]


class TestToolSchemaResolution:
    """U-34 through U-40: Resolution via CP and runners."""

    @pytest.mark.asyncio
    async def test_cp_system_tool(self, provider):
        """U-34: Resolve system tool python_exec via CP."""
        raw = await provider.call("workflow_tool_schema", {"mcp": "system", "tool": "python_exec"})
        result = _parse_mcp_result(raw)
        assert result["mcp_server"] == "system"
        assert result["tool"] == "python_exec"
        assert result["source"] == "cp"
        assert "code" in result["input_schema"].get("properties", {})
        assert result["runner"] is None

    @pytest.mark.asyncio
    async def test_cp_mcp_tool(self, provider):
        """U-35: Resolve CP-registered MCP tool."""
        raw = await provider.call("workflow_tool_schema", {"mcp": "github", "tool": "list_repos"})
        result = _parse_mcp_result(raw)
        assert result["source"] == "cp"
        assert result["mcp_server"] == "github"
        assert result["tool"] == "list_repos"

    @pytest.mark.asyncio
    async def test_runner_tool_with_schema(self, provider):
        """U-36: Resolve runner tool that has full schema (dict entry)."""
        raw = await provider.call("workflow_tool_schema", {"mcp": "fs", "tool": "read_file"})
        result = _parse_mcp_result(raw)
        assert result["source"] == "runner"
        assert result["runner"] == "mac"
        assert result["description"] == "Read a file"
        assert "path" in result["input_schema"].get("properties", {})

    @pytest.mark.asyncio
    async def test_runner_tool_string_only(self, provider):
        """U-37: Resolve runner tool that is a plain string (no schema)."""
        raw = await provider.call(
            "workflow_tool_schema", {"mcp": "docker", "tool": "run_container"}
        )
        result = _parse_mcp_result(raw)
        assert result["source"] == "runner"
        assert result["runner"] == "mac"
        assert result["input_schema"] == {}
        assert result["description"] is None

    @pytest.mark.asyncio
    async def test_not_found_returns_hint(self, provider):
        """U-38: Unknown tool returns structured not-found with hint."""
        raw = await provider.call("workflow_tool_schema", {"mcp": "nonexistent", "tool": "nope"})
        result = _parse_mcp_result(raw)
        assert result.get("found") is False
        assert "error" in result
        assert "available_tools" in result

    @pytest.mark.asyncio
    async def test_missing_mcp_param(self, provider):
        """U-39: Missing 'mcp' parameter raises error."""
        with pytest.raises(Exception):
            await provider.call("workflow_tool_schema", {"tool": "python_exec"})

    @pytest.mark.asyncio
    async def test_missing_tool_param(self, provider):
        """U-40: Missing 'tool' parameter raises error."""
        with pytest.raises(Exception):
            await provider.call("workflow_tool_schema", {"mcp": "system"})

    @pytest.mark.asyncio
    async def test_cp_takes_priority_over_runner(
        self, mock_workflow_registry, mock_runner_registry
    ):
        """U-41: CP tools resolve before runner tools."""
        cp_tool = ToolDefinition(
            name="read_file",
            description="CP version",
            source=ToolSource.MCP,
            server_name="fs",
            input_schema={"type": "object"},
            status=ToolStatus.AVAILABLE,
        )
        tool_reg = MagicMock()
        tool_reg.list_tools.return_value = [cp_tool]

        provider = WorkflowToolsProvider(
            workflow_registry=mock_workflow_registry,
            tool_registry=tool_reg,
            runner_registry=mock_runner_registry,
        )

        raw = await provider.call("workflow_tool_schema", {"mcp": "fs", "tool": "read_file"})
        result = _parse_mcp_result(raw)
        assert result["source"] == "cp"
        assert result["description"] == "CP version"


class TestToolSchemaNoRegistries:
    """U-42: Graceful behavior without registries."""

    @pytest.mark.asyncio
    async def test_no_registries(self, mock_workflow_registry):
        """U-42: Works with no tool/runner registries (returns not found)."""
        provider = WorkflowToolsProvider(
            workflow_registry=mock_workflow_registry,
            tool_registry=None,
            runner_registry=None,
        )
        raw = await provider.call("workflow_tool_schema", {"mcp": "system", "tool": "python_exec"})
        result = _parse_mcp_result(raw)
        assert result.get("found") is False


class TestWorkflowGetDefinition:
    """Tests for workflow_get_definition tool."""

    @pytest.fixture
    def mock_workflow(self):
        """Create a mock workflow with full details."""
        from ploston_core.types import OnError

        workflow = MagicMock()
        workflow.name = "test-wf"
        workflow.version = "1.0"
        workflow.description = "A test workflow"
        workflow.tags = ["test", "ci"]
        workflow.yaml_content = "name: test-wf\nversion: '1.0'"

        # Packages
        workflow.packages = MagicMock()
        workflow.packages.profile = "standard"
        workflow.packages.additional = ["requests"]

        # Defaults
        workflow.defaults = MagicMock()
        workflow.defaults.timeout = 30
        workflow.defaults.on_error = OnError.FAIL
        workflow.defaults.retry = None
        workflow.defaults.runner = None

        # Inputs
        inp = MagicMock()
        inp.name = "url"
        inp.type = "string"
        inp.required = True
        inp.default = None
        inp.description = "URL to fetch"
        inp.enum = None
        inp.pattern = None
        inp.minimum = None
        inp.maximum = None
        workflow.inputs = [inp]

        # Steps
        step1 = MagicMock()
        step1.id = "fetch"
        step1.tool = "http_get"
        step1.code = None
        step1.mcp = "http"
        step1.params = {"url": "{{ inputs.url }}"}
        step1.depends_on = None
        step1.when = None
        step1.on_error = None
        step1.timeout = 60
        step1.on_missing_tool = None
        step1.retry = None
        workflow.steps = [step1]

        # Outputs
        out = MagicMock()
        out.name = "body"
        out.from_path = "steps.fetch.output"
        out.value = None
        out.description = "Response body"
        workflow.outputs = [out]

        return workflow

    @pytest.mark.asyncio
    async def test_returns_full_definition(self, mock_workflow_registry, mock_workflow):
        """workflow_get_definition returns structured definition with steps."""
        mock_workflow_registry.get.return_value = mock_workflow

        provider = WorkflowToolsProvider(
            workflow_registry=mock_workflow_registry,
        )
        raw = await provider.call("workflow_get_definition", {"name": "test-wf"})
        result = _parse_mcp_result(raw)

        assert result["name"] == "test-wf"
        assert result["version"] == "1.0"
        assert result["description"] == "A test workflow"
        assert result["tags"] == ["test", "ci"]

        # Check packages
        assert result["packages"]["profile"] == "standard"
        assert result["packages"]["additional"] == ["requests"]

        # Check inputs
        assert len(result["inputs"]) == 1
        assert result["inputs"][0]["name"] == "url"
        assert result["inputs"][0]["type"] == "string"
        assert result["inputs"][0]["description"] == "URL to fetch"

        # Check steps
        assert len(result["steps"]) == 1
        step = result["steps"][0]
        assert step["id"] == "fetch"
        assert step["tool"] == "http_get"
        assert step["mcp"] == "http"
        assert step["params"] == {"url": "{{ inputs.url }}"}
        assert step["timeout"] == 60
        assert "code" not in step  # None values stripped

        # Check outputs
        assert len(result["outputs"]) == 1
        assert result["outputs"][0]["name"] == "body"
        assert result["outputs"][0]["from_path"] == "steps.fetch.output"

        # yaml_content included — directly consumable by workflow_create
        assert result["yaml_content"] == "name: test-wf\nversion: '1.0'"

    @pytest.mark.asyncio
    async def test_not_found(self, mock_workflow_registry):
        """workflow_get_definition raises error for unknown workflow."""
        mock_workflow_registry.get.return_value = None

        provider = WorkflowToolsProvider(
            workflow_registry=mock_workflow_registry,
        )
        with pytest.raises(Exception):
            await provider.call("workflow_get_definition", {"name": "nope"})

    @pytest.mark.asyncio
    async def test_missing_name_param(self, mock_workflow_registry):
        """workflow_get_definition raises error when name is missing."""
        provider = WorkflowToolsProvider(
            workflow_registry=mock_workflow_registry,
        )
        with pytest.raises(Exception):
            await provider.call("workflow_get_definition", {})


class TestWorkflowNameSanitization:
    """workflow_create / workflow_update replace dashes with underscores."""

    _YAML_WITH_DASHES = (
        "# header comment\n"
        "name: my-cool-workflow\n"
        "version: '1.0.0'\n"
        "description: A sanitization test\n"
        "steps:\n"
        "  - id: step-one\n"
        "    tool: echo\n"
        "    mcp: system\n"
        "    params:\n"
        "      name: should-not-change\n"
    )

    @pytest.mark.asyncio
    async def test_create_replaces_dashes_with_underscores(self, mock_workflow_registry):
        """workflow_create rewrites dashes in the workflow name before registering."""
        provider = WorkflowToolsProvider(workflow_registry=mock_workflow_registry)

        raw = await provider.call(
            "workflow_create",
            {"yaml_content": self._YAML_WITH_DASHES},
        )
        result = _parse_mcp_result(raw)

        assert result["name"] == "my_cool_workflow"
        assert result["status"] == "created"
        assert result["name_sanitized"] == {
            "original": "my-cool-workflow",
            "registered_as": "my_cool_workflow",
            "reason": "dashes replaced with underscores",
        }

        # register_from_yaml must receive YAML whose top-level name field was
        # rewritten — otherwise the persisted file and in-memory registry
        # would drift after a reload.
        mock_workflow_registry.register_from_yaml.assert_called_once()
        call_kwargs = mock_workflow_registry.register_from_yaml.call_args
        passed_yaml = (
            call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs["yaml_content"]
        )
        assert "name: my_cool_workflow" in passed_yaml
        assert "name: my-cool-workflow" not in passed_yaml
        # Nested name fields must be untouched.
        assert "name: should-not-change" in passed_yaml
        assert "id: step-one" in passed_yaml

    @pytest.mark.asyncio
    async def test_create_no_sanitization_field_when_already_clean(self, mock_workflow_registry):
        """Names without dashes pass through untouched and omit name_sanitized."""
        provider = WorkflowToolsProvider(workflow_registry=mock_workflow_registry)
        clean_yaml = (
            "name: already_clean\n"
            "version: '1.0.0'\n"
            "steps:\n"
            "  - id: step1\n"
            "    tool: echo\n"
            "    mcp: system\n"
        )

        raw = await provider.call("workflow_create", {"yaml_content": clean_yaml})
        result = _parse_mcp_result(raw)

        assert result["name"] == "already_clean"
        assert "name_sanitized" not in result

    @pytest.mark.asyncio
    async def test_update_sanitizes_lookup_name_and_yaml(self, mock_workflow_registry):
        """workflow_update sanitizes both the `name` param and the YAML body."""
        existing = MagicMock()
        existing.name = "my_cool_workflow"
        mock_workflow_registry.get.return_value = existing

        provider = WorkflowToolsProvider(workflow_registry=mock_workflow_registry)

        raw = await provider.call(
            "workflow_update",
            {"name": "my-cool-workflow", "yaml_content": self._YAML_WITH_DASHES},
        )
        result = _parse_mcp_result(raw)

        # Registry lookup must have happened under the sanitized key.
        mock_workflow_registry.get.assert_called_once_with("my_cool_workflow")
        mock_workflow_registry.unregister.assert_called_once_with("my_cool_workflow")
        mock_workflow_registry.register_from_yaml.assert_called_once()
        passed_yaml = mock_workflow_registry.register_from_yaml.call_args.args[0]
        assert "name: my_cool_workflow" in passed_yaml

        assert result["name"] == "my_cool_workflow"
        assert result["status"] == "updated"
        assert result["name_sanitized"]["original"] == "my-cool-workflow"
        assert result["name_sanitized"]["registered_as"] == "my_cool_workflow"

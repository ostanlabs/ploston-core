"""Unit tests for ``workflow_patch`` (S-287 / T-908).

Covers the str_replace patch surface: success path, validation errors,
round-trip preservation, change-notifications, and persistence semantics.
The handler is exercised through ``WorkflowToolsProvider.call`` so the
MCP envelope (``content`` + ``structuredContent``) is asserted alongside
the inner payload.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from ploston_core.errors import AELError
from ploston_core.workflow.parser import parse_workflow_yaml
from ploston_core.workflow.tools import WorkflowToolsProvider

_BASE_YAML = """name: dx_patch
version: "1.0"
description: "DX workflow_patch fixture"
steps:
  - id: greet
    code: |
      who = context.inputs.get("name", "world") or "world"
      result = {"greeting": "Hi, " + str(who) + "!"}
  - id: shout
    code: |
      result = {"loud": "HI"}
"""


def _parse(mcp_response: dict) -> dict:
    return json.loads(mcp_response["content"][0]["text"])


@pytest.fixture
def registry():
    """Mock WorkflowRegistry that returns the base YAML and re-parses on register."""
    reg = MagicMock()
    existing = parse_workflow_yaml(_BASE_YAML)
    existing.yaml_content = _BASE_YAML
    reg.get.return_value = existing
    reg.unregister.return_value = True
    reg.register_from_yaml.side_effect = lambda y, persist=False: parse_workflow_yaml(y)
    return reg


@pytest.fixture
def notify_calls() -> list[None]:
    return []


@pytest.fixture
def provider(registry, notify_calls):
    async def _on_tools_changed() -> None:
        notify_calls.append(None)

    return WorkflowToolsProvider(
        workflow_registry=registry,
        on_tools_changed=_on_tools_changed,
    )


class TestWorkflowPatch:
    @pytest.mark.asyncio
    async def test_basic_str_replace(self, provider, registry):
        raw = await provider.call(
            "workflow_patch",
            {
                "name": "dx_patch",
                "version": "1.1.0",
                "patches": [{"step_id": "greet", "old": "Hi, ", "new": "Hello, "}],
            },
        )
        body = _parse(raw)
        assert body["status"] == "patched"
        assert body["version"] == "1.1.0"
        assert body["patches_applied"] == 1
        patched = registry.register_from_yaml.call_args[0][0]
        assert "Hello, " in patched and "Hi, " not in patched

    @pytest.mark.asyncio
    async def test_multiple_patches(self, provider, registry):
        raw = await provider.call(
            "workflow_patch",
            {
                "name": "dx_patch",
                "version": "1.2.0",
                "patches": [
                    {"step_id": "greet", "old": "Hi, ", "new": "Hello, "},
                    {"step_id": "shout", "old": '"HI"', "new": '"HEY"'},
                ],
            },
        )
        body = _parse(raw)
        assert body["patches_applied"] == 2
        patched = registry.register_from_yaml.call_args[0][0]
        assert "Hello, " in patched
        assert '"HEY"' in patched

    @pytest.mark.asyncio
    async def test_old_not_found(self, provider, registry):
        with pytest.raises(AELError) as exc:
            await provider.call(
                "workflow_patch",
                {
                    "name": "dx_patch",
                    "version": "1.1.0",
                    "patches": [{"step_id": "greet", "old": "missing-substr", "new": "x"}],
                },
            )
        assert exc.value.code == "INPUT_INVALID"
        assert "not found" in (exc.value.detail or "")
        registry.unregister.assert_not_called()

    @pytest.mark.asyncio
    async def test_old_not_unique(self, provider, registry):
        # ``world`` appears 3 times in the greet code body, so it is not
        # unique within that step.
        with pytest.raises(AELError) as exc:
            await provider.call(
                "workflow_patch",
                {
                    "name": "dx_patch",
                    "version": "1.1.0",
                    "patches": [{"step_id": "greet", "old": "world", "new": "earth"}],
                },
            )
        assert exc.value.code == "INPUT_INVALID"
        assert "unique" in (exc.value.detail or "").lower()

    @pytest.mark.asyncio
    async def test_step_not_found(self, provider):
        with pytest.raises(AELError) as exc:
            await provider.call(
                "workflow_patch",
                {
                    "name": "dx_patch",
                    "version": "1.1.0",
                    "patches": [{"step_id": "nope", "old": "Hi", "new": "X"}],
                },
            )
        assert exc.value.code == "INPUT_INVALID"
        assert "nope" in (exc.value.detail or "")

    @pytest.mark.asyncio
    async def test_step_not_code(self, registry, notify_calls):
        # Build a fixture with a tool step (no ``code`` block).
        tool_yaml = (
            "name: dx_tool\n"
            'version: "1.0"\n'
            'description: "DX patch tool-step fixture"\n'
            "steps:\n"
            "  - id: pull\n"
            "    tool: python_exec\n"
            "    mcp: system\n"
            "    params:\n"
            '      code: "result = 1"\n'
        )
        existing = parse_workflow_yaml(tool_yaml)
        existing.yaml_content = tool_yaml
        registry.get.return_value = existing

        async def _on_tools_changed() -> None:
            notify_calls.append(None)

        provider = WorkflowToolsProvider(
            workflow_registry=registry, on_tools_changed=_on_tools_changed
        )
        with pytest.raises(AELError) as exc:
            await provider.call(
                "workflow_patch",
                {
                    "name": "dx_tool",
                    "version": "1.1.0",
                    "patches": [{"step_id": "pull", "old": "1", "new": "2"}],
                },
            )
        assert exc.value.code == "INPUT_INVALID"
        assert "code" in (exc.value.detail or "").lower()

    @pytest.mark.asyncio
    async def test_workflow_not_found(self, provider, registry):
        registry.get.return_value = None
        with pytest.raises(AELError) as exc:
            await provider.call(
                "workflow_patch",
                {
                    "name": "missing",
                    "version": "1.1.0",
                    "patches": [{"step_id": "x", "old": "y", "new": "z"}],
                },
            )
        assert exc.value.code == "WORKFLOW_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_version_supplied_by_caller(self, provider):
        raw = await provider.call(
            "workflow_patch",
            {
                "name": "dx_patch",
                "version": "9.9.9",
                "patches": [{"step_id": "greet", "old": "Hi, ", "new": "Hello, "}],
            },
        )
        body = _parse(raw)
        assert body["version"] == "9.9.9"

    @pytest.mark.asyncio
    async def test_version_required(self, provider):
        with pytest.raises(AELError) as exc:
            await provider.call(
                "workflow_patch",
                {
                    "name": "dx_patch",
                    "patches": [{"step_id": "greet", "old": "Hi, ", "new": "Hello, "}],
                },
            )
        assert exc.value.code == "PARAM_INVALID"

    @pytest.mark.asyncio
    async def test_tool_preview_returned(self, provider):
        raw = await provider.call(
            "workflow_patch",
            {
                "name": "dx_patch",
                "version": "1.1.0",
                "patches": [{"step_id": "greet", "old": "Hi, ", "new": "Hello, "}],
            },
        )
        body = _parse(raw)
        assert "tool_preview" in body
        assert isinstance(body["tool_preview"], dict)
        assert body["tool_preview"].get("tool_name") == "dx_patch"

    @pytest.mark.asyncio
    async def test_yaml_round_trip_preserved(self, provider, registry):
        await provider.call(
            "workflow_patch",
            {
                "name": "dx_patch",
                "version": "1.1.0",
                "patches": [{"step_id": "greet", "old": "Hi, ", "new": "Hello, "}],
            },
        )
        patched = registry.register_from_yaml.call_args[0][0]
        # ``code: |`` block scalar style preserved.
        assert "code: |" in patched
        # Re-parses cleanly back into a WorkflowDefinition.
        wf = parse_workflow_yaml(patched)
        assert wf.version == "1.1.0"
        assert wf.steps[0].id == "greet"

    @pytest.mark.asyncio
    async def test_tools_changed_notification(self, provider, notify_calls):
        await provider.call(
            "workflow_patch",
            {
                "name": "dx_patch",
                "version": "1.1.0",
                "patches": [{"step_id": "greet", "old": "Hi, ", "new": "Hello, "}],
            },
        )
        assert len(notify_calls) == 1

    @pytest.mark.asyncio
    async def test_partial_failure_no_persist(self, provider, registry, notify_calls):
        # Second patch is invalid (substring missing); no patch should
        # land on the registry, no tools-changed notification should fire.
        with pytest.raises(AELError):
            await provider.call(
                "workflow_patch",
                {
                    "name": "dx_patch",
                    "version": "1.1.0",
                    "patches": [
                        {"step_id": "greet", "old": "Hi, ", "new": "Hello, "},
                        {"step_id": "shout", "old": "DOES_NOT_EXIST", "new": "X"},
                    ],
                },
            )
        registry.unregister.assert_not_called()
        registry.register_from_yaml.assert_not_called()
        assert notify_calls == []

    @pytest.mark.asyncio
    async def test_structured_content_response(self, provider):
        raw = await provider.call(
            "workflow_patch",
            {
                "name": "dx_patch",
                "version": "1.1.0",
                "patches": [{"step_id": "greet", "old": "Hi, ", "new": "Hello, "}],
            },
        )
        assert raw["isError"] is False
        assert raw["content"][0]["type"] == "text"
        assert "structuredContent" in raw
        sc = raw["structuredContent"]
        assert sc["status"] == "patched"
        assert sc["version"] == "1.1.0"

    @pytest.mark.asyncio
    async def test_name_dash_sanitized(self, registry):
        # ``my-flow`` should resolve to ``my_flow`` for registry lookup.
        existing = parse_workflow_yaml(_BASE_YAML)
        existing.yaml_content = _BASE_YAML
        registry.get.return_value = existing
        provider = WorkflowToolsProvider(workflow_registry=registry)
        await provider.call(
            "workflow_patch",
            {
                "name": "my-flow",
                "version": "1.1.0",
                "patches": [{"step_id": "greet", "old": "Hi, ", "new": "Hello, "}],
            },
        )
        # registry.get must have been called with the sanitized name.
        registry.get.assert_called_with("my_flow")

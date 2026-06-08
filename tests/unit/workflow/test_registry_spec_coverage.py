"""Spec-coverage tests for WorkflowRegistry uncovered paths.

These tests assert the *intended* contract documented in the registry's
docstrings/DEC references — reserved-name and CP-tool-collision rejection,
validation-failure error envelopes, the bare-name MCP exposure surface with
input-schema construction and tags, lookup helpers, list/get behaviour,
``register_validated``, ``validate_yaml`` error wrapping, and
``get_for_mcp_exposure``.

External boundaries (tool registry, runner registry, redis store) are mocked;
the registry's own logic is exercised for real.

DO NOT duplicate test_registry_persistence.py / test_registry_robustness.py.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ploston_core.errors.errors import AELError
from ploston_core.workflow.registry import (
    WORKFLOW_RESERVED_NAMES,
    WorkflowRegistry,
)
from ploston_core.workflow.types import (
    InputDefinition,
    OutputDefinition,
    StepDefinition,
    WorkflowDefinition,
)

# ── Fixtures / helpers ───────────────────────────────────────────────


def _make_config(tmp_path: Path | None = None) -> MagicMock:
    config = MagicMock()
    config.directory = str((tmp_path or Path("/tmp/ploston-spec-cov")) / "workflows")
    config.draft_ttl_seconds = 1800
    return config


def _make_tool_registry(collision_name: str | None = None) -> MagicMock:
    """Mock ToolRegistry.

    ``get(name)`` returns a truthy object only for ``collision_name`` so the
    DEC-169 collision branch can be exercised on demand.
    """
    tr = MagicMock()

    def _get(name):
        return MagicMock() if name == collision_name else None

    tr.get.side_effect = _get
    # echo@system resolves for the validator's CP-direct path.
    echo = MagicMock()
    echo.name = "echo"
    echo.server_name = "system"
    tr.list_tools.return_value = [echo]
    return tr


def _code_workflow(name: str = "wf", version: str = "1.0.0") -> WorkflowDefinition:
    """A minimal valid code-step workflow (no tool resolution needed)."""
    return WorkflowDefinition(
        name=name,
        version=version,
        steps=[StepDefinition(id="s1", code="result = 1")],
    )


# ── Reserved-name rejection (register / register_validated) ──────────


class TestReservedNameRejection:
    @pytest.mark.parametrize("reserved", sorted(WORKFLOW_RESERVED_NAMES))
    def test_register_rejects_every_reserved_name(self, reserved: str):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config())
        wf = _code_workflow(name=reserved)
        with pytest.raises(AELError) as ei:
            reg.register(wf, validate=False)
        assert ei.value.code == "INPUT_INVALID"
        assert reserved in ei.value.detail
        # Rejected workflows must NOT be stored.
        assert reg.get(reserved) is None

    def test_register_validated_rejects_reserved_name(self):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config())
        wf = _code_workflow(name="run")
        with pytest.raises(AELError) as ei:
            reg.register_validated(wf, yaml_content="name: run\n", persist=False)
        assert ei.value.code == "INPUT_INVALID"
        assert "reserved" in ei.value.detail.lower()
        assert reg.get("run") is None


# ── CP-tool name collision rejection ─────────────────────────────────


class TestNameCollisionRejection:
    def test_register_rejects_collision_with_cp_tool(self):
        reg = WorkflowRegistry(_make_tool_registry(collision_name="taken"), _make_config())
        with pytest.raises(AELError) as ei:
            reg.register(_code_workflow(name="taken"), validate=False)
        assert ei.value.code == "INPUT_INVALID"
        assert "collides" in ei.value.detail
        assert reg.get("taken") is None

    def test_register_validated_rejects_collision_with_cp_tool(self):
        reg = WorkflowRegistry(_make_tool_registry(collision_name="taken"), _make_config())
        with pytest.raises(AELError) as ei:
            reg.register_validated(
                _code_workflow(name="taken"), yaml_content="name: taken\n", persist=False
            )
        assert ei.value.code == "INPUT_INVALID"
        assert "collides" in ei.value.detail


# ── Validation-failure error envelope on register(validate=True) ─────


class TestRegisterValidationFailure:
    def test_register_raises_input_invalid_with_joined_messages(self):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config())
        # version missing + a step lacking both tool and code → 2 errors.
        wf = WorkflowDefinition(
            name="bad",
            version="",
            steps=[StepDefinition(id="s1")],
        )
        with pytest.raises(AELError) as ei:
            reg.register(wf, validate=True)
        err = ei.value
        assert err.code == "INPUT_INVALID"
        # The detail joins "<path>: <message>" pairs with "; ".
        assert "version: Workflow version is required" in err.detail
        assert "steps.s1: Step must have either 'tool' or 'code'" in err.detail
        assert "; " in err.detail
        assert reg.get("bad") is None

    def test_register_validate_false_skips_validation_and_stores(self):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config())
        # Invalid (no version) but validate=False → stored, result.valid True.
        wf = WorkflowDefinition(name="nover", version="", steps=[])
        result = reg.register(wf, validate=False)
        assert result.valid is True
        assert reg.get("nover") is wf

    def test_register_returns_validation_result_on_success(self):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config())
        result = reg.register(_code_workflow(name="ok"), validate=True)
        assert result.valid is True
        assert result.errors == []


# ── Lookup helpers: get / get_or_raise / list_workflows ──────────────


class TestLookup:
    def test_get_returns_none_for_unknown(self):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config())
        assert reg.get("nope") is None

    def test_get_returns_definition_after_register(self):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config())
        wf = _code_workflow(name="present")
        reg.register(wf, validate=False)
        assert reg.get("present") is wf

    def test_get_or_raise_returns_when_present(self):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config())
        wf = _code_workflow(name="present")
        reg.register(wf, validate=False)
        assert reg.get_or_raise("present") is wf

    def test_get_or_raise_raises_not_found_with_name(self):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config())
        with pytest.raises(AELError) as ei:
            reg.get_or_raise("ghost")
        assert ei.value.code == "WORKFLOW_NOT_FOUND"
        assert "ghost" in ei.value.message

    def test_list_workflows_reflects_registered_set(self):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config())
        a = _code_workflow(name="a")
        b = _code_workflow(name="b")
        reg.register(a, validate=False)
        reg.register(b, validate=False)
        names = {w.name for w in reg.list_workflows()}
        assert names == {"a", "b"}

    def test_list_workflows_empty_initially(self):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config())
        assert reg.list_workflows() == []


# ── MCP exposure surface (bare names + tags + input schema) ──────────


class TestMcpExposure:
    def test_bare_name_and_workflow_tag(self):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config())
        reg.register(_code_workflow(name="my_wf"), validate=False)
        tools = reg.get_for_mcp_exposure()
        assert len(tools) == 1
        tool = tools[0]
        # DEC-169: bare name (no namespacing prefix).
        assert tool["name"] == "my_wf"
        assert tool["_ploston_tags"] == {"kind:workflow"}

    def test_default_description_when_missing(self):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config())
        reg.register(_code_workflow(name="nodesc"), validate=False)
        tool = reg.get_for_mcp_exposure()[0]
        assert tool["description"] == "Execute nodesc workflow"

    def test_uses_workflow_description_when_present(self):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config())
        wf = _code_workflow(name="withdesc")
        wf.description = "Does a thing"
        reg.register(wf, validate=False)
        tool = reg.get_for_mcp_exposure()[0]
        assert tool["description"] == "Does a thing"

    def test_input_schema_includes_constraints_and_required(self):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config())
        wf = WorkflowDefinition(
            name="schemawf",
            version="1.0.0",
            inputs=[
                InputDefinition(
                    name="n",
                    type="integer",
                    required=True,
                    description="a number",
                    minimum=1,
                    maximum=10,
                ),
                InputDefinition(
                    name="kind",
                    type="string",
                    required=False,
                    enum=["x", "y"],
                    pattern="^[xy]$",
                ),
            ],
            steps=[StepDefinition(id="s1", code="result = 1")],
        )
        reg.register(wf, validate=False)
        schema = reg.get_for_mcp_exposure()[0]["inputSchema"]
        assert schema["type"] == "object"
        n = schema["properties"]["n"]
        assert n == {
            "type": "integer",
            "description": "a number",
            "minimum": 1,
            "maximum": 10,
        }
        kind = schema["properties"]["kind"]
        assert kind["enum"] == ["x", "y"]
        assert kind["pattern"] == "^[xy]$"
        # Only required inputs appear in "required".
        assert schema["required"] == ["n"]

    def test_input_schema_omits_required_key_when_none_required(self):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config())
        wf = WorkflowDefinition(
            name="allopt",
            version="1.0.0",
            inputs=[InputDefinition(name="a", type="string", required=False)],
            steps=[StepDefinition(id="s1", code="result = 1")],
        )
        reg.register(wf, validate=False)
        schema = reg.get_for_mcp_exposure()[0]["inputSchema"]
        assert "required" not in schema

    def test_empty_registry_exposes_no_tools(self):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config())
        assert reg.get_for_mcp_exposure() == []


# ── validate_yaml: success + parse-error wrapping ────────────────────


class TestValidateYaml:
    _GOOD = 'name: vy\nversion: "1.0.0"\nsteps:\n  - id: s1\n    code: result = 1\n'

    def test_validate_yaml_valid(self):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config())
        result = reg.validate_yaml(self._GOOD)
        assert result.valid is True
        assert result.errors == []

    def test_validate_yaml_parse_error_wrapped_as_issue(self):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config())
        # Malformed YAML / missing required fields cause parse_workflow_yaml
        # to raise; validate_yaml must capture it as a path="yaml" issue
        # rather than propagating the exception.
        result = reg.validate_yaml("::: not valid yaml :::\n  - broken")
        assert result.valid is False
        assert len(result.errors) == 1
        assert result.errors[0].path == "yaml"
        assert result.errors[0].severity == "error"

    def test_validate_yaml_does_not_register(self):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config())
        reg.validate_yaml(self._GOOD)
        assert reg.get("vy") is None


# ── register_validated: stores, sets yaml_content, persists ──────────


class TestRegisterValidated:
    def test_stores_entry_and_sets_yaml_content(self, tmp_path: Path):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config(tmp_path))
        wf = _code_workflow(name="rv")
        yaml_text = "name: rv\nversion: '1.0.0'\n"

        loop = asyncio.new_event_loop()
        try:

            async def _run():
                entry = reg.register_validated(wf, yaml_content=yaml_text, persist=True)
                await asyncio.sleep(0.05)
                return entry

            entry = loop.run_until_complete(_run())
        finally:
            loop.close()

        assert entry.source == "api"
        assert reg.get("rv") is wf
        # Contract: yaml_content is rewritten onto the workflow.
        assert wf.yaml_content == yaml_text
        # persist=True writes to disk.
        target = Path(reg._config.directory) / "rv.yaml"
        assert target.exists()
        assert target.read_text() == yaml_text

    def test_persist_false_does_not_write(self, tmp_path: Path):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config(tmp_path))
        wf = _code_workflow(name="rv2")
        reg.register_validated(wf, yaml_content="name: rv2\n", persist=False)
        target = Path(reg._config.directory) / "rv2.yaml"
        assert not target.exists()
        assert reg.get("rv2") is wf

    def test_register_validated_skips_validation(self, tmp_path: Path):
        """register_validated must NOT run WorkflowValidator (caller pre-validated)."""
        reg = WorkflowRegistry(_make_tool_registry(), _make_config(tmp_path))
        # This workflow is invalid (no version, empty step) but register_validated
        # only enforces reserved-name/collision, never schema validation.
        wf = WorkflowDefinition(name="skipval", version="", steps=[StepDefinition(id="s1")])
        entry = reg.register_validated(wf, yaml_content="x", persist=False)
        assert entry.workflow is wf
        assert reg.get("skipval") is wf


# ── metrics wiring ───────────────────────────────────────────────────


class TestMetrics:
    def test_register_updates_metrics_count(self):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config())
        metrics = MagicMock()
        reg.set_metrics(metrics)
        reg.register(_code_workflow(name="m1"), validate=False)
        metrics.update_registered_workflows.assert_called_with(1)
        reg.register(_code_workflow(name="m2"), validate=False)
        metrics.update_registered_workflows.assert_called_with(2)

    def test_unregister_updates_metrics_count(self):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config())
        metrics = MagicMock()
        reg.register(_code_workflow(name="m1"), validate=False)
        reg.set_metrics(metrics)
        reg.unregister("m1")
        metrics.update_registered_workflows.assert_called_with(0)


# ── snapshot (frozen dict copy) ──────────────────────────────────────


class TestSnapshot:
    def test_snapshot_unknown_raises_not_found(self):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config())
        with pytest.raises(AELError) as ei:
            reg.snapshot("missing")
        assert ei.value.code == "WORKFLOW_NOT_FOUND"

    def test_snapshot_defaults_and_inputs_outputs(self):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config())
        wf = WorkflowDefinition(
            name="snap",
            version="3.0.0",
            description="d",
            inputs=[InputDefinition(name="a", type="string", required=True, default="z")],
            steps=[StepDefinition(id="s1", code="result = 1")],
            outputs=[OutputDefinition(name="o", value="{{ steps.s1.output }}")],
        )
        reg.register(wf, validate=False)
        snap = reg.snapshot("snap")
        assert snap["name"] == "snap"
        assert snap["version"] == "3.0.0"
        # No defaults block on the workflow → snapshot supplies fallbacks.
        assert snap["defaults"]["timeout"] == 30
        assert snap["defaults"]["on_error"] == "fail"
        assert snap["defaults"]["retry"] is None
        assert snap["packages"]["profile"] == "standard"
        assert snap["inputs"][0] == {
            "name": "a",
            "type": "string",
            "required": True,
            "default": "z",
            "description": None,
        }
        assert snap["outputs"][0]["value"] == "{{ steps.s1.output }}"
        assert snap["steps"][0]["id"] == "s1"
        assert snap["steps"][0]["code"] == "result = 1"


# ── file watcher toggles ─────────────────────────────────────────────


class TestWatcherToggles:
    def test_start_then_stop_watching_idempotent(self):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config())
        assert reg._watching is False
        reg.start_watching()
        assert reg._watching is True
        # Second start is a no-op (no raise).
        reg.start_watching()
        assert reg._watching is True
        reg.stop_watching()
        assert reg._watching is False
        # Stop when already stopped is a no-op.
        reg.stop_watching()
        assert reg._watching is False


# ── _on_file_change hot-reload ───────────────────────────────────────


class TestOnFileChange:
    def test_on_file_change_reloads_workflow(self, tmp_path: Path):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config(tmp_path))
        yaml_path = tmp_path / "reload.yaml"
        yaml_path.write_text(
            "name: reload\nversion: '1.0.0'\nsteps:\n  - id: s1\n    code: result = 1\n"
        )

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(reg._on_file_change(yaml_path))
        finally:
            loop.close()

        assert reg.get("reload") is not None
        # source_path provided → entry.source is "file".
        assert reg._workflows["reload"].source == "file"

    def test_on_file_change_logs_and_swallows_parse_error(self, tmp_path: Path):
        logger = MagicMock()
        reg = WorkflowRegistry(_make_tool_registry(), _make_config(tmp_path), logger=logger)
        bad = tmp_path / "bad.yaml"
        bad.write_text("::: not valid :::")

        loop = asyncio.new_event_loop()
        try:
            # Must not raise — error is logged.
            loop.run_until_complete(reg._on_file_change(bad))
        finally:
            loop.close()

        # An ERROR-level log was emitted for the failed reload.
        assert any("Failed to reload workflow" in str(c.args) for c in logger._log.call_args_list)


# ── persistence scheduling when no running loop (sync fallback) ──────


class TestPersistSyncFallback:
    def test_register_from_yaml_persist_without_loop_uses_asyncio_run(self, tmp_path: Path):
        """register_from_yaml(persist=True) outside an event loop falls back to
        asyncio.run so the disk write still happens synchronously."""
        reg = WorkflowRegistry(_make_tool_registry(), _make_config(tmp_path))
        yaml_text = "name: syncwf\nversion: '1.0.0'\nsteps:\n  - id: s1\n    code: result = 1\n"
        # No running loop here — the RuntimeError fallback path runs asyncio.run.
        reg.register_from_yaml(yaml_text, persist=True)
        target = Path(reg._config.directory) / "syncwf.yaml"
        assert target.exists()
        assert target.read_text() == yaml_text

    def test_register_validated_persist_without_loop_uses_asyncio_run(self, tmp_path: Path):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config(tmp_path))
        wf = _code_workflow(name="rvsync")
        reg.register_validated(wf, yaml_content="name: rvsync\n", persist=True)
        target = Path(reg._config.directory) / "rvsync.yaml"
        assert target.exists()


# ── persist dual-write to redis ──────────────────────────────────────


class TestPersistRedis:
    def test_persist_writes_to_redis_when_connected(self, tmp_path: Path):
        redis = MagicMock()
        redis.connected = True
        redis.set_value = AsyncMock(return_value=True)
        reg = WorkflowRegistry(_make_tool_registry(), _make_config(tmp_path), redis_store=redis)

        loop = asyncio.new_event_loop()
        try:

            async def _run():
                reg.register_validated(
                    _code_workflow(name="redwf"),
                    yaml_content="name: redwf\n",
                    persist=True,
                )
                await asyncio.sleep(0.05)

            loop.run_until_complete(_run())
        finally:
            loop.close()

        redis.set_value.assert_awaited_once_with("workflows:redwf", "name: redwf\n")

    def test_persist_skips_redis_when_disconnected(self, tmp_path: Path):
        redis = MagicMock()
        redis.connected = False
        redis.set_value = AsyncMock()
        reg = WorkflowRegistry(_make_tool_registry(), _make_config(tmp_path), redis_store=redis)

        loop = asyncio.new_event_loop()
        try:

            async def _run():
                reg.register_validated(
                    _code_workflow(name="redwf2"),
                    yaml_content="x",
                    persist=True,
                )
                await asyncio.sleep(0.05)

            loop.run_until_complete(_run())
        finally:
            loop.close()

        redis.set_value.assert_not_awaited()


# ── initialize: missing directory + per-file error resilience ────────


class TestInitialize:
    def test_initialize_missing_directory_returns_zero_and_warns(self, tmp_path: Path):
        logger = MagicMock()
        config = _make_config(tmp_path)  # directory does not exist
        reg = WorkflowRegistry(_make_tool_registry(), config, logger=logger)

        loop = asyncio.new_event_loop()
        try:
            count = loop.run_until_complete(reg.initialize())
        finally:
            loop.close()

        assert count == 0
        assert any("does not exist" in str(c.args) for c in logger._log.call_args_list)

    def test_initialize_skips_unparseable_file_and_logs(self, tmp_path: Path):
        logger = MagicMock()
        config = _make_config(tmp_path)
        wdir = Path(config.directory)
        wdir.mkdir(parents=True, exist_ok=True)
        (wdir / "good.yaml").write_text(
            "name: good\nversion: '1.0.0'\nsteps:\n  - id: s1\n    code: result = 1\n"
        )
        (wdir / "broken.yaml").write_text("::: not valid :::")

        reg = WorkflowRegistry(_make_tool_registry(), config, logger=logger)
        loop = asyncio.new_event_loop()
        try:
            count = loop.run_until_complete(reg.initialize())
        finally:
            loop.close()

        # Only the good one loads; the broken one is logged and skipped.
        assert count == 1
        assert reg.get("good") is not None
        assert any("Failed to load workflow" in str(c.args) for c in logger._log.call_args_list)

    def test_initialize_skips_unparseable_redis_value_and_logs(self, tmp_path: Path):
        logger = MagicMock()
        config = _make_config(tmp_path)
        Path(config.directory).mkdir(parents=True, exist_ok=True)

        redis = MagicMock()
        redis.connected = True
        redis.scan_keys = AsyncMock(return_value=["workflows:rgood", "workflows:rbad"])

        async def _get(key):
            if key == "workflows:rgood":
                return "name: rgood\nversion: '1.0.0'\nsteps:\n  - id: s1\n    code: result = 1\n"
            return "::: not valid :::"

        redis.get_value = AsyncMock(side_effect=_get)

        reg = WorkflowRegistry(_make_tool_registry(), config, logger=logger, redis_store=redis)
        loop = asyncio.new_event_loop()
        try:
            count = loop.run_until_complete(reg.initialize())
        finally:
            loop.close()

        assert count == 1
        assert reg.get("rgood") is not None
        assert any(
            "Failed to load workflow from Redis" in str(c.args) for c in logger._log.call_args_list
        )


# ── draft_store property + DraftStore TTL config guard ───────────────


class TestDraftStoreConfig:
    def test_draft_store_property_exposed(self):
        reg = WorkflowRegistry(_make_tool_registry(), _make_config())
        assert reg.draft_store is reg._draft_store

    def test_non_int_ttl_falls_back_to_default(self):
        config = MagicMock()
        config.directory = "/tmp/x"
        # MagicMock attribute for draft_ttl_seconds is not an int → default 1800.
        reg = WorkflowRegistry(_make_tool_registry(), config)
        assert reg.draft_store.ttl_seconds == 1800

    def test_nonpositive_ttl_falls_back_to_default(self):
        config = MagicMock()
        config.directory = "/tmp/x"
        config.draft_ttl_seconds = 0
        reg = WorkflowRegistry(_make_tool_registry(), config)
        assert reg.draft_store.ttl_seconds == 1800

    def test_custom_positive_ttl_respected(self):
        config = MagicMock()
        config.directory = "/tmp/x"
        config.draft_ttl_seconds = 60
        reg = WorkflowRegistry(_make_tool_registry(), config)
        assert reg.draft_store.ttl_seconds == 60

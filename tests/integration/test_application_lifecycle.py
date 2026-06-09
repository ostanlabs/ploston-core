"""Integration coverage for the application wiring/lifecycle (V-2 / S-343).

application.py is the OSS Control-Plane orchestrator: ``PlostApplication.initialize()``
loads config, sets up logging + telemetry, builds the error registry, the MCP
client manager, the tool/workflow registries, the sandbox + template + invoker
+ engine, and finally the MCP frontend with the REST app mounted. Unit tests
exercise the pieces in isolation; nothing drove the *whole* wiring sequence end
to end, leaving application.py largely uncovered.

These tests run the real initialize/shutdown lifecycle in-process with a
default (configuration-mode) config and a throwaway SQLite telemetry store —
no external services required, so they run everywhere (not gated on Docker).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from ploston_core.application import PlostApplication

pytestmark = [pytest.mark.integration]


@pytest_asyncio.fixture
async def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A fully-initialized PlostApplication backed by a temp SQLite store."""
    # Keep telemetry off the repo's ./data dir, and ensure no Redis/ClickHouse
    # backend is selected from a leaked env var (pure in-process run).
    monkeypatch.setenv("TELEMETRY_STORE_SQLITE_PATH", str(tmp_path / "telemetry.db"))
    monkeypatch.delenv("PLOSTON_TELEMETRY_BACKEND", raising=False)
    monkeypatch.delenv("PLOSTON_REDIS_URL", raising=False)
    monkeypatch.delenv("AEL_REDIS_URL", raising=False)
    monkeypatch.chdir(tmp_path)

    application = PlostApplication(config_path=None, with_rest_api=True)
    await application.initialize()
    try:
        yield application
    finally:
        await application.shutdown()


@pytest.mark.asyncio
async def test_initialize_wires_all_core_components(app: PlostApplication):
    """initialize() leaves every core component wired and the app marked ready."""
    assert app._initialized is True
    # The full dependency graph the orchestrator promises to build.
    for attr in (
        "config",
        "config_loader",
        "logger",
        "error_registry",
        "error_factory",
        "mcp_manager",
        "tool_registry",
        "workflow_registry",
        "sandbox_factory",
        "template_engine",
        "tool_invoker",
        "workflow_engine",
        "mcp_frontend",
        "telemetry_store",
    ):
        assert getattr(app, attr) is not None, f"{attr} should be wired by initialize()"


@pytest.mark.asyncio
async def test_initialize_is_idempotent(app: PlostApplication):
    """A second initialize() is a no-op and does not rebuild components."""
    engine_before = app.workflow_engine
    frontend_before = app.mcp_frontend
    await app.initialize()
    assert app.workflow_engine is engine_before
    assert app.mcp_frontend is frontend_before
    assert app._initialized is True


@pytest.mark.asyncio
async def test_telemetry_store_is_live_and_queryable(app: PlostApplication):
    """The default OSS SQLite store is live and answers through its async API."""
    records, total = await app.telemetry_store.list_executions(page=1, page_size=1)
    assert isinstance(records, list)
    assert total == 0  # fresh temp store


@pytest.mark.asyncio
async def test_rest_app_mounted_and_healthy(app: PlostApplication):
    """The REST app the orchestrator mounts answers /health against real wiring."""
    from starlette.testclient import TestClient

    rest_app = app.mcp_frontend._rest_app
    assert rest_app is not None, "REST app should be mounted on the frontend"
    with TestClient(rest_app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_shutdown_marks_uninitialized_and_is_idempotent(app: PlostApplication):
    """shutdown() tears down cleanly and is safe to call twice."""
    await app.shutdown()
    assert app._initialized is False
    # Second shutdown is a no-op (no raise).
    await app.shutdown()
    assert app._initialized is False


@pytest.mark.asyncio
async def test_run_workflow_unknown_id_is_structured_error(app: PlostApplication):
    """run_workflow on an unknown id surfaces a structured failure, not a crash."""
    from ploston_core.errors import AELError

    with pytest.raises((AELError, KeyError, ValueError)):
        await app.run_workflow("definitely-not-a-workflow", inputs={})

"""Tests for runner_static H-7 reconnect race + CA-from-EmbeddedCA wiring.

H-7: the connection registry is keyed by runner_id. On reconnect the new socket
replaces the old entry; when the OLD socket's finally-cleanup runs it must NOT
pop/disconnect the NEW connection. Cleanup is session-scoped.

CR-2: /runner/ca.crt must serve the live EmbeddedCA PEM when one is attached to
app.state.embedded_ca.
"""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ploston_core.api.routers import runner_static
from ploston_core.api.routers.runner_static import (
    RunnerConnection,
    runner_static_router,
)


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(runner_static_router)
    return app


class TestCACertFromEmbeddedCA:
    """CR-2: ca.crt returns the real CA PEM from EmbeddedCA."""

    def test_ca_crt_serves_embedded_ca(self, app: FastAPI, tmp_path) -> None:
        from ploston_core.runner_management.embedded_ca import EmbeddedCA

        ca = EmbeddedCA(ca_dir=tmp_path / "ca")
        ca.initialize()
        app.state.embedded_ca = ca

        client = TestClient(app)
        resp = client.get("/runner/ca.crt")

        assert resp.status_code == 200
        assert "BEGIN CERTIFICATE" in resp.text
        assert resp.text.encode() == ca.get_ca_cert_pem()


class TestReconnectRace:
    """H-7: old-socket cleanup must not disconnect the live new socket."""

    def teardown_method(self) -> None:
        runner_static._runner_connections.clear()

    def test_session_id_distinguishes_connections(self) -> None:
        old = RunnerConnection(runner_id="r-1", runner_name="local", websocket=MagicMock())
        new = RunnerConnection(runner_id="r-1", runner_name="local", websocket=MagicMock())
        # Each connection gets a unique session id.
        assert old.session_id != new.session_id

    @pytest.mark.asyncio
    async def test_old_cleanup_does_not_disconnect_new(self) -> None:
        """Simulate reconnect: new conn replaces old; old's cleanup is a no-op."""
        registry = MagicMock()

        old = RunnerConnection(runner_id="r-1", runner_name="local", websocket=MagicMock())
        runner_static._runner_connections["r-1"] = old

        # New connection registers (reconnect), replacing the old entry.
        new = RunnerConnection(runner_id="r-1", runner_name="local", websocket=MagicMock())
        runner_static._runner_connections["r-1"] = new

        # Old socket's finally-cleanup runs AFTER the new one took over.
        runner_static._cleanup_runner_connection(registry, "r-1", old)

        # The live (new) connection must still be registered, and the registry
        # must NOT have been marked disconnected.
        assert runner_static._runner_connections.get("r-1") is new
        registry.set_disconnected.assert_not_called()

    @pytest.mark.asyncio
    async def test_matching_cleanup_does_disconnect(self) -> None:
        """When the stored conn IS this socket, cleanup disconnects normally."""
        registry = MagicMock()

        conn = RunnerConnection(runner_id="r-1", runner_name="local", websocket=MagicMock())
        runner_static._runner_connections["r-1"] = conn

        runner_static._cleanup_runner_connection(registry, "r-1", conn)

        assert "r-1" not in runner_static._runner_connections
        registry.set_disconnected.assert_called_once_with("r-1")

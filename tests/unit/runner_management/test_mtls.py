"""Unit tests for CR-2 runner mTLS wiring.

Covers:
- EmbeddedCA can build a server-side mTLS SSLContext (CERT_REQUIRED + CA loaded).
- EmbeddedCA can build a client-side SSLContext that trusts the CA and presents
  a runner client cert.
- RunnerWebSocketServer passes the ssl context through to ws_serve(...).
"""

import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ploston_core.runner_management.embedded_ca import EmbeddedCA
from ploston_core.runner_management.registry import RunnerRegistry
from ploston_core.runner_management.websocket_server import RunnerWebSocketServer


@pytest.fixture
def ca(tmp_path) -> EmbeddedCA:
    ca = EmbeddedCA(ca_dir=tmp_path / "ca")
    ca.initialize()
    return ca


class TestServerSSLContext:
    """The server context must require + verify runner client certs."""

    def test_build_server_ssl_context_requires_client_cert(self, ca: EmbeddedCA) -> None:
        ctx = ca.build_server_ssl_context()
        assert isinstance(ctx, ssl.SSLContext)
        # mTLS: client (runner) cert is mandatory and verified against the CA.
        assert ctx.verify_mode == ssl.CERT_REQUIRED

    def test_build_server_ssl_context_loads_ca_for_verification(self, ca: EmbeddedCA) -> None:
        ctx = ca.build_server_ssl_context()
        # The CA cert must be present in the context's trust store so that
        # runner client certs signed by it validate. Compare on the integer
        # serial to avoid hex zero-padding differences.
        loaded = {int(c["serialNumber"], 16) for c in ctx.get_ca_certs()}
        assert ca._ca_cert.serial_number in loaded


class TestClientSSLContext:
    """The runner client context trusts the CA and presents its client cert."""

    def test_build_client_ssl_context_verifies_server(self, ca: EmbeddedCA) -> None:
        key_pem, cert_pem = ca.generate_runner_cert("test-runner", "runner_123")
        ctx = ca.build_client_ssl_context(
            ca_cert_pem=ca.get_ca_cert_pem(),
            client_cert_pem=cert_pem,
            client_key_pem=key_pem,
        )
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode == ssl.CERT_REQUIRED


class TestServerPassesSSL:
    """RunnerWebSocketServer.start must forward ssl= to ws_serve."""

    @pytest.mark.asyncio
    async def test_ws_serve_receives_ssl(self, ca: EmbeddedCA) -> None:
        registry = RunnerRegistry()
        ctx = ca.build_server_ssl_context()
        server = RunnerWebSocketServer(registry, host="0.0.0.0", port=0, ssl=ctx)

        with patch(
            "ploston_core.runner_management.websocket_server.ws_serve",
            new_callable=AsyncMock,
        ) as mock_serve:
            mock_serve.return_value = MagicMock()
            await server.start()

        assert mock_serve.called
        _, kwargs = mock_serve.call_args
        assert kwargs.get("ssl") is ctx

    @pytest.mark.asyncio
    async def test_real_mtls_handshake_round_trip(self, ca: EmbeddedCA) -> None:
        """A wss server with the CA-built context accepts a CA-signed client."""
        import websockets
        from websockets.asyncio.server import serve as ws_serve

        server_ctx = ca.build_server_ssl_context(hostname="localhost", alt_names=["127.0.0.1"])
        key_pem, cert_pem = ca.generate_runner_cert("rt", "runner_rt")
        client_ctx = ca.build_client_ssl_context(
            ca_cert_pem=ca.get_ca_cert_pem(),
            client_cert_pem=cert_pem,
            client_key_pem=key_pem,
        )

        async def echo(ws):
            async for msg in ws:
                await ws.send(msg)

        async with ws_serve(echo, "127.0.0.1", 0, ssl=server_ctx) as server:
            port = server.sockets[0].getsockname()[1]
            async with websockets.connect(f"wss://127.0.0.1:{port}", ssl=client_ctx) as ws:
                await ws.send("ping")
                assert await ws.recv() == "ping"

    @pytest.mark.asyncio
    async def test_ws_serve_no_ssl_when_not_configured(self) -> None:
        registry = RunnerRegistry()
        server = RunnerWebSocketServer(registry, host="127.0.0.1", port=0)

        with patch(
            "ploston_core.runner_management.websocket_server.ws_serve",
            new_callable=AsyncMock,
        ) as mock_serve:
            mock_serve.return_value = MagicMock()
            await server.start()

        _, kwargs = mock_serve.call_args
        assert kwargs.get("ssl") is None

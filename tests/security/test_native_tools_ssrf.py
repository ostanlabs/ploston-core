"""Security tests for SSRF protection in native-tools http_request (PL-C4/C5).

These cover:
- url to 127.0.0.1 / 169.254.169.254 / 10.x BLOCKED
- file:// (and other non-http schemes) BLOCKED
- the SSRF guard rejects BEFORE any HTTP send is attempted
- an allowed public host works (HTTP send monkeypatched)
- denied_hosts honored
- allowed_hosts allowlist honored
"""

from __future__ import annotations

import pytest

from ploston_core.native_tools import network
from ploston_core.native_tools.network import make_http_request


class _SendTracker:
    """Tracks whether an actual HTTP send was attempted."""

    def __init__(self):
        self.sent = False


@pytest.fixture
def no_real_send(monkeypatch):
    """Replace httpx.AsyncClient.request so no real network call happens.

    Also asserts (via the returned tracker) whether a send was attempted so
    tests can prove the SSRF guard short-circuits before any send.
    """
    import httpx

    tracker = _SendTracker()

    async def fake_request(self, *args, **kwargs):  # noqa: ANN001
        tracker.sent = True

        class _Resp:
            status_code = 200
            headers = {"content-type": "application/json"}
            content = b'{"ok": true}'
            text = '{"ok": true}'

            def json(self):
                return {"ok": True}

        return _Resp()

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    return tracker


# ---------------------------------------------------------------------------
# PL-C4: blocked private / loopback / link-local destinations
# ---------------------------------------------------------------------------


async def test_loopback_blocked(no_real_send):
    res = await make_http_request(url="http://127.0.0.1/admin")
    assert res["success"] is False
    assert no_real_send.sent is False, "SSRF guard must reject before any send"


async def test_localhost_name_blocked(no_real_send):
    res = await make_http_request(url="http://localhost:8080/")
    assert res["success"] is False
    assert no_real_send.sent is False


async def test_cloud_metadata_blocked(no_real_send):
    res = await make_http_request(url="http://169.254.169.254/latest/meta-data/")
    assert res["success"] is False
    assert no_real_send.sent is False


async def test_private_10_block(no_real_send):
    res = await make_http_request(url="http://10.0.0.5/")
    assert res["success"] is False
    assert no_real_send.sent is False


async def test_private_192_168_block(no_real_send):
    res = await make_http_request(url="http://192.168.1.1/")
    assert res["success"] is False
    assert no_real_send.sent is False


# ---------------------------------------------------------------------------
# PL-C4: scheme restriction
# ---------------------------------------------------------------------------


async def test_file_scheme_blocked(no_real_send):
    res = await make_http_request(url="file:///etc/passwd")
    assert res["success"] is False
    assert no_real_send.sent is False


async def test_gopher_scheme_blocked(no_real_send):
    res = await make_http_request(url="gopher://127.0.0.1/")
    assert res["success"] is False
    assert no_real_send.sent is False


# ---------------------------------------------------------------------------
# PL-C5: denied_hosts / allowed_hosts
# ---------------------------------------------------------------------------


async def test_denied_hosts_honored(no_real_send, monkeypatch):
    # Make the host resolve to a public IP so only the denylist blocks it.
    monkeypatch.setattr(network, "_resolve_host_ips", lambda host: ["93.184.216.34"], raising=False)
    res = await make_http_request(
        url="http://blocked.example.com/", denied_hosts=["blocked.example.com"]
    )
    assert res["success"] is False
    assert no_real_send.sent is False


async def test_allowed_hosts_allows_listed_host(no_real_send, monkeypatch):
    monkeypatch.setattr(network, "_resolve_host_ips", lambda host: ["93.184.216.34"], raising=False)
    res = await make_http_request(url="http://example.com/", allowed_hosts=["example.com"])
    assert res["success"] is True
    assert no_real_send.sent is True


async def test_allowed_hosts_blocks_unlisted_host(no_real_send, monkeypatch):
    monkeypatch.setattr(network, "_resolve_host_ips", lambda host: ["93.184.216.34"], raising=False)
    res = await make_http_request(url="http://other.example.com/", allowed_hosts=["example.com"])
    assert res["success"] is False
    assert no_real_send.sent is False


# ---------------------------------------------------------------------------
# PL-C4: allowed public host works (guard passes, send happens)
# ---------------------------------------------------------------------------


async def test_public_host_allowed(no_real_send, monkeypatch):
    monkeypatch.setattr(network, "_resolve_host_ips", lambda host: ["93.184.216.34"], raising=False)
    res = await make_http_request(url="http://example.com/")
    assert res["success"] is True
    assert no_real_send.sent is True

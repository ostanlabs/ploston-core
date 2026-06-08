"""Security hardening tests for OSS REST middleware.

Covers:
- CR-3: CORS wildcard origins must not be combined with credentials.
- H-6: excluded/public paths must use EXACT matching (no startswith bypass).
- H-4: X-Forwarded-For only honored for trusted proxies; empty buckets evicted.
- H-8: rate-limit bucket key uses a hash of the FULL api key (no [:8] collision).
"""

import hashlib

from fastapi import FastAPI, Request
from starlette.testclient import TestClient

from ploston_core.api.app import create_rest_app
from ploston_core.api.config import APIKeyConfig, RESTConfig
from ploston_core.api.middleware import (
    APIKeyAuthMiddleware,
    RateLimitMiddleware,
)


def _stub_app_deps() -> dict:
    """Minimal stub dependencies for create_rest_app.

    The middleware-level behavior we test does not need real engines/registries;
    the routers we hit (/health) don't touch them, and middleware runs before
    routing for auth/rate-limit cases. Use plain objects as placeholders.
    """
    stub = object()
    return {
        "workflow_registry": stub,
        "workflow_engine": stub,
        "tool_registry": stub,
        "tool_invoker": stub,
    }


def _simple_app() -> FastAPI:
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint(request: Request) -> dict:
        return {"ok": True}

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    return app


# --------------------------------------------------------------------------- #
# CR-3 — CORS wildcard must not be combined with credentials
# --------------------------------------------------------------------------- #
class TestCORSWildcardCredentials:
    def test_wildcard_origins_disables_credentials(self) -> None:
        config = RESTConfig(
            cors_enabled=True,
            cors_origins=["*"],
            require_auth=False,
            rate_limiting_enabled=False,
        )
        app = create_rest_app(config=config, **_stub_app_deps())
        client = TestClient(app)

        resp = client.get("/api/v1/health", headers={"Origin": "https://evil.example"})
        assert resp.status_code == 200
        # Wildcard + credentials is forbidden -> header must NOT be true.
        assert resp.headers.get("access-control-allow-credentials") != "true"

    def test_explicit_origins_allow_credentials(self) -> None:
        config = RESTConfig(
            cors_enabled=True,
            cors_origins=["https://app.example"],
            require_auth=False,
            rate_limiting_enabled=False,
        )
        app = create_rest_app(config=config, **_stub_app_deps())
        client = TestClient(app)

        resp = client.get("/api/v1/health", headers={"Origin": "https://app.example"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-credentials") == "true"


# --------------------------------------------------------------------------- #
# H-6 — exact path matching for excluded/public paths
# --------------------------------------------------------------------------- #
class TestAuthExactPathMatching:
    def _app(self) -> TestClient:
        app = FastAPI()

        @app.get("/health")
        async def health() -> dict:
            return {"status": "ok"}

        @app.get("/healthx")
        async def healthx() -> dict:
            return {"status": "ok"}

        @app.get("/docsanything")
        async def docsanything() -> dict:
            return {"status": "ok"}

        api_keys = [APIKeyConfig(name="t", key="valid-key")]
        app.add_middleware(APIKeyAuthMiddleware, api_keys=api_keys)
        return TestClient(app)

    def test_exact_excluded_path_passes(self) -> None:
        client = self._app()
        assert client.get("/health").status_code == 200

    def test_prefix_bypass_rejected(self) -> None:
        client = self._app()
        assert client.get("/healthx").status_code == 401
        assert client.get("/docsanything").status_code == 401


class TestRateLimitExactPathMatching:
    def _app(self) -> TestClient:
        app = FastAPI()

        @app.get("/health")
        async def health() -> dict:
            return {"status": "ok"}

        @app.get("/healthx")
        async def healthx() -> dict:
            return {"status": "ok"}

        app.add_middleware(RateLimitMiddleware, requests_per_minute=1)
        return TestClient(app)

    def test_exact_excluded_path_not_limited(self) -> None:
        client = self._app()
        # health is excluded -> never limited even over the limit
        assert client.get("/health").status_code == 200
        assert client.get("/health").status_code == 200
        assert client.get("/health").status_code == 200

    def test_prefix_path_is_limited(self) -> None:
        client = self._app()
        # /healthx is NOT excluded -> second request over limit (rpm=1) -> 429
        assert client.get("/healthx").status_code == 200
        assert client.get("/healthx").status_code == 429


# --------------------------------------------------------------------------- #
# H-4 — X-Forwarded-For trust + empty bucket eviction
# --------------------------------------------------------------------------- #
class TestForwardedForTrust:
    def test_spoofed_xff_does_not_bypass_when_no_trusted_proxy(self) -> None:
        """Without trusted_proxies, X-Forwarded-For must be ignored.

        Two requests with different spoofed XFF should hit the SAME bucket
        (keyed by the real client host), so the second is rate limited.
        """
        app = FastAPI()

        @app.get("/test")
        async def test_endpoint() -> dict:
            return {"ok": True}

        app.add_middleware(RateLimitMiddleware, requests_per_minute=1)
        client = TestClient(app)

        r1 = client.get("/test", headers={"X-Forwarded-For": "1.1.1.1"})
        r2 = client.get("/test", headers={"X-Forwarded-For": "2.2.2.2"})
        assert r1.status_code == 200
        assert r2.status_code == 429

    def test_trusted_proxy_honors_xff(self) -> None:
        """With the direct client as a trusted proxy, XFF is honored.

        Two distinct spoofed XFF values -> two distinct buckets -> both pass.
        """
        app = FastAPI()

        @app.get("/test")
        async def test_endpoint() -> dict:
            return {"ok": True}

        # TestClient direct client host is "testclient".
        app.add_middleware(
            RateLimitMiddleware,
            requests_per_minute=1,
            trusted_proxies=["testclient"],
        )
        client = TestClient(app)

        r1 = client.get("/test", headers={"X-Forwarded-For": "1.1.1.1"})
        r2 = client.get("/test", headers={"X-Forwarded-For": "2.2.2.2"})
        assert r1.status_code == 200
        assert r2.status_code == 200


class TestEmptyBucketEviction:
    def test_empty_buckets_are_evicted(self) -> None:
        mw = RateLimitMiddleware(app=object(), requests_per_minute=100)

        # Create a bucket then make its window empty.
        limited, _ = mw._is_rate_limited("ip:1.2.3.4")
        assert limited is False
        assert "ip:1.2.3.4" in mw.clients
        size_before = len(mw.clients)

        # Force the only recorded request to be outside the window.
        mw.clients["ip:1.2.3.4"].requests = [0.0]  # epoch, far outside 60s window

        # Re-checking a DIFFERENT client triggers eviction sweep of stale buckets.
        mw._is_rate_limited("ip:9.9.9.9")

        assert "ip:1.2.3.4" not in mw.clients
        assert len(mw.clients) < size_before + 2  # stale one gone, new one (maybe) added


# --------------------------------------------------------------------------- #
# H-8 — full-key hashing (no 8-char prefix collision)
# --------------------------------------------------------------------------- #
class TestRateLimitKeyHashing:
    def test_keys_sharing_prefix_get_separate_buckets(self) -> None:
        app = FastAPI()

        @app.get("/test")
        async def test_endpoint() -> dict:
            return {"ok": True}

        app.add_middleware(RateLimitMiddleware, requests_per_minute=1)
        client = TestClient(app)

        key_a = "PREFIX12_aaaaaaaa"
        key_b = "PREFIX12_bbbbbbbb"  # same first 8 chars: "PREFIX12"
        assert key_a[:8] == key_b[:8]

        r1 = client.get("/test", headers={"X-API-Key": key_a})
        r2 = client.get("/test", headers={"X-API-Key": key_b})
        # Distinct full keys -> distinct buckets -> both allowed.
        assert r1.status_code == 200
        assert r2.status_code == 200

    def test_bucket_key_uses_full_key_hash(self) -> None:
        mw = RateLimitMiddleware(app=object(), requests_per_minute=10)

        class _FakeURL:
            path = "/test"

        class _FakeClient:
            host = "127.0.0.1"

        class _FakeRequest:
            url = _FakeURL()
            client = _FakeClient()
            headers = {"X-API-Key": "supersecretfullkey"}

        cid = mw._get_client_id(_FakeRequest())
        expected = hashlib.sha256(b"supersecretfullkey").hexdigest()
        assert expected in cid
        # The raw key prefix must NOT be the identifier.
        assert "supersecr" not in cid

"""Tests for the OSS scope-not-enforced honesty warning.

Per the open-core model, per-API-key `scopes` are accepted but NOT enforced in
OSS (enforcement is a Pro feature). Configuring a key with anything other than
full access must emit a one-time warning making clear scopes are not enforced
here. Enforcement itself is intentionally NOT implemented in OSS.
"""

import logging

from fastapi import FastAPI, Request
from starlette.testclient import TestClient

from ploston_core.api.config import APIKeyConfig
from ploston_core.api.middleware import APIKeyAuthMiddleware


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint(request: Request) -> dict:
        return {"ok": True}

    return app


def _reset_warning_guard() -> None:
    """Reset the process-wide one-time warning guard for isolated assertions."""
    APIKeyAuthMiddleware._scope_warning_emitted = False


def test_restricted_scopes_logs_not_enforced_warning(caplog) -> None:
    """Constructing the middleware with a restricted-scope key warns (Pro-only)."""
    _reset_warning_guard()
    api_keys = [APIKeyConfig(name="limited", key="k1", scopes=["read"])]

    with caplog.at_level(logging.WARNING):
        # Construct directly so the warning is captured deterministically
        # (Starlette instantiates add_middleware factories lazily on app build).
        APIKeyAuthMiddleware(app=_app(), api_keys=api_keys)

    messages = " ".join(r.getMessage() for r in caplog.records).lower()
    assert "not enforced" in messages
    assert "pro" in messages
    assert "limited" in messages  # names the offending key


def test_full_access_scopes_no_warning(caplog) -> None:
    """A key with default full-access scopes must not warn."""
    # Default scopes == full access.
    api_keys = [APIKeyConfig(name="full", key="k2")]

    with caplog.at_level(logging.WARNING):
        APIKeyAuthMiddleware(app=_app(), api_keys=api_keys)

    messages = " ".join(r.getMessage() for r in caplog.records).lower()
    assert "not enforced" not in messages


def test_restricted_scopes_warning_not_emitted_per_request(caplog) -> None:
    """The warning is emitted at construction, not inside dispatch (per request)."""
    _reset_warning_guard()
    api_keys = [APIKeyConfig(name="limited", key="k1", scopes=["read"])]
    # Construct once; this consumes the one-time guard.
    APIKeyAuthMiddleware(app=_app(), api_keys=api_keys)

    app = _app()
    app.add_middleware(APIKeyAuthMiddleware, api_keys=api_keys)
    client = TestClient(app)

    with caplog.at_level(logging.WARNING):
        caplog.clear()  # drop the construction-time warning captured above
        # Many requests (each may rebuild the stack) must add no warnings.
        for _ in range(3):
            client.get("/test", headers={"X-API-Key": "k1"})

    fired = [r for r in caplog.records if "not enforced" in r.getMessage().lower()]
    assert fired == []  # one-time guard already consumed; dispatch never warns


def test_restricted_scopes_still_allows_request() -> None:
    """Scopes are not enforced: a 'read' key may still call any endpoint."""
    app = _app()
    api_keys = [APIKeyConfig(name="limited", key="k1", scopes=["read"])]
    app.add_middleware(APIKeyAuthMiddleware, api_keys=api_keys)
    client = TestClient(app)

    response = client.get("/test", headers={"X-API-Key": "k1"})
    assert response.status_code == 200

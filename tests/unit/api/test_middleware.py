"""Tests for REST API middleware."""

import pytest
from starlette.testclient import TestClient
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

from ploston_core.api.config import APIKeyConfig, RESTConfig
from ploston_core.api.middleware import (
    APIKeyAuthMiddleware,
    RateLimitMiddleware,
    RequestIDMiddleware,
)


def create_test_app() -> FastAPI:
    """Create a minimal test app."""
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint(request: Request) -> dict:
        return {"request_id": getattr(request.state, "request_id", None)}

    return app


class TestRequestIDMiddleware:
    """Tests for RequestIDMiddleware."""

    def test_adds_request_id(self) -> None:
        """Test that middleware adds request ID to request state."""
        app = create_test_app()
        app.add_middleware(RequestIDMiddleware)
        client = TestClient(app)

        response = client.get("/test")
        assert response.status_code == 200
        data = response.json()
        assert data["request_id"] is not None
        assert len(data["request_id"]) > 0

    def test_adds_request_id_header(self) -> None:
        """Test that middleware adds X-Request-ID header to response."""
        app = create_test_app()
        app.add_middleware(RequestIDMiddleware)
        client = TestClient(app)

        response = client.get("/test")
        assert "X-Request-ID" in response.headers
        assert len(response.headers["X-Request-ID"]) > 0

    def test_uses_provided_request_id(self) -> None:
        """Test that middleware uses provided X-Request-ID header."""
        app = create_test_app()
        app.add_middleware(RequestIDMiddleware)
        client = TestClient(app)

        custom_id = "custom-request-id-123"
        response = client.get("/test", headers={"X-Request-ID": custom_id})
        assert response.headers["X-Request-ID"] == custom_id
        assert response.json()["request_id"] == custom_id


class TestAPIKeyAuthMiddleware:
    """Tests for APIKeyAuthMiddleware."""

    def test_allows_request_with_valid_key(self) -> None:
        """Test that valid API key is accepted."""
        app = create_test_app()
        api_keys = [APIKeyConfig(name="test", key="valid-key")]
        app.add_middleware(APIKeyAuthMiddleware, api_keys=api_keys)
        client = TestClient(app)

        response = client.get("/test", headers={"X-API-Key": "valid-key"})
        assert response.status_code == 200

    def test_rejects_request_without_key(self) -> None:
        """Test that missing API key is rejected."""
        app = create_test_app()
        api_keys = [APIKeyConfig(name="test", key="valid-key")]
        app.add_middleware(APIKeyAuthMiddleware, api_keys=api_keys)
        client = TestClient(app)

        response = client.get("/test")
        assert response.status_code == 401

    def test_rejects_request_with_invalid_key(self) -> None:
        """Test that invalid API key is rejected."""
        app = create_test_app()
        api_keys = [APIKeyConfig(name="test", key="valid-key")]
        app.add_middleware(APIKeyAuthMiddleware, api_keys=api_keys)
        client = TestClient(app)

        response = client.get("/test", headers={"X-API-Key": "invalid-key"})
        assert response.status_code == 401

    def test_allows_health_endpoint_without_auth(self) -> None:
        """Test that health endpoint is accessible without auth."""
        app = FastAPI()

        @app.get("/health")
        async def health() -> dict:
            return {"status": "ok"}

        api_keys = [APIKeyConfig(name="test", key="valid-key")]
        app.add_middleware(APIKeyAuthMiddleware, api_keys=api_keys)
        client = TestClient(app)

        response = client.get("/health")
        assert response.status_code == 200

    def test_allows_docs_endpoint_without_auth(self) -> None:
        """Test that docs endpoint is accessible without auth."""
        app = FastAPI()

        @app.get("/docs")
        async def docs() -> dict:
            return {"docs": "here"}

        api_keys = [APIKeyConfig(name="test", key="valid-key")]
        app.add_middleware(APIKeyAuthMiddleware, api_keys=api_keys)
        client = TestClient(app)

        response = client.get("/docs")
        assert response.status_code == 200

    def test_custom_exclude_paths(self) -> None:
        """Test custom exclude paths."""
        app = FastAPI()

        @app.get("/custom-public")
        async def custom_public() -> dict:
            return {"public": True}

        api_keys = [APIKeyConfig(name="test", key="valid-key")]
        app.add_middleware(
            APIKeyAuthMiddleware,
            api_keys=api_keys,
            exclude_paths=["/custom-public"],
        )
        client = TestClient(app)

        response = client.get("/custom-public")
        assert response.status_code == 200


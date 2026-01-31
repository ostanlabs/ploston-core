"""Tests for Runner REST API router.

Implements S-184: Runner REST API
- UT-094: POST /runners - Create runner
- UT-095: GET /runners - List runners
- UT-096: GET /runners/{name} - Get runner
- UT-097: DELETE /runners/{name} - Delete runner
- UT-098: Error handling tests
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ploston_core.api.routers.runners import runner_router
from ploston_core.runner_management import RunnerRegistry


@pytest.fixture
def app() -> FastAPI:
    """Create test FastAPI app with runner router."""
    app = FastAPI()
    app.state.runner_registry = RunnerRegistry()
    app.include_router(runner_router, prefix="/api/v1")
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Create test client."""
    return TestClient(app)


class TestCreateRunner:
    """Tests for POST /runners (UT-094)."""

    def test_create_runner_success(self, client: TestClient) -> None:
        """Test successful runner creation."""
        response = client.post(
            "/api/v1/runners",
            json={"name": "marc-laptop"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "marc-laptop"
        assert data["id"].startswith("runner_")
        assert data["token"].startswith("ploston_runner_")
        assert "install_command" in data

    def test_create_runner_with_mcps(self, client: TestClient) -> None:
        """Test runner creation with MCP configs."""
        response = client.post(
            "/api/v1/runners",
            json={
                "name": "test-runner",
                "mcps": {"native-tools": {"url": "http://localhost:8081"}},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "test-runner"

    def test_create_runner_duplicate_name(self, client: TestClient) -> None:
        """Test creating runner with duplicate name."""
        # Create first runner
        client.post("/api/v1/runners", json={"name": "duplicate"})
        # Try to create second with same name
        response = client.post("/api/v1/runners", json={"name": "duplicate"})
        assert response.status_code == 409
        assert "already exists" in response.json()["detail"]

    def test_create_runner_empty_name(self, client: TestClient) -> None:
        """Test creating runner with empty name."""
        response = client.post("/api/v1/runners", json={"name": ""})
        assert response.status_code == 422  # Validation error


class TestListRunners:
    """Tests for GET /runners (UT-095)."""

    def test_list_empty(self, client: TestClient) -> None:
        """Test listing when no runners exist."""
        response = client.get("/api/v1/runners")
        assert response.status_code == 200
        data = response.json()
        assert data["runners"] == []
        assert data["total"] == 0

    def test_list_with_runners(self, client: TestClient) -> None:
        """Test listing multiple runners."""
        # Create runners
        client.post("/api/v1/runners", json={"name": "runner-1"})
        client.post("/api/v1/runners", json={"name": "runner-2"})

        response = client.get("/api/v1/runners")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        names = [r["name"] for r in data["runners"]]
        assert "runner-1" in names
        assert "runner-2" in names

    def test_list_filter_by_status(self, client: TestClient) -> None:
        """Test filtering runners by status."""
        # Create a runner (will be disconnected by default)
        client.post("/api/v1/runners", json={"name": "test-runner"})

        # Filter by disconnected
        response = client.get("/api/v1/runners?status=disconnected")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1

        # Filter by connected (should be empty)
        response = client.get("/api/v1/runners?status=connected")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0


class TestGetRunner:
    """Tests for GET /runners/{name} (UT-096)."""

    def test_get_runner_success(self, client: TestClient) -> None:
        """Test getting runner details."""
        # Create runner
        client.post("/api/v1/runners", json={"name": "marc-laptop"})

        response = client.get("/api/v1/runners/marc-laptop")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "marc-laptop"
        assert data["status"] == "disconnected"
        assert "created_at" in data
        assert "available_tools" in data

    def test_get_runner_not_found(self, client: TestClient) -> None:
        """Test getting non-existent runner."""
        response = client.get("/api/v1/runners/nonexistent")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]


class TestDeleteRunner:
    """Tests for DELETE /runners/{name} (UT-097)."""

    def test_delete_runner_success(self, client: TestClient) -> None:
        """Test successful runner deletion."""
        # Create runner
        client.post("/api/v1/runners", json={"name": "to-delete"})

        response = client.delete("/api/v1/runners/to-delete")
        assert response.status_code == 200
        data = response.json()
        assert data["deleted"] is True
        assert data["name"] == "to-delete"

        # Verify it's gone
        response = client.get("/api/v1/runners/to-delete")
        assert response.status_code == 404

    def test_delete_runner_not_found(self, client: TestClient) -> None:
        """Test deleting non-existent runner."""
        response = client.delete("/api/v1/runners/nonexistent")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]


class TestErrorHandling:
    """Tests for error handling (UT-098)."""

    def test_registry_not_available(self) -> None:
        """Test error when registry is not configured."""
        app = FastAPI()
        # Don't set runner_registry
        app.include_router(runner_router, prefix="/api/v1")
        client = TestClient(app)

        response = client.get("/api/v1/runners")
        assert response.status_code == 503
        assert "not available" in response.json()["detail"]

"""Spec tests for the workflows REST router.

These tests assert the *intended* HTTP contract of
``ploston_core.api.routers.workflows`` — status codes, response shapes, and
error envelopes — for representative, edge, and error cases. Collaborators
(workflow registry / engine) are mocked; the router itself is exercised
through a Starlette/FastAPI ``TestClient``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ploston_core.api.routers.workflows import workflow_router
from ploston_core.engine.types import ExecutionResult, StepResult
from ploston_core.errors import AELError, ErrorCategory
from ploston_core.types import ExecutionStatus as CoreExecutionStatus
from ploston_core.types import StepStatus
from ploston_core.workflow import WorkflowDefinition

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_workflow(
    name: str = "demo",
    version: str = "1.0.0",
    description: str | None = "A demo workflow",
    tags: list[str] | None = None,
    yaml_content: str | None = None,
) -> WorkflowDefinition:
    """Build a minimal WorkflowDefinition the router can serialize."""
    return WorkflowDefinition(
        name=name,
        version=version,
        description=description,
        tags=tags if tags is not None else [],
        inputs=[],
        steps=[],
        outputs=[],
        yaml_content=yaml_content,
    )


@pytest.fixture
def app() -> FastAPI:
    application = FastAPI()
    application.include_router(workflow_router, prefix="/api/v1")
    return application


@pytest.fixture
def registry() -> MagicMock:
    reg = MagicMock()
    reg.list_workflows = MagicMock(return_value=[])
    reg.get = MagicMock(return_value=None)
    reg.register_from_yaml = MagicMock(return_value=None)
    reg.unregister = MagicMock(return_value=True)
    reg._validator = MagicMock()
    return reg


@pytest.fixture
def engine() -> MagicMock:
    eng = MagicMock()
    eng.execute = AsyncMock()
    return eng


@pytest.fixture
def client(app: FastAPI, registry: MagicMock, engine: MagicMock) -> TestClient:
    app.state.workflow_registry = registry
    app.state.workflow_engine = engine
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /workflows (list)
# ---------------------------------------------------------------------------


class TestListWorkflows:
    def test_empty_list_returns_200_with_pagination_envelope(
        self, client: TestClient, registry: MagicMock
    ) -> None:
        registry.list_workflows.return_value = []

        resp = client.get("/api/v1/workflows")

        assert resp.status_code == 200
        body = resp.json()
        assert body["workflows"] == []
        assert body["total"] == 0
        assert body["page"] == 1
        assert body["page_size"] == 20
        assert body["has_next"] is False
        assert body["has_prev"] is False

    def test_list_returns_summaries(self, client: TestClient, registry: MagicMock) -> None:
        registry.list_workflows.return_value = [
            _make_workflow(name="alpha", tags=["x"]),
            _make_workflow(name="beta"),
        ]

        resp = client.get("/api/v1/workflows")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        names = {w["name"] for w in body["workflows"]}
        assert names == {"alpha", "beta"}
        # Summary shape: id mirrors name, status defaults to active.
        alpha = next(w for w in body["workflows"] if w["name"] == "alpha")
        assert alpha["id"] == "alpha"
        assert alpha["status"] == "active"
        assert alpha["tags"] == ["x"]

    def test_filter_by_tag(self, client: TestClient, registry: MagicMock) -> None:
        registry.list_workflows.return_value = [
            _make_workflow(name="alpha", tags=["keep"]),
            _make_workflow(name="beta", tags=["drop"]),
        ]

        resp = client.get("/api/v1/workflows", params={"tag": "keep"})

        body = resp.json()
        assert body["total"] == 1
        assert body["workflows"][0]["name"] == "alpha"

    def test_filter_by_search_matches_name_and_description(
        self, client: TestClient, registry: MagicMock
    ) -> None:
        registry.list_workflows.return_value = [
            _make_workflow(name="invoice-flow", description=None),
            _make_workflow(name="other", description="handles INVOICE parsing"),
            _make_workflow(name="unrelated", description="nope"),
        ]

        resp = client.get("/api/v1/workflows", params={"search": "invoice"})

        body = resp.json()
        names = {w["name"] for w in body["workflows"]}
        assert names == {"invoice-flow", "other"}
        assert body["total"] == 2

    def test_pagination_has_next_and_prev_flags(
        self, client: TestClient, registry: MagicMock
    ) -> None:
        registry.list_workflows.return_value = [_make_workflow(name=f"wf-{i}") for i in range(5)]

        page1 = client.get("/api/v1/workflows", params={"page": 1, "page_size": 2}).json()
        assert page1["total"] == 5
        assert len(page1["workflows"]) == 2
        assert page1["has_next"] is True
        assert page1["has_prev"] is False

        page3 = client.get("/api/v1/workflows", params={"page": 3, "page_size": 2}).json()
        assert len(page3["workflows"]) == 1
        assert page3["has_next"] is False
        assert page3["has_prev"] is True

    def test_page_size_above_limit_is_rejected(self, client: TestClient) -> None:
        resp = client.get("/api/v1/workflows", params={"page_size": 1000})
        assert resp.status_code == 422

    def test_page_below_one_is_rejected(self, client: TestClient) -> None:
        resp = client.get("/api/v1/workflows", params={"page": 0})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /workflows (create)
# ---------------------------------------------------------------------------


class TestCreateWorkflow:
    _YAML = "name: created\nversion: 2.0.0\nsteps: []\n"

    def test_create_returns_201_and_metadata(
        self, client: TestClient, registry: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import ploston_core.api.routers.workflows as wf_mod

        monkeypatch.setattr(
            wf_mod,
            "parse_workflow_yaml",
            lambda _c: _make_workflow(name="created", version="2.0.0"),
        )

        resp = client.post(
            "/api/v1/workflows",
            content=self._YAML,
            headers={"Content-Type": "application/x-yaml"},
        )

        assert resp.status_code == 201
        body = resp.json()
        assert body["id"] == "created"
        assert body["name"] == "created"
        assert body["version"] == "2.0.0"
        assert body["status"] == "active"
        registry.register_from_yaml.assert_called_once()
        # persist + validate flags forwarded
        _, kwargs = registry.register_from_yaml.call_args
        assert kwargs["persist"] is True
        assert kwargs["validate"] is True

    def test_create_with_validate_false_forwards_flag(
        self, client: TestClient, registry: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import ploston_core.api.routers.workflows as wf_mod

        monkeypatch.setattr(wf_mod, "parse_workflow_yaml", lambda _c: _make_workflow())

        resp = client.post(
            "/api/v1/workflows?validate=false",
            content=self._YAML,
            headers={"Content-Type": "application/x-yaml"},
        )

        assert resp.status_code == 201
        _, kwargs = registry.register_from_yaml.call_args
        assert kwargs["validate"] is False

    def test_create_aelerror_maps_to_http_status_and_envelope(
        self, client: TestClient, registry: MagicMock
    ) -> None:
        registry.register_from_yaml.side_effect = AELError(
            code="WORKFLOW_INVALID",
            category=ErrorCategory.VALIDATION,
            message="bad workflow",
            http_status=422,
        )

        resp = client.post(
            "/api/v1/workflows",
            content="name: x\nversion: 1\n",
            headers={"Content-Type": "application/x-yaml"},
        )

        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "WORKFLOW_INVALID"
        assert detail["category"] == "VALIDATION"
        assert detail["message"] == "bad workflow"


# ---------------------------------------------------------------------------
# POST /workflows/validate
# ---------------------------------------------------------------------------


class TestValidateWorkflow:
    def test_valid_workflow_returns_valid_true(
        self, client: TestClient, registry: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import ploston_core.api.routers.workflows as wf_mod

        monkeypatch.setattr(wf_mod, "parse_workflow_yaml", lambda _c: _make_workflow())
        registry._validator.validate.return_value = SimpleNamespace(
            valid=True, errors=[], warnings=[]
        )

        resp = client.post(
            "/api/v1/workflows/validate",
            content="name: ok\nversion: 1\n",
            headers={"Content-Type": "application/x-yaml"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["errors"] == []
        assert body["warnings"] == []

    def test_validation_errors_and_warnings_surface(
        self, client: TestClient, registry: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import ploston_core.api.routers.workflows as wf_mod

        monkeypatch.setattr(wf_mod, "parse_workflow_yaml", lambda _c: _make_workflow())
        registry._validator.validate.return_value = SimpleNamespace(
            valid=False,
            errors=[SimpleNamespace(path="steps.0", message="missing tool", line=3)],
            warnings=[SimpleNamespace(path="meta", message="no description", line=None)],
        )

        resp = client.post(
            "/api/v1/workflows/validate",
            content="name: bad\nversion: 1\n",
            headers={"Content-Type": "application/x-yaml"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert body["errors"][0] == {"path": "steps.0", "message": "missing tool", "line": 3}
        assert body["warnings"][0]["path"] == "meta"

    def test_parse_failure_returns_valid_false_with_yaml_error(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import ploston_core.api.routers.workflows as wf_mod

        def _boom(_c: str) -> WorkflowDefinition:
            raise ValueError("not yaml")

        monkeypatch.setattr(wf_mod, "parse_workflow_yaml", _boom)

        resp = client.post(
            "/api/v1/workflows/validate",
            content="::::not yaml::::",
            headers={"Content-Type": "application/x-yaml"},
        )

        # Contract: parse errors do NOT 500; they return a structured invalid result.
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert body["errors"][0]["path"] == "yaml"
        assert "not yaml" in body["errors"][0]["message"]


# ---------------------------------------------------------------------------
# GET /workflows/{id}
# ---------------------------------------------------------------------------


class TestGetWorkflow:
    def test_get_existing_returns_detail(self, client: TestClient, registry: MagicMock) -> None:
        registry.get.return_value = _make_workflow(
            name="demo", yaml_content="name: demo\nversion: 1.0.0\n"
        )

        resp = client.get("/api/v1/workflows/demo")

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "demo"
        assert body["name"] == "demo"
        assert body["status"] == "active"
        assert "definition" in body
        assert body["yaml"] == "name: demo\nversion: 1.0.0\n"

    def test_get_missing_returns_404(self, client: TestClient, registry: MagicMock) -> None:
        registry.get.return_value = None

        resp = client.get("/api/v1/workflows/ghost")

        assert resp.status_code == 404
        assert "ghost" in resp.json()["detail"]

    def test_get_falls_back_to_synthesized_yaml_when_missing(
        self, client: TestClient, registry: MagicMock
    ) -> None:
        registry.get.return_value = _make_workflow(
            name="noyaml", version="3.1.4", yaml_content=None
        )

        resp = client.get("/api/v1/workflows/noyaml")

        assert resp.status_code == 200
        yaml_repr = resp.json()["yaml"]
        assert "name: noyaml" in yaml_repr
        assert "version: 3.1.4" in yaml_repr


# ---------------------------------------------------------------------------
# PUT /workflows/{id}
# ---------------------------------------------------------------------------


class TestUpdateWorkflow:
    def test_update_missing_returns_404_before_parsing(
        self, client: TestClient, registry: MagicMock
    ) -> None:
        registry.get.return_value = None

        resp = client.put(
            "/api/v1/workflows/ghost",
            content="name: ghost\nversion: 1\n",
            headers={"Content-Type": "application/x-yaml"},
        )

        assert resp.status_code == 404
        registry.unregister.assert_not_called()
        registry.register_from_yaml.assert_not_called()

    def test_update_name_mismatch_returns_400(
        self, client: TestClient, registry: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import ploston_core.api.routers.workflows as wf_mod

        registry.get.return_value = _make_workflow(name="demo")
        monkeypatch.setattr(
            wf_mod, "parse_workflow_yaml", lambda _c: _make_workflow(name="different")
        )

        resp = client.put(
            "/api/v1/workflows/demo",
            content="name: different\nversion: 1\n",
            headers={"Content-Type": "application/x-yaml"},
        )

        assert resp.status_code == 400
        assert "does not match" in resp.json()["detail"]
        # Must not mutate the registry on a rejected update.
        registry.unregister.assert_not_called()
        registry.register_from_yaml.assert_not_called()

    def test_update_success_reregisters_and_returns_metadata(
        self, client: TestClient, registry: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import ploston_core.api.routers.workflows as wf_mod

        registry.get.return_value = _make_workflow(name="demo")
        monkeypatch.setattr(
            wf_mod,
            "parse_workflow_yaml",
            lambda _c: _make_workflow(name="demo", version="9.9.9"),
        )

        resp = client.put(
            "/api/v1/workflows/demo",
            content="name: demo\nversion: 9.9.9\n",
            headers={"Content-Type": "application/x-yaml"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "demo"
        assert body["version"] == "9.9.9"
        registry.unregister.assert_called_once_with("demo")
        registry.register_from_yaml.assert_called_once()

    def test_update_aelerror_maps_to_envelope(
        self, client: TestClient, registry: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import ploston_core.api.routers.workflows as wf_mod

        registry.get.return_value = _make_workflow(name="demo")
        monkeypatch.setattr(wf_mod, "parse_workflow_yaml", lambda _c: _make_workflow(name="demo"))
        registry.register_from_yaml.side_effect = AELError(
            code="TOOL_UNAVAILABLE",
            category=ErrorCategory.TOOL,
            message="tool gone",
            http_status=409,
        )

        resp = client.put(
            "/api/v1/workflows/demo",
            content="name: demo\nversion: 1\n",
            headers={"Content-Type": "application/x-yaml"},
        )

        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "TOOL_UNAVAILABLE"


# ---------------------------------------------------------------------------
# DELETE /workflows/{id}
# ---------------------------------------------------------------------------


class TestDeleteWorkflow:
    def test_delete_existing_returns_204_no_body(
        self, client: TestClient, registry: MagicMock
    ) -> None:
        registry.unregister.return_value = True

        resp = client.delete("/api/v1/workflows/demo")

        assert resp.status_code == 204
        assert resp.content == b""
        registry.unregister.assert_called_once_with("demo")

    def test_delete_missing_returns_404(self, client: TestClient, registry: MagicMock) -> None:
        registry.unregister.return_value = False

        resp = client.delete("/api/v1/workflows/ghost")

        assert resp.status_code == 404
        assert "ghost" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# POST /workflows/{id}/execute
# ---------------------------------------------------------------------------


def _execution_result(
    status: CoreExecutionStatus = CoreExecutionStatus.COMPLETED,
    steps: list[StepResult] | None = None,
    outputs: dict | None = None,
) -> ExecutionResult:
    now = datetime.now(UTC)
    return ExecutionResult(
        execution_id="exec-123",
        workflow_id="demo",
        workflow_version="1.0.0",
        status=status,
        started_at=now,
        completed_at=now,
        duration_ms=42,
        inputs={},
        outputs=outputs or {"answer": 7},
        steps=steps or [],
    )


class TestExecuteWorkflow:
    def test_execute_returns_execution_detail(
        self, client: TestClient, registry: MagicMock, engine: MagicMock
    ) -> None:
        step = StepResult(
            step_id="s1",
            status=StepStatus.COMPLETED,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            duration_ms=10,
        )
        engine.execute.return_value = _execution_result(steps=[step])
        # Workflow lookup supplies the tool mapping for steps.
        wf = _make_workflow(name="demo")
        wf.steps = [SimpleNamespace(id="s1", tool="my_tool")]
        registry.get.return_value = wf

        resp = client.post("/api/v1/workflows/demo/execute", json={"inputs": {"x": 1}})

        assert resp.status_code == 200
        body = resp.json()
        assert body["execution_id"] == "exec-123"
        assert body["workflow_id"] == "demo"
        assert body["status"] == "completed"
        assert body["duration_ms"] == 42
        assert body["outputs"] == {"answer": 7}
        assert body["inputs"] == {"x": 1}
        assert len(body["steps"]) == 1
        s = body["steps"][0]
        assert s["id"] == "s1"
        assert s["tool"] == "my_tool"
        assert s["type"] == "tool"
        assert s["status"] == "completed"
        engine.execute.assert_awaited_once_with("demo", {"x": 1})

    def test_execute_step_without_tool_is_code_type(
        self, client: TestClient, registry: MagicMock, engine: MagicMock
    ) -> None:
        step = StepResult(step_id="code-step", status=StepStatus.COMPLETED)
        engine.execute.return_value = _execution_result(steps=[step])
        wf = _make_workflow(name="demo")
        wf.steps = [SimpleNamespace(id="code-step", tool=None)]
        registry.get.return_value = wf

        resp = client.post("/api/v1/workflows/demo/execute", json={"inputs": {}})

        assert resp.status_code == 200
        s = resp.json()["steps"][0]
        assert s["tool"] is None
        assert s["type"] == "code"

    def test_execute_empty_body_uses_default_inputs(
        self, client: TestClient, registry: MagicMock, engine: MagicMock
    ) -> None:
        engine.execute.return_value = _execution_result()
        registry.get.return_value = _make_workflow(name="demo")

        resp = client.post("/api/v1/workflows/demo/execute", json={})

        assert resp.status_code == 200
        engine.execute.assert_awaited_once_with("demo", {})

    def test_execute_aelerror_maps_to_envelope(self, client: TestClient, engine: MagicMock) -> None:
        engine.execute.side_effect = AELError(
            code="WORKFLOW_NOT_FOUND",
            category=ErrorCategory.WORKFLOW,
            message="no such workflow",
            http_status=404,
        )

        resp = client.post("/api/v1/workflows/ghost/execute", json={"inputs": {}})

        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail["code"] == "WORKFLOW_NOT_FOUND"
        assert detail["category"] == "WORKFLOW"

    def test_execute_failed_step_surfaces_error_string(
        self, client: TestClient, registry: MagicMock, engine: MagicMock
    ) -> None:
        step = StepResult(
            step_id="s1",
            status=StepStatus.FAILED,
            error=AELError(code="TOOL_ERROR", category=ErrorCategory.TOOL, message="kaboom"),
        )
        engine.execute.return_value = _execution_result(
            status=CoreExecutionStatus.FAILED, steps=[step]
        )
        registry.get.return_value = _make_workflow(name="demo")

        resp = client.post("/api/v1/workflows/demo/execute", json={"inputs": {}})

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "failed"
        assert "kaboom" in body["steps"][0]["error"]

    # -- BUG R-6 regression -------------------------------------------------
    # StepStatus has values (`pending`, `skipped`) that the execute/get
    # endpoint must surface faithfully. Previously the router did
    # ``ExecutionStatus(step.status.value)`` and `skipped` is not a member of
    # the API ExecutionStatus enum -> ValueError -> HTTP 500. The contract:
    # a skipped/pending step round-trips with HTTP 200 and the correct status.

    def test_execute_skipped_step_round_trips_with_200(
        self, client: TestClient, registry: MagicMock, engine: MagicMock
    ) -> None:
        step = StepResult(
            step_id="skipme",
            status=StepStatus.SKIPPED,
            skip_reason="condition false",
        )
        engine.execute.return_value = _execution_result(
            status=CoreExecutionStatus.COMPLETED, steps=[step]
        )
        registry.get.return_value = _make_workflow(name="demo")

        resp = client.post("/api/v1/workflows/demo/execute", json={"inputs": {}})

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["steps"]) == 1
        s = body["steps"][0]
        assert s["id"] == "skipme"
        # Skipped must be surfaced faithfully, not coerced/dropped.
        assert s["status"] == "skipped"

    def test_execute_pending_step_round_trips_with_200(
        self, client: TestClient, registry: MagicMock, engine: MagicMock
    ) -> None:
        step = StepResult(step_id="waiting", status=StepStatus.PENDING)
        engine.execute.return_value = _execution_result(
            status=CoreExecutionStatus.RUNNING, steps=[step]
        )
        registry.get.return_value = _make_workflow(name="demo")

        resp = client.post("/api/v1/workflows/demo/execute", json={"inputs": {}})

        assert resp.status_code == 200
        s = resp.json()["steps"][0]
        assert s["id"] == "waiting"
        assert s["status"] == "pending"

    def test_execute_mixed_step_statuses_all_surface(
        self, client: TestClient, registry: MagicMock, engine: MagicMock
    ) -> None:
        steps = [
            StepResult(step_id="done", status=StepStatus.COMPLETED),
            StepResult(step_id="skipped", status=StepStatus.SKIPPED),
            StepResult(step_id="pending", status=StepStatus.PENDING),
            StepResult(step_id="running", status=StepStatus.RUNNING),
            StepResult(
                step_id="failed",
                status=StepStatus.FAILED,
                error=AELError(code="TOOL_ERROR", category=ErrorCategory.TOOL, message="boom"),
            ),
        ]
        engine.execute.return_value = _execution_result(
            status=CoreExecutionStatus.FAILED, steps=steps
        )
        registry.get.return_value = _make_workflow(name="demo")

        resp = client.post("/api/v1/workflows/demo/execute", json={"inputs": {}})

        assert resp.status_code == 200
        by_id = {s["id"]: s["status"] for s in resp.json()["steps"]}
        assert by_id == {
            "done": "completed",
            "skipped": "skipped",
            "pending": "pending",
            "running": "running",
            "failed": "failed",
        }

"""Tests for Runner REST API models.

Implements S-184: Runner REST API
- UT-087: RunnerStatusEnum tests
- UT-088: RunnerSummary tests
- UT-089: RunnerDetail tests
- UT-090: RunnerCreateRequest tests
- UT-091: RunnerCreateResponse tests
- UT-092: RunnerListResponse tests
- UT-093: RunnerDeleteResponse tests
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ploston_core.api.models import (
    RunnerCreateRequest,
    RunnerCreateResponse,
    RunnerDeleteResponse,
    RunnerDetail,
    RunnerListResponse,
    RunnerStatusEnum,
    RunnerSummary,
)


class TestRunnerStatusEnum:
    """Tests for RunnerStatusEnum (UT-087)."""

    def test_connected_value(self) -> None:
        """Test CONNECTED enum value."""
        assert RunnerStatusEnum.CONNECTED.value == "connected"

    def test_disconnected_value(self) -> None:
        """Test DISCONNECTED enum value."""
        assert RunnerStatusEnum.DISCONNECTED.value == "disconnected"


class TestRunnerSummary:
    """Tests for RunnerSummary model (UT-088)."""

    def test_basic_summary(self) -> None:
        """Test basic RunnerSummary creation."""
        summary = RunnerSummary(
            id="runner_abc123",
            name="marc-laptop",
            status=RunnerStatusEnum.CONNECTED,
            last_seen=datetime.now(UTC),
            tool_count=5,
        )
        assert summary.id == "runner_abc123"
        assert summary.name == "marc-laptop"
        assert summary.status == RunnerStatusEnum.CONNECTED
        assert summary.tool_count == 5

    def test_summary_defaults(self) -> None:
        """Test RunnerSummary default values."""
        summary = RunnerSummary(
            id="runner_abc123",
            name="test-runner",
            status=RunnerStatusEnum.DISCONNECTED,
        )
        assert summary.last_seen is None
        assert summary.tool_count == 0


class TestRunnerDetail:
    """Tests for RunnerDetail model (UT-089)."""

    def test_full_detail(self) -> None:
        """Test RunnerDetail with all fields."""
        now = datetime.now(UTC)
        detail = RunnerDetail(
            id="runner_abc123",
            name="marc-laptop",
            status=RunnerStatusEnum.CONNECTED,
            created_at=now,
            last_seen=now,
            available_tools=["read_file", "write_file"],
            mcps={"native-tools": {"url": "http://localhost:8081"}},
        )
        assert detail.id == "runner_abc123"
        assert detail.name == "marc-laptop"
        assert len(detail.available_tools) == 2
        assert "native-tools" in detail.mcps

    def test_detail_defaults(self) -> None:
        """Test RunnerDetail default values."""
        detail = RunnerDetail(
            id="runner_abc123",
            name="test-runner",
            status=RunnerStatusEnum.DISCONNECTED,
            created_at=datetime.now(UTC),
        )
        assert detail.last_seen is None
        assert detail.available_tools == []
        assert detail.mcps == {}


class TestRunnerCreateRequest:
    """Tests for RunnerCreateRequest model (UT-090)."""

    def test_basic_request(self) -> None:
        """Test basic create request."""
        request = RunnerCreateRequest(name="marc-laptop")
        assert request.name == "marc-laptop"
        assert request.mcps is None

    def test_request_with_mcps(self) -> None:
        """Test create request with MCP configs."""
        request = RunnerCreateRequest(
            name="marc-laptop",
            mcps={"native-tools": {"url": "http://localhost:8081"}},
        )
        assert request.name == "marc-laptop"
        assert "native-tools" in request.mcps

    def test_name_min_length(self) -> None:
        """Test name minimum length validation."""
        with pytest.raises(ValidationError):
            RunnerCreateRequest(name="")

    def test_name_max_length(self) -> None:
        """Test name maximum length validation."""
        with pytest.raises(ValidationError):
            RunnerCreateRequest(name="a" * 65)


class TestRunnerCreateResponse:
    """Tests for RunnerCreateResponse model (UT-091)."""

    def test_create_response(self) -> None:
        """Test create response with all fields."""
        response = RunnerCreateResponse(
            id="runner_abc123",
            name="marc-laptop",
            token="ploston_runner_abc123xyz",
            install_command="uv tool install ploston-runner && ploston-runner connect ...",
        )
        assert response.id == "runner_abc123"
        assert response.name == "marc-laptop"
        assert response.token.startswith("ploston_runner_")
        assert "ploston-runner" in response.install_command


class TestRunnerListResponse:
    """Tests for RunnerListResponse model (UT-092)."""

    def test_empty_list(self) -> None:
        """Test empty runner list."""
        response = RunnerListResponse(runners=[], total=0)
        assert len(response.runners) == 0
        assert response.total == 0

    def test_list_with_runners(self) -> None:
        """Test list with multiple runners."""
        runners = [
            RunnerSummary(
                id="runner_1",
                name="laptop-1",
                status=RunnerStatusEnum.CONNECTED,
                tool_count=3,
            ),
            RunnerSummary(
                id="runner_2",
                name="laptop-2",
                status=RunnerStatusEnum.DISCONNECTED,
                tool_count=0,
            ),
        ]
        response = RunnerListResponse(runners=runners, total=2)
        assert len(response.runners) == 2
        assert response.total == 2


class TestRunnerDeleteResponse:
    """Tests for RunnerDeleteResponse model (UT-093)."""

    def test_delete_response(self) -> None:
        """Test delete response."""
        response = RunnerDeleteResponse(deleted=True, name="marc-laptop")
        assert response.deleted is True
        assert response.name == "marc-laptop"

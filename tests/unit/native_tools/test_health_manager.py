"""Unit tests for HealthManager."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ploston_core.native_tools.health import (
    DEPENDENCY_TOOLS,
    DependencyStatus,
    DependencyUnavailableError,
    HealthManager,
    OverallStatus,
    reset_health_manager,
)


@pytest.fixture
def health_manager():
    """Create a fresh HealthManager for each test."""
    reset_health_manager()
    manager = HealthManager(check_interval=30, check_timeout=5)
    yield manager
    # Cleanup
    reset_health_manager()


class TestDependencyUnavailableError:
    """Tests for DependencyUnavailableError."""

    def test_error_with_default_message(self):
        """Test error with default message."""
        error = DependencyUnavailableError("kafka")
        assert error.dependency == "kafka"
        assert error.code == "DEPENDENCY_UNAVAILABLE"
        assert error.retryable is True
        assert "Kafka" in error.message
        assert "health endpoint" in error.message

    def test_error_with_custom_message(self):
        """Test error with custom message."""
        error = DependencyUnavailableError("ollama", "Custom error message")
        assert error.dependency == "ollama"
        assert error.message == "Custom error message"

    def test_error_to_dict(self):
        """Test error serialization."""
        error = DependencyUnavailableError("firecrawl")
        result = error.to_dict()
        assert result["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
        assert result["error"]["dependency"] == "firecrawl"
        assert result["error"]["retryable"] is True


class TestHealthManagerConfiguration:
    """Tests for HealthManager configuration."""

    def test_configure_kafka_enabled(self, health_manager):
        """Test configuring Kafka with valid settings."""
        health_manager.configure_kafka(
            bootstrap_servers="kafka.example.com:9092",
            client_id="test-client",
            security_protocol="PLAINTEXT",
        )
        assert health_manager.is_dependency_enabled("kafka")
        assert not health_manager.is_dependency_healthy("kafka")  # Initially unhealthy

    def test_configure_kafka_disabled_default(self, health_manager):
        """Test Kafka is disabled with default localhost."""
        health_manager.configure_kafka(
            bootstrap_servers="localhost:9092",
            client_id="test-client",
            security_protocol="PLAINTEXT",
        )
        assert not health_manager.is_dependency_enabled("kafka")
        # Disabled dependencies are considered "healthy" (won't block tools)
        assert health_manager.is_dependency_healthy("kafka")

    def test_configure_ollama_enabled(self, health_manager):
        """Test configuring Ollama with valid settings."""
        health_manager.configure_ollama(host="http://ollama.example.com:11434")
        assert health_manager.is_dependency_enabled("ollama")

    def test_configure_ollama_disabled_default(self, health_manager):
        """Test Ollama is disabled with default localhost."""
        health_manager.configure_ollama(host="http://localhost:11434")
        assert not health_manager.is_dependency_enabled("ollama")

    def test_configure_firecrawl_enabled(self, health_manager):
        """Test configuring Firecrawl with valid settings."""
        health_manager.configure_firecrawl(base_url="http://firecrawl.example.com:3002")
        assert health_manager.is_dependency_enabled("firecrawl")

    def test_configure_firecrawl_disabled_default(self, health_manager):
        """Test Firecrawl is disabled with default localhost."""
        health_manager.configure_firecrawl(base_url="http://localhost:3002")
        assert not health_manager.is_dependency_enabled("firecrawl")


class TestHealthManagerStatus:
    """Tests for HealthManager status methods."""

    def test_overall_status_healthy_when_all_disabled(self, health_manager):
        """Test overall status is healthy when all deps disabled."""
        health_manager.configure_kafka(
            bootstrap_servers="localhost:9092", client_id="test", security_protocol="PLAINTEXT"
        )
        health_manager.configure_ollama(host="http://localhost:11434")
        health_manager.configure_firecrawl(base_url="http://localhost:3002")
        assert health_manager.get_overall_status() == OverallStatus.HEALTHY

    def test_overall_status_degraded_when_some_unhealthy(self, health_manager):
        """Test overall status is degraded when some deps unhealthy."""
        health_manager.configure_kafka(
            bootstrap_servers="kafka.example.com:9092",
            client_id="test",
            security_protocol="PLAINTEXT",
        )
        # Kafka is enabled but not checked yet, so it's unhealthy
        assert health_manager.get_overall_status() == OverallStatus.DEGRADED

    def test_get_tool_counts(self, health_manager):
        """Test tool count calculation."""
        health_manager.configure_kafka(
            bootstrap_servers="kafka.example.com:9092",
            client_id="test",
            security_protocol="PLAINTEXT",
        )
        counts = health_manager.get_tool_counts()
        assert counts["total"] == len(DEPENDENCY_TOOLS["kafka"])
        assert counts["degraded"] == len(DEPENDENCY_TOOLS["kafka"])  # Kafka unhealthy
        assert counts["available"] == 0

    def test_get_health_response(self, health_manager):
        """Test health response format."""
        health_manager.configure_kafka(
            bootstrap_servers="localhost:9092",
            client_id="test",
            security_protocol="PLAINTEXT",
        )
        response = health_manager.get_health_response(version="1.0.0")
        assert "status" in response
        assert "timestamp" in response
        assert "version" in response
        assert response["version"] == "1.0.0"
        assert "uptime_seconds" in response
        assert "dependencies" in response
        assert "tools" in response


class TestHealthManagerChecks:
    """Tests for HealthManager health check methods."""

    @pytest.mark.asyncio
    async def test_check_kafka_disabled(self, health_manager):
        """Test Kafka check returns disabled when not configured."""
        health_manager.configure_kafka(
            bootstrap_servers="localhost:9092",
            client_id="test",
            security_protocol="PLAINTEXT",
        )
        result = await health_manager.check_kafka()
        assert result.status == DependencyStatus.DISABLED

    @pytest.mark.asyncio
    async def test_check_ollama_disabled(self, health_manager):
        """Test Ollama check returns disabled when not configured."""
        health_manager.configure_ollama(host="http://localhost:11434")
        result = await health_manager.check_ollama()
        assert result.status == DependencyStatus.DISABLED

    @pytest.mark.asyncio
    async def test_check_firecrawl_disabled(self, health_manager):
        """Test Firecrawl check returns disabled when not configured."""
        health_manager.configure_firecrawl(base_url="http://localhost:3002")
        result = await health_manager.check_firecrawl()
        assert result.status == DependencyStatus.DISABLED

    @pytest.mark.asyncio
    async def test_check_ollama_healthy(self, health_manager):
        """Test Ollama check returns healthy when service responds."""
        health_manager.configure_ollama(host="http://ollama.example.com:11434")

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            result = await health_manager.check_ollama()
            assert result.status == DependencyStatus.HEALTHY
            assert result.latency_ms is not None

    @pytest.mark.asyncio
    async def test_check_ollama_unhealthy(self, health_manager):
        """Test Ollama check returns unhealthy when service fails."""
        health_manager.configure_ollama(host="http://ollama.example.com:11434")

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=Exception("Connection refused")
            )

            result = await health_manager.check_ollama()
            assert result.status == DependencyStatus.UNHEALTHY
            assert "Connection refused" in result.error

    @pytest.mark.asyncio
    async def test_check_firecrawl_healthy(self, health_manager):
        """Test Firecrawl check returns healthy when service responds."""
        health_manager.configure_firecrawl(base_url="http://firecrawl.example.com:3002")

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            result = await health_manager.check_firecrawl()
            assert result.status == DependencyStatus.HEALTHY


class TestHealthManagerLifecycle:
    """Tests for HealthManager start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_runs_initial_check(self, health_manager):
        """Test that start() runs initial health checks."""
        health_manager.configure_kafka(
            bootstrap_servers="localhost:9092",
            client_id="test",
            security_protocol="PLAINTEXT",
        )

        with patch.object(health_manager, "check_all", new_callable=AsyncMock) as mock_check:
            await health_manager.start()
            mock_check.assert_called_once()
            await health_manager.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_background_task(self, health_manager):
        """Test that stop() cancels background task."""
        health_manager.configure_kafka(
            bootstrap_servers="localhost:9092",
            client_id="test",
            security_protocol="PLAINTEXT",
        )

        with patch.object(health_manager, "check_all", new_callable=AsyncMock):
            await health_manager.start()
            assert health_manager._running is True
            await health_manager.stop()
            assert health_manager._running is False

    @pytest.mark.asyncio
    async def test_on_change_callback(self, health_manager):
        """Test health change callbacks are called."""
        callback = MagicMock()
        health_manager.on_change(callback)

        health_manager.configure_ollama(host="http://ollama.example.com:11434")

        # Simulate health check that changes status
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            await health_manager.check_all()
            # Callback should be called because status changed from UNHEALTHY to HEALTHY
            callback.assert_called_once()

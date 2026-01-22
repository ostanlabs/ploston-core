"""Tests for OTLP trace and log exporters."""

import pytest

from ploston_core.telemetry import (
    TelemetryConfig,
    OTLPExporterConfig,
    setup_telemetry,
    reset_telemetry,
)
from ploston_core.telemetry.setup import _create_otlp_span_exporter, _create_otlp_log_exporter


@pytest.fixture(autouse=True)
def reset_telemetry_state():
    """Reset telemetry state before and after each test."""
    reset_telemetry()
    yield
    reset_telemetry()


class TestOTLPExporterConfig:
    """Tests for OTLPExporterConfig."""
    
    def test_default_config(self):
        """Test default OTLP config values."""
        config = OTLPExporterConfig()
        assert config.enabled is False
        assert config.endpoint == "http://localhost:4317"
        assert config.insecure is True
        assert config.protocol == "grpc"
        assert config.headers == {}
    
    def test_custom_config(self):
        """Test custom OTLP config values."""
        config = OTLPExporterConfig(
            enabled=True,
            endpoint="http://otel-collector:4317",
            insecure=False,
            protocol="http",
            headers={"Authorization": "Bearer token"},
        )
        assert config.enabled is True
        assert config.endpoint == "http://otel-collector:4317"
        assert config.insecure is False
        assert config.protocol == "http"
        assert config.headers == {"Authorization": "Bearer token"}


class TestCreateOTLPSpanExporter:
    """Tests for _create_otlp_span_exporter."""
    
    def test_grpc_exporter_created(self):
        """Test gRPC exporter is created."""
        config = OTLPExporterConfig(
            enabled=True,
            endpoint="http://collector:4317",
            protocol="grpc",
        )
        exporter = _create_otlp_span_exporter(config)
        assert exporter is not None
        # Check it's the gRPC exporter type
        assert "grpc" in type(exporter).__module__
    
    def test_http_exporter_created(self):
        """Test HTTP exporter is created."""
        config = OTLPExporterConfig(
            enabled=True,
            endpoint="http://collector:4318",
            protocol="http",
        )
        exporter = _create_otlp_span_exporter(config)
        assert exporter is not None
        # Check it's the HTTP exporter type
        assert "http" in type(exporter).__module__


class TestCreateOTLPLogExporter:
    """Tests for _create_otlp_log_exporter."""
    
    def test_grpc_log_exporter_created(self):
        """Test gRPC log exporter is created."""
        config = OTLPExporterConfig(
            enabled=True,
            endpoint="http://collector:4317",
            protocol="grpc",
        )
        exporter = _create_otlp_log_exporter(config)
        assert exporter is not None
        assert "grpc" in type(exporter).__module__
    
    def test_http_log_exporter_created(self):
        """Test HTTP log exporter is created."""
        config = OTLPExporterConfig(
            enabled=True,
            endpoint="http://collector:4318",
            protocol="http",
        )
        exporter = _create_otlp_log_exporter(config)
        assert exporter is not None
        assert "http" in type(exporter).__module__


class TestTelemetryConfigWithOTLP:
    """Tests for TelemetryConfig with OTLP settings."""
    
    def test_default_otlp_disabled(self):
        """Test OTLP is disabled by default."""
        config = TelemetryConfig()
        assert config.otlp.enabled is False
    
    def test_traces_enabled_with_otlp(self):
        """Test traces can be enabled with OTLP export."""
        config = TelemetryConfig(
            traces_enabled=True,
            otlp=OTLPExporterConfig(enabled=True),
        )
        assert config.traces_enabled is True
        assert config.otlp.enabled is True
    
    def test_logs_enabled_with_otlp(self):
        """Test logs can be enabled with OTLP export."""
        config = TelemetryConfig(
            logs_enabled=True,
            otlp=OTLPExporterConfig(enabled=True),
        )
        assert config.logs_enabled is True
        assert config.otlp.enabled is True


class TestSetupTelemetryWithOTLP:
    """Tests for setup_telemetry with OTLP configuration."""
    
    def test_setup_without_otlp(self):
        """Test setup works without OTLP enabled."""
        config = TelemetryConfig(
            metrics_enabled=True,
            traces_enabled=False,
            logs_enabled=False,
        )
        telemetry = setup_telemetry(config)
        assert telemetry["meter"] is not None
        assert telemetry["tracer"] is not None
        assert telemetry["metrics"] is not None
    
    def test_setup_with_traces_but_no_otlp(self):
        """Test setup with traces enabled but OTLP disabled."""
        config = TelemetryConfig(
            traces_enabled=True,
            otlp=OTLPExporterConfig(enabled=False),
        )
        telemetry = setup_telemetry(config)
        assert telemetry["tracer"] is not None
        assert telemetry["tracer_provider"] is not None
    
    def test_setup_returns_logger_none_when_logs_disabled(self):
        """Test logger is None when logs are disabled."""
        config = TelemetryConfig(logs_enabled=False)
        telemetry = setup_telemetry(config)
        assert telemetry["logger"] is None
    
    def test_telemetry_dict_has_expected_keys(self):
        """Test telemetry dict has all expected keys."""
        config = TelemetryConfig()
        telemetry = setup_telemetry(config)
        assert "meter" in telemetry
        assert "tracer" in telemetry
        assert "logger" in telemetry
        assert "metrics" in telemetry
        assert "config" in telemetry
        assert "tracer_provider" in telemetry

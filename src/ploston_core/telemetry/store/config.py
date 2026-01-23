"""Telemetry Store configuration."""

import re
from dataclasses import dataclass, field


@dataclass
class RedactionPattern:
    """Pattern-based redaction rule."""

    regex: re.Pattern[str]
    replacement: str

    @classmethod
    def from_string(cls, pattern: str, replacement: str) -> "RedactionPattern":
        """Create from string pattern."""
        return cls(regex=re.compile(pattern), replacement=replacement)


@dataclass
class RedactionConfig:
    """Redaction configuration."""

    enabled: bool = True

    # Field names to always redact (case-insensitive)
    fields: list[str] = field(
        default_factory=lambda: [
            "password",
            "secret",
            "api_key",
            "token",
            "authorization",
            "credential",
            "private_key",
        ]
    )

    # Regex patterns to redact
    patterns: list[RedactionPattern] = field(default_factory=list)

    @classmethod
    def default(cls) -> "RedactionConfig":
        """Create default redaction config with common patterns."""
        return cls(
            enabled=True,
            patterns=[
                RedactionPattern(
                    regex=re.compile(r"sk-[a-zA-Z0-9]{32,}"),
                    replacement="[REDACTED_API_KEY]",
                ),
                RedactionPattern(
                    regex=re.compile(r"\b[0-9]{13,16}\b"),
                    replacement="[REDACTED_CARD]",
                ),
                RedactionPattern(
                    regex=re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
                    replacement="[REDACTED_EMAIL]",
                ),
            ],
        )


@dataclass
class RetentionConfig:
    """Retention policy configuration."""

    policy: str = "rolling"  # "rolling" | "fixed"
    retention_days: int = 7  # OSS max: 7 days
    cleanup_interval_seconds: int = 3600  # 1 hour


@dataclass
class OTLPExportConfig:
    """OpenTelemetry export configuration."""

    enabled: bool = False
    endpoint: str = "http://localhost:4317"
    protocol: str = "grpc"  # "grpc" | "http"
    headers: dict[str, str] = field(default_factory=dict)
    traces: bool = True
    metrics: bool = True


@dataclass
class TelemetryStoreConfig:
    """Complete telemetry store configuration."""

    enabled: bool = True

    # Storage backend
    storage_type: str = "memory"  # "memory" | "sqlite" | "postgres"

    # SQLite settings
    sqlite_path: str = "./data/telemetry.db"

    # PostgreSQL settings (Premium)
    postgres_connection_string: str | None = None

    # Memory settings
    max_memory_records: int = 1000

    # Retention
    retention: RetentionConfig = field(default_factory=RetentionConfig)

    # Redaction
    redaction: RedactionConfig = field(default_factory=RedactionConfig.default)

    # Export
    otlp: OTLPExportConfig = field(default_factory=OTLPExportConfig)

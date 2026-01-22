"""Tests for telemetry store redactor."""

import pytest

from ploston_core.telemetry.store.config import RedactionConfig, RedactionPattern
from ploston_core.telemetry.store.redactor import Redactor


class TestRedactor:
    """Test Redactor class."""

    def test_redact_disabled(self) -> None:
        """Test redaction when disabled."""
        config = RedactionConfig(enabled=False)
        redactor = Redactor(config)

        data = {"password": "secret123", "name": "test"}
        result = redactor.redact(data)

        assert result == data  # No redaction

    def test_redact_field_names(self) -> None:
        """Test redaction of sensitive field names."""
        config = RedactionConfig(
            enabled=True,
            fields=["password", "secret", "api_key"],
        )
        redactor = Redactor(config)

        data = {
            "password": "secret123",
            "secret": "mysecret",
            "api_key": "sk-12345",
            "name": "test",
        }
        result = redactor.redact(data)

        assert result["password"] == "[REDACTED]"
        assert result["secret"] == "[REDACTED]"
        assert result["api_key"] == "[REDACTED]"
        assert result["name"] == "test"

    def test_redact_case_insensitive(self) -> None:
        """Test field name matching is case-insensitive."""
        config = RedactionConfig(enabled=True, fields=["password"])
        redactor = Redactor(config)

        data = {"PASSWORD": "secret", "Password": "secret2"}
        result = redactor.redact(data)

        assert result["PASSWORD"] == "[REDACTED]"
        assert result["Password"] == "[REDACTED]"

    def test_redact_nested_dict(self) -> None:
        """Test redaction in nested dictionaries."""
        config = RedactionConfig(enabled=True, fields=["password"])
        redactor = Redactor(config)

        data = {
            "user": {
                "name": "test",
                "password": "secret",
            }
        }
        result = redactor.redact(data)

        assert result["user"]["name"] == "test"
        assert result["user"]["password"] == "[REDACTED]"

    def test_redact_list(self) -> None:
        """Test redaction in lists."""
        config = RedactionConfig(enabled=True, fields=["password"])
        redactor = Redactor(config)

        data = [{"password": "secret1"}, {"password": "secret2"}]
        result = redactor.redact(data)

        assert result[0]["password"] == "[REDACTED]"
        assert result[1]["password"] == "[REDACTED]"

    def test_redact_patterns(self) -> None:
        """Test pattern-based redaction."""
        config = RedactionConfig(
            enabled=True,
            fields=[],
            patterns=[
                RedactionPattern.from_string(r"sk-[a-zA-Z0-9]+", "[REDACTED_KEY]"),
            ],
        )
        redactor = Redactor(config)

        data = {"key": "sk-abc123xyz", "other": "normal text"}
        result = redactor.redact(data)

        assert result["key"] == "[REDACTED_KEY]"
        assert result["other"] == "normal text"

    def test_redact_default_config(self) -> None:
        """Test default redaction config."""
        config = RedactionConfig.default()
        redactor = Redactor(config)

        data = {
            "api_key": "sk-abcdefghijklmnopqrstuvwxyz123456",
            "email": "test@example.com",
            "card": "4111111111111111",
            "name": "test",
        }
        result = redactor.redact(data)

        assert result["api_key"] == "[REDACTED]"  # Field name match
        assert "[REDACTED_EMAIL]" in result["email"]
        assert "[REDACTED_CARD]" in result["card"]
        assert result["name"] == "test"

    def test_redact_non_dict_non_list(self) -> None:
        """Test redaction of non-dict, non-list values."""
        config = RedactionConfig(enabled=True)
        redactor = Redactor(config)

        assert redactor.redact(123) == 123
        assert redactor.redact(None) is None
        assert redactor.redact(True) is True


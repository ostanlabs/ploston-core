"""Redactor for sensitive data before storage."""

from typing import Any

from .config import RedactionConfig


class Redactor:
    """Redacts sensitive data before storage."""

    REDACTED = "[REDACTED]"

    def __init__(self, config: RedactionConfig) -> None:
        """Initialize redactor with configuration.

        Args:
            config: Redaction configuration
        """
        self._config = config
        self._field_set = {f.lower() for f in config.fields}

    def redact(self, data: Any) -> Any:
        """Recursively redact sensitive data.

        Args:
            data: Data to redact (dict, list, str, or other)

        Returns:
            Redacted data with sensitive values replaced
        """
        if not self._config.enabled:
            return data

        if isinstance(data, dict):
            return {
                key: (self.REDACTED if key.lower() in self._field_set else self.redact(value))
                for key, value in data.items()
            }
        elif isinstance(data, list):
            return [self.redact(item) for item in data]
        elif isinstance(data, str):
            return self._redact_patterns(data)
        else:
            return data

    def _redact_patterns(self, text: str) -> str:
        """Apply regex patterns to redact.

        Args:
            text: Text to apply patterns to

        Returns:
            Text with patterns replaced
        """
        result = text
        for pattern in self._config.patterns:
            result = pattern.regex.sub(pattern.replacement, result)
        return result

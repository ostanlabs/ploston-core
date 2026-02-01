"""Secret Detection - Detect literal secrets and suggest environment variable names."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class SecretDetection:
    """Result of secret detection."""

    original_value: str
    masked_value: str
    suggested_env_var: str
    pattern_matched: str | None  # Which pattern matched, if any
    key_matched: bool  # Whether key name suggested it's a secret


class SecretDetector:
    """
    Detect literal secrets in configuration values and suggest environment variable names.

    Uses pattern matching on values and key names to identify potential secrets.
    """

    # Patterns that indicate a literal secret value
    # Format: (regex_pattern, suggested_env_var_name)
    VALUE_PATTERNS: list[tuple[str, str]] = [
        (r"^ghp_[a-zA-Z0-9]{36,}$", "GITHUB_TOKEN"),
        (r"^github_pat_[a-zA-Z0-9_]{22,}$", "GITHUB_TOKEN"),
        (r"^gho_[a-zA-Z0-9]{36,}$", "GITHUB_OAUTH_TOKEN"),
        (r"^sk-[a-zA-Z0-9]{32,}$", "OPENAI_API_KEY"),
        (r"^sk-proj-[a-zA-Z0-9\-_]{32,}$", "OPENAI_API_KEY"),
        (r"^sk-ant-[a-zA-Z0-9\-_]{32,}$", "ANTHROPIC_API_KEY"),
        (r"^xoxb-[0-9]+-[0-9]+-[a-zA-Z0-9]+$", "SLACK_BOT_TOKEN"),
        (r"^xoxp-[0-9]+-[0-9]+-[0-9]+-[a-f0-9]+$", "SLACK_USER_TOKEN"),
        (r"^xoxa-[0-9]+-[a-zA-Z0-9]+$", "SLACK_APP_TOKEN"),
        (r"^AKIA[A-Z0-9]{16}$", "AWS_ACCESS_KEY_ID"),
        (r"^[a-zA-Z0-9/+]{40}$", "AWS_SECRET_ACCESS_KEY"),  # AWS secret key pattern
        (r"^AIza[a-zA-Z0-9\-_]{35}$", "GOOGLE_API_KEY"),
        (r"^ya29\.[a-zA-Z0-9\-_]+$", "GOOGLE_OAUTH_TOKEN"),
        (r"^[a-f0-9]{32}$", "API_KEY"),  # Generic 32-char hex (many APIs)
        (r"^[a-f0-9]{64}$", "API_SECRET"),  # Generic 64-char hex
    ]

    # Key names that suggest secret values (case-insensitive)
    KEY_PATTERNS: list[str] = [
        "token",
        "key",
        "secret",
        "password",
        "credential",
        "api_key",
        "apikey",
        "auth",
        "bearer",
        "private",
    ]

    def detect(self, key: str, value: str) -> SecretDetection | None:
        """
        Detect if a value appears to be a literal secret.

        Args:
            key: The configuration key name
            value: The configuration value

        Returns:
            SecretDetection with suggested env var name, or None if not a secret
        """
        if not isinstance(value, str):
            return None

        # Skip if already using ${VAR} syntax
        if "${" in value:
            return None

        # Skip empty or very short values
        if len(value) < 8:
            return None

        pattern_matched: str | None = None
        suggested_env_var: str | None = None
        key_matched = False

        # Check value against known secret patterns
        for pattern, env_var in self.VALUE_PATTERNS:
            if re.match(pattern, value):
                pattern_matched = pattern
                suggested_env_var = env_var
                break

        # Check if key name suggests it's a secret
        key_lower = key.lower()
        for key_pattern in self.KEY_PATTERNS:
            if key_pattern in key_lower:
                key_matched = True
                # If no pattern matched, derive env var from key
                if not suggested_env_var:
                    suggested_env_var = self._derive_env_var_name(key)
                break

        # Only return detection if we found something
        if pattern_matched or key_matched:
            return SecretDetection(
                original_value=value,
                masked_value=self.mask_value(value),
                suggested_env_var=suggested_env_var or self._derive_env_var_name(key),
                pattern_matched=pattern_matched,
                key_matched=key_matched,
            )

        return None

    def mask_value(self, value: str) -> str:
        """
        Mask a secret value for logging/display.

        Shows prefix and suffix with *** in the middle.
        Example: "ghp_abc123xyz789" -> "ghp_***789"

        Args:
            value: The secret value to mask

        Returns:
            Masked value safe for display
        """
        if len(value) <= 8:
            return "***"

        # Show first 4 chars and last 3 chars
        prefix_len = min(4, len(value) // 4)
        suffix_len = min(3, len(value) // 4)

        # For tokens with known prefixes, show more of the prefix
        for prefix in ["ghp_", "gho_", "sk-", "xoxb-", "xoxp-", "AKIA"]:
            if value.startswith(prefix):
                prefix_len = len(prefix)
                break

        return f"{value[:prefix_len]}***{value[-suffix_len:]}"

    def _derive_env_var_name(self, key: str) -> str:
        """
        Derive an environment variable name from a key.

        Args:
            key: The configuration key

        Returns:
            Suggested environment variable name
        """
        # Convert to uppercase and replace non-alphanumeric with underscore
        env_var = re.sub(r"[^a-zA-Z0-9]", "_", key.upper())
        # Remove consecutive underscores
        env_var = re.sub(r"_+", "_", env_var)
        # Remove leading/trailing underscores
        env_var = env_var.strip("_")
        return env_var or "SECRET"

    def check_env_var_set(self, env_var: str) -> bool:
        """
        Check if an environment variable is set.

        Args:
            env_var: Environment variable name

        Returns:
            True if set, False otherwise
        """
        import os

        return env_var in os.environ

    def extract_env_var_refs(self, value: str) -> list[str]:
        """
        Extract environment variable references from a value.

        Supports ${VAR} and ${VAR:-default} syntax.

        Args:
            value: Value to check

        Returns:
            List of environment variable names referenced
        """
        if not isinstance(value, str):
            return []

        # Match ${VAR} or ${VAR:-default}
        pattern = r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)(?::-[^}]*)?\}"
        matches = re.findall(pattern, value)
        return matches

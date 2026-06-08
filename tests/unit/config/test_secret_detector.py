"""Unit tests for SecretDetector."""

import pytest

from ploston_core.config.secrets import SecretDetector


class TestSecretDetector:
    """Tests for SecretDetector."""

    @pytest.fixture
    def detector(self):
        """Create SecretDetector instance."""
        return SecretDetector()

    def test_detect_github_token(self, detector):
        """Detect GitHub personal access token."""
        result = detector.detect("GITHUB_TOKEN", "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")

        assert result is not None
        assert result.suggested_env_var == "GITHUB_TOKEN"
        assert result.pattern_matched is not None

    def test_detect_openai_key(self, detector):
        """Detect OpenAI API key."""
        result = detector.detect("api_key", "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")

        assert result is not None
        assert result.suggested_env_var == "OPENAI_API_KEY"

    def test_detect_anthropic_key(self, detector):
        """Detect Anthropic API key."""
        result = detector.detect("api_key", "sk-ant-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")

        assert result is not None
        assert result.suggested_env_var == "ANTHROPIC_API_KEY"

    def test_detect_slack_bot_token(self, detector):
        """Detect Slack bot token."""
        result = detector.detect("token", "xoxb-123456789-123456789-abcdefghij")

        assert result is not None
        assert result.suggested_env_var == "SLACK_BOT_TOKEN"

    def test_detect_aws_access_key(self, detector):
        """Detect AWS access key ID."""
        result = detector.detect("aws_key", "AKIAIOSFODNN7EXAMPLE")

        assert result is not None
        assert result.suggested_env_var == "AWS_ACCESS_KEY_ID"

    def test_detect_by_key_name(self, detector):
        """Detect secret by key name pattern."""
        result = detector.detect("my_api_token", "some_long_value_that_is_not_a_known_pattern")

        assert result is not None
        assert result.key_matched is True

    def test_skip_env_var_syntax(self, detector):
        """Skip values already using ${VAR} syntax."""
        result = detector.detect("GITHUB_TOKEN", "${GITHUB_TOKEN}")

        assert result is None

    def test_skip_short_values(self, detector):
        """Skip very short values."""
        result = detector.detect("token", "short")

        assert result is None

    def test_mask_value_github_token(self, detector):
        """Mask GitHub token value."""
        masked = detector.mask_value("ghp_abc123def456xyz789")

        assert masked.startswith("ghp_")
        assert "***" in masked
        assert len(masked) < len("ghp_abc123def456xyz789")

    def test_mask_value_short(self, detector):
        """Mask short value."""
        masked = detector.mask_value("short")

        assert masked == "***"

    def test_extract_env_var_refs(self, detector):
        """Extract environment variable references."""
        refs = detector.extract_env_var_refs("${GITHUB_TOKEN}")

        assert refs == ["GITHUB_TOKEN"]

    def test_extract_env_var_refs_with_default(self, detector):
        """Extract env var refs with default value."""
        refs = detector.extract_env_var_refs("${API_KEY:-default}")

        assert refs == ["API_KEY"]

    def test_extract_multiple_env_var_refs(self, detector):
        """Extract multiple env var references."""
        refs = detector.extract_env_var_refs("${VAR1} and ${VAR2}")

        assert "VAR1" in refs
        assert "VAR2" in refs

    def test_extract_no_env_var_refs(self, detector):
        """No env var references in plain string."""
        refs = detector.extract_env_var_refs("plain string")

        assert refs == []

    def test_plain_hex_id_under_nonsecret_key_not_secret(self, detector):
        """A plain 32-char hex id under a non-secret key is NOT a secret.

        The generic ^[a-f0-9]{32}$ pattern matches huge classes of non-secret
        values (UUIDs without dashes, content hashes, request ids). Without key
        corroboration these must not trigger auto-conversion.
        """
        # 32 hex chars — looks like an md5/uuid, but key name is innocuous.
        result = detector.detect("request_id", "d41d8cd98f00b204e9800998ecf8427e")
        assert result is None

    def test_plain_64_hex_under_nonsecret_key_not_secret(self, detector):
        """A plain 64-char hex digest under a non-secret key is NOT a secret."""
        sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        result = detector.detect("content_hash", sha256)
        assert result is None

    def test_generic_b64_under_nonsecret_key_not_secret(self, detector):
        """A 40-char base64-ish value under a non-secret key is NOT a secret."""
        result = detector.detect("cache_etag", "abcdefghijklmnopqrstuvwxyzABCDEFGHIJ0123")
        assert result is None

    def test_hex_secret_under_secret_key_is_detected(self, detector):
        """A 32-hex value under a secret-named key IS still flagged."""
        result = detector.detect("api_secret", "d41d8cd98f00b204e9800998ecf8427e")
        assert result is not None
        assert result.key_matched is True

    def test_real_api_key_under_secret_key_still_detected(self, detector):
        """A real, well-formed API key under a secret-named key is detected."""
        result = detector.detect("ANTHROPIC_API_KEY", "sk-ant-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        assert result is not None
        assert result.suggested_env_var == "ANTHROPIC_API_KEY"

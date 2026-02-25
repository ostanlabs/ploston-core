"""Unit tests for Pro Auth Foundation API key functions."""

from ploston_core.auth import (
    extract_key_prefix,
    generate_api_key,
    hash_api_key,
    validate_api_key_format,
    verify_api_key,
)


class TestGenerateApiKey:
    """Tests for generate_api_key function."""

    def test_generates_valid_format(self):
        """Test generated key matches expected format."""
        key = generate_api_key("test")
        assert key.startswith("plt_")
        assert validate_api_key_format(key)

    def test_includes_prefix(self):
        """Test generated key includes the prefix."""
        key = generate_api_key("myservice")
        # Key format: plt_{prefix}_{random}
        parts = key.split("_")
        assert len(parts) == 3
        assert parts[0] == "plt"
        assert parts[1] == "myservic"  # Truncated to 8 chars max

    def test_random_part_is_32_chars(self):
        """Test random part is 32 characters."""
        key = generate_api_key("test")
        parts = key.split("_")
        assert len(parts[2]) == 32

    def test_generates_unique_keys(self):
        """Test each call generates a unique key."""
        keys = {generate_api_key("test") for _ in range(100)}
        assert len(keys) == 100


class TestValidateApiKeyFormat:
    """Tests for validate_api_key_format function."""

    def test_valid_key(self):
        """Test valid key format."""
        assert validate_api_key_format("plt_test_abcdefghijklmnopqrstuvwxyz123456")

    def test_invalid_prefix(self):
        """Test invalid prefix."""
        assert not validate_api_key_format("xxx_test_abcdefghijklmnopqrstuvwxyz123456")

    def test_missing_parts(self):
        """Test missing parts."""
        assert not validate_api_key_format("plt_test")
        assert not validate_api_key_format("plt")

    def test_short_random_part(self):
        """Test short random part."""
        assert not validate_api_key_format("plt_test_short")

    def test_empty_string(self):
        """Test empty string."""
        assert not validate_api_key_format("")


class TestHashApiKey:
    """Tests for hash_api_key function."""

    def test_returns_bcrypt_hash(self):
        """Test returns bcrypt hash."""
        key = generate_api_key("test")
        hashed = hash_api_key(key)
        assert hashed.startswith("$2b$")  # bcrypt prefix

    def test_different_hashes_for_same_key(self):
        """Test bcrypt generates different hashes (due to salt)."""
        key = generate_api_key("test")
        hash1 = hash_api_key(key)
        hash2 = hash_api_key(key)
        assert hash1 != hash2  # Different salts


class TestVerifyApiKey:
    """Tests for verify_api_key function."""

    def test_verify_correct_key(self):
        """Test verifying correct key."""
        key = generate_api_key("test")
        hashed = hash_api_key(key)
        assert verify_api_key(key, hashed)

    def test_verify_wrong_key(self):
        """Test verifying wrong key."""
        key1 = generate_api_key("test")
        key2 = generate_api_key("test")
        hashed = hash_api_key(key1)
        assert not verify_api_key(key2, hashed)

    def test_verify_invalid_hash(self):
        """Test verifying with invalid hash."""
        key = generate_api_key("test")
        assert not verify_api_key(key, "invalid_hash")


class TestExtractKeyPrefix:
    """Tests for extract_key_prefix function."""

    def test_extracts_prefix(self):
        """Test extracting prefix from key."""
        key = "plt_myser_abcdefghijklmnopqrstuvwxyz123456"
        assert extract_key_prefix(key) == "plt_myser"

    def test_handles_short_key(self):
        """Test handling short/invalid key."""
        # Keys without proper format return "invalid"
        assert extract_key_prefix("plt") == "invalid"
        assert extract_key_prefix("") == "invalid"
        assert extract_key_prefix("xxx_test_abc") == "invalid"

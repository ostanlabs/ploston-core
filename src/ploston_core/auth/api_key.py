"""API key generation and validation for Pro Auth Foundation.

Key format: plt_{prefix}_{32_random_alphanumeric_chars}
- prefix: 3-8 chars, [a-z0-9] only (derived from principal name)
- Total length: 40-45 chars
- Regex: ^plt_[a-z0-9]{3,8}_[a-zA-Z0-9]{32}$

Keys are never stored in plain text. On creation:
1. Generate key: plt_{name_prefix}_{random_32_chars}
2. Store bcrypt(key) in Redis
3. Display key to admin once
4. Key prefix (plt_{name_prefix}) stored for identification in logs
"""

from __future__ import annotations

import re
import secrets
import string

import bcrypt

# Key format validation
API_KEY_REGEX = re.compile(r"^plt_[a-z0-9]{3,8}_[a-zA-Z0-9]{32}$")
API_KEY_PREFIX_REGEX = re.compile(r"^plt_[a-z0-9]{3,8}$")

# Characters for random part of key
KEY_CHARS = string.ascii_letters + string.digits


def _sanitize_name_for_prefix(name: str) -> str:
    """Convert principal name to valid key prefix.

    Rules:
    - Lowercase
    - Only alphanumeric
    - 3-8 chars (truncate or pad)
    """
    # Lowercase and keep only alphanumeric
    sanitized = "".join(c for c in name.lower() if c.isalnum())

    # Ensure 3-8 chars
    if len(sanitized) < 3:
        sanitized = sanitized.ljust(3, "x")
    elif len(sanitized) > 8:
        sanitized = sanitized[:8]

    return sanitized


def generate_api_key(principal_name: str, prefix_override: str | None = None) -> str:
    """Generate a new API key for a principal.

    Args:
        principal_name: Name of the principal (used to derive prefix)
        prefix_override: Optional custom prefix (3-8 chars, [a-z0-9])

    Returns:
        Full API key: plt_{prefix}_{random_32_chars}

    Example:
        generate_api_key("claude-bridge") -> "plt_claud_a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6"
    """
    if prefix_override:
        prefix = prefix_override.lower()
        if not re.match(r"^[a-z0-9]{3,8}$", prefix):
            raise ValueError("Prefix must be 3-8 alphanumeric characters")
    else:
        prefix = _sanitize_name_for_prefix(principal_name)

    # Generate 32 random alphanumeric characters
    random_part = "".join(secrets.choice(KEY_CHARS) for _ in range(32))

    return f"plt_{prefix}_{random_part}"


def extract_key_prefix(api_key: str) -> str:
    """Extract the prefix portion of an API key for logging.

    Args:
        api_key: Full API key

    Returns:
        Prefix portion: "plt_{prefix}"

    Example:
        extract_key_prefix("plt_claud_a1B2c3...") -> "plt_claud"
    """
    if not api_key.startswith("plt_"):
        return "invalid"

    parts = api_key.split("_")
    if len(parts) >= 2:
        return f"plt_{parts[1]}"
    return "invalid"


def hash_api_key(api_key: str) -> str:
    """Hash an API key using bcrypt.

    Args:
        api_key: Plain text API key

    Returns:
        bcrypt hash (string)
    """
    return bcrypt.hashpw(api_key.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_api_key(api_key: str, hashed: str) -> bool:
    """Verify an API key against its hash.

    Args:
        api_key: Plain text API key to verify
        hashed: bcrypt hash to verify against

    Returns:
        True if key matches hash
    """
    try:
        return bcrypt.checkpw(api_key.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def validate_api_key_format(api_key: str) -> bool:
    """Validate API key format.

    Args:
        api_key: API key to validate

    Returns:
        True if key matches expected format
    """
    return bool(API_KEY_REGEX.match(api_key))

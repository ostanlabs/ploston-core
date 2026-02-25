"""Principal store for Pro Auth Foundation.

Stores principal data in Redis:
- API key hashes (bcrypt)
- Last used timestamps
- Rate limit counters

Principal definitions come from config file (ael-config.yaml).
Redis stores the runtime data (key hashes, usage tracking).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .api_key import extract_key_prefix, generate_api_key, hash_api_key, verify_api_key
from .models import (
    ANONYMOUS_PRINCIPAL,
    Principal,
    PrincipalSettings,
    PrincipalType,
    Scope,
    ToolAccess,
    ToolAccessMode,
)

if TYPE_CHECKING:
    from ploston_core.config.redis_store import RedisConfigStore

logger = logging.getLogger(__name__)

# Redis key prefixes
PRINCIPAL_PREFIX = "principal"
KEY_HASH_SUFFIX = "api_key_hash"
LAST_USED_SUFFIX = "last_used_at"
BOOTSTRAPPED_SUFFIX = "bootstrapped"


class PrincipalStore:
    """Store for principal data using Redis.

    Principal definitions come from config file.
    This store manages:
    - API key hashes (stored in Redis)
    - Last used timestamps
    - Key validation and lookup
    """

    def __init__(self, redis_store: RedisConfigStore):
        """Initialize principal store.

        Args:
            redis_store: Redis config store for persistence
        """
        self._redis = redis_store
        self._principals: dict[str, Principal] = {}  # name -> Principal
        self._key_to_principal: dict[str, str] = {}  # key_hash -> principal_name (cache)

    @property
    def connected(self) -> bool:
        """Check if Redis is connected."""
        return self._redis.connected

    async def load_from_config(self, principals_config: dict[str, Any]) -> list[str]:
        """Load principals from config and bootstrap keys if needed.

        Args:
            principals_config: Dict of principal name -> config from ael-config.yaml

        Returns:
            List of newly generated API keys (for display to admin)
        """
        new_keys: list[str] = []

        for name, config in principals_config.items():
            principal = self._config_to_principal(name, config)
            self._principals[name] = principal

            # Check if key needs to be bootstrapped
            if not await self._is_bootstrapped(name):
                api_key = await self._bootstrap_key(name, principal)
                if api_key:
                    new_keys.append(f"{name}: {api_key}")

        logger.info(f"Loaded {len(self._principals)} principals from config")
        return new_keys

    def _config_to_principal(self, name: str, config: dict[str, Any]) -> Principal:
        """Convert config dict to Principal object."""
        # Parse type
        principal_type = PrincipalType(config.get("type", "service"))

        # Parse scopes
        scope_strs = config.get("scopes", ["read"])
        scopes = {Scope(s) for s in scope_strs}

        # Parse tool_access
        tool_access_config = config.get("tool_access", {})
        tool_access = ToolAccess(
            mode=ToolAccessMode(tool_access_config.get("mode", "all")),
            servers=tool_access_config.get("servers", []),
        )

        # Parse settings
        settings_config = config.get("settings", {})
        settings = PrincipalSettings(
            default_timeout=settings_config.get("default_timeout"),
            log_level=settings_config.get("log_level"),
            rate_limit=settings_config.get("rate_limit"),
            tool_name_prefix=settings_config.get("tool_name_prefix"),
        )

        # Generate ID based on type
        id_prefix = "usr" if principal_type == PrincipalType.USER else "svc"
        principal_id = f"{id_prefix}_{name}"

        return Principal(
            id=principal_id,
            name=name,
            type=principal_type,
            api_key_prefix=f"plt_{name[:5].lower()}",
            scopes=scopes,
            tool_access=tool_access,
            settings=settings,
            tags=config.get("tags", []),
            enabled=config.get("enabled", True),
        )

    async def _is_bootstrapped(self, name: str) -> bool:
        """Check if principal has been bootstrapped (has key in Redis)."""
        key = f"{PRINCIPAL_PREFIX}:{name}:{BOOTSTRAPPED_SUFFIX}"
        value = await self._redis.get_value(key)
        return value == "true"

    async def _bootstrap_key(self, name: str, principal: Principal) -> str | None:
        """Generate and store API key for a new principal.

        Returns:
            The generated API key (for display to admin), or None if failed
        """
        try:
            # Generate key
            api_key = generate_api_key(name)
            key_hash = hash_api_key(api_key)

            # Store hash in Redis
            hash_key = f"{PRINCIPAL_PREFIX}:{name}:{KEY_HASH_SUFFIX}"
            await self._redis.set_value(hash_key, key_hash)

            # Mark as bootstrapped
            bootstrap_key = f"{PRINCIPAL_PREFIX}:{name}:{BOOTSTRAPPED_SUFFIX}"
            await self._redis.set_value(bootstrap_key, "true")

            logger.info(f"[AUTH] API key generated for '{name}': {extract_key_prefix(api_key)}...")
            return api_key

        except Exception as e:
            logger.error(f"Failed to bootstrap key for {name}: {e}")
            return None

    async def validate_key(self, api_key: str) -> Principal | None:
        """Validate an API key and return the associated principal.

        Args:
            api_key: The API key to validate

        Returns:
            Principal if valid, None if invalid
        """
        # Check each principal's key hash
        for name, principal in self._principals.items():
            if not principal.enabled:
                continue

            hash_key = f"{PRINCIPAL_PREFIX}:{name}:{KEY_HASH_SUFFIX}"
            stored_hash = await self._redis.get_value(hash_key)

            if stored_hash and verify_api_key(api_key, stored_hash):
                # Update last used
                await self._update_last_used(name)
                return principal

        return None

    async def _update_last_used(self, name: str) -> None:
        """Update last_used_at timestamp for a principal."""
        try:
            key = f"{PRINCIPAL_PREFIX}:{name}:{LAST_USED_SUFFIX}"
            await self._redis.set_value(key, datetime.utcnow().isoformat())
        except Exception as e:
            logger.warning(f"Failed to update last_used for {name}: {e}")

    async def get_principal(self, name: str) -> Principal | None:
        """Get a principal by name."""
        return self._principals.get(name)

    def list_principals(self) -> list[Principal]:
        """List all principals."""
        return list(self._principals.values())

    async def rotate_key(self, name: str) -> str | None:
        """Rotate API key for a principal.

        Args:
            name: Principal name

        Returns:
            New API key, or None if failed
        """
        principal = self._principals.get(name)
        if not principal:
            return None

        try:
            # Generate new key
            api_key = generate_api_key(name)
            key_hash = hash_api_key(api_key)

            # Store new hash (overwrites old)
            hash_key = f"{PRINCIPAL_PREFIX}:{name}:{KEY_HASH_SUFFIX}"
            await self._redis.set_value(hash_key, key_hash)

            logger.info(f"[AUTH] API key rotated for '{name}'")
            return api_key

        except Exception as e:
            logger.error(f"Failed to rotate key for {name}: {e}")
            return None

    async def disable_principal(self, name: str) -> bool:
        """Disable a principal (soft delete)."""
        principal = self._principals.get(name)
        if not principal:
            return False

        principal.enabled = False
        logger.info(f"[AUTH] Principal '{name}' disabled")
        return True

    async def enable_principal(self, name: str) -> bool:
        """Enable a disabled principal."""
        principal = self._principals.get(name)
        if not principal:
            return False

        principal.enabled = True
        logger.info(f"[AUTH] Principal '{name}' enabled")
        return True

    def get_anonymous_principal(self) -> Principal:
        """Get the anonymous principal for OSS mode."""
        return ANONYMOUS_PRINCIPAL

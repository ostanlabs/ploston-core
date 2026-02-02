"""Integration tests for Redis Config Store.

These tests require a running Redis instance. They are skipped if Redis
is not available.

To run these tests:
    pytest packages/ploston-core/tests/integration/test_redis_config_store.py -v

With a local Redis:
    docker run -d --name redis-test -p 6379:6379 redis:7-alpine
"""

import asyncio
import json
import os
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

# Skip all tests if redis is not installed
pytest.importorskip("redis")


@pytest.fixture
def redis_url() -> str:
    """Get Redis URL from environment or use default."""
    return os.getenv("REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture
def test_prefix() -> str:
    """Use a unique prefix for test isolation."""
    return f"test:config:{datetime.now(UTC).timestamp()}"


@pytest.fixture
async def redis_client(redis_url: str):
    """Create a Redis client for test setup/teardown."""
    import redis.asyncio as redis

    client = redis.from_url(redis_url, decode_responses=True)
    try:
        await client.ping()
        yield client
    except Exception:
        pytest.skip("Redis not available")
    finally:
        await client.aclose()


@pytest.fixture
async def config_store(redis_url: str, test_prefix: str):
    """Create a RedisConfigStore for testing."""
    from ploston_core.config import RedisConfigStore, RedisConfigStoreOptions

    options = RedisConfigStoreOptions(
        redis_url=redis_url,
        key_prefix=test_prefix,
        channel=f"{test_prefix}:changed",
    )
    store = RedisConfigStore(options)

    connected = await store.connect()
    if not connected:
        pytest.skip("Could not connect to Redis")

    yield store

    await store.close()


@pytest.fixture
async def cleanup_keys(redis_client, test_prefix: str):
    """Clean up test keys after test."""
    yield
    # Clean up all keys with test prefix
    keys = await redis_client.keys(f"{test_prefix}:*")
    if keys:
        await redis_client.delete(*keys)


@pytest.fixture
async def require_redis(redis_url: str):
    """Skip test if Redis is not available."""
    import redis.asyncio as redis

    client = redis.from_url(redis_url, decode_responses=True)
    try:
        await client.ping()
        yield
    except Exception:
        pytest.skip("Redis not available")
    finally:
        await client.aclose()


class TestRedisConfigStoreConnection:
    """Tests for Redis connection handling."""

    @pytest.mark.asyncio
    async def test_connect_success(self, redis_url: str, test_prefix: str, require_redis):
        """Test successful connection to Redis."""
        from ploston_core.config import RedisConfigStore, RedisConfigStoreOptions

        options = RedisConfigStoreOptions(
            redis_url=redis_url,
            key_prefix=test_prefix,
        )
        store = RedisConfigStore(options)

        connected = await store.connect()
        assert connected is True
        assert store.connected is True

        await store.close()

    @pytest.mark.asyncio
    async def test_connect_failure_bad_url(self, test_prefix: str):
        """Test connection failure with bad URL."""
        from ploston_core.config import RedisConfigStore, RedisConfigStoreOptions

        options = RedisConfigStoreOptions(
            redis_url="redis://nonexistent:6379/0",
            key_prefix=test_prefix,
        )
        store = RedisConfigStore(options)

        # Should fail quickly
        connected = await store.connect()
        assert connected is False
        assert store.connected is False


class TestRedisConfigStorePublish:
    """Tests for config publishing."""

    @pytest.mark.asyncio
    async def test_publish_config(self, config_store, cleanup_keys):
        """Test publishing a config."""
        config = {
            "kafka": {"enabled": True, "bootstrap_servers": "localhost:9092"},
            "firecrawl": {"enabled": False},
        }

        success = await config_store.publish_config("test-service", config)
        assert success is True

    @pytest.mark.asyncio
    async def test_publish_increments_version(self, config_store, cleanup_keys):
        """Test that publishing increments version."""
        config1 = {"value": 1}
        config2 = {"value": 2}

        await config_store.publish_config("test-service", config1)
        payload1 = await config_store.get_config("test-service")

        await config_store.publish_config("test-service", config2)
        payload2 = await config_store.get_config("test-service")

        assert payload1 is not None
        assert payload2 is not None
        assert payload2.version == payload1.version + 1

    @pytest.mark.asyncio
    async def test_get_config_returns_payload(self, config_store, cleanup_keys):
        """Test getting config returns full payload."""
        config = {"key": "value", "nested": {"a": 1}}

        await config_store.publish_config("test-service", config)
        payload = await config_store.get_config("test-service")

        assert payload is not None
        assert payload.config == config
        assert payload.version >= 1
        assert payload.updated_by.startswith("ploston")  # May include PID
        assert payload.updated_at is not None

    @pytest.mark.asyncio
    async def test_get_nonexistent_config(self, config_store, cleanup_keys):
        """Test getting nonexistent config returns None."""
        payload = await config_store.get_config("nonexistent-service")
        assert payload is None


class TestRedisConfigStoreMode:
    """Tests for mode persistence."""

    @pytest.mark.asyncio
    async def test_set_and_get_mode(self, config_store, cleanup_keys):
        """Test setting and getting mode."""
        await config_store.set_mode("CONFIGURATION")
        mode = await config_store.get_mode()
        assert mode == "CONFIGURATION"

        await config_store.set_mode("RUNNING")
        mode = await config_store.get_mode()
        assert mode == "RUNNING"

    @pytest.mark.asyncio
    async def test_get_mode_not_set(self, redis_url: str, test_prefix: str, cleanup_keys):
        """Test getting mode when not set returns None."""
        from ploston_core.config import RedisConfigStore, RedisConfigStoreOptions

        # Use a fresh prefix to ensure mode is not set
        fresh_prefix = f"{test_prefix}:fresh"
        options = RedisConfigStoreOptions(
            redis_url=redis_url,
            key_prefix=fresh_prefix,
        )
        store = RedisConfigStore(options)
        await store.connect()

        mode = await store.get_mode()
        assert mode is None

        await store.close()


class TestRedisConfigStorePubSub:
    """Tests for pub/sub notifications."""

    @pytest.mark.asyncio
    async def test_publish_sends_notification(
        self, config_store, redis_client, test_prefix: str, cleanup_keys
    ):
        """Test that publishing sends a notification."""
        channel = f"{test_prefix}:changed"
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(channel)

        # Wait for subscription to be ready
        await asyncio.sleep(0.1)

        # Publish config
        await config_store.publish_config("test-service", {"key": "value"})

        # Wait for notification
        message = None
        for _ in range(10):
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
            if msg and msg["type"] == "message":
                message = msg
                break

        await pubsub.unsubscribe()
        await pubsub.aclose()

        assert message is not None
        data = json.loads(message["data"])
        assert data["service"] == "test-service"
        assert "version" in data
        assert "updated_at" in data  # Field is updated_at, not timestamp


class TestConfigPropagation:
    """End-to-end tests for config propagation."""

    @pytest.mark.asyncio
    async def test_config_propagation_flow(self, redis_url: str, test_prefix: str, cleanup_keys):
        """Test full config propagation from ploston to native-tools."""
        from ploston_core.config import RedisConfigStore, RedisConfigStoreOptions

        # Simulate ploston publishing config
        ploston_options = RedisConfigStoreOptions(
            redis_url=redis_url,
            key_prefix=test_prefix,
            channel=f"{test_prefix}:changed",
        )
        ploston_store = RedisConfigStore(ploston_options)
        await ploston_store.connect()

        # Publish ploston config
        ploston_config = {
            "tools": {
                "native_tools": {
                    "kafka": {"enabled": True, "bootstrap_servers": "kafka:9092"},
                    "firecrawl": {"enabled": True, "base_url": "http://firecrawl:3002"},
                }
            }
        }
        await ploston_store.publish_config("ploston", ploston_config)

        # Build and publish native-tools config
        from ploston_core.config.service_configs import build_native_tools_config

        native_config = build_native_tools_config(ploston_config)
        await ploston_store.publish_config("native-tools", native_config)

        # Verify native-tools can read the config
        native_payload = await ploston_store.get_config("native-tools")
        assert native_payload is not None
        assert native_payload.config.get("kafka", {}).get("enabled") is True
        assert native_payload.config.get("kafka", {}).get("bootstrap_servers") == "kafka:9092"

        await ploston_store.close()


class TestEnvVarResolution:
    """Tests for environment variable resolution."""

    def test_resolve_env_var_simple(self):
        """Test resolving simple env var."""
        from ploston_core.config import resolve_env_vars

        with patch.dict(os.environ, {"TEST_VAR": "test_value"}):
            result = resolve_env_vars("${TEST_VAR}")
            assert result == "test_value"

    def test_resolve_env_var_with_default(self):
        """Test resolving env var with default."""
        from ploston_core.config import resolve_env_vars

        # Unset var uses default
        result = resolve_env_vars("${UNSET_VAR:-default_value}")
        assert result == "default_value"

        # Set var ignores default
        with patch.dict(os.environ, {"SET_VAR": "actual_value"}):
            result = resolve_env_vars("${SET_VAR:-default_value}")
            assert result == "actual_value"

    def test_resolve_env_var_in_string(self):
        """Test resolving env var embedded in string."""
        from ploston_core.config import resolve_env_vars

        with patch.dict(os.environ, {"HOST": "localhost", "PORT": "9092"}):
            result = resolve_env_vars("${HOST}:${PORT}")
            assert result == "localhost:9092"

    def test_resolve_config_env_vars_recursive(self):
        """Test resolving env vars in nested config."""
        from ploston_core.config.loader import _resolve_env_vars_recursive

        with patch.dict(os.environ, {"API_KEY": "secret123", "HOST": "example.com"}):
            config = {
                "api_key": "${API_KEY}",
                "nested": {
                    "url": "https://${HOST}/api",
                    "list": ["${HOST}", "other"],
                },
            }
            result = _resolve_env_vars_recursive(config)

            assert result["api_key"] == "secret123"
            assert result["nested"]["url"] == "https://example.com/api"
            assert result["nested"]["list"] == ["example.com", "other"]


# =============================================================================
# M-049 S-145: Integration Tests for Config Distribution
# =============================================================================


class TestStartupConfigPropagation:
    """T-481: Test startup config propagation from ploston to native-tools."""

    @pytest.mark.asyncio
    async def test_native_tools_receives_config_on_startup(
        self, redis_url: str, test_prefix: str, cleanup_keys
    ):
        """Test that native-tools receives config published before it starts.

        Simulates the scenario where ploston publishes config to Redis,
        then native-tools starts and reads the existing config.
        """
        from ploston_core.config import RedisConfigStore, RedisConfigStoreOptions
        from ploston_core.config.service_configs import build_native_tools_config

        # Step 1: Ploston publishes config before native-tools starts
        ploston_options = RedisConfigStoreOptions(
            redis_url=redis_url,
            key_prefix=test_prefix,
            channel=f"{test_prefix}:changed",
        )
        ploston_store = RedisConfigStore(ploston_options)
        await ploston_store.connect()

        ploston_config = {
            "tools": {
                "native_tools": {
                    "kafka": {"enabled": True, "bootstrap_servers": "kafka:9092"},
                    "firecrawl": {"enabled": True, "base_url": "http://firecrawl:3002"},
                    "ollama": {"enabled": False},
                }
            }
        }
        await ploston_store.publish_config("ploston", ploston_config)

        # Build and publish native-tools config
        native_config = build_native_tools_config(ploston_config)
        await ploston_store.publish_config("native-tools", native_config)

        # Step 2: Native-tools starts and reads existing config
        native_options = RedisConfigStoreOptions(
            redis_url=redis_url,
            key_prefix=test_prefix,
        )
        native_store = RedisConfigStore(native_options)
        await native_store.connect()

        # Read config that was published before startup
        payload = await native_store.get_config("native-tools")

        assert payload is not None
        assert payload.config.get("kafka", {}).get("enabled") is True
        assert payload.config.get("kafka", {}).get("bootstrap_servers") == "kafka:9092"
        assert payload.config.get("firecrawl", {}).get("enabled") is True

        await ploston_store.close()
        await native_store.close()


class TestConfigChangePropagation:
    """T-482: Test config change propagation via pub/sub."""

    @pytest.mark.asyncio
    async def test_config_change_triggers_notification(
        self, redis_url: str, test_prefix: str, cleanup_keys
    ):
        """Test that config changes are propagated via pub/sub.

        Simulates the scenario where ploston updates config and
        native-tools receives the change notification.
        """
        import redis.asyncio as redis

        from ploston_core.config import RedisConfigStore, RedisConfigStoreOptions

        channel = f"{test_prefix}:changed"

        # Step 1: Native-tools subscribes to config changes
        subscriber = redis.from_url(redis_url, decode_responses=True)
        pubsub = subscriber.pubsub()
        await pubsub.subscribe(channel)

        # Wait for subscription to be ready
        await asyncio.sleep(0.1)

        # Step 2: Ploston publishes config change
        ploston_options = RedisConfigStoreOptions(
            redis_url=redis_url,
            key_prefix=test_prefix,
            channel=channel,
        )
        ploston_store = RedisConfigStore(ploston_options)
        await ploston_store.connect()

        # Publish initial config
        await ploston_store.publish_config("native-tools", {"kafka": {"enabled": False}})

        # Wait for notification
        notification1 = None
        for _ in range(10):
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
            if msg and msg["type"] == "message":
                notification1 = json.loads(msg["data"])
                break

        assert notification1 is not None
        assert notification1["service"] == "native-tools"
        version1 = notification1["version"]

        # Step 3: Ploston updates config
        await ploston_store.publish_config(
            "native-tools", {"kafka": {"enabled": True, "bootstrap_servers": "new-kafka:9092"}}
        )

        # Wait for second notification
        notification2 = None
        for _ in range(10):
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
            if msg and msg["type"] == "message":
                notification2 = json.loads(msg["data"])
                break

        assert notification2 is not None
        assert notification2["version"] == version1 + 1

        # Step 4: Native-tools reads updated config
        payload = await ploston_store.get_config("native-tools")
        assert payload is not None
        assert payload.config["kafka"]["enabled"] is True
        assert payload.config["kafka"]["bootstrap_servers"] == "new-kafka:9092"

        await pubsub.unsubscribe()
        await pubsub.aclose()
        await subscriber.aclose()
        await ploston_store.close()


class TestRedisFailureAndRecovery:
    """T-483: Test Redis failure and recovery scenarios."""

    @pytest.mark.asyncio
    async def test_graceful_handling_of_connection_failure(self, test_prefix: str):
        """Test that connection failure is handled gracefully."""
        from ploston_core.config import RedisConfigStore, RedisConfigStoreOptions

        # Try to connect to non-existent Redis
        options = RedisConfigStoreOptions(
            redis_url="redis://nonexistent-host:6379/0",
            key_prefix=test_prefix,
        )
        store = RedisConfigStore(options)

        # Should return False, not raise exception
        connected = await store.connect()
        assert connected is False
        assert store.connected is False

    @pytest.mark.asyncio
    async def test_operations_fail_gracefully_when_disconnected(
        self, redis_url: str, test_prefix: str
    ):
        """Test that operations fail gracefully when Redis is disconnected."""
        from ploston_core.config import RedisConfigStore, RedisConfigStoreOptions

        options = RedisConfigStoreOptions(
            redis_url=redis_url,
            key_prefix=test_prefix,
        )
        store = RedisConfigStore(options)

        # Don't connect - operations should handle this gracefully
        # get_config should return None when not connected
        payload = await store.get_config("test-service")
        assert payload is None

    @pytest.mark.asyncio
    async def test_reconnection_after_disconnect(
        self, redis_url: str, test_prefix: str, cleanup_keys
    ):
        """Test that store can reconnect after being disconnected."""
        from ploston_core.config import RedisConfigStore, RedisConfigStoreOptions

        options = RedisConfigStoreOptions(
            redis_url=redis_url,
            key_prefix=test_prefix,
        )
        store = RedisConfigStore(options)

        # Connect
        connected = await store.connect()
        assert connected is True

        # Publish config
        await store.publish_config("test-service", {"key": "value1"})

        # Close connection
        await store.close()
        assert store.connected is False

        # Reconnect
        connected = await store.connect()
        assert connected is True

        # Should be able to read previously published config
        payload = await store.get_config("test-service")
        assert payload is not None
        assert payload.config["key"] == "value1"

        # Should be able to publish new config
        await store.publish_config("test-service", {"key": "value2"})
        payload = await store.get_config("test-service")
        assert payload.config["key"] == "value2"

        await store.close()

    @pytest.mark.asyncio
    async def test_last_config_preserved_during_disconnect(
        self, redis_url: str, test_prefix: str, cleanup_keys
    ):
        """Test that last known config is preserved in Redis during disconnect.

        This simulates the scenario where native-tools loses connection
        but the config remains in Redis for when it reconnects.
        """
        from ploston_core.config import RedisConfigStore, RedisConfigStoreOptions

        # Ploston publishes config
        ploston_options = RedisConfigStoreOptions(
            redis_url=redis_url,
            key_prefix=test_prefix,
        )
        ploston_store = RedisConfigStore(ploston_options)
        await ploston_store.connect()

        await ploston_store.publish_config(
            "native-tools",
            {"kafka": {"enabled": True, "bootstrap_servers": "kafka:9092"}},
        )

        # Native-tools connects and reads config
        native_store = RedisConfigStore(
            RedisConfigStoreOptions(redis_url=redis_url, key_prefix=test_prefix)
        )
        await native_store.connect()

        payload1 = await native_store.get_config("native-tools")
        assert payload1 is not None
        original_version = payload1.version

        # Native-tools disconnects (simulating network issue)
        await native_store.close()

        # Ploston updates config while native-tools is disconnected
        await ploston_store.publish_config(
            "native-tools",
            {"kafka": {"enabled": True, "bootstrap_servers": "new-kafka:9092"}},
        )

        # Native-tools reconnects
        await native_store.connect()

        # Should get the updated config
        payload2 = await native_store.get_config("native-tools")
        assert payload2 is not None
        assert payload2.version == original_version + 1
        assert payload2.config["kafka"]["bootstrap_servers"] == "new-kafka:9092"

        await ploston_store.close()
        await native_store.close()

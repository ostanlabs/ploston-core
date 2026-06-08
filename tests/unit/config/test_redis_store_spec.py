"""Spec tests for RedisConfigStore.

Redis is an external boundary and is fully mocked via ``redis.asyncio`` /
``AsyncMock``. Tests assert the documented contract:

- connect(): True on success, False on failure / missing package; idempotent
  when already connected.
- publish_config(): atomic incr -> set -> publish; returns False when
  disconnected or on error.
- get_config(): parses ServiceConfigPayload; None when missing/disconnected.
- get_mode/set_mode, delete_config, list_services, scan_keys and the generic
  value methods: prefix handling, disconnected guards, error -> safe default.
- disconnect() / close alias and _sanitize_url password redaction.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ploston_core.config.redis_store import (
    ConfigChangeNotification,
    RedisConfigStore,
    RedisConfigStoreOptions,
    ServiceConfigPayload,
)


def _store(connected: bool = True) -> RedisConfigStore:
    store = RedisConfigStore(
        RedisConfigStoreOptions(
            redis_url="redis://localhost:6379/0",
            key_prefix="ploston:config",
            channel="ploston:config:changed",
            instance_id="test-instance",
        )
    )
    if connected:
        store._connected = True
        store._client = AsyncMock()
    return store


# ---------------------------------------------------------------------------
# Options + payload models
# ---------------------------------------------------------------------------


class TestOptionsAndModels:
    def test_options_env_defaults(self, monkeypatch) -> None:
        monkeypatch.setenv("REDIS_URL", "redis://h:1/2")
        monkeypatch.setenv("REDIS_CONFIG_PREFIX", "pfx")
        monkeypatch.setenv("REDIS_CONFIG_CHANNEL", "chan")
        opts = RedisConfigStoreOptions()
        assert opts.redis_url == "redis://h:1/2"
        assert opts.key_prefix == "pfx"
        assert opts.channel == "chan"

    def test_options_fallback_defaults(self, monkeypatch) -> None:
        for var in ("REDIS_URL", "REDIS_CONFIG_PREFIX", "REDIS_CONFIG_CHANNEL"):
            monkeypatch.delenv(var, raising=False)
        opts = RedisConfigStoreOptions()
        assert opts.redis_url == "redis://localhost:6379/0"
        assert opts.key_prefix == "ploston:config"
        assert opts.channel == "ploston:config:changed"

    def test_payload_requires_version_ge_1(self) -> None:
        with pytest.raises(Exception):
            ServiceConfigPayload(
                version=0,
                updated_at=datetime.now(UTC),
                updated_by="x",
                config={},
            )

    def test_payload_round_trip_json(self) -> None:
        now = datetime.now(UTC)
        payload = ServiceConfigPayload(version=3, updated_at=now, updated_by="me", config={"a": 1})
        restored = ServiceConfigPayload.model_validate_json(payload.model_dump_json())
        assert restored.version == 3
        assert restored.config == {"a": 1}

    def test_notification_default_type(self) -> None:
        n = ConfigChangeNotification(
            service="svc", version=1, updated_at=datetime.now(UTC), updated_by="me"
        )
        assert n.type == "config_updated"


# ---------------------------------------------------------------------------
# connect / disconnect
# ---------------------------------------------------------------------------


class TestConnect:
    async def test_connect_success(self, monkeypatch) -> None:
        import redis.asyncio as redis_asyncio

        store = RedisConfigStore()
        client = AsyncMock()
        monkeypatch.setattr(redis_asyncio, "from_url", MagicMock(return_value=client))
        result = await store.connect()
        assert result is True
        assert store.connected is True
        client.ping.assert_awaited_once()

    async def test_connect_idempotent_when_already_connected(self) -> None:
        store = _store(connected=True)
        # Already connected -> returns True without touching from_url.
        assert await store.connect() is True

    async def test_connect_ping_failure_returns_false(self, monkeypatch) -> None:
        import redis.asyncio as redis_asyncio

        store = RedisConfigStore()
        client = AsyncMock()
        client.ping.side_effect = ConnectionError("refused")
        monkeypatch.setattr(redis_asyncio, "from_url", MagicMock(return_value=client))
        result = await store.connect()
        assert result is False
        assert store.connected is False

    async def test_connect_missing_package_returns_false(self, monkeypatch) -> None:
        store = RedisConfigStore()
        # Force ImportError on ``import redis.asyncio``.
        monkeypatch.setitem(sys.modules, "redis", None)
        monkeypatch.setitem(sys.modules, "redis.asyncio", None)
        result = await store.connect()
        assert result is False

    async def test_disconnect_closes_client(self) -> None:
        store = _store(connected=True)
        client = store._client
        await store.disconnect()
        client.aclose.assert_awaited_once()
        assert store.connected is False
        assert store._client is None

    async def test_disconnect_swallows_close_error(self) -> None:
        store = _store(connected=True)
        store._client.aclose.side_effect = RuntimeError("boom")
        await store.disconnect()  # must not raise
        assert store._client is None
        assert store.connected is False

    async def test_disconnect_when_no_client_noop(self) -> None:
        store = _store(connected=False)
        await store.disconnect()  # no client -> no-op
        assert store.connected is False

    async def test_close_is_disconnect_alias(self) -> None:
        store = _store(connected=True)
        client = store._client
        await store.close()
        client.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# publish_config
# ---------------------------------------------------------------------------


class TestPublishConfig:
    async def test_publish_disconnected_returns_false(self) -> None:
        store = _store(connected=False)
        assert await store.publish_config("svc", {"a": 1}) is False

    async def test_publish_increments_writes_and_notifies(self) -> None:
        store = _store(connected=True)
        store._client.incr.return_value = 5
        result = await store.publish_config("native-tools", {"k": "v"})
        assert result is True
        store._client.incr.assert_awaited_once_with("ploston:config:native-tools:version")
        # set called with the config key
        set_args = store._client.set.await_args
        assert set_args.args[0] == "ploston:config:native-tools"
        # publish called on configured channel
        pub_args = store._client.publish.await_args
        assert pub_args.args[0] == "ploston:config:changed"
        # payload carries the version + config
        payload = ServiceConfigPayload.model_validate_json(set_args.args[1])
        assert payload.version == 5
        assert payload.config == {"k": "v"}
        assert payload.updated_by == "test-instance"

    async def test_publish_error_returns_false(self) -> None:
        store = _store(connected=True)
        store._client.incr.side_effect = RuntimeError("redis down")
        assert await store.publish_config("svc", {}) is False


# ---------------------------------------------------------------------------
# get_config
# ---------------------------------------------------------------------------


class TestGetConfig:
    async def test_get_disconnected_returns_none(self) -> None:
        store = _store(connected=False)
        assert await store.get_config("svc") is None

    async def test_get_returns_payload(self) -> None:
        store = _store(connected=True)
        now = datetime.now(UTC)
        payload = ServiceConfigPayload(version=2, updated_at=now, updated_by="me", config={"x": 1})
        store._client.get.return_value = payload.model_dump_json()
        result = await store.get_config("svc")
        assert result is not None
        assert result.version == 2
        assert result.config == {"x": 1}
        store._client.get.assert_awaited_once_with("ploston:config:svc")

    async def test_get_missing_returns_none(self) -> None:
        store = _store(connected=True)
        store._client.get.return_value = None
        assert await store.get_config("svc") is None

    async def test_get_invalid_json_returns_none(self) -> None:
        store = _store(connected=True)
        store._client.get.return_value = "not json"
        assert await store.get_config("svc") is None

    async def test_get_error_returns_none(self) -> None:
        store = _store(connected=True)
        store._client.get.side_effect = RuntimeError("boom")
        assert await store.get_config("svc") is None


# ---------------------------------------------------------------------------
# mode get/set
# ---------------------------------------------------------------------------


class TestMode:
    async def test_get_mode_disconnected_none(self) -> None:
        store = _store(connected=False)
        assert await store.get_mode() is None

    async def test_get_mode_reads_mode_key(self) -> None:
        store = _store(connected=True)
        store._client.get.return_value = "RUNNING"
        result = await store.get_mode()
        assert result == "RUNNING"
        # prefix "ploston:config" -> rsplit drops last segment -> "ploston:mode"
        store._client.get.assert_awaited_once_with("ploston:mode")

    async def test_get_mode_error_returns_none(self) -> None:
        store = _store(connected=True)
        store._client.get.side_effect = RuntimeError("x")
        assert await store.get_mode() is None

    async def test_set_mode_disconnected_false(self) -> None:
        store = _store(connected=False)
        assert await store.set_mode("RUNNING") is False

    async def test_set_mode_writes_and_publishes(self) -> None:
        store = _store(connected=True)
        result = await store.set_mode("CONFIGURATION")
        assert result is True
        store._client.set.assert_awaited_once_with("ploston:mode", "CONFIGURATION")
        store._client.publish.assert_awaited_once_with("ploston:mode:changed", "CONFIGURATION")

    async def test_set_mode_error_false(self) -> None:
        store = _store(connected=True)
        store._client.set.side_effect = RuntimeError("x")
        assert await store.set_mode("RUNNING") is False


# ---------------------------------------------------------------------------
# delete_config / list_services
# ---------------------------------------------------------------------------


class TestDeleteAndList:
    async def test_delete_disconnected_false(self) -> None:
        store = _store(connected=False)
        assert await store.delete_config("svc") is False

    async def test_delete_removes_both_keys(self) -> None:
        store = _store(connected=True)
        result = await store.delete_config("svc")
        assert result is True
        store._client.delete.assert_awaited_once_with(
            "ploston:config:svc", "ploston:config:svc:version"
        )

    async def test_delete_error_false(self) -> None:
        store = _store(connected=True)
        store._client.delete.side_effect = RuntimeError("x")
        assert await store.delete_config("svc") is False

    async def test_list_services_disconnected_empty(self) -> None:
        store = _store(connected=False)
        assert await store.list_services() == []

    async def test_list_services_filters_version_keys(self) -> None:
        store = _store(connected=True)

        async def scan_iter(match=None):
            for k in [
                "ploston:config:svc-a",
                "ploston:config:svc-a:version",
                "ploston:config:svc-b",
            ]:
                yield k

        store._client.scan_iter = scan_iter
        result = await store.list_services()
        assert set(result) == {"svc-a", "svc-b"}

    async def test_list_services_error_empty(self) -> None:
        store = _store(connected=True)

        def scan_iter(match=None):
            raise RuntimeError("boom")

        store._client.scan_iter = scan_iter
        assert await store.list_services() == []


# ---------------------------------------------------------------------------
# generic value methods
# ---------------------------------------------------------------------------


class TestValueMethods:
    async def test_set_value_disconnected_false(self) -> None:
        store = _store(connected=False)
        assert await store.set_value("k", "v") is False

    async def test_set_value_prefixes_key(self) -> None:
        store = _store(connected=True)
        assert await store.set_value("k", "v") is True
        store._client.set.assert_awaited_once_with("ploston:config:k", "v")

    async def test_set_value_error_false(self) -> None:
        store = _store(connected=True)
        store._client.set.side_effect = RuntimeError("x")
        assert await store.set_value("k", "v") is False

    async def test_get_value_disconnected_none(self) -> None:
        store = _store(connected=False)
        assert await store.get_value("k") is None

    async def test_get_value_prefixes_key(self) -> None:
        store = _store(connected=True)
        store._client.get.return_value = "val"
        assert await store.get_value("k") == "val"
        store._client.get.assert_awaited_once_with("ploston:config:k")

    async def test_get_value_error_none(self) -> None:
        store = _store(connected=True)
        store._client.get.side_effect = RuntimeError("x")
        assert await store.get_value("k") is None

    async def test_delete_value_disconnected_false(self) -> None:
        store = _store(connected=False)
        assert await store.delete_value("k") is False

    async def test_delete_value_prefixes_key(self) -> None:
        store = _store(connected=True)
        assert await store.delete_value("k") is True
        store._client.delete.assert_awaited_once_with("ploston:config:k")

    async def test_delete_value_error_false(self) -> None:
        store = _store(connected=True)
        store._client.delete.side_effect = RuntimeError("x")
        assert await store.delete_value("k") is False


# ---------------------------------------------------------------------------
# scan_keys (complements existing test_redis_scan_keys.py)
# ---------------------------------------------------------------------------


class TestScanKeysErrors:
    async def test_scan_keys_disconnected_empty(self) -> None:
        store = _store(connected=False)
        assert await store.scan_keys("workflows:*") == []

    async def test_scan_keys_error_returns_empty(self) -> None:
        store = _store(connected=True)

        def scan_iter(match=None):
            raise RuntimeError("boom")

        store._client.scan_iter = scan_iter
        assert await store.scan_keys("workflows:*") == []


# ---------------------------------------------------------------------------
# _sanitize_url
# ---------------------------------------------------------------------------


class TestSanitizeUrl:
    def test_redacts_password(self) -> None:
        out = RedisConfigStore._sanitize_url("redis://user:secret@host:6379/0")
        assert "secret" not in out
        assert "***" in out

    def test_no_credentials_unchanged(self) -> None:
        url = "redis://localhost:6379/0"
        assert RedisConfigStore._sanitize_url(url) == url

    def test_host_with_port_but_no_userinfo_unchanged(self) -> None:
        # No '@' -> returned as-is even though ':' present.
        url = "redis://host:6379"
        assert RedisConfigStore._sanitize_url(url) == url

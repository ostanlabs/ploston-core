"""Unit tests for RedisConfigStore.scan_keys (M-068 / S-228).

Tests that scan_keys correctly strips the key prefix and handles
disconnected state.
"""

import asyncio
from unittest.mock import MagicMock

from ploston_core.config.redis_store import RedisConfigStore, RedisConfigStoreOptions


def _make_store(connected: bool = True) -> RedisConfigStore:
    """Create a RedisConfigStore with mocked internals."""
    options = RedisConfigStoreOptions(
        redis_url="redis://localhost:6379/0",
        key_prefix="ploston:config",
    )
    store = RedisConfigStore(options)
    store._connected = connected
    if connected:
        store._client = MagicMock()
    return store


class TestScanKeys:
    """Tests for scan_keys method."""

    def test_scan_keys_strips_prefix(self):
        """scan_keys returns relative keys with prefix stripped."""
        store = _make_store(connected=True)

        async def mock_scan_iter(match=None):
            for key in [
                "ploston:config:workflows:foo",
                "ploston:config:workflows:bar",
            ]:
                yield key

        store._client.scan_iter = mock_scan_iter

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(store.scan_keys("workflows:*"))
        finally:
            loop.close()

        assert result == ["workflows:foo", "workflows:bar"]

    def test_scan_keys_empty_when_disconnected(self):
        """scan_keys returns empty list when not connected."""
        store = _make_store(connected=False)

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(store.scan_keys("workflows:*"))
        finally:
            loop.close()

        assert result == []

    def test_scan_keys_empty_on_error(self):
        """scan_keys returns empty list on Redis error."""
        store = _make_store(connected=True)

        async def mock_scan_iter(match=None):
            raise ConnectionError("Redis gone")
            yield  # make it a generator

        store._client.scan_iter = mock_scan_iter

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(store.scan_keys("workflows:*"))
        finally:
            loop.close()

        assert result == []

    def test_scan_keys_uses_correct_pattern(self):
        """scan_keys prepends the key prefix to the pattern."""
        store = _make_store(connected=True)

        captured_match = None

        async def mock_scan_iter(match=None):
            nonlocal captured_match
            captured_match = match
            return
            yield  # make it a generator

        store._client.scan_iter = mock_scan_iter

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(store.scan_keys("workflows:*"))
        finally:
            loop.close()

        assert captured_match == "ploston:config:workflows:*"

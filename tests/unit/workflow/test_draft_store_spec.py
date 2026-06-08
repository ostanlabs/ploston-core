"""Spec-coverage tests for the in-memory DraftStore (S-291 P3).

Asserts the documented contract: positive-TTL guard, put returning a
``draft-`` prefixed id, lazy TTL eviction on every access, capacity cap with
oldest-eviction, get/pop semantics, and replace_yaml preserving created_at.
The clock is monkeypatched via the documented ``_now`` indirection.
"""

from __future__ import annotations

import pytest

from ploston_core.types import ValidationResult
from ploston_core.workflow.registry import DraftEntry, DraftStore


def _vr() -> ValidationResult:
    return ValidationResult(valid=False, errors=[], warnings=[])


class TestConstruction:
    def test_rejects_nonpositive_ttl(self):
        with pytest.raises(ValueError):
            DraftStore(ttl_seconds=0)
        with pytest.raises(ValueError):
            DraftStore(ttl_seconds=-5)

    def test_ttl_property(self):
        assert DraftStore(ttl_seconds=123).ttl_seconds == 123


class TestPutGetPop:
    def test_put_returns_prefixed_id_and_stores(self):
        store = DraftStore(ttl_seconds=1800)
        did = store.put("yaml: 1", "wf", "1.0.0", _vr())
        assert did.startswith("draft-")
        entry = store.get(did)
        assert isinstance(entry, DraftEntry)
        assert entry.draft_id == did
        assert entry.yaml_content == "yaml: 1"
        assert entry.name == "wf"
        assert entry.version == "1.0.0"

    def test_put_generates_unique_ids(self):
        store = DraftStore(ttl_seconds=1800)
        ids = {store.put("y", "n", "v", _vr()) for _ in range(20)}
        assert len(ids) == 20

    def test_get_unknown_returns_none(self):
        store = DraftStore(ttl_seconds=1800)
        assert store.get("draft-nope") is None

    def test_pop_removes_entry(self):
        store = DraftStore(ttl_seconds=1800)
        did = store.put("y", "n", "v", _vr())
        popped = store.pop(did)
        assert popped is not None
        assert store.get(did) is None
        # Second pop returns None.
        assert store.pop(did) is None

    def test_len_counts_live_entries(self):
        store = DraftStore(ttl_seconds=1800)
        store.put("y", "n", "v", _vr())
        store.put("y", "n", "v", _vr())
        assert len(store) == 2


class TestTtlEviction:
    """Eviction compares ``_now() - entry.created_at`` against the TTL.

    ``_now`` is the documented monkeypatch point. ``created_at`` is stamped by
    a ``default_factory`` captured at class-definition time, so it cannot be
    moved by patching ``time``; instead we set the stored entry's
    ``created_at`` directly to simulate a known age and drive ``_now``.
    """

    def test_expired_entry_evicted_on_access(self, monkeypatch):
        store = DraftStore(ttl_seconds=10)
        did = store.put("y", "n", "v", _vr())
        store._drafts[did].created_at = 1000.0
        monkeypatch.setattr(store, "_now", lambda: 1011.0)  # age 11 > 10
        assert store.get(did) is None
        assert len(store) == 0

    def test_entry_within_ttl_survives(self, monkeypatch):
        store = DraftStore(ttl_seconds=10)
        did = store.put("y", "n", "v", _vr())
        store._drafts[did].created_at = 1000.0
        monkeypatch.setattr(store, "_now", lambda: 1009.0)  # age 9 < 10
        assert store.get(did) is not None

    def test_boundary_not_evicted_at_exact_ttl(self, monkeypatch):
        """Eviction uses strict ``>`` ttl, so exactly-at-ttl is retained."""
        store = DraftStore(ttl_seconds=10)
        did = store.put("y", "n", "v", _vr())
        store._drafts[did].created_at = 1000.0
        monkeypatch.setattr(store, "_now", lambda: 1010.0)  # age == ttl, not >
        assert store.get(did) is not None


class TestCapacity:
    def test_oldest_dropped_when_at_capacity(self, monkeypatch):
        store = DraftStore(ttl_seconds=10_000, max_size=3)
        # Stable clock so nothing TTL-expires during the test.
        monkeypatch.setattr(store, "_now", lambda: 10_000.0)

        ids = []
        for i in range(3):
            did = store.put(f"y{i}", "n", "v", _vr())
            # Ascending created_at so ids[0] is unambiguously the oldest.
            store._drafts[did].created_at = float(i)
            ids.append(did)

        # At capacity; inserting a 4th drops the oldest (ids[0]).
        new_id = store.put("y3", "n", "v", _vr())
        store._drafts[new_id].created_at = 100.0

        assert store.get(ids[0]) is None, "oldest entry should be evicted"
        assert store.get(ids[1]) is not None
        assert store.get(ids[2]) is not None
        assert store.get(new_id) is not None
        assert len(store) == 3


class TestReplaceYaml:
    def test_replace_updates_body_and_keeps_created_at(self, monkeypatch):
        store = DraftStore(ttl_seconds=10_000)
        monkeypatch.setattr(store, "_now", lambda: 10_000.0)
        did = store.put("original", "n", "v", _vr())
        store._drafts[did].created_at = 5.0
        created = store.get(did).created_at

        updated = store.replace_yaml(did, "patched")
        assert updated is not None
        assert updated.yaml_content == "patched"
        # created_at anchored to original creation (TTL not reset).
        assert store.get(did).created_at == created

    def test_replace_unknown_returns_none(self):
        store = DraftStore(ttl_seconds=10)
        assert store.replace_yaml("draft-missing", "x") is None

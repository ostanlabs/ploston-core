"""Tests for SequenceTracker, TemporalTracker, and composite scoring (Tier 4)."""

import time
from collections import deque

import pytest

from ploston_core.telemetry.chain_detector import (
    ChainDetector,
    SequenceTracker,
    TemporalTracker,
)


class TestSequenceTracker:
    def test_first_call_produces_no_pairs(self):
        t = SequenceTracker()
        result = t.record_call("s1", "tool_a")
        assert result == []

    def test_second_call_creates_pair_count_1_returned(self):
        """T-753: fire on count=1 (was threshold=2, now threshold=1)."""
        t = SequenceTracker()
        t.record_call("s1", "tool_a")
        result = t.record_call("s1", "tool_b")
        assert len(result) == 1
        assert result[0] == ("tool_a", "tool_b", 1)

    def test_pair_returned_on_second_repetition(self):
        """T-752: Window sees all prior tools, so pair (A,B) may appear
        multiple times in the result (once per A in the window)."""
        t = SequenceTracker()
        t.record_call("s1", "tool_a")
        t.record_call("s1", "tool_b")
        t.record_call("s1", "tool_a")
        result = t.record_call("s1", "tool_b")
        assert len(result) >= 1
        # At least one entry for (tool_a, tool_b) with count >= 2
        assert any(f == "tool_a" and to == "tool_b" and c >= 2 for f, to, c in result)

    def test_different_sessions_isolated(self):
        t = SequenceTracker()
        t.record_call("s1", "tool_a")
        t.record_call("s1", "tool_b")
        # s2 starts fresh
        result = t.record_call("s2", "tool_b")
        assert result == []

    def test_max_sessions_evicts_oldest_session(self):
        t = SequenceTracker(max_sessions=2)
        t.record_call("s1", "tool_a")
        t.record_call("s2", "tool_a")
        t.record_call("s3", "tool_a")  # s1 should be evicted
        assert "s1" not in t._sessions
        assert "s2" in t._sessions
        assert "s3" in t._sessions


class TestTemporalTracker:
    def test_first_call_creates_chunk(self):
        t = TemporalTracker()
        chunk_id, cooccurring = t.record_call("s1", "tool_a")
        assert chunk_id  # non-empty
        assert cooccurring == []

    def test_calls_within_30s_same_chunk(self):
        t = TemporalTracker()
        chunk1, _ = t.record_call("s1", "tool_a")
        chunk2, _ = t.record_call("s1", "tool_b")
        assert chunk1 == chunk2

    def test_call_after_30s_not_cooccurring(self):
        """T-755: Sliding window — calls outside window are evicted."""
        t = TemporalTracker()
        chunk1, _ = t.record_call("s1", "tool_a")
        # Manipulate the call_history timestamps to simulate time passing
        # Session is (chunk_id, call_history: deque[(tool, ts)], pair_counts)
        cid, call_history, pairs = t._sessions["s1"]
        # Move the only entry 31s into the past
        old_history: deque[tuple[str, float]] = deque()
        old_history.append(("tool_a", time.monotonic() - 31.0))
        t._sessions["s1"] = (cid, old_history, pairs)
        chunk2, result = t.record_call("s1", "tool_b")
        # tool_a was evicted from window, so no co-occurrence
        assert result == []
        # chunk_id is stable within a session (T-755)
        assert chunk1 == chunk2

    def test_cooccurrence_returned_on_first_occurrence(self):
        """T-755: fire on count >= 1 (was threshold=2, now threshold=1)."""
        t = TemporalTracker()
        t.record_call("s1", "tool_a")
        _chunk, result = t.record_call("s1", "tool_b")
        assert len(result) == 1  # count=1 now fires

    def test_different_sessions_isolated(self):
        t = TemporalTracker()
        t.record_call("s1", "tool_a")
        _chunk, result = t.record_call("s2", "tool_a")
        assert result == []  # different session

    def test_pair_sorted_alphabetically(self):
        t = TemporalTracker()
        t.record_call("s1", "z_tool")
        _chunk, result = t.record_call("s1", "a_tool")
        # T-755: fires on first co-occurrence now
        assert len(result) >= 1
        for from_t, to_t, _count in result:
            assert from_t <= to_t

    def test_max_sessions_evicts_oldest_session(self):
        t = TemporalTracker(max_sessions=2)
        t.record_call("s1", "tool_a")
        t.record_call("s2", "tool_a")
        t.record_call("s3", "tool_a")  # s1 evicted
        assert "s1" not in t._sessions
        assert "s2" in t._sessions
        assert "s3" in t._sessions


class TestCompositeScore:
    @pytest.mark.asyncio
    async def test_no_signal_gives_0_00(self):
        cd = ChainDetector(meter=None)
        result = await cd.process_tool_call(
            tool_name="tool_b",
            params={"data": "no_match"},
            result="output",
        )
        assert result == []
        assert ("tool_a", "tool_b", "") not in cd._composite_scores

    @pytest.mark.asyncio
    async def test_trackers_run_without_meter(self):
        """RC-9: trackers accumulate data even when meter is None."""
        cd = ChainDetector(meter=None)
        await cd.process_tool_call("tool_a", {}, "out1", session_id="s1")
        await cd.process_tool_call("tool_b", {}, "out2", session_id="s1")
        # SequenceTracker should have recorded the pair (T-752: deque window)
        recent_tools, pair_counts = cd._sequence_tracker._sessions.get("s1", (None, {}))
        assert recent_tools is not None
        assert list(recent_tools)[-1] == "tool_b"
        assert ("tool_a", "tool_b") in pair_counts

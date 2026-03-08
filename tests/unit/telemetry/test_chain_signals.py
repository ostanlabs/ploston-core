"""Tests for SequenceTracker, TemporalTracker, and composite scoring (Tier 4)."""

import time

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

    def test_second_call_creates_pair_count_1_not_returned(self):
        t = SequenceTracker()
        t.record_call("s1", "tool_a")
        result = t.record_call("s1", "tool_b")
        assert result == []  # count=1, threshold is 2

    def test_pair_returned_on_second_repetition(self):
        t = SequenceTracker()
        t.record_call("s1", "tool_a")
        t.record_call("s1", "tool_b")
        t.record_call("s1", "tool_a")
        result = t.record_call("s1", "tool_b")
        assert len(result) == 1
        assert result[0] == ("tool_a", "tool_b", 2)

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

    def test_call_after_30s_new_chunk(self):
        t = TemporalTracker()
        chunk1, _ = t.record_call("s1", "tool_a")
        # Manipulate the session tuple to simulate time passing
        # Session is (chunk_id, chunk_start, tools, pair_counts)
        cid, _start, tools, pairs = t._sessions["s1"]
        t._sessions["s1"] = (cid, time.monotonic() - 31.0, tools, pairs)
        chunk2, _ = t.record_call("s1", "tool_b")
        assert chunk1 != chunk2

    def test_cooccurrence_not_returned_until_count_2(self):
        t = TemporalTracker()
        t.record_call("s1", "tool_a")
        _chunk, result = t.record_call("s1", "tool_b")
        assert result == []  # count=1

    def test_different_sessions_isolated(self):
        t = TemporalTracker()
        t.record_call("s1", "tool_a")
        _chunk, result = t.record_call("s2", "tool_a")
        assert result == []  # different session

    def test_pair_sorted_alphabetically(self):
        t = TemporalTracker()
        t.record_call("s1", "z_tool")
        t.record_call("s1", "a_tool")
        # Force new chunk
        cid, _start, tools, pairs = t._sessions["s1"]
        t._sessions["s1"] = (cid, time.monotonic() - 31.0, tools, pairs)
        t.record_call("s1", "z_tool")
        _chunk, result = t.record_call("s1", "a_tool")
        if result:
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
        assert ("tool_a", "tool_b") not in cd._composite_scores

    @pytest.mark.asyncio
    async def test_trackers_run_without_meter(self):
        """RC-9: trackers accumulate data even when meter is None."""
        cd = ChainDetector(meter=None)
        await cd.process_tool_call("tool_a", {}, "out1", session_id="s1")
        await cd.process_tool_call("tool_b", {}, "out2", session_id="s1")
        # SequenceTracker should have recorded the pair
        last_tool, pair_counts = cd._sequence_tracker._sessions.get("s1", (None, {}))
        assert last_tool == "tool_b"
        assert ("tool_a", "tool_b") in pair_counts

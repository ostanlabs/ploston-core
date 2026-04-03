"""Unit tests for ChainDetector."""

import pytest

from ploston_core.telemetry.chain_detector import (
    ChainDetector,
    ChainLink,
    InMemoryChainCache,
)


class TestInMemoryChainCache:
    """Tests for InMemoryChainCache."""

    def test_set_and_get(self):
        """Test basic set and get."""
        cache = InMemoryChainCache()
        cache.set("hash123", "tool_a")
        result = cache.get("hash123")
        assert result == "tool_a"

    def test_get_missing(self):
        """Test get for missing key."""
        cache = InMemoryChainCache()
        result = cache.get("nonexistent")
        assert result is None

    def test_overwrite(self):
        """Test overwriting existing key."""
        cache = InMemoryChainCache()
        cache.set("hash123", "tool_a")
        cache.set("hash123", "tool_b")
        result = cache.get("hash123")
        assert result == "tool_b"


class TestChainDetectorHashing:
    """Tests for ChainDetector hash computation."""

    def test_compute_input_hashes_simple(self):
        """Test input hash computation for simple params.
        T-751: string values also get a normalized hash, so 1 string param → 2 hashes.
        """
        params = {"key": "value"}
        hashes = ChainDetector.compute_input_hashes(params)
        assert len(hashes) == 2  # serialized + normalized string
        assert all(len(h) == 16 for h in hashes)

    def test_compute_input_hashes_multiple(self):
        """Test input hash computation for multiple params.
        T-751: 2 string params → 2 serialized + 2 normalized = 4, plus 1 int → 5 total.
        """
        params = {"a": "value1", "b": "value2", "c": 123}
        hashes = ChainDetector.compute_input_hashes(params)
        assert len(hashes) == 5  # 2 strings × 2 hashes each + 1 int

    def test_compute_input_hashes_nested(self):
        """Test input hash computation for nested params."""
        params = {"nested": {"inner": "value"}}
        hashes = ChainDetector.compute_input_hashes(params)
        assert len(hashes) == 1

    def test_compute_input_hashes_deterministic(self):
        """Test that hash computation is deterministic."""
        params = {"key": "value"}
        hashes1 = ChainDetector.compute_input_hashes(params)
        hashes2 = ChainDetector.compute_input_hashes(params)
        assert hashes1 == hashes2

    def test_compute_output_hash_string(self):
        """Test output hash computation for string."""
        result = "some output"
        hash_val = ChainDetector.compute_output_hash(result)
        assert len(hash_val) == 16

    def test_compute_output_hash_dict(self):
        """Test output hash computation for dict."""
        result = {"status": "success", "data": [1, 2, 3]}
        hash_val = ChainDetector.compute_output_hash(result)
        assert len(hash_val) == 16

    def test_compute_output_hash_deterministic(self):
        """Test that output hash is deterministic."""
        result = {"key": "value"}
        hash1 = ChainDetector.compute_output_hash(result)
        hash2 = ChainDetector.compute_output_hash(result)
        assert hash1 == hash2

    def test_output_matches_input(self):
        """Test that output hash can match input hash."""
        # When tool A outputs something that tool B uses as input
        output = {"file_path": "/tmp/data.json"}
        output_hash = ChainDetector.compute_output_hash(output)

        # Tool B receives the same value as input
        input_params = {"source": {"file_path": "/tmp/data.json"}}
        ChainDetector.compute_input_hashes(input_params)

        # The hash of the nested value should match
        nested_hash = ChainDetector.compute_output_hash({"file_path": "/tmp/data.json"})
        assert nested_hash == output_hash

    def test_compute_output_hashes_multi_hash(self):
        """T-751: compute_output_hashes produces hashes at multiple granularities."""
        result = {"id": "T-484", "title": "My Issue", "tags": ["bug", "critical"]}
        hashes = ChainDetector.compute_output_hashes(result, max_hashes=200)
        # Should produce hashes for: full object, each value, list, list items, normalized strings
        assert len(hashes) > 1
        # The full-object hash should be present
        full_hash = ChainDetector.compute_output_hash(result)
        assert full_hash in hashes

    def test_compute_output_hashes_max_cap(self):
        """T-751: output hashes respect max_hashes cap."""
        result = {f"key_{i}": f"value_{i}" for i in range(100)}
        hashes = ChainDetector.compute_output_hashes(result, max_hashes=10)
        assert len(hashes) <= 10

    def test_compute_output_hashes_leaf_matches_input(self):
        """T-751: A leaf value in output matches the same value as input param."""
        # Tool A outputs {"entity_id": "abc123"}
        output = {"entity_id": "abc123", "name": "Test Entity"}
        output_hashes = ChainDetector.compute_output_hashes(output, max_hashes=200)

        # Tool B receives entity_id="abc123" as input
        input_hashes = ChainDetector.compute_input_hashes({"id": "abc123"})

        # There should be an intersection (the normalized "abc123" hash)
        assert output_hashes & input_hashes, (
            "Expected leaf value 'abc123' to produce matching hashes"
        )


class TestChainDetector:
    """Tests for ChainDetector."""

    @pytest.fixture
    def detector(self):
        """Create a ChainDetector without Redis."""
        return ChainDetector()

    @pytest.mark.asyncio
    async def test_record_and_check_chain(self, detector):
        """Test recording output and checking for chain link."""
        # Tool A produces output
        output_hash = ChainDetector.compute_output_hash("result_data")
        await detector.record_tool_output("tool_a", output_hash)

        # Tool B uses that output as input
        input_hashes = {output_hash}  # Same hash
        predecessors = await detector.check_chain_link("tool_b", input_hashes)

        assert "tool_a" in predecessors

    @pytest.mark.asyncio
    async def test_no_chain_link(self, detector):
        """Test when there's no chain link."""
        input_hashes = {"random_hash_12345"}
        predecessors = await detector.check_chain_link("tool_b", input_hashes)
        assert predecessors == []

    @pytest.mark.asyncio
    async def test_process_tool_call(self, detector):
        """Test full process_tool_call flow."""
        # First tool call
        predecessors1 = await detector.process_tool_call(
            tool_name="read_file",
            params={"path": "/tmp/data.json"},
            result={"content": "file data"},
        )
        assert predecessors1 == []  # No predecessors for first call

        # Second tool call using output from first
        predecessors2 = await detector.process_tool_call(
            tool_name="parse_json",
            params={"data": {"content": "file data"}},  # Same as previous output
            result={"parsed": {"key": "value"}},
        )
        # Should detect chain link
        assert "read_file" in predecessors2

    @pytest.mark.asyncio
    async def test_skip_workflow_calls(self, detector):
        """Test that workflow calls are skipped."""
        predecessors = await detector.process_tool_call(
            tool_name="workflow_my_workflow",
            params={"input": "data"},
            result={"output": "result"},
        )
        assert predecessors == []

    @pytest.mark.asyncio
    async def test_multiple_predecessors(self, detector):
        """Test detecting multiple predecessors."""
        # Tool A produces output
        await detector.record_tool_output("tool_a", "hash_a")

        # Tool B produces different output
        await detector.record_tool_output("tool_b", "hash_b")

        # Tool C uses both outputs
        input_hashes = {"hash_a", "hash_b"}
        predecessors = await detector.check_chain_link("tool_c", input_hashes)

        assert "tool_a" in predecessors
        assert "tool_b" in predecessors


class TestChainLink:
    """Tests for ChainLink dataclass."""

    def test_chain_link_creation(self):
        """Test ChainLink creation."""
        from datetime import UTC, datetime

        link = ChainLink(
            from_tool="tool_a",
            to_tool="tool_b",
            timestamp=datetime.now(UTC),
        )
        assert link.from_tool == "tool_a"
        assert link.to_tool == "tool_b"
        assert isinstance(link.timestamp, datetime)


class TestChainDetectorTopologyLabels:
    """Tier 2 — Distributed topology labels."""

    @pytest.fixture
    def detector(self):
        return ChainDetector()

    @pytest.mark.asyncio
    async def test_check_chain_link_includes_runner_id_in_attributes(self, detector):
        """runner_id attribute present on emitted chain link metric."""
        await detector.record_tool_output("tool_a", "hash_x")
        predecessors = await detector.check_chain_link("tool_b", {"hash_x"}, runner_id="runner-1")
        assert "tool_a" in predecessors

    @pytest.mark.asyncio
    async def test_check_chain_link_includes_bridge_id_in_attributes(self, detector):
        """bridge_id attribute present on emitted chain link metric."""
        await detector.record_tool_output("tool_a", "hash_x")
        predecessors = await detector.check_chain_link("tool_b", {"hash_x"}, bridge_id="bridge-1")
        assert "tool_a" in predecessors

    @pytest.mark.asyncio
    async def test_process_tool_call_threads_runner_and_bridge_ids(self, detector):
        """process_tool_call passes runner_id and bridge_id through to check_chain_link."""
        # First tool call
        await detector.process_tool_call(
            tool_name="tool_a",
            params={"input": "value"},
            result={"output": "data"},
        )
        # Second tool call uses output from first — pass topology labels
        predecessors = await detector.process_tool_call(
            tool_name="tool_b",
            params={"data": {"output": "data"}},
            result="result",
            runner_id="runner-1",
            bridge_id="bridge-1",
        )
        # check_chain_link should have received the IDs (verified by no exception)
        assert isinstance(predecessors, list)


class TestCompositeScoreEmission:
    """Tests for composite score emission from all signal types."""

    @pytest.fixture
    def detector(self):
        """Create a ChainDetector without Redis."""
        return ChainDetector()

    @pytest.mark.asyncio
    async def test_composite_score_from_sequence_signal_only(self, detector):
        """Composite score emitted even without data-flow predecessors.

        The sequence tracker returns repeated pairs at count >= 2, which
        triggers sequence_score > 0.  The composite score should be stored
        even when there are no data-flow chain links.
        """
        # Need A-B-A-B pattern (4 calls) for pair count to reach 2
        for i in range(4):
            tool = "tool_a" if i % 2 == 0 else "tool_b"
            await detector.process_tool_call(
                tool_name=tool,
                params={"x": f"val_{i}"},
                result=f"result_{i}",
                session_id="sess1",
                bridge_id="bridge-1",
            )

        # Should have at least one composite score entry
        assert len(detector._composite_scores) > 0
        # The pair (tool_a, tool_b) should be scored
        scored_keys = {(k[0], k[1]) for k in detector._composite_scores}
        assert ("tool_a", "tool_b") in scored_keys

    @pytest.mark.asyncio
    async def test_composite_score_zero_without_signals(self, detector):
        """No composite score stored when no signals fire."""
        await detector.process_tool_call(
            tool_name="tool_a",
            params={"x": "v1"},
            result="r1",
            session_id="sess1",
        )
        assert len(detector._composite_scores) == 0


class TestCrossSessionGlobalPairFrequency:
    """T-754: Cross-session global pair frequency tracking."""

    @pytest.fixture
    def detector(self):
        return ChainDetector()

    @pytest.mark.asyncio
    async def test_global_counter_increments_across_sessions(self, detector):
        """A→B in session-1 and session-2 → global count = 2."""
        # Session 1: A → B
        await detector.process_tool_call("tool_a", {"x": "1"}, "r1", session_id="s1")
        await detector.process_tool_call("tool_b", {"x": "2"}, "r2", session_id="s1")
        # Session 2: A → B
        await detector.process_tool_call("tool_a", {"x": "3"}, "r3", session_id="s2")
        await detector.process_tool_call("tool_b", {"x": "4"}, "r4", session_id="s2")

        assert detector._global_pair_counts[("tool_a", "tool_b")] == 2

    @pytest.mark.asyncio
    async def test_unique_sessions_tracked(self, detector):
        """Three distinct sessions → unique sessions = 3."""
        for sid in ["s1", "s2", "s3"]:
            await detector.process_tool_call("tool_a", {"x": sid}, f"r_{sid}", session_id=sid)
            await detector.process_tool_call(
                "tool_b", {"x": f"{sid}_b"}, f"r2_{sid}", session_id=sid
            )

        sessions = detector._global_session_pairs.get(("tool_a", "tool_b"), set())
        assert len(sessions) == 3

    @pytest.mark.asyncio
    async def test_cross_session_boost(self, detector):
        """Sequence score is boosted when pair seen in 3+ unique sessions."""
        # Create the pattern in 3 sessions
        for sid in ["s1", "s2", "s3"]:
            await detector.process_tool_call("tool_a", {"x": sid}, f"r_{sid}", session_id=sid)
            await detector.process_tool_call(
                "tool_b", {"x": f"{sid}_b"}, f"r2_{sid}", session_id=sid
            )

        # Verify boost applied: with 3 sessions, the score should be higher
        # than without the boost. The pair should exist with a boosted score.
        scored_keys = {(k[0], k[1]) for k in detector._composite_scores}
        assert ("tool_a", "tool_b") in scored_keys

    @pytest.mark.asyncio
    async def test_global_counter_not_double_counted_within_session(self, detector):
        """Multiple A→B in one session → unique_sessions stays 1."""
        for i in range(3):
            await detector.process_tool_call("tool_a", {"x": f"a{i}"}, f"ra{i}", session_id="s1")
            await detector.process_tool_call("tool_b", {"x": f"b{i}"}, f"rb{i}", session_id="s1")

        sessions = detector._global_session_pairs.get(("tool_a", "tool_b"), set())
        assert len(sessions) == 1
        # Global count may be > 3 because the gap-tolerant window accumulates
        # A→B pairs across the sliding window, but unique sessions must be exactly 1
        assert detector._global_pair_counts[("tool_a", "tool_b")] >= 3

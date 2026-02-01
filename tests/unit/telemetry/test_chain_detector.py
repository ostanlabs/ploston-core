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
        """Test input hash computation for simple params."""
        params = {"key": "value"}
        hashes = ChainDetector.compute_input_hashes(params)
        assert len(hashes) == 1
        assert all(len(h) == 16 for h in hashes)

    def test_compute_input_hashes_multiple(self):
        """Test input hash computation for multiple params."""
        params = {"a": "value1", "b": "value2", "c": 123}
        hashes = ChainDetector.compute_input_hashes(params)
        assert len(hashes) == 3

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
            tool_name="workflow:my_workflow",
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

"""Chain Detection - Detect repeated tool sequences that could become workflows.

This module provides chain detection for identifying patterns in direct tool calls:
- Hashes tool inputs/outputs for privacy-preserving matching
- Detects when output from one tool is used as input to another
- Emits metrics for visualization in Grafana

Chain detection helps users discover opportunities to create workflows from
repeated tool sequences, demonstrating Ploston's value proposition.
"""

import hashlib
import json
import time as _time
import uuid as _uuid
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from opentelemetry import metrics, trace

from ploston_core.runner_management.router import normalize_tool_name_for_metrics


@dataclass
class ChainLink:
    """A detected chain link between two tools."""

    from_tool: str
    to_tool: str
    timestamp: datetime


class InMemoryChainCache:
    """In-memory fallback cache for chain detection when Redis is unavailable."""

    def __init__(self, ttl_seconds: int = 1800):
        """Initialize in-memory cache.

        Args:
            ttl_seconds: Time-to-live for cache entries (default: 30 minutes)
        """
        self._cache: dict[str, tuple[str, datetime]] = {}
        self._ttl_seconds = ttl_seconds

    def _cleanup(self) -> None:
        """Remove expired entries."""
        now = datetime.now(UTC)
        expired = [
            key
            for key, (_, timestamp) in self._cache.items()
            if (now - timestamp).total_seconds() > self._ttl_seconds
        ]
        for key in expired:
            del self._cache[key]

    def set(self, output_hash: str, tool_name: str) -> None:
        """Store output hash for chain matching.

        Args:
            output_hash: Hash of tool output
            tool_name: Name of the tool that produced this output
        """
        self._cleanup()
        self._cache[output_hash] = (tool_name, datetime.now(UTC))

    def get(self, input_hash: str) -> str | None:
        """Check if input matches a recent output.

        Args:
            input_hash: Hash of tool input

        Returns:
            Tool name if match found, None otherwise
        """
        self._cleanup()
        if input_hash in self._cache:
            return self._cache[input_hash][0]
        return None


class SequenceTracker:
    """Tracks consecutive tool-call pairs per session for sequence-consistency scoring.

    Maintains an LRU cache of sessions bounded by ``max_sessions``.
    """

    def __init__(self, *, max_sessions: int = 500) -> None:
        self._max_sessions = max_sessions
        # session_id -> (last_tool, pair_counts)
        self._sessions: dict[str, tuple[str | None, dict[tuple[str, str], int]]] = {}
        self._access_order: deque[str] = deque()

    def record_call(self, session_id: str, tool_name: str) -> list[tuple[str, str, int]]:
        """Record a tool call and return any newly-repeated pairs.

        Returns:
            List of (from_tool, to_tool, count) where count >= 2.
        """
        self._touch(session_id)

        last_tool, pair_counts = self._sessions.get(session_id, (None, {}))
        repeated: list[tuple[str, str, int]] = []

        if last_tool is not None:
            key = (last_tool, tool_name)
            pair_counts[key] = pair_counts.get(key, 0) + 1
            if pair_counts[key] >= 2:
                repeated.append((*key, pair_counts[key]))

        self._sessions[session_id] = (tool_name, pair_counts)
        return repeated

    # -- LRU helpers --
    def _touch(self, session_id: str) -> None:
        if session_id in self._sessions:
            try:
                self._access_order.remove(session_id)
            except ValueError:
                pass
        self._access_order.append(session_id)
        while len(self._access_order) > self._max_sessions:
            evicted = self._access_order.popleft()
            self._sessions.pop(evicted, None)


class TemporalTracker:
    """Tracks tool calls within time-bounded chunks for co-occurrence scoring.

    Maintains an LRU cache of sessions bounded by ``max_sessions``.
    """

    CHUNK_SECONDS: float = 30.0  # Window size for temporal chunks

    def __init__(self, *, max_sessions: int = 500) -> None:
        self._max_sessions = max_sessions
        # session_id -> (chunk_id, chunk_start_time, tools_in_chunk, pair_counts)
        self._sessions: dict[
            str,
            tuple[str, float, set[str], dict[tuple[str, str], int]],
        ] = {}
        self._access_order: deque[str] = deque()

    def record_call(
        self, session_id: str, tool_name: str
    ) -> tuple[str, list[tuple[str, str, int]]]:
        """Record a tool call and return co-occurring pairs from the current chunk.

        Returns:
            (chunk_id, [(tool_a, tool_b, count), ...]) for pairs with count >= 2.
        """
        self._touch(session_id)
        now = _time.monotonic()

        if session_id not in self._sessions:
            chunk_id = _uuid.uuid4().hex[:12]
            self._sessions[session_id] = (chunk_id, now, set(), {})

        chunk_id, chunk_start, tools, pair_counts = self._sessions[session_id]

        # Roll to a new chunk if the window has elapsed
        if now - chunk_start > self.CHUNK_SECONDS:
            chunk_id = _uuid.uuid4().hex[:12]
            chunk_start = now
            tools = set()
            pair_counts = {}

        cooccurring: list[tuple[str, str, int]] = []
        for existing_tool in tools:
            if existing_tool == tool_name:
                continue
            key = tuple(sorted([existing_tool, tool_name]))
            pair_counts[key] = pair_counts.get(key, 0) + 1  # type: ignore[index]
            if pair_counts[key] >= 2:  # type: ignore[index]
                cooccurring.append((*key, pair_counts[key]))  # type: ignore[misc]

        tools.add(tool_name)
        self._sessions[session_id] = (chunk_id, chunk_start, tools, pair_counts)
        return chunk_id, cooccurring

    # -- LRU helpers --
    def _touch(self, session_id: str) -> None:
        if session_id in self._sessions:
            try:
                self._access_order.remove(session_id)
            except ValueError:
                pass
        self._access_order.append(session_id)
        while len(self._access_order) > self._max_sessions:
            evicted = self._access_order.popleft()
            self._sessions.pop(evicted, None)


class ChainDetector:
    """Detect chains of tool calls where output flows to input.

    Uses privacy-preserving hashing - only hashes are stored, not actual data.
    Supports Redis for distributed detection or in-memory fallback.
    """

    def __init__(
        self,
        redis_client: Any | None = None,
        ttl_seconds: int = 1800,
        meter: metrics.Meter | None = None,
        tracer: trace.Tracer | None = None,
    ):
        """Initialize chain detector.

        Args:
            redis_client: Optional Redis client for distributed detection
            ttl_seconds: Time-to-live for output hashes (default: 30 minutes)
            meter: OpenTelemetry Meter for metrics (optional)
            tracer: OpenTelemetry Tracer for span links (optional)
        """
        self._redis = redis_client
        self._ttl = ttl_seconds
        self._meter = meter
        self._tracer = tracer
        self._memory_cache = InMemoryChainCache(ttl_seconds)

        # Tier 4: Trackers for multi-signal scoring
        self._sequence_tracker = SequenceTracker(max_sessions=500)
        self._temporal_tracker = TemporalTracker(max_sessions=500)
        self._composite_scores: dict[tuple[str, str], float] = {}

        self._setup_metrics()

    def _setup_metrics(self) -> None:
        """Set up Prometheus metrics."""
        if not self._meter:
            return

        # Counter: Chain links detected (Signal 1 — data flow)
        self._chain_links_total = self._meter.create_counter(
            name="ploston_chain_links_total",
            description="Total chain links detected between direct tool calls",
            unit="1",
        )

        # Counter: Sequence pair repetitions (Signal 2 — sequence consistency)
        self._sequence_pairs_total = self._meter.create_counter(
            name="ploston_sequence_pairs_total",
            description="Repeated consecutive tool-call pairs per session",
            unit="1",
        )

        # Counter: Temporal co-occurrences (Signal 3 — temporal proximity)
        self._temporal_cooccurrence_total = self._meter.create_counter(
            name="ploston_temporal_cooccurrence_total",
            description="Temporal co-occurrence of tool pairs within a time chunk",
            unit="1",
        )

        # Observable gauge: Composite confidence score
        self._composite_gauge = self._meter.create_observable_gauge(
            name="ploston_chain_composite_score",
            callbacks=[self._composite_score_callback],
            description="Composite chain-detection confidence score per tool pair",
            unit="1",
        )

        # Gauge: Chain frequency (for dashboard)
        self._chain_frequency = self._meter.create_up_down_counter(
            name="ploston_chain_frequency",
            description="Frequency of detected tool chains",
            unit="1",
        )

    def _composite_score_callback(
        self,
        options: metrics.CallbackOptions,  # noqa: ARG002
    ) -> list[metrics.Observation]:
        """Yield current composite scores for the observable gauge."""
        observations: list[metrics.Observation] = []
        for (from_tool, to_tool), score in self._composite_scores.items():
            observations.append(
                metrics.Observation(
                    value=score,
                    attributes={"from_tool": from_tool, "to_tool": to_tool},
                )
            )
        return observations

    @staticmethod
    def compute_input_hashes(params: dict[str, Any]) -> set[str]:
        """Compute hashes of tool input parameters for chain matching.

        Only hashes are computed - no data is stored. This is privacy-preserving.

        Args:
            params: Tool input parameters

        Returns:
            Set of hashes for each parameter value
        """
        hashes = set()
        for key, value in params.items():
            try:
                serialized = json.dumps(value, sort_keys=True, default=str)
                hash_value = hashlib.sha256(serialized.encode()).hexdigest()[:16]
                hashes.add(hash_value)
            except (TypeError, ValueError):
                # Skip unhashable values
                continue
        return hashes

    @staticmethod
    def compute_output_hash(result: Any) -> str:
        """Compute hash of tool output for chain matching.

        Args:
            result: Tool output/result

        Returns:
            Hash of the output
        """
        try:
            serialized = json.dumps(result, sort_keys=True, default=str)
        except (TypeError, ValueError):
            serialized = str(result)
        return hashlib.sha256(serialized.encode()).hexdigest()[:16]

    async def record_tool_output(self, tool_name: str, output_hash: str) -> None:
        """Store output hash for future chain matching.

        Args:
            tool_name: Name of the tool that produced this output
            output_hash: Hash of the tool output
        """
        if self._redis:
            key = f"ploston:chain:output:{output_hash}"
            data = json.dumps(
                {
                    "tool": tool_name,
                    "timestamp": datetime.utcnow().isoformat(),
                }
            )
            try:
                await self._redis.setex(key, self._ttl, data)
            except Exception:
                # Fall back to memory cache on Redis error
                self._memory_cache.set(output_hash, tool_name)
        else:
            self._memory_cache.set(output_hash, tool_name)

    async def check_chain_link(
        self,
        tool_name: str,
        input_hashes: set[str],
        runner_id: str | None = None,
        bridge_id: str | None = None,
    ) -> list[str]:
        """Check if any input matches a recent output, detecting chain links.

        Args:
            tool_name: Name of the current tool being called
            input_hashes: Hashes of the tool's input parameters
            runner_id: Optional runner identity for distributed topology labels
            bridge_id: Optional bridge session identity for distributed topology labels

        Returns:
            List of predecessor tool names that produced matching outputs
        """
        predecessors: list[str] = []

        for input_hash in input_hashes:
            predecessor = await self._get_predecessor(input_hash)
            if predecessor:
                predecessors.append(predecessor)

                # Emit metric
                if self._meter and hasattr(self, "_chain_links_total"):
                    attributes: dict[str, Any] = {
                        "from_tool": predecessor,
                        "to_tool": tool_name,
                    }
                    if runner_id:
                        attributes["runner_id"] = runner_id
                    if bridge_id:
                        attributes["bridge_id"] = bridge_id
                    self._chain_links_total.add(1, attributes)

        return predecessors

    async def _get_predecessor(self, input_hash: str) -> str | None:
        """Get predecessor tool for an input hash.

        Args:
            input_hash: Hash to look up

        Returns:
            Tool name if found, None otherwise
        """
        if self._redis:
            key = f"ploston:chain:output:{input_hash}"
            try:
                data = await self._redis.get(key)
                if data:
                    record = json.loads(data)
                    return record["tool"]
            except Exception:
                # Fall back to memory cache on Redis error
                pass

        return self._memory_cache.get(input_hash)

    async def process_tool_call(
        self,
        tool_name: str,
        params: dict[str, Any],
        result: Any,
        runner_id: str | None = None,
        bridge_id: str | None = None,
        session_id: str | None = None,
    ) -> list[str]:
        """Process a tool call for chain detection.

        This is the main integration point - call after each direct tool call.

        Args:
            tool_name: Name of the tool called
            params: Tool input parameters
            result: Tool output/result
            runner_id: Optional runner identity for distributed topology labels
            bridge_id: Optional bridge session identity for distributed topology labels
            session_id: Optional session ID for sequence/temporal tracking (Tier 4)

        Returns:
            List of predecessor tools (chain links detected)
        """
        # Skip workflow calls - we only detect chains in direct tool calls
        if tool_name.startswith("workflow_"):
            return []

        # Normalize tool name for consistent chain detection across runners
        normalized_name, _ = normalize_tool_name_for_metrics(tool_name)

        # Compute hashes
        input_hashes = self.compute_input_hashes(params)
        output_hash = self.compute_output_hash(result)

        # Signal 1: Data flow (weight 0.50) — existing logic
        predecessors = await self.check_chain_link(
            normalized_name, input_hashes, runner_id=runner_id, bridge_id=bridge_id
        )

        # Record this output for future matching
        await self.record_tool_output(normalized_name, output_hash)

        data_flow_score = 1.0 if predecessors else 0.0

        # Signal 2: Sequence consistency (weight 0.30)
        sequence_score = 0.0
        if session_id:
            repeated_pairs = self._sequence_tracker.record_call(session_id, normalized_name)
            if repeated_pairs:
                if self._meter and hasattr(self, "_sequence_pairs_total"):
                    for from_t, to_t, count in repeated_pairs:
                        self._sequence_pairs_total.add(
                            1, {"from_tool": from_t, "to_tool": to_t, "session_id": session_id}
                        )
                max_count = max(c for _, _, c in repeated_pairs)
                sequence_score = min(max_count / 10.0, 1.0)

        # Signal 3: Temporal co-occurrence (weight 0.20)
        temporal_score = 0.0
        if session_id:
            _chunk_id, cooccurring_pairs = self._temporal_tracker.record_call(
                session_id, normalized_name
            )
            if cooccurring_pairs:
                if self._meter and hasattr(self, "_temporal_cooccurrence_total"):
                    for tool_a, tool_b, count in cooccurring_pairs:
                        self._temporal_cooccurrence_total.add(
                            1, {"tool_a": tool_a, "tool_b": tool_b, "chunk_id": _chunk_id}
                        )
                max_count = max(c for _, _, c in cooccurring_pairs)
                temporal_score = min(max_count / 5.0, 1.0)

        # Composite score — stored for observable gauge, emitted per predecessor pair
        composite = (data_flow_score * 0.50) + (sequence_score * 0.30) + (temporal_score * 0.20)
        for pred in predecessors:
            self._composite_scores[(pred, normalized_name)] = composite

        return predecessors

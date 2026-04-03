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
import logging
import math
import time as _time
import uuid as _uuid
from collections import Counter, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from opentelemetry import metrics, trace

from ploston_core.runner_management.router import normalize_tool_name_for_metrics

logger = logging.getLogger("ploston.chain_detection")


@dataclass
class ChainDetectorConfig:
    """Configuration for ChainDetector tunables.

    Defaults match DEC-057. All parameters are operator-configurable.
    """

    # Signal weights (must sum to 1.0) — defaults per DEC-057
    data_flow_weight: float = 0.50
    sequence_weight: float = 0.30
    temporal_weight: float = 0.20

    # T-752 / T-753
    sequence_window_size: int = 3
    log_score_scale: float = 30.0

    # T-755
    temporal_window_seconds: float = 30.0
    max_temporal_pairs_per_session: int = 500

    # T-751
    max_output_hashes: int = 200

    # T-756
    min_composite_threshold: float = 0.05
    score_ttl_minutes: float = 60.0


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
    """Tracks tool-call pairs within a sliding window per session.

    Uses a deque of recent tools (window_size) instead of only the last tool,
    allowing gap-tolerant detection of patterns like A, X, B, A, X, B.
    Self-pairs (A→A) are intentionally excluded — see T-752 spec rationale.
    Maintains an LRU cache of sessions bounded by ``max_sessions``.
    """

    WINDOW_SIZE: int = 3  # Default; overridden by ChainDetectorConfig.sequence_window_size

    def __init__(self, *, max_sessions: int = 500, window_size: int = 3) -> None:
        self._max_sessions = max_sessions
        self._window_size = window_size
        # session_id -> (recent_tools: deque[str], pair_counts: dict)
        self._sessions: dict[str, tuple[deque[str], dict[tuple[str, str], int]]] = {}
        self._access_order: deque[str] = deque()

    def record_call(self, session_id: str, tool_name: str) -> list[tuple[str, str, int]]:
        """Record a tool call and return any repeated pairs.

        Checks all tools in the recent window (not just the immediate predecessor).
        Self-pairs are excluded.

        Returns:
            List of (from_tool, to_tool, count) where count >= 2.
        """
        self._touch(session_id)

        recent_tools, pair_counts = self._sessions.get(
            session_id, (deque(maxlen=self._window_size), {})
        )
        repeated: list[tuple[str, str, int]] = []

        # Check all tools in the recent window
        for prior_tool in recent_tools:
            if prior_tool == tool_name:
                continue  # Intentionally excludes self-loops — see T-752 spec rationale
            key = (prior_tool, tool_name)
            pair_counts[key] = pair_counts.get(key, 0) + 1
            if pair_counts[key] >= 1:  # T-753: fire on first occurrence
                repeated.append((*key, pair_counts[key]))

        recent_tools.append(tool_name)
        self._sessions[session_id] = (recent_tools, pair_counts)
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
    """Tracks tool calls within a sliding time window for co-occurrence scoring.

    Uses a timestamped ring buffer per session instead of a tumbling window,
    eliminating boundary-straddling loss. Self-pairs are excluded.
    Pair counts are capped at ``max_pairs_per_session`` with LFU eviction.
    Maintains an LRU cache of sessions bounded by ``max_sessions``.
    """

    WINDOW_SECONDS: float = 30.0
    MAX_PAIRS_PER_SESSION: int = 500

    def __init__(
        self,
        *,
        max_sessions: int = 500,
        window_seconds: float = 30.0,
        max_pairs_per_session: int = 500,
    ) -> None:
        self._max_sessions = max_sessions
        self._window_seconds = window_seconds
        self._max_pairs_per_session = max_pairs_per_session
        # session_id -> (chunk_id, call_history: deque[(tool, ts)], pair_counts)
        self._sessions: dict[
            str,
            tuple[str, deque[tuple[str, float]], dict[tuple[str, str], int]],
        ] = {}
        self._access_order: deque[str] = deque()

    def record_call(
        self, session_id: str, tool_name: str
    ) -> tuple[str, list[tuple[str, str, int]]]:
        """Record a tool call and return co-occurring pairs from the sliding window.

        Returns:
            (chunk_id, [(tool_a, tool_b, count), ...]) for pairs with count >= 1.
        """
        self._touch(session_id)
        now = _time.monotonic()

        if session_id not in self._sessions:
            chunk_id = _uuid.uuid4().hex[:12]
            self._sessions[session_id] = (chunk_id, deque(), {})

        chunk_id, call_history, pair_counts = self._sessions[session_id]

        # Evict calls outside the sliding window
        while call_history and now - call_history[0][1] > self._window_seconds:
            call_history.popleft()

        # All remaining calls in the window co-occur with the current call
        cooccurring: list[tuple[str, str, int]] = []
        seen_tools_in_window: set[str] = {t for t, _ in call_history}

        for prior_tool in seen_tools_in_window:
            if prior_tool == tool_name:
                continue
            key = tuple(sorted([prior_tool, tool_name]))
            pair_counts[key] = pair_counts.get(key, 0) + 1  # type: ignore[index]

            # Bound pair_counts to prevent unbounded memory growth (T-755)
            if len(pair_counts) > self._max_pairs_per_session:
                min_key = min(pair_counts, key=lambda k: pair_counts[k])
                del pair_counts[min_key]

            if pair_counts.get(key, 0) >= 1:  # T-755: fire on count >= 1
                cooccurring.append((*key, pair_counts[key]))  # type: ignore[misc]

        call_history.append((tool_name, now))
        self._sessions[session_id] = (chunk_id, call_history, pair_counts)
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
        config: ChainDetectorConfig | None = None,
    ):
        """Initialize chain detector.

        Args:
            redis_client: Optional Redis client for distributed detection
            ttl_seconds: Time-to-live for output hashes (default: 30 minutes)
            meter: OpenTelemetry Meter for metrics (optional)
            tracer: OpenTelemetry Tracer for span links (optional)
            config: Tunable parameters (defaults per DEC-057)
        """
        self._config = config or ChainDetectorConfig()
        self._redis = redis_client
        self._ttl = ttl_seconds
        self._meter = meter
        self._tracer = tracer
        self._memory_cache = InMemoryChainCache(ttl_seconds)

        # Tier 4: Trackers for multi-signal scoring
        self._sequence_tracker = SequenceTracker(
            max_sessions=500, window_size=self._config.sequence_window_size
        )
        self._temporal_tracker = TemporalTracker(
            max_sessions=500,
            window_seconds=self._config.temporal_window_seconds,
            max_pairs_per_session=self._config.max_temporal_pairs_per_session,
        )
        # Key: (from_tool, to_tool, bridge_id) → score (max-over-time)
        self._composite_scores: dict[tuple[str, str, str], float] = {}
        # Key: (from_tool, to_tool, bridge_id) → last-updated monotonic timestamp
        self._composite_score_timestamps: dict[tuple[str, str, str], float] = {}

        # T-754: Cross-session global pair frequency
        self._global_pair_counts: Counter[tuple[str, str]] = Counter()
        self._global_session_pairs: dict[tuple[str, str], set[str]] = {}  # pair → unique sessions

        logger.info(
            "ChainDetector initialized: meter=%s, redis=%s, ttl=%ds",
            "present" if meter else "NONE",
            "connected" if redis_client else "in-memory",
            ttl_seconds,
        )

        self._setup_metrics()

    def _setup_metrics(self) -> None:
        """Set up Prometheus metrics."""
        if not self._meter:
            logger.warning(
                "ChainDetector._setup_metrics: No meter — metrics will NOT be registered"
            )
            return

        logger.info("ChainDetector._setup_metrics: Registering chain detection metrics")

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

        # UpDownCounter: Chain frequency — tracks active chain count
        self._chain_frequency = self._meter.create_up_down_counter(
            name="ploston_chain_frequency",
            description="Frequency of detected tool chains",
            unit="1",
        )
        # Track which (from, to, bid) keys we've already counted for chain_frequency
        # so we increment on first discovery and don't double-count.
        self._chain_frequency_seen: set[tuple[str, str, str]] = set()

        # T-754: Cross-session global pair frequency
        self._global_sequence_pairs_total = self._meter.create_counter(
            name="ploston_global_sequence_pairs_total",
            description="Cross-session count of repeated tool pair sequences",
            unit="1",
        )
        self._pair_unique_sessions_gauge = self._meter.create_observable_gauge(
            name="ploston_pair_unique_sessions",
            callbacks=[self._pair_sessions_callback],
            description="Number of unique sessions that observed each tool pair",
            unit="1",
        )

        logger.info(
            "ChainDetector._setup_metrics: All 7 metrics registered "
            "(chain_links_total, sequence_pairs_total, temporal_cooccurrence_total, "
            "chain_composite_score, chain_frequency, global_sequence_pairs_total, "
            "pair_unique_sessions)"
        )

    def _composite_score_callback(
        self,
        options: metrics.CallbackOptions,  # noqa: ARG002
    ) -> list[metrics.Observation]:
        """Yield current composite scores for the observable gauge.

        Called by the OTEL SDK during Prometheus scrape. Must be safe against
        concurrent dict modification from ``process_tool_call``.
        """
        try:
            # TTL eviction — remove stale entries before yielding (T-756)
            ttl_seconds = self._config.score_ttl_minutes * 60
            now = _time.monotonic()
            stale_keys = [
                k for k, ts in self._composite_score_timestamps.items() if now - ts > ttl_seconds
            ]
            for k in stale_keys:
                self._composite_scores.pop(k, None)
                self._composite_score_timestamps.pop(k, None)

            # Snapshot to avoid RuntimeError from concurrent dict mutation
            snapshot = dict(self._composite_scores)
            observations: list[metrics.Observation] = []

            if not snapshot:
                logger.info(
                    "composite_score_callback: no scored pairs stored, returning empty observations"
                )
            else:
                for (from_tool, to_tool, bid), score in snapshot.items():
                    if score < self._config.min_composite_threshold:
                        continue  # T-756: filter noise below threshold
                    attrs: dict[str, Any] = {"from_tool": from_tool, "to_tool": to_tool}
                    if bid:
                        attrs["bridge_id"] = bid
                    observations.append(metrics.Observation(value=score, attributes=attrs))
                logger.info(
                    "composite_score_callback: yielding %d observations "
                    "from %d stored pairs (after threshold filter), top_score=%.3f",
                    len(observations),
                    len(snapshot),
                    max(snapshot.values()) if snapshot else 0.0,
                )

            return observations
        except Exception:
            logger.exception(
                "composite_score_callback: EXCEPTION during callback — "
                "returning empty observations (metric will be missing from scrape)"
            )
            return []

    def _pair_sessions_callback(
        self,
        options: metrics.CallbackOptions,  # noqa: ARG002
    ) -> list[metrics.Observation]:
        """T-754: Observable gauge callback for unique session count per pair."""
        try:
            snapshot = dict(self._global_session_pairs)
            observations: list[metrics.Observation] = []
            for (from_tool, to_tool), sessions in snapshot.items():
                observations.append(
                    metrics.Observation(
                        value=len(sessions),
                        attributes={"from_tool": from_tool, "to_tool": to_tool},
                    )
                )
            return observations
        except Exception:
            logger.exception("pair_sessions_callback: EXCEPTION")
            return []

    @staticmethod
    def compute_input_hashes(params: dict[str, Any]) -> set[str]:
        """Compute hashes of tool input parameters for chain matching.

        Only hashes are computed - no data is stored. This is privacy-preserving.
        T-751: Also adds normalized string hashes for fuzzy matching.

        Args:
            params: Tool input parameters

        Returns:
            Set of hashes for each parameter value
        """
        hashes = set()
        for _key, value in params.items():
            try:
                serialized = json.dumps(value, sort_keys=True, default=str)
                hash_value = hashlib.sha256(serialized.encode()).hexdigest()[:16]
                hashes.add(hash_value)
            except (TypeError, ValueError):
                # Skip unhashable values
                continue
            # T-751: string normalization — stripped/lowercased hash
            if isinstance(value, str) and value.strip():
                normalized = value.strip().lower()
                hashes.add(hashlib.sha256(normalized.encode()).hexdigest()[:16])
        return hashes

    @staticmethod
    def compute_output_hashes(result: Any, max_hashes: int = 200) -> set[str]:
        """Compute hashes of output at every granularity level (T-751).

        BFS traversal emits hashes for:
        - The full serialized output
        - Each nested sub-object (dict/list), up to max_hashes total
        - Each leaf scalar value (str, int, float)
        - Each string value normalized (stripped, lowercased)

        Stops walking once max_hashes is reached to bound Redis write volume.

        Args:
            result: Tool output/result
            max_hashes: Maximum number of hashes to produce

        Returns:
            Set of hashes for the output at various granularities
        """
        hashes: set[str] = set()
        queue: list[Any] = [result]

        while queue and len(hashes) < max_hashes:
            obj = queue.pop(0)
            if obj is None:
                continue
            try:
                s = json.dumps(obj, sort_keys=True, default=str)
                hashes.add(hashlib.sha256(s.encode()).hexdigest()[:16])
            except Exception:
                pass
            if len(hashes) >= max_hashes:
                break
            if isinstance(obj, dict):
                queue.extend(obj.values())
            elif isinstance(obj, list):
                queue.extend(obj)
            elif isinstance(obj, str) and obj.strip():
                normalized = obj.strip().lower()
                hashes.add(hashlib.sha256(normalized.encode()).hexdigest()[:16])

        return hashes

    @staticmethod
    def compute_output_hash(result: Any) -> str:
        """Compute single hash of tool output (legacy, kept for backward compat).

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

    async def record_tool_output(self, tool_name: str, output_hashes: str | set[str]) -> None:
        """Store output hash(es) for future chain matching.

        Args:
            tool_name: Name of the tool that produced this output
            output_hashes: Single hash string or set of hashes (T-751)
        """
        if isinstance(output_hashes, str):
            output_hashes = {output_hashes}
        for output_hash in output_hashes:
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
            logger.debug("process_tool_call: skipping workflow tool %s", tool_name)
            return []

        # Normalize tool name for consistent chain detection across runners
        normalized_name, _ = normalize_tool_name_for_metrics(tool_name)

        logger.info(
            "process_tool_call: tool=%s normalized=%s session=%s bridge=%s runner=%s",
            tool_name,
            normalized_name,
            session_id,
            bridge_id,
            runner_id,
        )

        # Compute hashes — T-751: multi-hash output recording
        input_hashes = self.compute_input_hashes(params)
        output_hashes = self.compute_output_hashes(
            result, max_hashes=self._config.max_output_hashes
        )

        # Signal 1: Data flow (weight configurable) — existing logic
        predecessors = await self.check_chain_link(
            normalized_name, input_hashes, runner_id=runner_id, bridge_id=bridge_id
        )

        # Record this output for future matching (T-751: all hashes)
        await self.record_tool_output(normalized_name, output_hashes)

        data_flow_score = 1.0 if predecessors else 0.0

        logger.info(
            "process_tool_call: Signal 1 (data-flow) tool=%s predecessors=%s score=%.2f "
            "input_hashes=%d output_hashes=%d",
            normalized_name,
            predecessors,
            data_flow_score,
            len(input_hashes),
            len(output_hashes),
        )

        # Signal 2: Sequence consistency (weight 0.30)
        sequence_score = 0.0
        repeated_pairs: list[tuple[str, str, int]] = []
        if session_id:
            repeated_pairs = self._sequence_tracker.record_call(session_id, normalized_name)
            if repeated_pairs:
                if self._meter and hasattr(self, "_sequence_pairs_total"):
                    for from_t, to_t, count in repeated_pairs:
                        attrs: dict[str, str] = {
                            "from_tool": from_t,
                            "to_tool": to_t,
                            "session_id": session_id,
                        }
                        if bridge_id:
                            attrs["bridge_id"] = bridge_id
                        self._sequence_pairs_total.add(1, attrs)
                max_count = max(c for _, _, c in repeated_pairs)
                # T-753: Log-scale scoring — count=1→~0.15, count=30→1.0
                sequence_score = min(
                    math.log1p(max_count) / math.log1p(self._config.log_score_scale),
                    1.0,
                )

        # T-754: Cross-session global pair frequency tracking
        if session_id and repeated_pairs:
            for from_t, to_t, _count in repeated_pairs:
                pair_key = (from_t, to_t)
                self._global_pair_counts[pair_key] += 1
                if pair_key not in self._global_session_pairs:
                    self._global_session_pairs[pair_key] = set()
                self._global_session_pairs[pair_key].add(session_id)
                # Emit counter metric
                if self._meter and hasattr(self, "_global_sequence_pairs_total"):
                    self._global_sequence_pairs_total.add(
                        1,
                        {"from_tool": from_t, "to_tool": to_t, "session_id": session_id},
                    )

            # T-754: Cross-session boost — 50% boost when pair seen in 3+ unique sessions
            for from_t, to_t, _count in repeated_pairs:
                unique_sessions = len(self._global_session_pairs.get((from_t, to_t), set()))
                if unique_sessions >= 3:
                    sequence_score = min(sequence_score * 1.5, 1.0)
                    break  # Apply boost once per call, not per pair

        if session_id:
            logger.info(
                "process_tool_call: Signal 2 (sequence) tool=%s repeated_pairs=%s score=%.2f",
                normalized_name,
                [(f, t, c) for f, t, c in repeated_pairs] if repeated_pairs else "none",
                sequence_score,
            )
        else:
            logger.info(
                "process_tool_call: Signal 2 (sequence) SKIPPED — no session_id for tool=%s",
                normalized_name,
            )

        # Signal 3: Temporal co-occurrence (weight 0.20)
        temporal_score = 0.0
        cooccurring_pairs: list[tuple[str, str, int]] = []
        if session_id:
            _chunk_id, cooccurring_pairs = self._temporal_tracker.record_call(
                session_id, normalized_name
            )
            if cooccurring_pairs:
                if self._meter and hasattr(self, "_temporal_cooccurrence_total"):
                    for tool_a, tool_b, count in cooccurring_pairs:
                        t_attrs: dict[str, str] = {
                            "tool_a": tool_a,
                            "tool_b": tool_b,
                            "chunk_id": _chunk_id,
                        }
                        if bridge_id:
                            t_attrs["bridge_id"] = bridge_id
                        self._temporal_cooccurrence_total.add(1, t_attrs)
                max_count = max(c for _, _, c in cooccurring_pairs)
                temporal_score = min(max_count / 5.0, 1.0)

        if session_id:
            logger.info(
                "process_tool_call: Signal 3 (temporal) tool=%s cooccurring_pairs=%s score=%.2f",
                normalized_name,
                [(a, b, c) for a, b, c in cooccurring_pairs] if cooccurring_pairs else "none",
                temporal_score,
            )

        # Composite score — stored for observable gauge.
        # Collect all tool pairs that contributed to ANY signal so the gauge
        # is populated even when there is no data-flow match.
        composite = (
            (data_flow_score * self._config.data_flow_weight)
            + (sequence_score * self._config.sequence_weight)
            + (temporal_score * self._config.temporal_weight)
        )

        scored_pairs: set[tuple[str, str]] = set()

        # Data-flow predecessors (Signal 1)
        for pred in predecessors:
            scored_pairs.add((pred, normalized_name))

        # Sequence pairs (Signal 2) — only when they contributed a non-zero score
        if sequence_score > 0 and session_id:
            # Re-fetch repeated pairs from tracker state for pair names
            for from_t, to_t, _count in repeated_pairs:
                scored_pairs.add((from_t, to_t))

        # Temporal pairs (Signal 3) — only when they contributed a non-zero score
        if temporal_score > 0 and session_id:
            for tool_a, tool_b, _count in cooccurring_pairs:
                scored_pairs.add((tool_a, tool_b))

        bid = bridge_id or ""
        for from_tool, to_tool in scored_pairs:
            # T-756: Max-over-time accumulation — never regress a stored score
            existing = self._composite_scores.get((from_tool, to_tool, bid), 0.0)
            self._composite_scores[(from_tool, to_tool, bid)] = max(existing, composite)
            self._composite_score_timestamps[(from_tool, to_tool, bid)] = _time.monotonic()

            # Wire up chain_frequency: increment on first discovery of this pair
            if self._meter and hasattr(self, "_chain_frequency"):
                key = (from_tool, to_tool, bid)
                if key not in self._chain_frequency_seen:
                    self._chain_frequency_seen.add(key)
                    freq_attrs: dict[str, str] = {
                        "from_tool": from_tool,
                        "to_tool": to_tool,
                    }
                    if bid:
                        freq_attrs["bridge_id"] = bid
                    self._chain_frequency.add(1, freq_attrs)
                    logger.info(
                        "process_tool_call: chain_frequency +1 for new pair "
                        "(%s -> %s, bridge=%s) total_unique=%d",
                        from_tool,
                        to_tool,
                        bid or "none",
                        len(self._chain_frequency_seen),
                    )

        if scored_pairs:
            logger.info(
                "process_tool_call: COMPOSITE tool=%s composite=%.3f "
                "(data_flow=%.2f seq=%.2f temporal=%.2f) scored_pairs=%s "
                "total_stored=%d",
                normalized_name,
                composite,
                data_flow_score,
                sequence_score,
                temporal_score,
                [(f, t) for f, t in scored_pairs],
                len(self._composite_scores),
            )
        else:
            logger.info(
                "process_tool_call: NO scored pairs for tool=%s composite=%.3f "
                "(data_flow=%.2f seq=%.2f temporal=%.2f) session=%s "
                "(need >=2 repetitions of A->B or data-flow hash match)",
                normalized_name,
                composite,
                data_flow_score,
                sequence_score,
                temporal_score,
                session_id,
            )

        return predecessors

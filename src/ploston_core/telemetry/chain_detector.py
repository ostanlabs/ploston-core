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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from opentelemetry import metrics, trace


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
        self._setup_metrics()

    def _setup_metrics(self) -> None:
        """Set up Prometheus metrics."""
        if not self._meter:
            return

        # Counter: Chain links detected
        self._chain_links_total = self._meter.create_counter(
            name="ploston_chain_links_total",
            description="Total chain links detected between direct tool calls",
            unit="1",
        )

        # Gauge: Chain frequency (for dashboard)
        self._chain_frequency = self._meter.create_up_down_counter(
            name="ploston_chain_frequency",
            description="Frequency of detected tool chains",
            unit="1",
        )

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
            data = json.dumps({
                "tool": tool_name,
                "timestamp": datetime.utcnow().isoformat(),
            })
            try:
                await self._redis.setex(key, self._ttl, data)
            except Exception:
                # Fall back to memory cache on Redis error
                self._memory_cache.set(output_hash, tool_name)
        else:
            self._memory_cache.set(output_hash, tool_name)

    async def check_chain_link(
        self, tool_name: str, input_hashes: set[str]
    ) -> list[str]:
        """Check if any input matches a recent output, detecting chain links.

        Args:
            tool_name: Name of the current tool being called
            input_hashes: Hashes of the tool's input parameters

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
                    self._chain_links_total.add(
                        1,
                        {"from_tool": predecessor, "to_tool": tool_name},
                    )

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
    ) -> list[str]:
        """Process a tool call for chain detection.

        This is the main integration point - call after each direct tool call.

        Args:
            tool_name: Name of the tool called
            params: Tool input parameters
            result: Tool output/result

        Returns:
            List of predecessor tools (chain links detected)
        """
        # Skip workflow calls - we only detect chains in direct tool calls
        if tool_name.startswith("workflow:"):
            return []

        # Compute hashes
        input_hashes = self.compute_input_hashes(params)
        output_hash = self.compute_output_hash(result)

        # Check for chain links (input matches previous output)
        predecessors = await self.check_chain_link(tool_name, input_hashes)

        # Record this output for future matching
        await self.record_tool_output(tool_name, output_hash)

        return predecessors

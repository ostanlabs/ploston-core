"""Runner Registry for Control Plane.

Implements S-182: Runner Registry (CP)
- T-519: RunnerRegistry data model
- T-520: Runner CRUD operations
- T-521: Runner status tracking
- T-522: Runner available_tools tracking
- T-523: Runner token generation
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ploston_core.telemetry.metrics import AELMetrics


class RunnerStatus(str, Enum):
    """Runner connection status."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


@dataclass
class Runner:
    """Runner data model per DEC-122.

    Attributes:
        id: Internal ID (runner_a1b2c3)
        name: Human-readable name (marc-laptop)
        created_at: Creation timestamp
        last_seen: Last heartbeat timestamp
        status: Connection status
        available_tools: List of available tools (strings or dicts with schema)
        token_hash: SHA-256 hash of auth token
        mcps: Assigned MCP configurations
    """

    id: str
    name: str
    created_at: datetime
    last_seen: datetime | None = None
    status: RunnerStatus = RunnerStatus.DISCONNECTED
    available_tools: list[str | dict[str, Any]] = field(default_factory=list)
    token_hash: str = ""
    mcps: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at.isoformat(),
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "status": self.status.value,
            "available_tools": self.available_tools,
            "mcps": self.mcps,
        }


def generate_runner_id() -> str:
    """Generate a unique runner ID."""
    suffix = secrets.token_hex(6)
    return f"runner_{suffix}"


def generate_runner_token() -> str:
    """Generate a secure runner token.

    Format: ploston_runner_<random> per DEC-119
    """
    suffix = secrets.token_urlsafe(24)
    return f"ploston_runner_{suffix}"


def hash_token(token: str) -> str:
    """Hash a token for secure storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def validate_token_format(token: str) -> bool:
    """Validate token format.

    Token must start with 'ploston_runner_' and have at least 8 chars suffix.
    """
    if not token.startswith("ploston_runner_"):
        return False
    suffix = token[len("ploston_runner_") :]
    return len(suffix) >= 8


class RunnerRegistry:
    """Registry for managing runners on the Control Plane.

    Provides CRUD operations and status tracking for runners.
    """

    def __init__(self) -> None:
        """Initialize the registry."""
        self._runners: dict[str, Runner] = {}  # id -> Runner
        self._name_to_id: dict[str, str] = {}  # name -> id
        self._token_to_id: dict[str, str] = {}  # token_hash -> id
        self._metrics: AELMetrics | None = None

    def set_metrics(self, metrics: AELMetrics) -> None:
        """Set the metrics instance for telemetry.

        Args:
            metrics: AELMetrics instance
        """
        self._metrics = metrics

    def _update_metrics(self) -> None:
        """Update telemetry metrics based on current runner state."""
        if not self._metrics:
            return

        # Count connected runners and their tools
        connected_count = 0
        total_runner_tools = 0
        for runner in self._runners.values():
            if runner.status == RunnerStatus.CONNECTED:
                connected_count += 1
                total_runner_tools += len(runner.available_tools)

        # Update metrics
        self._metrics.update_connected_runners(connected_count)
        self._metrics.update_runner_tools(total_runner_tools)

    def create(self, name: str, mcps: dict[str, dict] | None = None) -> tuple[Runner, str]:
        """Create a new runner.

        Args:
            name: Human-readable runner name
            mcps: Optional MCP configurations

        Returns:
            Tuple of (Runner, token) - token is only returned once

        Raises:
            ValueError: If name already exists
        """
        if name in self._name_to_id:
            raise ValueError(f"Runner with name '{name}' already exists")

        runner_id = generate_runner_id()
        token = generate_runner_token()
        token_hash = hash_token(token)

        runner = Runner(
            id=runner_id,
            name=name,
            created_at=datetime.now(UTC),
            token_hash=token_hash,
            mcps=mcps or {},
        )

        self._runners[runner_id] = runner
        self._name_to_id[name] = runner_id
        self._token_to_id[token_hash] = runner_id

        return runner, token

    def get(self, runner_id: str) -> Runner | None:
        """Get a runner by ID."""
        return self._runners.get(runner_id)

    def get_by_name(self, name: str) -> Runner | None:
        """Get a runner by name."""
        runner_id = self._name_to_id.get(name)
        if runner_id:
            return self._runners.get(runner_id)
        return None

    def get_by_token(self, token: str) -> Runner | None:
        """Get a runner by token (validates token)."""
        token_hash = hash_token(token)
        runner_id = self._token_to_id.get(token_hash)
        if runner_id:
            return self._runners.get(runner_id)
        return None

    def list(self) -> list[Runner]:
        """List all runners."""
        return list(self._runners.values())

    def list_connected(self) -> list[Runner]:
        """List all connected runners."""
        return [r for r in self._runners.values() if r.status == RunnerStatus.CONNECTED]

    def update(self, runner_id: str, **kwargs) -> Runner | None:
        """Update a runner's fields.

        Args:
            runner_id: Runner ID
            **kwargs: Fields to update (status, available_tools, mcps, last_seen)

        Returns:
            Updated runner or None if not found
        """
        runner = self._runners.get(runner_id)
        if not runner:
            return None

        for key, value in kwargs.items():
            if hasattr(runner, key) and key not in ("id", "name", "created_at", "token_hash"):
                setattr(runner, key, value)

        return runner

    def delete(self, runner_id: str) -> bool:
        """Delete a runner (revokes token).

        Args:
            runner_id: Runner ID

        Returns:
            True if deleted, False if not found
        """
        runner = self._runners.get(runner_id)
        if not runner:
            return False

        del self._runners[runner_id]
        del self._name_to_id[runner.name]
        del self._token_to_id[runner.token_hash]

        return True

    def delete_by_name(self, name: str) -> bool:
        """Delete a runner by name."""
        runner_id = self._name_to_id.get(name)
        if runner_id:
            return self.delete(runner_id)
        return False

    def set_connected(self, runner_id: str) -> Runner | None:
        """Mark a runner as connected."""
        result = self.update(
            runner_id,
            status=RunnerStatus.CONNECTED,
            last_seen=datetime.now(UTC),
        )
        if result:
            self._update_metrics()
        return result

    def set_disconnected(self, runner_id: str) -> Runner | None:
        """Mark a runner as disconnected."""
        result = self.update(runner_id, status=RunnerStatus.DISCONNECTED)
        if result:
            self._update_metrics()
        return result

    def update_heartbeat(self, runner_id: str) -> Runner | None:
        """Update last_seen timestamp from heartbeat."""
        return self.update(runner_id, last_seen=datetime.now(UTC))

    def update_available_tools(
        self, runner_id: str, tools: list[str | dict[str, Any]]
    ) -> Runner | None:
        """Update the list of available tools for a runner.

        Args:
            runner_id: Runner ID
            tools: List of tools (strings or dicts with name/description/inputSchema)

        Returns:
            Updated runner or None if not found
        """
        result = self.update(runner_id, available_tools=tools)
        if result:
            self._update_metrics()
        return result

    def _get_tool_name(self, tool: str | dict[str, Any]) -> str:
        """Extract tool name from string or dict format.

        Args:
            tool: Tool as string or dict with 'name' key

        Returns:
            Tool name
        """
        if isinstance(tool, str):
            return tool
        return tool.get("name", "")

    def has_tool(self, runner_name: str, tool_name: str) -> bool:
        """Check if a runner has a specific tool available.

        Args:
            runner_name: Runner name
            tool_name: Tool name (may include runner__mcp__ prefix)

        Returns:
            True if tool is available on the runner
        """
        runner = self.get_by_name(runner_name)
        if not runner or runner.status != RunnerStatus.CONNECTED:
            return False

        # Strip runner__mcp__ prefix if present (format: runner__mcp__tool)
        # We need to extract the mcp__tool part to match against available_tools
        if "__" in tool_name:
            parts = tool_name.split("__")
            if len(parts) >= 3:
                # runner__mcp__tool -> mcp__tool (what runner stores)
                tool_name = "__".join(parts[1:])

        # Check against tool names (handle both string and dict formats)
        for tool in runner.available_tools:
            if self._get_tool_name(tool) == tool_name:
                return True
        return False

    def get_runner_for_tool(self, tool_name: str) -> Runner | None:
        """Find a connected runner that has a specific tool.

        Args:
            tool_name: Tool name (may include runner__mcp__ prefix)

        Returns:
            Runner with the tool, or None
        """
        # If tool has runner__mcp__ prefix, look for that specific runner
        # Format: runner__mcp__tool (e.g., mac__fs__read_file)
        if "__" in tool_name:
            parts = tool_name.split("__")
            if len(parts) >= 3:
                runner_name = parts[0]
                # mcp__tool is what runner stores in available_tools
                actual_tool = "__".join(parts[1:])
                runner = self.get_by_name(runner_name)
                if runner and runner.status == RunnerStatus.CONNECTED:
                    for tool in runner.available_tools:
                        if self._get_tool_name(tool) == actual_tool:
                            return runner
                return None

        # Otherwise, find any connected runner with the tool
        for runner in self.list_connected():
            for tool in runner.available_tools:
                if self._get_tool_name(tool) == tool_name:
                    return runner

        return None

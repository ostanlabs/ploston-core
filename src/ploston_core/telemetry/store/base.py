"""Telemetry Store abstract base class and factory."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from .types import ExecutionRecord, ExecutionStatus, ExecutionType

if TYPE_CHECKING:
    from .config import TelemetryStoreConfig


class TelemetryStore(ABC):
    """Abstract interface for telemetry storage.

    Implementations:
    - MemoryTelemetryStore: In-memory with LRU eviction
    - SQLiteTelemetryStore: SQLite file-based persistence
    - PostgresTelemetryStore: PostgreSQL (Premium)
    """

    @abstractmethod
    async def save_execution(self, record: ExecutionRecord) -> None:
        """Save or update an execution record.

        Args:
            record: Execution record to save
        """
        ...

    @abstractmethod
    async def get_execution(self, execution_id: str) -> Optional[ExecutionRecord]:
        """Get execution by ID.

        Args:
            execution_id: Execution ID to retrieve

        Returns:
            ExecutionRecord if found, None otherwise
        """
        ...

    @abstractmethod
    async def list_executions(
        self,
        execution_type: Optional[ExecutionType] = None,
        workflow_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        status: Optional[ExecutionStatus] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        caller_id: Optional[str] = None,
        session_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[ExecutionRecord], int]:
        """List executions with filtering.

        Args:
            execution_type: Filter by execution type
            workflow_id: Filter by workflow ID
            tool_name: Filter by tool name (for direct executions)
            status: Filter by status
            since: Filter by start time (inclusive)
            until: Filter by start time (inclusive)
            caller_id: Filter by caller ID
            session_id: Filter by session ID
            page: Page number (1-indexed)
            page_size: Number of records per page

        Returns:
            Tuple of (records, total_count)
        """
        ...

    @abstractmethod
    async def delete_execution(self, execution_id: str) -> bool:
        """Delete an execution.

        Args:
            execution_id: Execution ID to delete

        Returns:
            True if deleted, False if not found
        """
        ...

    @abstractmethod
    async def delete_before(self, cutoff: datetime) -> int:
        """Delete executions before cutoff.

        Args:
            cutoff: Delete executions started before this time

        Returns:
            Number of records deleted
        """
        ...

    @abstractmethod
    async def get_tool_call_stats(
        self,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> Dict[str, Dict[str, int]]:
        """Get tool call statistics.

        Args:
            since: Start of time range
            until: End of time range

        Returns:
            Dict mapping tool_name to {"total": N, "success": M, "error": K}
        """
        ...

    async def close(self) -> None:
        """Close any open connections."""
        pass


def create_telemetry_store(config: "TelemetryStoreConfig") -> TelemetryStore:
    """Factory function to create appropriate store.

    Args:
        config: Telemetry store configuration

    Returns:
        Configured TelemetryStore instance

    Raises:
        ValueError: If storage type is unknown
    """
    from .memory import MemoryTelemetryStore
    from .sqlite import SQLiteTelemetryStore

    if config.storage_type == "memory":
        return MemoryTelemetryStore(max_records=config.max_memory_records)
    elif config.storage_type == "sqlite":
        return SQLiteTelemetryStore(
            db_path=config.sqlite_path,
            redaction=config.redaction,
        )
    elif config.storage_type == "postgres":
        if not config.postgres_connection_string:
            raise ValueError("PostgreSQL connection string required")
        # PostgreSQL store is Premium feature - not implemented in OSS
        raise NotImplementedError("PostgreSQL store is a Premium feature")
    else:
        raise ValueError(f"Unknown storage type: {config.storage_type}")


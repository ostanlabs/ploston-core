"""In-memory telemetry store with LRU eviction."""

import asyncio
from collections import OrderedDict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .base import TelemetryStore
from .types import ExecutionRecord, ExecutionStatus, ExecutionType


class MemoryTelemetryStore(TelemetryStore):
    """In-memory telemetry store with LRU eviction.

    Data is lost on restart. Suitable for development/testing.
    """

    def __init__(self, max_records: int = 1000) -> None:
        """Initialize memory store.

        Args:
            max_records: Maximum number of records to keep (LRU eviction)
        """
        self._max_records = max_records
        self._records: OrderedDict[str, ExecutionRecord] = OrderedDict()
        self._lock = asyncio.Lock()

    async def save_execution(self, record: ExecutionRecord) -> None:
        """Save or update an execution record."""
        async with self._lock:
            # Move to end if exists (LRU)
            if record.execution_id in self._records:
                self._records.move_to_end(record.execution_id)

            self._records[record.execution_id] = record

            # Evict oldest if over limit
            while len(self._records) > self._max_records:
                self._records.popitem(last=False)

    async def get_execution(self, execution_id: str) -> Optional[ExecutionRecord]:
        """Get execution by ID."""
        return self._records.get(execution_id)

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
        """List executions with filtering."""
        # Filter
        filtered = []
        for record in self._records.values():
            if execution_type and record.execution_type != execution_type:
                continue
            if workflow_id and record.workflow_id != workflow_id:
                continue
            if tool_name and record.tool_name != tool_name:
                continue
            if status and record.status != status:
                continue
            if since and record.started_at and record.started_at < since:
                continue
            if until and record.started_at and record.started_at > until:
                continue
            if caller_id and record.caller_id != caller_id:
                continue
            if session_id and record.session_id != session_id:
                continue
            filtered.append(record)

        # Sort by started_at descending
        filtered.sort(key=lambda r: r.started_at or datetime.min, reverse=True)

        # Paginate
        total = len(filtered)
        start = (page - 1) * page_size
        end = start + page_size

        return filtered[start:end], total

    async def delete_execution(self, execution_id: str) -> bool:
        """Delete an execution."""
        async with self._lock:
            if execution_id in self._records:
                del self._records[execution_id]
                return True
            return False

    async def delete_before(self, cutoff: datetime) -> int:
        """Delete executions before cutoff."""
        async with self._lock:
            to_delete = [
                eid
                for eid, record in self._records.items()
                if record.started_at and record.started_at < cutoff
            ]
            for eid in to_delete:
                del self._records[eid]
            return len(to_delete)

    async def get_tool_call_stats(
        self,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> Dict[str, Dict[str, int]]:
        """Get tool call statistics."""
        stats: Dict[str, Dict[str, int]] = {}

        for record in self._records.values():
            if since and record.started_at and record.started_at < since:
                continue
            if until and record.started_at and record.started_at > until:
                continue

            for step in record.steps:
                for call in step.tool_calls:
                    if call.tool_name not in stats:
                        stats[call.tool_name] = {"total": 0, "success": 0, "error": 0}

                    stats[call.tool_name]["total"] += 1
                    if call.error:
                        stats[call.tool_name]["error"] += 1
                    else:
                        stats[call.tool_name]["success"] += 1

        return stats


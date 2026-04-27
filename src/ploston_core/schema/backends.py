"""Backend protocol and implementations for persisting learned schemas.

F-088 · T-886. Each tool key gets one JSON file in the FileSchemaBackend;
the InMemorySchemaBackend is for tests.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Protocol

from .extractor import ExtractionPattern
from .types import SuggestedOutputSchema


def _key(server_name: str, tool_name: str) -> str:
    return f"{server_name}__{tool_name}"


class SchemaStoreBackend(Protocol):
    """Pluggable persistence layer for ``ToolOutputSchemaStore``."""

    async def load_all(
        self,
    ) -> dict[str, tuple[SuggestedOutputSchema, ExtractionPattern | None]]:
        """Return every persisted schema keyed by ``server__tool``."""

    async def save(
        self,
        key: str,
        schema: SuggestedOutputSchema,
        pattern: ExtractionPattern | None = None,
    ) -> None:
        """Persist a single schema (and optionally its extraction pattern)."""

    async def delete(self, key: str) -> None:
        """Remove a single schema entry."""

    async def clear_all(self) -> None:
        """Remove every persisted schema."""


class InMemorySchemaBackend:
    """Non-persistent backend. Used by tests and short-lived processes."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[SuggestedOutputSchema, ExtractionPattern | None]] = {}

    async def load_all(
        self,
    ) -> dict[str, tuple[SuggestedOutputSchema, ExtractionPattern | None]]:
        return dict(self._entries)

    async def save(
        self,
        key: str,
        schema: SuggestedOutputSchema,
        pattern: ExtractionPattern | None = None,
    ) -> None:
        self._entries[key] = (schema, pattern)

    async def delete(self, key: str) -> None:
        self._entries.pop(key, None)

    async def clear_all(self) -> None:
        self._entries.clear()


class FileSchemaBackend:
    """JSON-per-key file backend.

    Default directory: ``~/.ploston/schemas/`` (follows the ``~/.ploston/ca/``
    precedent used by the runner embedded CA). Override ``data_dir`` for tests.
    """

    def __init__(self, data_dir: Path | str | None = None) -> None:
        self._data_dir = (
            Path(data_dir) if data_dir is not None else Path.home() / ".ploston" / "schemas"
        )

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    def _path(self, key: str) -> Path:
        # Keys are already safe (server__tool) -- replace path separators just in case.
        safe = key.replace("/", "_").replace("\\", "_")
        return self._data_dir / f"{safe}.json"

    async def load_all(
        self,
    ) -> dict[str, tuple[SuggestedOutputSchema, ExtractionPattern | None]]:
        return await asyncio.to_thread(self._load_all_sync)

    def _load_all_sync(
        self,
    ) -> dict[str, tuple[SuggestedOutputSchema, ExtractionPattern | None]]:
        out: dict[str, tuple[SuggestedOutputSchema, ExtractionPattern | None]] = {}
        if not self._data_dir.exists():
            return out
        for entry in self._data_dir.iterdir():
            if not entry.is_file() or entry.suffix != ".json":
                continue
            try:
                data = json.loads(entry.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            schema_payload = data.get("schema")
            if not schema_payload:
                continue
            try:
                schema = SuggestedOutputSchema.from_dict(schema_payload)
            except (KeyError, TypeError, ValueError):
                continue
            pattern_payload = data.get("pattern")
            pattern: ExtractionPattern | None = None
            if pattern_payload:
                try:
                    pattern = ExtractionPattern.from_dict(pattern_payload)
                except (KeyError, TypeError, ValueError):
                    pattern = None
            out[entry.stem] = (schema, pattern)
        return out

    async def save(
        self,
        key: str,
        schema: SuggestedOutputSchema,
        pattern: ExtractionPattern | None = None,
    ) -> None:
        payload: dict[str, Any] = {"schema": schema.to_dict()}
        if pattern is not None:
            payload["pattern"] = pattern.to_dict()
        await asyncio.to_thread(self._write_sync, key, payload)

    def _write_sync(self, key: str, payload: dict[str, Any]) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        path = self._path(key)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._delete_sync, key)

    def _delete_sync(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

    async def clear_all(self) -> None:
        await asyncio.to_thread(self._clear_sync)

    def _clear_sync(self) -> None:
        if not self._data_dir.exists():
            return
        for entry in self._data_dir.iterdir():
            if entry.is_file() and entry.suffix == ".json":
                entry.unlink()

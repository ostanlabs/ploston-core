"""Layer-2 schema store (F-088 · T-885).

Observes successful tool call outputs through a two-layer pipeline:
Layer 1 (``ResponsePatternExtractor``) locates the JSON payload inside a
text response; Layer 2 (this module) infers structural metadata via
recursive walks and union merging. Never stores actual data values.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from .backends import SchemaStoreBackend, _key
from .extractor import ResponsePatternExtractor
from .types import InferredJsonSchema, InferredPropertySchema, SuggestedOutputSchema


def _size_of(output: Any) -> int:
    try:
        if isinstance(output, (dict, list)):
            return len(json.dumps(output, default=str))
        if isinstance(output, (bytes, bytearray)):
            return len(output)
        return len(str(output))
    except (TypeError, ValueError):
        return 0


def _scalar_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


class ToolOutputSchemaStore:
    """Observes successful tool outputs and surfaces learned schemas."""

    MAX_OBSERVATION_SIZE = 256 * 1024  # 256 KB
    EXCLUDED_TOOLS: set[str] = {"python_exec"}
    CONFIDENCE_THRESHOLD = 10  # observations for max confidence

    def __init__(
        self,
        backend: SchemaStoreBackend,
        extractor: ResponsePatternExtractor | None = None,
        logger: Any | None = None,
    ) -> None:
        self._backend = backend
        self._extractor = extractor or ResponsePatternExtractor()
        self._schemas: dict[str, SuggestedOutputSchema] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._logger = logger
        self._initialized = False

    async def initialize(self) -> None:
        """Load persisted schemas and extraction patterns from the backend."""
        entries = await self._backend.load_all()
        for key, (schema, pattern) in entries.items():
            self._schemas[key] = schema
            if pattern is not None:
                self._extractor.set_pattern(pattern)
        self._initialized = True

    # ------------------------------------------------------------------
    # Observation pipeline
    # ------------------------------------------------------------------

    async def observe(
        self,
        tool_name: str,
        server_name: str,
        output: Any,
        params: dict[str, Any] | None = None,
    ) -> None:
        """Observe one successful tool call. Safe to call fire-and-forget."""
        if not tool_name or tool_name in self.EXCLUDED_TOOLS:
            return
        if output is None:
            return
        if _size_of(output) > self.MAX_OBSERVATION_SIZE:
            return

        key = _key(server_name, tool_name)
        tool_key = f"{server_name}__{tool_name}"  # extractor key (canonical)
        structured = self._extractor.extract_and_learn(tool_key, output)
        if structured is None:
            return

        async with self._lock_for(key):
            existing = self._schemas.get(key)
            inferred = self._infer_schema(structured)
            now = datetime.now()
            if existing is None:
                schema = SuggestedOutputSchema(
                    tool_name=tool_name,
                    server_name=server_name,
                    success_schema=inferred,
                    observation_count=1,
                    first_observed=now,
                    last_observed=now,
                )
            else:
                structural_change = self._detect_structural_change(
                    existing.success_schema, inferred
                )
                existing.success_schema = self._merge_schemas(existing.success_schema, inferred)
                existing.observation_count += 1
                existing.last_observed = now
                if structural_change:
                    existing.schema_version += 1
                schema = existing
            schema.confidence = self._compute_confidence(schema)
            self._schemas[key] = schema
            pattern = self._extractor.get_pattern(tool_key)
            await self._backend.save(key, schema, pattern)

    # ------------------------------------------------------------------
    # Read paths
    # ------------------------------------------------------------------

    def get(self, server_name: str, tool_name: str) -> SuggestedOutputSchema | None:
        return self._schemas.get(_key(server_name, tool_name))

    def get_extractor(self) -> ResponsePatternExtractor:
        return self._extractor

    async def clear(
        self,
        server_name: str | None = None,
        tool_name: str | None = None,
    ) -> None:
        if server_name is None and tool_name is None:
            self._schemas.clear()
            await self._backend.clear_all()
            return
        if server_name is not None and tool_name is not None:
            key = _key(server_name, tool_name)
            self._schemas.pop(key, None)
            await self._backend.delete(key)
            return
        # server_name only: remove everything starting with server__
        prefix = f"{server_name}__"
        for key in [k for k in self._schemas if k.startswith(prefix)]:
            del self._schemas[key]
            await self._backend.delete(key)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _lock_for(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def _infer_schema(self, value: Any) -> InferredJsonSchema:
        schema = InferredJsonSchema(types_observed={_scalar_type(value)}, observation_count=1)
        if isinstance(value, dict):
            for key, sub in value.items():
                if not isinstance(key, str):
                    continue
                schema.properties[key] = InferredPropertySchema(
                    field_schema=self._infer_schema(sub),
                    frequency=1,
                    total_observations=1,
                )
        elif isinstance(value, list):
            if value:
                items_schema: InferredJsonSchema | None = None
                for item in value:
                    item_schema = self._infer_schema(item)
                    items_schema = (
                        item_schema
                        if items_schema is None
                        else self._merge_schemas(items_schema, item_schema)
                    )
                schema.items_schema = items_schema
            else:
                schema.items_schema = InferredJsonSchema(observation_count=1)
        return schema

    def _merge_schemas(
        self,
        existing: InferredJsonSchema,
        observed: InferredJsonSchema,
    ) -> InferredJsonSchema:
        merged = InferredJsonSchema(
            types_observed=set(existing.types_observed) | set(observed.types_observed),
            observation_count=existing.observation_count + observed.observation_count,
        )

        all_keys = set(existing.properties) | set(observed.properties)
        for key in all_keys:
            ex = existing.properties.get(key)
            ob = observed.properties.get(key)
            if ex and ob:
                merged.properties[key] = InferredPropertySchema(
                    field_schema=self._merge_schemas(ex.field_schema, ob.field_schema),
                    frequency=ex.frequency + ob.frequency,
                    total_observations=ex.total_observations + ob.total_observations,
                )
            elif ex:
                merged.properties[key] = InferredPropertySchema(
                    field_schema=ex.field_schema,
                    frequency=ex.frequency,
                    total_observations=ex.total_observations + observed.observation_count,
                )
            elif ob:
                merged.properties[key] = InferredPropertySchema(
                    field_schema=ob.field_schema,
                    frequency=ob.frequency,
                    total_observations=existing.observation_count + ob.total_observations,
                )

        if existing.items_schema and observed.items_schema:
            merged.items_schema = self._merge_schemas(existing.items_schema, observed.items_schema)
        elif existing.items_schema:
            merged.items_schema = existing.items_schema
        elif observed.items_schema:
            merged.items_schema = observed.items_schema

        return merged

    def _compute_confidence(self, schema: SuggestedOutputSchema) -> float:
        count_factor = min(schema.observation_count / self.CONFIDENCE_THRESHOLD, 1.0)
        type_variance_penalty = self._type_variance_penalty(schema.success_schema)
        consistency = max(0.0, 1.0 - type_variance_penalty)
        return round(count_factor * consistency, 4)

    def _type_variance_penalty(self, schema: InferredJsonSchema) -> float:
        # Penalty grows with the number of distinct types observed at each
        # level. Heavy hitters (objects returning inconsistent shapes) get
        # squeezed down; a single-type schema has penalty 0.
        penalty = 0.0
        if len(schema.types_observed) > 1:
            penalty += 0.25 * (len(schema.types_observed) - 1)
        for prop in schema.properties.values():
            penalty += 0.05 * self._type_variance_penalty(prop.field_schema)
        if schema.items_schema is not None:
            penalty += 0.1 * self._type_variance_penalty(schema.items_schema)
        return min(penalty, 1.0)

    def _detect_structural_change(
        self,
        existing: InferredJsonSchema,
        observed: InferredJsonSchema,
    ) -> bool:
        if set(observed.properties) - set(existing.properties):
            return True
        if (
            set(observed.types_observed) - set(existing.types_observed)
            and existing.observation_count > 0
        ):
            return True
        return False

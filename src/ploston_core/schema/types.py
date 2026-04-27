"""Data models for learned tool output schemas (F-088 · T-884).

Never stores actual data values. Only structural metadata: types,
key names, nesting depth, frequency of appearance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class InferredJsonSchema:
    """Recursive schema inferred from observed values. Never stores actual data."""

    types_observed: set[str] = field(default_factory=set)
    properties: dict[str, InferredPropertySchema] = field(default_factory=dict)
    items_schema: InferredJsonSchema | None = None
    observation_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "types_observed": sorted(self.types_observed),
            "properties": {k: v.to_dict() for k, v in self.properties.items()},
            "items_schema": self.items_schema.to_dict() if self.items_schema else None,
            "observation_count": self.observation_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InferredJsonSchema:
        return cls(
            types_observed=set(data.get("types_observed") or []),
            properties={
                k: InferredPropertySchema.from_dict(v)
                for k, v in (data.get("properties") or {}).items()
            },
            items_schema=(
                InferredJsonSchema.from_dict(data["items_schema"])
                if data.get("items_schema")
                else None
            ),
            observation_count=int(data.get("observation_count", 0)),
        )


@dataclass
class InferredPropertySchema:
    """Schema for a single property within an observed object."""

    field_schema: InferredJsonSchema
    frequency: int = 0
    total_observations: int = 0

    @property
    def is_required(self) -> bool:
        return self.frequency > 0 and self.frequency == self.total_observations

    @property
    def presence_ratio(self) -> float:
        return self.frequency / max(self.total_observations, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_schema": self.field_schema.to_dict(),
            "frequency": self.frequency,
            "total_observations": self.total_observations,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InferredPropertySchema:
        return cls(
            field_schema=InferredJsonSchema.from_dict(data["field_schema"]),
            frequency=int(data.get("frequency", 0)),
            total_observations=int(data.get("total_observations", 0)),
        )


@dataclass
class SuggestedOutputSchema:
    """Per-tool learned output schema with metadata."""

    tool_name: str
    server_name: str
    success_schema: InferredJsonSchema
    error_schema: InferredJsonSchema | None = None
    observation_count: int = 0
    error_count: int = 0
    first_observed: datetime | None = None
    last_observed: datetime | None = None
    schema_version: int = 1
    confidence: float = 0.0
    # Premium only (S-283):
    variant_schemas: dict[str, InferredJsonSchema] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "server_name": self.server_name,
            "success_schema": self.success_schema.to_dict(),
            "error_schema": self.error_schema.to_dict() if self.error_schema else None,
            "observation_count": self.observation_count,
            "error_count": self.error_count,
            "first_observed": self.first_observed.isoformat() if self.first_observed else None,
            "last_observed": self.last_observed.isoformat() if self.last_observed else None,
            "schema_version": self.schema_version,
            "confidence": self.confidence,
            "variant_schemas": (
                {k: v.to_dict() for k, v in self.variant_schemas.items()}
                if self.variant_schemas
                else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SuggestedOutputSchema:
        def _parse_dt(value: str | None) -> datetime | None:
            return datetime.fromisoformat(value) if value else None

        variants = data.get("variant_schemas")
        return cls(
            tool_name=data["tool_name"],
            server_name=data["server_name"],
            success_schema=InferredJsonSchema.from_dict(data["success_schema"]),
            error_schema=(
                InferredJsonSchema.from_dict(data["error_schema"])
                if data.get("error_schema")
                else None
            ),
            observation_count=int(data.get("observation_count", 0)),
            error_count=int(data.get("error_count", 0)),
            first_observed=_parse_dt(data.get("first_observed")),
            last_observed=_parse_dt(data.get("last_observed")),
            schema_version=int(data.get("schema_version", 1)),
            confidence=float(data.get("confidence", 0.0)),
            variant_schemas=(
                {k: InferredJsonSchema.from_dict(v) for k, v in variants.items()}
                if variants
                else None
            ),
        )

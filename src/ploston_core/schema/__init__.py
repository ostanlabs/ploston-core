"""Tool output schema learning (F-088 · DEC-186).

Two-layer system that observes successful tool call outputs and infers
structural metadata (types, keys, frequency) without storing actual data
values. Surfaced back to agents via ``workflow_tool_schema`` responses.
"""

from .backends import FileSchemaBackend, InMemorySchemaBackend, SchemaStoreBackend
from .extractor import ExtractionPattern, PatternType, ResponsePatternExtractor
from .formatters import format_inferred_schema
from .store import ToolOutputSchemaStore
from .types import InferredJsonSchema, InferredPropertySchema, SuggestedOutputSchema

__all__ = [
    "ExtractionPattern",
    "FileSchemaBackend",
    "InMemorySchemaBackend",
    "InferredJsonSchema",
    "InferredPropertySchema",
    "PatternType",
    "ResponsePatternExtractor",
    "SchemaStoreBackend",
    "SuggestedOutputSchema",
    "ToolOutputSchemaStore",
    "format_inferred_schema",
]

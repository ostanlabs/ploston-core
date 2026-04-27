"""Layer-1 response pattern extractor (F-088 · T-884 + T-885).

Learns how each tool wraps JSON in its text responses (prefix, suffix,
full JSON, or none) and, once a stable pattern is observed, jumps
directly to the known offset instead of brute-force scanning.

Designed to be promotable into ``MCPConnection._extract_fastmcp_content``
in Phase 2 -- the same learned patterns would then improve every
downstream consumer (template expressions, code steps, outputs).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class PatternType(str, Enum):
    FULL_JSON = "full_json"  # Entire output is valid JSON
    PREFIX_JSON = "prefix_json"  # Text prefix before JSON body
    SUFFIX_JSON = "suffix_json"  # JSON body followed by text
    WRAPPED_JSON = "wrapped_json"  # Text on both sides of JSON body
    NO_JSON = "no_json"  # Plain text, no embedded JSON


_SAMPLE_CHARS = 50


@dataclass
class ExtractionPattern:
    """Learned pattern for extracting JSON from a tool's text response.

    Stores only structural metadata (offsets, prefix strings) -- never
    actual data values.
    """

    tool_key: str
    pattern_type: PatternType
    prefix_length: int = 0
    prefix_sample: str | None = None
    suffix_sample: str | None = None
    json_start_char: str = "{"
    observation_count: int = 0
    match_count: int = 0
    last_observed: datetime | None = None

    @property
    def consistency(self) -> float:
        """How often the pattern holds (0.0-1.0)."""
        return self.match_count / max(self.observation_count, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_key": self.tool_key,
            "pattern_type": self.pattern_type.value,
            "prefix_length": self.prefix_length,
            "prefix_sample": self.prefix_sample,
            "suffix_sample": self.suffix_sample,
            "json_start_char": self.json_start_char,
            "observation_count": self.observation_count,
            "match_count": self.match_count,
            "last_observed": self.last_observed.isoformat() if self.last_observed else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExtractionPattern:
        last_observed = data.get("last_observed")
        return cls(
            tool_key=data["tool_key"],
            pattern_type=PatternType(data["pattern_type"]),
            prefix_length=int(data.get("prefix_length", 0)),
            prefix_sample=data.get("prefix_sample"),
            suffix_sample=data.get("suffix_sample"),
            json_start_char=data.get("json_start_char") or "{",
            observation_count=int(data.get("observation_count", 0)),
            match_count=int(data.get("match_count", 0)),
            last_observed=(datetime.fromisoformat(last_observed) if last_observed else None),
        )


class ResponsePatternExtractor:
    """Layer 1 of F-088: learn how tools wrap JSON in text responses.

    After a few observations, skip brute-force parsing and jump
    directly to the known JSON offset.
    """

    _CONSISTENCY_THRESHOLD = 0.8

    def __init__(self) -> None:
        self._patterns: dict[str, ExtractionPattern] = {}

    def get_pattern(self, tool_key: str) -> ExtractionPattern | None:
        return self._patterns.get(tool_key)

    def get_all_patterns(self) -> dict[str, ExtractionPattern]:
        return dict(self._patterns)

    def set_pattern(self, pattern: ExtractionPattern) -> None:
        """Restore a persisted pattern (used by the store on load)."""
        self._patterns[pattern.tool_key] = pattern

    def extract_and_learn(self, tool_key: str, raw_output: Any) -> Any | None:
        """Extract structured data from ``raw_output``, updating the pattern.

        Returns dict/list if extractable, ``None`` otherwise.
        """
        # F-088 T-899: NO_JSON sentinel -- if we've been holding a NO_JSON
        # pattern and now see structured data, invalidate (observation++ but
        # match_count stays flat so consistency decays below threshold and
        # future calls fall back to full brute-force analysis).
        existing = self._patterns.get(tool_key)
        now = datetime.now()

        if isinstance(raw_output, (dict, list)):
            if existing is not None and existing.pattern_type == PatternType.NO_JSON:
                existing.observation_count += 1
                existing.last_observed = now
                return raw_output
            pattern = self._get_or_create(tool_key, PatternType.FULL_JSON)
            pattern.pattern_type = PatternType.FULL_JSON
            pattern.prefix_length = 0
            pattern.observation_count += 1
            pattern.match_count += 1
            pattern.last_observed = now
            return raw_output

        if raw_output is None:
            return None

        if not isinstance(raw_output, str):
            # Scalar / non-serialisable: keep or set NO_JSON.
            pattern = self._get_or_create(tool_key, PatternType.NO_JSON)
            pattern.observation_count += 1
            pattern.match_count += 1
            pattern.last_observed = now
            return None

        return self._extract_from_string(tool_key, raw_output)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_or_create(self, tool_key: str, default_type: PatternType) -> ExtractionPattern:
        pattern = self._patterns.get(tool_key)
        if pattern is None:
            pattern = ExtractionPattern(tool_key=tool_key, pattern_type=default_type)
            self._patterns[tool_key] = pattern
        return pattern

    def _extract_from_string(self, tool_key: str, raw: str) -> Any | None:
        pattern = self._patterns.get(tool_key)
        now = datetime.now()

        # Fast path: existing NO_JSON pattern -- verify and hold.
        if pattern and pattern.pattern_type == PatternType.NO_JSON:
            stripped = raw.lstrip()
            looks_like_json = bool(stripped) and stripped[0] in ("{", "[")
            if not looks_like_json:
                pattern.observation_count += 1
                pattern.match_count += 1
                pattern.last_observed = now
                return None
            pattern.observation_count += 1
            pattern.last_observed = now
            return self._rebuild_from_json_like(tool_key, raw, pattern, now)

        # Fast path: existing stable pattern -- jump to offset.
        if (
            pattern
            and pattern.pattern_type
            in (
                PatternType.FULL_JSON,
                PatternType.PREFIX_JSON,
                PatternType.SUFFIX_JSON,
                PatternType.WRAPPED_JSON,
            )
            and pattern.observation_count > 0
            and pattern.consistency >= self._CONSISTENCY_THRESHOLD
        ):
            fast = self._try_fast_path(pattern, raw)
            if fast is not None:
                pattern.observation_count += 1
                pattern.match_count += 1
                pattern.last_observed = now
                return fast
            pattern.observation_count += 1
            pattern.last_observed = now
            return self._rebuild_from_json_like(tool_key, raw, pattern, now)

        # First observation or NO_JSON without existing pattern.
        return self._rebuild_from_json_like(tool_key, raw, pattern, now)

    def _rebuild_from_json_like(
        self,
        tool_key: str,
        raw: str,
        pattern: ExtractionPattern | None,
        now: datetime,
    ) -> Any | None:
        extracted, new_type, new_prefix_len, start_char = self._brute_force(raw)

        if pattern is None:
            pattern = ExtractionPattern(
                tool_key=tool_key,
                pattern_type=new_type,
                prefix_length=new_prefix_len,
                json_start_char=start_char,
            )
            self._patterns[tool_key] = pattern
            pattern.observation_count = 1
        pattern.last_observed = now

        if extracted is None:
            if pattern.pattern_type != PatternType.NO_JSON:
                pattern.pattern_type = PatternType.NO_JSON
                pattern.prefix_length = 0
                pattern.prefix_sample = None
                pattern.suffix_sample = None
            pattern.match_count += 1
            return None

        if pattern.pattern_type != new_type or pattern.prefix_length != new_prefix_len:
            pattern.pattern_type = new_type
            pattern.prefix_length = new_prefix_len
            pattern.json_start_char = start_char
            pattern.prefix_sample = raw[:_SAMPLE_CHARS] if new_prefix_len else None
            if new_type in (PatternType.SUFFIX_JSON, PatternType.WRAPPED_JSON):
                pattern.suffix_sample = raw[-_SAMPLE_CHARS:]
        pattern.match_count += 1
        return extracted

    def _try_fast_path(self, pattern: ExtractionPattern, raw: str) -> Any | None:
        body = raw[pattern.prefix_length :]
        if not body or body[0] != pattern.json_start_char:
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            payload = _find_balanced(body, pattern.json_start_char)
            if payload is None:
                return None
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                return None

    def _brute_force(self, raw: str) -> tuple[Any | None, PatternType, int, str]:
        """Try to extract JSON from ``raw``. Returns (value, type, prefix_len, start_char)."""
        stripped = raw.strip()
        if not stripped:
            return None, PatternType.NO_JSON, 0, "{"

        try:
            value = json.loads(raw)
            if isinstance(value, (dict, list)):
                start_char = "{" if isinstance(value, dict) else "["
                return value, PatternType.FULL_JSON, 0, start_char
        except json.JSONDecodeError:
            pass

        first_obj = raw.find("{")
        first_arr = raw.find("[")
        candidates = [p for p in (first_obj, first_arr) if p >= 0]
        if not candidates:
            return None, PatternType.NO_JSON, 0, "{"
        start = min(candidates)
        start_char = raw[start]
        payload = _find_balanced(raw[start:], start_char)
        if payload is None:
            return None, PatternType.NO_JSON, 0, "{"
        try:
            value = json.loads(payload)
        except json.JSONDecodeError:
            return None, PatternType.NO_JSON, 0, "{"
        if not isinstance(value, (dict, list)):
            return None, PatternType.NO_JSON, 0, "{"

        end = start + len(payload)
        has_prefix = start > 0
        has_suffix = end < len(raw.rstrip())
        if has_prefix and has_suffix:
            return value, PatternType.WRAPPED_JSON, start, start_char
        if has_prefix:
            return value, PatternType.PREFIX_JSON, start, start_char
        if has_suffix:
            return value, PatternType.SUFFIX_JSON, 0, start_char
        return value, PatternType.FULL_JSON, 0, start_char


def _find_balanced(text: str, start_char: str) -> str | None:
    """Return balanced JSON substring starting at position 0 of ``text``.

    Respects string quoting and escapes. Returns ``None`` if unbalanced.
    """
    if not text or text[0] != start_char:
        return None
    close_char = "}" if start_char == "{" else "]"
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == start_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                return text[: i + 1]
    return None

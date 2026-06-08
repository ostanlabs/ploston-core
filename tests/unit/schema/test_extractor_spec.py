"""Spec tests for the Layer-1 response pattern extractor (F-088).

Asserts the *contract* described in the module/class/method docstrings:

- ``extract_and_learn`` returns dict/list when extractable, ``None`` otherwise.
- Patterns are learned from observations and, once stable
  (consistency >= 0.8 over >0 observations), the fast path jumps to the
  known JSON offset instead of brute-force scanning.
- NO_JSON sentinel behaviour: holds for plain text, invalidates (decays
  consistency) when structured data later appears.
- ``ExtractionPattern`` round-trips through ``to_dict``/``from_dict`` and
  exposes a correct ``consistency`` property.
- ``_find_balanced`` respects string quoting / escapes.

These assert intended behaviour, not merely current output.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from ploston_core.schema.extractor import (
    ExtractionPattern,
    PatternType,
    ResponsePatternExtractor,
    _find_balanced,
)

# ---------------------------------------------------------------------------
# ExtractionPattern dataclass: consistency + (de)serialisation
# ---------------------------------------------------------------------------


class TestExtractionPatternConsistency:
    def test_consistency_zero_observations_is_zero(self) -> None:
        """consistency divides by max(observation_count, 1) → 0/1 == 0.0."""
        p = ExtractionPattern(tool_key="t", pattern_type=PatternType.FULL_JSON)
        assert p.consistency == 0.0

    def test_consistency_ratio(self) -> None:
        p = ExtractionPattern(
            tool_key="t",
            pattern_type=PatternType.FULL_JSON,
            observation_count=4,
            match_count=3,
        )
        assert p.consistency == pytest.approx(0.75)

    def test_consistency_never_divides_by_zero_with_matches(self) -> None:
        # observation_count 0 but match_count set: guarded by max(...,1)
        p = ExtractionPattern(
            tool_key="t",
            pattern_type=PatternType.FULL_JSON,
            observation_count=0,
            match_count=5,
        )
        assert p.consistency == 5.0  # 5 / max(0,1)


class TestExtractionPatternSerialisation:
    def test_to_dict_contains_all_fields_and_pattern_value(self) -> None:
        ts = datetime(2025, 1, 2, 3, 4, 5)
        p = ExtractionPattern(
            tool_key="srv:tool",
            pattern_type=PatternType.PREFIX_JSON,
            prefix_length=7,
            prefix_sample="PREFIX:",
            suffix_sample=None,
            json_start_char="[",
            observation_count=3,
            match_count=2,
            last_observed=ts,
        )
        d = p.to_dict()
        assert d["tool_key"] == "srv:tool"
        assert d["pattern_type"] == "prefix_json"  # enum serialised to value
        assert d["prefix_length"] == 7
        assert d["prefix_sample"] == "PREFIX:"
        assert d["json_start_char"] == "["
        assert d["observation_count"] == 3
        assert d["match_count"] == 2
        assert d["last_observed"] == ts.isoformat()

    def test_to_dict_last_observed_none(self) -> None:
        p = ExtractionPattern(tool_key="t", pattern_type=PatternType.NO_JSON)
        assert p.to_dict()["last_observed"] is None

    def test_round_trip_from_dict(self) -> None:
        ts = datetime(2025, 6, 7, 8, 9, 10)
        original = ExtractionPattern(
            tool_key="t",
            pattern_type=PatternType.WRAPPED_JSON,
            prefix_length=5,
            prefix_sample="abc",
            suffix_sample="xyz",
            json_start_char="{",
            observation_count=10,
            match_count=9,
            last_observed=ts,
        )
        restored = ExtractionPattern.from_dict(original.to_dict())
        assert restored == original

    def test_from_dict_defaults_when_optional_missing(self) -> None:
        restored = ExtractionPattern.from_dict({"tool_key": "t", "pattern_type": "no_json"})
        assert restored.prefix_length == 0
        assert restored.prefix_sample is None
        assert restored.suffix_sample is None
        assert restored.json_start_char == "{"
        assert restored.observation_count == 0
        assert restored.match_count == 0
        assert restored.last_observed is None

    def test_from_dict_coerces_falsy_json_start_char_to_default(self) -> None:
        # ``json_start_char`` uses ``or "{"`` so empty string -> "{".
        restored = ExtractionPattern.from_dict(
            {"tool_key": "t", "pattern_type": "full_json", "json_start_char": ""}
        )
        assert restored.json_start_char == "{"


# ---------------------------------------------------------------------------
# extract_and_learn: structured (dict/list) inputs
# ---------------------------------------------------------------------------


class TestExtractStructuredInputs:
    def test_dict_input_returns_input_and_learns_full_json(self) -> None:
        ex = ResponsePatternExtractor()
        data = {"a": 1}
        out = ex.extract_and_learn("t", data)
        assert out == data
        p = ex.get_pattern("t")
        assert p is not None
        assert p.pattern_type == PatternType.FULL_JSON
        assert p.observation_count == 1
        assert p.match_count == 1
        assert p.last_observed is not None

    def test_list_input_returns_input_and_learns_full_json(self) -> None:
        ex = ResponsePatternExtractor()
        data = [1, 2, 3]
        assert ex.extract_and_learn("t", data) == data
        assert ex.get_pattern("t").pattern_type == PatternType.FULL_JSON

    def test_none_input_returns_none_and_creates_no_pattern(self) -> None:
        ex = ResponsePatternExtractor()
        assert ex.extract_and_learn("t", None) is None
        assert ex.get_pattern("t") is None

    def test_scalar_input_returns_none_and_marks_no_json(self) -> None:
        ex = ResponsePatternExtractor()
        # An int is neither dict/list nor str -> NO_JSON, returns None.
        assert ex.extract_and_learn("t", 42) is None
        p = ex.get_pattern("t")
        assert p is not None
        assert p.pattern_type == PatternType.NO_JSON
        assert p.observation_count == 1
        assert p.match_count == 1

    def test_no_json_then_structured_data_invalidates(self) -> None:
        """Contract (T-899): a held NO_JSON pattern that later sees structured
        data bumps observation_count but NOT match_count, so consistency decays
        below threshold (forcing future brute-force)."""
        ex = ResponsePatternExtractor()
        ex.extract_and_learn("t", 42)  # -> NO_JSON, obs=1, match=1
        p = ex.get_pattern("t")
        assert p.consistency == 1.0

        out = ex.extract_and_learn("t", {"x": 1})  # structured arrives
        assert out == {"x": 1}
        assert p.pattern_type == PatternType.NO_JSON  # type preserved
        assert p.observation_count == 2
        assert p.match_count == 1  # NOT incremented
        assert p.consistency == pytest.approx(0.5)
        assert p.consistency < ResponsePatternExtractor._CONSISTENCY_THRESHOLD


# ---------------------------------------------------------------------------
# extract_and_learn: string inputs -> brute force + pattern types
# ---------------------------------------------------------------------------


class TestExtractStringInputs:
    def test_full_json_string(self) -> None:
        ex = ResponsePatternExtractor()
        out = ex.extract_and_learn("t", '{"k": "v"}')
        assert out == {"k": "v"}
        p = ex.get_pattern("t")
        assert p.pattern_type == PatternType.FULL_JSON
        assert p.prefix_length == 0

    def test_full_json_array_string_sets_start_char(self) -> None:
        ex = ResponsePatternExtractor()
        out = ex.extract_and_learn("t", "[1, 2, 3]")
        assert out == [1, 2, 3]
        assert ex.get_pattern("t").pattern_type == PatternType.FULL_JSON

    def test_prefix_json(self) -> None:
        ex = ResponsePatternExtractor()
        raw = 'Result: {"ok": true}'
        out = ex.extract_and_learn("t", raw)
        assert out == {"ok": True}
        p = ex.get_pattern("t")
        assert p.pattern_type == PatternType.PREFIX_JSON
        assert p.prefix_length == raw.index("{")

    def test_prefix_sample_populated_when_pattern_type_changes(self) -> None:
        """prefix_sample is captured when a pattern transitions to PREFIX_JSON.

        (Note: per the implementation the sample is only written on a *change*
        of type/prefix_length, not on the very first observation - see report.)
        """
        ex = ResponsePatternExtractor()
        # First observation establishes FULL_JSON.
        ex.extract_and_learn("t", '{"ok": true}')
        # Next observation changes it to PREFIX_JSON -> sample captured.
        raw = 'Result: {"ok": true}'
        ex.extract_and_learn("t", raw)
        p = ex.get_pattern("t")
        assert p.pattern_type == PatternType.PREFIX_JSON
        assert p.prefix_sample == raw[:50]

    def test_suffix_json(self) -> None:
        ex = ResponsePatternExtractor()
        raw = '{"ok": true} -- done'
        out = ex.extract_and_learn("t", raw)
        assert out == {"ok": True}
        p = ex.get_pattern("t")
        assert p.pattern_type == PatternType.SUFFIX_JSON
        assert p.prefix_length == 0

    def test_wrapped_json(self) -> None:
        ex = ResponsePatternExtractor()
        raw = 'before {"ok": true} after'
        out = ex.extract_and_learn("t", raw)
        assert out == {"ok": True}
        p = ex.get_pattern("t")
        assert p.pattern_type == PatternType.WRAPPED_JSON
        assert p.prefix_length == raw.index("{")

    def test_plain_text_string_no_json(self) -> None:
        ex = ResponsePatternExtractor()
        assert ex.extract_and_learn("t", "just some text") is None
        p = ex.get_pattern("t")
        assert p.pattern_type == PatternType.NO_JSON

    def test_empty_string_no_json(self) -> None:
        ex = ResponsePatternExtractor()
        assert ex.extract_and_learn("t", "   ") is None
        assert ex.get_pattern("t").pattern_type == PatternType.NO_JSON

    def test_unbalanced_braces_treated_as_no_json(self) -> None:
        ex = ResponsePatternExtractor()
        assert ex.extract_and_learn("t", 'prefix {"a": 1') is None
        assert ex.get_pattern("t").pattern_type == PatternType.NO_JSON

    def test_json_scalar_in_braces_position_but_invalid_is_no_json(self) -> None:
        # "{not json}" - balanced braces but not valid JSON object.
        ex = ResponsePatternExtractor()
        assert ex.extract_and_learn("t", "{not json}") is None
        assert ex.get_pattern("t").pattern_type == PatternType.NO_JSON


# ---------------------------------------------------------------------------
# Fast-path behaviour once a stable pattern is learned
# ---------------------------------------------------------------------------


class TestFastPath:
    def test_stable_prefix_pattern_uses_fast_path(self) -> None:
        ex = ResponsePatternExtractor()
        # Observe the same prefix shape a few times to reach stable consistency.
        for _ in range(3):
            out = ex.extract_and_learn("t", 'Result: {"n": 1}')
            assert out == {"n": 1}
        p = ex.get_pattern("t")
        assert p.consistency >= ResponsePatternExtractor._CONSISTENCY_THRESHOLD
        before = p.match_count
        # Same shape again: fast path should succeed and bump match_count.
        out = ex.extract_and_learn("t", 'Result: {"n": 99}')
        assert out == {"n": 99}
        assert p.match_count == before + 1

    def test_fast_path_miss_falls_back_to_rebuild(self) -> None:
        """If the learned offset no longer aligns, the extractor must fall back
        to brute force and still extract correctly (observation bumped but the
        miss is not counted as a match in the fast branch)."""
        ex = ResponsePatternExtractor()
        for _ in range(4):
            ex.extract_and_learn("t", 'Result: {"n": 1}')
        p = ex.get_pattern("t")
        assert p.pattern_type == PatternType.PREFIX_JSON
        # Now a differently-shaped response with JSON at a different offset.
        out = ex.extract_and_learn("t", 'DIFFERENT PREFIX HERE {"m": 2}')
        assert out == {"m": 2}

    def test_held_no_json_with_jsonlike_input_rebuilds(self) -> None:
        """A held NO_JSON pattern that suddenly sees a JSON-looking string must
        re-run brute force (rebuild) rather than blindly returning None."""
        ex = ResponsePatternExtractor()
        # Establish NO_JSON over plain text.
        ex.extract_and_learn("t", "plain text one")
        ex.extract_and_learn("t", "plain text two")
        p = ex.get_pattern("t")
        assert p.pattern_type == PatternType.NO_JSON
        # Now JSON-looking input arrives.
        out = ex.extract_and_learn("t", '{"now": "json"}')
        assert out == {"now": "json"}
        assert p.pattern_type == PatternType.FULL_JSON


# ---------------------------------------------------------------------------
# Registry accessors
# ---------------------------------------------------------------------------


class TestRegistryAccessors:
    def test_get_all_patterns_returns_copy(self) -> None:
        ex = ResponsePatternExtractor()
        ex.extract_and_learn("a", {"x": 1})
        snap = ex.get_all_patterns()
        assert "a" in snap
        # Mutating the snapshot must not affect internal state.
        snap.clear()
        assert ex.get_pattern("a") is not None

    def test_set_pattern_restores_persisted(self) -> None:
        ex = ResponsePatternExtractor()
        p = ExtractionPattern(
            tool_key="restored",
            pattern_type=PatternType.FULL_JSON,
            observation_count=5,
            match_count=5,
        )
        ex.set_pattern(p)
        assert ex.get_pattern("restored") is p


# ---------------------------------------------------------------------------
# _find_balanced helper
# ---------------------------------------------------------------------------


class TestFindBalanced:
    def test_simple_object(self) -> None:
        assert _find_balanced('{"a": 1}', "{") == '{"a": 1}'

    def test_object_with_trailing_text(self) -> None:
        assert _find_balanced('{"a": 1} trailing', "{") == '{"a": 1}'

    def test_nested(self) -> None:
        s = '{"a": {"b": [1, 2]}}'
        assert _find_balanced(s, "{") == s

    def test_array(self) -> None:
        assert _find_balanced("[1, [2, 3]] x", "[") == "[1, [2, 3]]"

    def test_brace_inside_string_ignored(self) -> None:
        s = '{"a": "}{ not real"}'
        assert _find_balanced(s, "{") == s

    def test_escaped_quote_in_string(self) -> None:
        s = '{"a": "she said \\"hi\\""}'
        result = _find_balanced(s, "{")
        assert result == s
        assert json.loads(result) == {"a": 'she said "hi"'}

    def test_unbalanced_returns_none(self) -> None:
        assert _find_balanced('{"a": 1', "{") is None

    def test_wrong_start_char_returns_none(self) -> None:
        assert _find_balanced('{"a": 1}', "[") is None

    def test_empty_returns_none(self) -> None:
        assert _find_balanced("", "{") is None

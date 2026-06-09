"""TEST-FIRST spec for R-5: sample capture on first observation.

On the FIRST observation of a tool, ``_rebuild_from_json_like`` creates the
pattern already set to the detected type, so the later
``if pattern.pattern_type != new_type`` guard is False and
``prefix_sample`` / ``suffix_sample`` are never populated.

The first sighting of a tool's output must populate prefix_sample /
suffix_sample for PREFIX_/SUFFIX_/WRAPPED_JSON patterns.
"""

from __future__ import annotations

from ploston_core.schema.extractor import (
    PatternType,
    ResponsePatternExtractor,
)


def test_first_observation_prefix_json_populates_prefix_sample():
    ext = ResponsePatternExtractor()
    raw = "Here is the result: " + '{"value": 42}'
    result = ext.extract_and_learn("tool.a", raw)

    assert result == {"value": 42}
    pattern = ext.get_pattern("tool.a")
    assert pattern is not None
    assert pattern.pattern_type == PatternType.PREFIX_JSON
    assert pattern.observation_count == 1
    # Defect under test: must be populated on FIRST observation.
    assert pattern.prefix_sample is not None
    assert pattern.prefix_sample.startswith("Here is the result:")


def test_first_observation_suffix_json_populates_suffix_sample():
    ext = ResponsePatternExtractor()
    raw = '{"value": 42}' + " -- done extracting now"
    result = ext.extract_and_learn("tool.b", raw)

    assert result == {"value": 42}
    pattern = ext.get_pattern("tool.b")
    assert pattern is not None
    assert pattern.pattern_type == PatternType.SUFFIX_JSON
    assert pattern.observation_count == 1
    assert pattern.suffix_sample is not None
    assert pattern.suffix_sample.endswith("done extracting now")


def test_first_observation_wrapped_json_populates_both_samples():
    ext = ResponsePatternExtractor()
    raw = "PREFIX>> " + '{"value": 42}' + " <<SUFFIX"
    result = ext.extract_and_learn("tool.c", raw)

    assert result == {"value": 42}
    pattern = ext.get_pattern("tool.c")
    assert pattern is not None
    assert pattern.pattern_type == PatternType.WRAPPED_JSON
    assert pattern.observation_count == 1
    assert pattern.prefix_sample is not None
    assert pattern.prefix_sample.startswith("PREFIX>>")
    assert pattern.suffix_sample is not None
    assert pattern.suffix_sample.endswith("<<SUFFIX")


def test_first_observation_full_json_has_no_samples():
    """FULL_JSON has no prefix/suffix, so samples stay None."""
    ext = ResponsePatternExtractor()
    result = ext.extract_and_learn("tool.d", '{"value": 42}')

    assert result == {"value": 42}
    pattern = ext.get_pattern("tool.d")
    assert pattern is not None
    assert pattern.pattern_type == PatternType.FULL_JSON
    assert pattern.prefix_sample is None
    assert pattern.suffix_sample is None

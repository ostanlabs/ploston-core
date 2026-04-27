"""F-088 · T-899/T-901 · SE-01..SE-04 -- NO_JSON sentinel & recovery behaviour."""

from ploston_core.schema.extractor import PatternType, ResponsePatternExtractor


def _make() -> ResponsePatternExtractor:
    return ResponsePatternExtractor()


def test_se01_pure_text_keeps_pattern_and_high_consistency():
    # SE-01: repeated plain-text outputs -> NO_JSON with consistency ~1.
    extractor = _make()
    for _ in range(10):
        result = extractor.extract_and_learn("svc__log", "info: nothing happened")
        assert result is None

    pattern = extractor.get_all_patterns()["svc__log"]
    assert pattern.pattern_type == PatternType.NO_JSON
    assert pattern.observation_count == 10
    assert pattern.match_count == 10
    assert pattern.consistency == 1.0


def test_se02_structured_output_invalidates_no_json_pattern():
    # SE-02: flipping from text to dict drops consistency below threshold so
    # future calls fall back to full analysis instead of treating it as NO_JSON.
    extractor = _make()
    for _ in range(3):
        extractor.extract_and_learn("svc__mixed", "hello world")
    baseline = extractor.get_all_patterns()["svc__mixed"]
    assert baseline.pattern_type == PatternType.NO_JSON
    assert baseline.consistency == 1.0

    # Structured observation: the pattern must remain NO_JSON with consistency
    # strictly below 1.0 -- match_count frozen while observation_count grows.
    returned = extractor.extract_and_learn("svc__mixed", {"id": 7})
    assert returned == {"id": 7}
    pattern = extractor.get_all_patterns()["svc__mixed"]
    assert pattern.pattern_type == PatternType.NO_JSON
    assert pattern.observation_count == 4
    assert pattern.match_count == 3
    assert pattern.consistency < 1.0


def test_se03_json_like_string_on_no_json_triggers_full_analysis():
    # SE-03: string starting with '{' re-runs brute-force and upgrades the
    # pattern to FULL_JSON (or PREFIX_JSON etc.), not NO_JSON.
    extractor = _make()
    for _ in range(3):
        extractor.extract_and_learn("svc__flip", "just a line")

    extracted = extractor.extract_and_learn("svc__flip", '{"x": 1}')
    assert extracted == {"x": 1}
    pattern = extractor.get_all_patterns()["svc__flip"]
    assert pattern.pattern_type == PatternType.FULL_JSON


def test_se04_alternating_flips_pattern_type_each_observation():
    # SE-04: alternating JSON/text observations force the stored pattern type
    # to flip every step -- the extractor never settles, so downstream
    # consumers always fall back to brute force.
    extractor = _make()
    types_seen: list[str] = []
    for i in range(10):
        if i % 2 == 0:
            extractor.extract_and_learn("svc__alt", "plain text line")
        else:
            extractor.extract_and_learn("svc__alt", f'{{"x": {i}}}')
        types_seen.append(extractor.get_all_patterns()["svc__alt"].pattern_type.value)

    # Expect alternation between no_json and full_json.
    assert "no_json" in types_seen
    assert "full_json" in types_seen
    # Final observation is JSON → last recorded type is full_json.
    assert types_seen[-1] == "full_json"


def test_se05_scalar_outputs_keep_no_json_pattern_stable():
    # SE-05: scalar (int/bool/None) results on an existing NO_JSON pattern
    # still match (match_count increments alongside observation_count).
    extractor = _make()
    extractor.extract_and_learn("svc__scalar", "idle")
    extractor.extract_and_learn("svc__scalar", 42)
    extractor.extract_and_learn("svc__scalar", True)

    pattern = extractor.get_all_patterns()["svc__scalar"]
    assert pattern.pattern_type == PatternType.NO_JSON
    assert pattern.observation_count == 3
    # ``None`` is short-circuited earlier and shouldn't be counted; other
    # scalars flow into the NO_JSON accept branch.
    assert pattern.match_count == 3

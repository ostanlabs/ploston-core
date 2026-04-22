"""S-272 T-862: import_notes and common_patterns on workflow_schema output."""

from ploston_core.workflow.schema_generator import generate_workflow_schema


def _code_steps():
    return generate_workflow_schema()["code_steps"]


# ── WS-01: import_notes present with fromisoformat recommendation ──


def test_ws01_import_notes_present():
    code_steps = _code_steps()
    assert "import_notes" in code_steps


def test_ws01_import_notes_general_covers_both_forms():
    notes = _code_steps()["import_notes"]
    general = notes.get("general", "")
    assert "import X" in general
    assert "from X import Y" in general


def test_ws01_import_notes_datetime_strptime_allowed():
    datetime_notes = _code_steps()["import_notes"]["datetime"]
    assert "strptime" in datetime_notes
    assert "_strptime" in datetime_notes["strptime"]  # references the reason


def test_ws01_import_notes_datetime_fromisoformat_recommended():
    datetime_notes = _code_steps()["import_notes"]["datetime"]
    assert "fromisoformat" in datetime_notes
    text = datetime_notes["fromisoformat"]
    assert "fromisoformat" in text
    assert "ISO 8601" in text


# ── WS-02: common_patterns includes parse_iso_timestamp via fromisoformat ──


def test_ws02_common_patterns_present():
    assert "common_patterns" in _code_steps()


def test_ws02_common_patterns_parse_iso_timestamp_uses_fromisoformat():
    patterns = _code_steps()["common_patterns"]
    assert "parse_iso_timestamp" in patterns
    snippet = patterns["parse_iso_timestamp"]
    assert "fromisoformat" in snippet
    # Must NOT recommend the old manual string-splitting approach.
    assert "strptime" not in snippet


def test_ws02b_duration_between_timestamps_uses_fromisoformat():
    snippet = _code_steps()["common_patterns"]["duration_between_timestamps"]
    assert "fromisoformat" in snippet
    assert "total_seconds" in snippet


# ── WS-03: common_patterns includes safe_json_extract ──


def test_ws03_common_patterns_safe_json_extract():
    patterns = _code_steps()["common_patterns"]
    assert "safe_json_extract" in patterns
    snippet = patterns["safe_json_extract"]
    assert "context.steps" in snippet
    assert "isinstance" in snippet


def test_ws03_safe_json_extract_documents_normalization():
    snippet = _code_steps()["common_patterns"]["safe_json_extract"]
    assert "normalized" in snippet

"""Tests for MCP response envelope normalization (S-270, T-857)."""

from ploston_core.engine.normalize import normalize_mcp_response


def test_n01_status_result_content_triple_wrap():
    raw = {"status": "success", "result": {"content": {"workflow_runs": [1, 2]}}}
    assert normalize_mcp_response(raw) == {"workflow_runs": [1, 2]}


def test_n02_content_block_array_with_json_text():
    raw = [{"type": "text", "text": '{"data": 1}'}]
    assert normalize_mcp_response(raw) == {"data": 1}


def test_n03_content_block_array_with_plain_string():
    raw = [{"type": "text", "text": "plain string"}]
    assert normalize_mcp_response(raw) == "plain string"


def test_n04_result_content_wrap():
    raw = {"result": {"content": {"items": []}}}
    assert normalize_mcp_response(raw) == {"items": []}


def test_n05_already_normalized_dict_idempotent():
    raw = {"workflow_runs": [1, 2]}
    assert normalize_mcp_response(raw) == {"workflow_runs": [1, 2]}
    # Second pass must be a no-op
    assert normalize_mcp_response(normalize_mcp_response(raw)) == raw


def test_n06_primitives_passthrough():
    assert normalize_mcp_response(42) == 42
    assert normalize_mcp_response("hello") == "hello"
    assert normalize_mcp_response(None) is None
    assert normalize_mcp_response(True) is True


def test_n07_regression_bare_result_with_siblings_not_stripped():
    """Bare {"result": X, "warnings": [...]} must keep its shape."""
    raw = {"result": {"items": [1, 2]}, "warnings": ["w1"]}
    assert normalize_mcp_response(raw) == raw


def test_n08_empty_list_passthrough():
    assert normalize_mcp_response([]) == []


def test_n07b_bare_result_without_status_not_stripped():
    """Bare {"result": X} without status sibling — tool response, leave it alone."""
    raw = {"result": {"items": [1, 2]}}
    # Because result's value is a dict without "content", leave it alone.
    assert normalize_mcp_response(raw) == raw


def test_content_block_invalid_json_returns_text():
    raw = [{"type": "text", "text": "{not valid json"}]
    assert normalize_mcp_response(raw) == "{not valid json"


def test_single_key_content_dict_unwrap():
    raw = {"content": {"data": 42}}
    assert normalize_mcp_response(raw) == {"data": 42}


def test_multi_key_dict_with_content_not_unwrapped():
    """Only unwrap {"content": X} when it's the SOLE key."""
    raw = {"content": {"data": 42}, "meta": "x"}
    assert normalize_mcp_response(raw) == raw


def test_content_block_array_non_text_type_passthrough():
    raw = [{"type": "image", "url": "http://..."}]
    assert normalize_mcp_response(raw) == raw


def test_status_result_unwraps_when_result_is_primitive():
    raw = {"status": "success", "result": 42}
    assert normalize_mcp_response(raw) == 42


def test_list_of_primitives_passthrough():
    assert normalize_mcp_response([1, 2, 3]) == [1, 2, 3]


# ─── S-289 P1: {"content": X, "error": None} envelope unwrap ────────────────


def test_n09_content_error_null_envelope_unwrap():
    """{"content": X, "error": None} → unwrap to X."""
    raw = {"content": {"workflow_runs": [1, 2]}, "error": None}
    assert normalize_mcp_response(raw) == {"workflow_runs": [1, 2]}


def test_n10_content_error_null_envelope_unwrap_primitive():
    raw = {"content": 42, "error": None}
    assert normalize_mcp_response(raw) == 42


def test_n11_content_error_envelope_with_extra_keys_not_unwrapped():
    """Only unwrap when keys are EXACTLY {"content", "error"}."""
    raw = {"content": {"x": 1}, "error": None, "meta": "info"}
    assert normalize_mcp_response(raw) == raw


def test_n12_domain_payload_with_error_key_not_unwrapped():
    """Tool payloads that legitimately have an "error" key (alongside other
    application keys) must keep their shape — only the exact-shape envelope
    is recognized as transport-level."""
    raw = {"items": [], "error": "no results found"}
    assert normalize_mcp_response(raw) == raw


def test_content_error_non_null_envelope_left_alone_by_normalizer():
    """A non-null error envelope is *not* unwrapped by the normalizer — sandbox
    call sites raise ToolError before normalization. The normalizer leaves it
    as-is so direct callers can still see the error shape."""
    raw = {"content": None, "error": "tool blew up"}
    assert normalize_mcp_response(raw) == raw

"""Tests for MCP response envelope normalization (S-270, T-857)
and multi-content-entry extraction."""

from ploston_core.engine.normalize import normalize_mcp_response
from ploston_core.mcp.connection import MCPConnection


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


# ─── Multi-content-entry support ─────────────────────────────────────────────


def test_n13_multi_text_entries_joined():
    """Multiple text entries in the content array should all be joined."""
    raw = [
        {"type": "text", "text": "message one"},
        {"type": "text", "text": "message two"},
    ]
    assert normalize_mcp_response(raw) == "message one\nmessage two"


def test_n14_text_plus_embedded_resource_text():
    """TextContent + EmbeddedResource (text) — both should be extracted."""
    raw = [
        {"type": "text", "text": "successfully downloaded text file (SHA: abc123)"},
        {
            "type": "resource",
            "resource": {
                "uri": "repo://owner/repo/contents/file.yml",
                "text": "name: ci\non: push\njobs: {}",
                "mimeType": "text/yaml",
            },
        },
    ]
    result = normalize_mcp_response(raw)
    assert "successfully downloaded" in result
    assert "name: ci" in result


def test_n15_text_plus_embedded_resource_binary():
    """TextContent + binary EmbeddedResource — binary gets a placeholder."""
    raw = [
        {"type": "text", "text": "successfully downloaded binary file"},
        {
            "type": "resource",
            "resource": {
                "uri": "repo://owner/repo/contents/logo.png",
                "blob": "iVBORw0KGgo=",
                "mimeType": "image/png",
            },
        },
    ]
    result = normalize_mcp_response(raw)
    assert "successfully downloaded binary file" in result
    assert "[binary content:" in result
    assert "image/png" in result


def test_n16_single_text_entry_still_parses_json():
    """Single text entry with JSON is still parsed — backward compat."""
    raw = [{"type": "text", "text": '{"items": [1, 2]}'}]
    assert normalize_mcp_response(raw) == {"items": [1, 2]}


def test_n17_non_text_type_only_passthrough():
    """Content array with only non-text types is returned as-is."""
    raw = [{"type": "image", "data": "abc123", "mimeType": "image/png"}]
    assert normalize_mcp_response(raw) == raw


def test_n18_embedded_resource_with_empty_resource_passthrough():
    """EmbeddedResource with empty resource dict — gracefully handled."""
    raw = [
        {"type": "text", "text": "file info"},
        {"type": "resource", "resource": {}},
    ]
    result = normalize_mcp_response(raw)
    assert result == "file info"


# ─── _extract_text_from_item (MCPConnection static helper) ───────────────────


class _FakeTextContent:
    """Simulates mcp.types.TextContent."""

    def __init__(self, text: str):
        self.text = text
        self.type = "text"


class _FakeEmbeddedResource:
    """Simulates mcp.types.EmbeddedResource with a text resource."""

    def __init__(self, text: str | None = None, blob: str | None = None, mime: str = "text/plain"):
        self.type = "resource"
        self.resource = type(
            "Resource",
            (),
            {
                "text": text,
                "blob": blob,
                "mimeType": mime,
                "uri": "repo://owner/repo/file.txt",
            },
        )()


class _FakeResourceLink:
    """Simulates mcp.types.ResourceLink."""

    def __init__(self, uri: str, name: str = "", mime: str = ""):
        self.type = "resource_link"
        self.uri = uri
        self.name = name
        self.mimeType = mime


class _FakeImageContent:
    """Simulates mcp.types.ImageContent."""

    def __init__(self, data: str, mime: str = "image/png"):
        self.type = "image"
        self.data = data
        self.mimeType = mime


def test_extract_text_content():
    item = _FakeTextContent("hello world")
    assert MCPConnection._extract_text_from_item(item) == "hello world"


def test_extract_embedded_resource_text():
    item = _FakeEmbeddedResource(text="file contents here")
    result = MCPConnection._extract_text_from_item(item)
    assert result == "file contents here"


def test_extract_embedded_resource_binary():
    item = _FakeEmbeddedResource(blob="iVBORw0KGgo=", mime="image/png")
    result = MCPConnection._extract_text_from_item(item)
    assert "[binary content:" in result
    assert "image/png" in result


def test_extract_embedded_resource_empty():
    """EmbeddedResource with None text and None blob."""
    item = _FakeEmbeddedResource(text=None, blob=None)
    result = MCPConnection._extract_text_from_item(item)
    assert result is None


def test_extract_resource_link():
    item = _FakeResourceLink(uri="https://example.com/file.zip", name="file.zip")
    result = MCPConnection._extract_text_from_item(item)
    assert "resource link" in result
    assert "file.zip" in result
    assert "https://example.com/file.zip" in result


def test_extract_image_content():
    item = _FakeImageContent(data="base64data" * 100, mime="image/jpeg")
    result = MCPConnection._extract_text_from_item(item)
    assert "[image content:" in result
    assert "image/jpeg" in result


def test_extract_dict_text():
    item = {"type": "text", "text": "dict text"}
    assert MCPConnection._extract_text_from_item(item) == "dict text"


def test_extract_bare_string():
    assert MCPConnection._extract_text_from_item("bare string") == "bare string"


def test_extract_unknown_type_returns_none():
    assert MCPConnection._extract_text_from_item(12345) is None
    assert MCPConnection._extract_text_from_item({"type": "unknown"}) is None


# ─── _extract_fastmcp_content integration ────────────────────────────────────


class _FakeCallToolResult:
    """Simulates FastMCP CallToolResult."""

    def __init__(self, content: list, is_error: bool = False):
        self.content = content
        self.isError = is_error


def test_extract_fastmcp_text_plus_embedded_resource():
    """Full integration: TextContent + EmbeddedResource → joined text."""
    conn = MCPConnection.__new__(MCPConnection)
    result = _FakeCallToolResult(
        [
            _FakeTextContent("downloaded file (SHA: abc)"),
            _FakeEmbeddedResource(text="actual: file: content"),
        ]
    )
    extracted = conn._extract_fastmcp_content(result)
    assert "downloaded file" in extracted
    assert "actual: file: content" in extracted


def test_extract_fastmcp_single_json_still_parsed():
    """Backward compat: single TextContent with JSON → parsed dict."""
    conn = MCPConnection.__new__(MCPConnection)
    result = _FakeCallToolResult(
        [
            _FakeTextContent('{"items": [1, 2, 3]}'),
        ]
    )
    extracted = conn._extract_fastmcp_content(result)
    assert extracted == {"items": [1, 2, 3]}


def test_extract_fastmcp_empty_result():
    conn = MCPConnection.__new__(MCPConnection)
    assert conn._extract_fastmcp_content(None) == ""
    assert conn._extract_fastmcp_content("") == ""

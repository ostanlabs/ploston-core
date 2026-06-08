"""Behavioral tests for ploston_core.native_tools.extraction.

These call the real async functions and assert real output/error envelope shapes,
covering auto-detection, each extraction type, bad regex, jsonpath, and file metadata
(including the missing-file path), using a temporary filesystem only where needed.
"""

import pytest

from ploston_core.native_tools.extraction import (
    extract_metadata,
    extract_structured_data,
    extract_text_content,
)

# ---------------------------------------------------------------------------
# extract_text_content
# ---------------------------------------------------------------------------


async def test_extract_text_html():
    result = await extract_text_content("<p>Hello <b>World</b></p>", extraction_type="html")
    assert result["success"] is True
    assert result["source_type"] == "html"
    assert result["extracted_text"] == "Hello World"
    assert result["text_length"] == len("Hello World")
    assert result["word_count"] == 2
    assert result["line_count"] == 1


async def test_extract_text_markdown():
    md = "# Title\n\nSome **bold** and *italic* and `code` and [link](http://x)"
    result = await extract_text_content(md, extraction_type="markdown")
    assert result["success"] is True
    assert result["source_type"] == "markdown"
    text = result["extracted_text"]
    assert "Title" in text
    assert "bold" in text and "**" not in text
    assert "italic" in text and "*" not in text
    assert "code" in text and "`" not in text
    assert "link" in text and "http://x" not in text


async def test_extract_text_json():
    result = await extract_text_content('{"a": "foo", "b": ["bar", 42]}', extraction_type="json")
    assert result["success"] is True
    assert result["source_type"] == "json"
    assert "foo" in result["extracted_text"]
    assert "bar" in result["extracted_text"]
    assert "42" in result["extracted_text"]


async def test_extract_text_json_invalid_falls_back_to_raw():
    # Invalid JSON under explicit json type returns the raw string.
    result = await extract_text_content("{not json", extraction_type="json")
    assert result["success"] is True
    assert result["extracted_text"] == "{not json"


async def test_extract_text_xml():
    result = await extract_text_content(
        "<root><a>alpha</a> <b>beta</b></root>", extraction_type="xml"
    )
    assert result["success"] is True
    assert result["source_type"] == "xml"
    # Tags are stripped and runs of whitespace collapsed to a single space.
    assert result["extracted_text"] == "alpha beta"


async def test_extract_text_plain():
    result = await extract_text_content("  just text  ", extraction_type="plain")
    assert result["success"] is True
    assert result["source_type"] == "plain"
    assert result["extracted_text"] == "just text"


async def test_extract_text_auto_detect_html():
    result = await extract_text_content("<div>hi</div>")
    assert result["success"] is True
    assert result["source_type"] == "html"


async def test_extract_text_auto_detect_json():
    result = await extract_text_content('{"k": "v"}')
    assert result["success"] is True
    assert result["source_type"] == "json"


async def test_extract_text_auto_detect_markdown():
    # Has '#' and '*' but does not start with '<', '{', '[' -> markdown.
    result = await extract_text_content("intro # heading with *emphasis*")
    assert result["success"] is True
    assert result["source_type"] == "markdown"


async def test_extract_text_auto_detect_plain():
    result = await extract_text_content("just a sentence with no markup")
    assert result["success"] is True
    assert result["source_type"] == "plain"


async def test_extract_text_empty_source():
    result = await extract_text_content("")
    assert result["success"] is False
    assert result["error"] == "Source is required"


async def test_extract_text_exceeds_max_size():
    result = await extract_text_content("abcdef", max_content_size=3)
    assert result["success"] is False
    assert "exceeds maximum" in result["error"]


# ---------------------------------------------------------------------------
# extract_structured_data
# ---------------------------------------------------------------------------


async def test_extract_structured_regex_found_and_missing():
    source = "Contact: alice@example.com phone unknown"
    patterns = {
        "email": r"[\w.]+@[\w.]+",
        "ssn": r"\d{3}-\d{2}-\d{4}",
    }
    result = await extract_structured_data(source, patterns)
    assert result["success"] is True
    assert result["extraction_type"] == "regex"
    assert result["extracted_data"]["email"] == ["alice@example.com"]
    assert result["fields_found"] == 1
    assert result["fields_missing"] == ["ssn"]


async def test_extract_structured_bad_regex_returns_error_envelope():
    result = await extract_structured_data("text", {"bad": "([unclosed"})
    assert result["success"] is False
    assert "extraction failed" in result["error"]


async def test_extract_structured_empty_source():
    result = await extract_structured_data("", {"a": "b"})
    assert result["success"] is False
    assert result["error"] == "Source is required"


async def test_extract_structured_empty_patterns():
    result = await extract_structured_data("text", {})
    assert result["success"] is False
    assert result["error"] == "Patterns are required"


async def test_extract_structured_jsonpath_dot_notation():
    source = '{"user": {"name": "alice", "tags": ["a", "b"]}}'
    patterns = {"name": "user.name", "second_tag": "user.tags.1", "missing": "user.age"}
    result = await extract_structured_data(source, patterns, extraction_type="jsonpath")
    assert result["success"] is True
    assert result["extracted_data"]["name"] == "alice"
    assert result["extracted_data"]["second_tag"] == "b"
    assert "missing" in result["fields_missing"]


async def test_extract_structured_jsonpath_out_of_range_index():
    source = '{"items": ["only"]}'
    result = await extract_structured_data(source, {"x": "items.5"}, extraction_type="jsonpath")
    assert result["success"] is True
    assert result["fields_missing"] == ["x"]


async def test_extract_structured_jsonpath_invalid_json():
    result = await extract_structured_data("{bad json", {"x": "a.b"}, extraction_type="jsonpath")
    assert result["success"] is False
    assert "Invalid JSON" in result["error"]


# ---------------------------------------------------------------------------
# extract_metadata
# ---------------------------------------------------------------------------


async def test_extract_metadata_happy_path(tmp_path):
    f = tmp_path / "sample.txt"
    f.write_text("hello world")
    result = await extract_metadata(str(f))
    assert result["success"] is True
    assert result["file_name"] == "sample.txt"
    assert result["file_size"] == len("hello world")
    assert result["file_type"] == ".txt"
    assert result["is_file"] is True
    assert result["is_dir"] is False
    assert result["is_readable"] is True
    assert isinstance(result["created_at"], float)
    assert isinstance(result["modified_at"], float)


async def test_extract_metadata_with_workspace_relative_path(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("# hi")
    result = await extract_metadata("doc.md", workspace_dir=str(tmp_path))
    assert result["success"] is True
    assert result["file_name"] == "doc.md"
    assert result["file_path"] == "doc.md"


async def test_extract_metadata_missing_file(tmp_path):
    result = await extract_metadata("does_not_exist.txt", workspace_dir=str(tmp_path))
    assert result["success"] is False
    assert "File not found" in result["error"]


async def test_extract_metadata_outside_workspace_rejected(tmp_path):
    # Absolute path outside the workspace should be rejected by the security check.
    outside = tmp_path.parent / "elsewhere.txt"
    result = await extract_metadata(str(outside), workspace_dir=str(tmp_path / "ws"))
    assert result["success"] is False
    assert "outside workspace" in result["error"]


async def test_extract_metadata_prefix_sibling_falls_back_to_full_path(tmp_path):
    # A sibling dir whose path shares a string prefix with the workspace passes the
    # startswith() security check but is not an actual subpath, so relative_to()
    # raises ValueError and file_path falls back to the absolute path.
    ws = tmp_path / "ws"
    ws.mkdir()
    sibling = tmp_path / "ws_sibling"
    sibling.mkdir()
    f = sibling / "data.txt"
    f.write_text("x")
    result = await extract_metadata(str(f), workspace_dir=str(ws))
    assert result["success"] is True
    assert result["file_path"] == str(f.resolve())


async def test_extract_metadata_directory(tmp_path):
    d = tmp_path / "subdir"
    d.mkdir()
    result = await extract_metadata(str(d))
    assert result["success"] is True
    assert result["is_dir"] is True
    assert result["is_file"] is False


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

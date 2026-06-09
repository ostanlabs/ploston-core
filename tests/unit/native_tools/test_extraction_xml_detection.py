"""TEST-FIRST spec for R-1: XML auto-detection in extraction.

The ``<?xml`` (and ``<root>...</root>``-style) auto-detect branch must be
reachable: XML-declared content must be detected/extracted as ``xml``, not
mis-detected as ``html`` by the generic ``<``→html fallback.

Plain ``<html>``/``<div>`` must still be html; json/markdown/plain detection
must be unchanged.
"""

from ploston_core.native_tools.extraction import (
    _detect_source_type,
    extract_text_content,
)

# ---------------------------------------------------------------------------
# R-1: XML detection must win over the generic "<" → html fallback
# ---------------------------------------------------------------------------


def test_detect_xml_declaration_is_xml():
    src = '<?xml version="1.0"?><note><body>Hi</body></note>'
    assert _detect_source_type(src) == "xml"


def test_detect_xml_declaration_with_leading_whitespace_is_xml():
    src = '   \n<?xml version="1.0" encoding="UTF-8"?><a>b</a>'
    assert _detect_source_type(src) == "xml"


async def test_extract_xml_declaration_source_type_is_xml():
    src = '<?xml version="1.0"?><note><body>Hello World</body></note>'
    result = await extract_text_content(src)  # auto
    assert result["success"] is True
    assert result["source_type"] == "xml"
    assert "Hello World" in result["extracted_text"]


# ---------------------------------------------------------------------------
# Regression: plain HTML still html
# ---------------------------------------------------------------------------


def test_detect_html_tag_is_html():
    assert _detect_source_type("<html><body>Hi</body></html>") == "html"


def test_detect_div_is_html():
    assert _detect_source_type("<div>Hello</div>") == "html"


# ---------------------------------------------------------------------------
# Regression: json / markdown / plain unchanged
# ---------------------------------------------------------------------------


def test_detect_json_object_unchanged():
    assert _detect_source_type('{"a": 1}') == "json"


def test_detect_json_array_unchanged():
    assert _detect_source_type("[1, 2, 3]") == "json"


def test_detect_markdown_unchanged():
    assert _detect_source_type("# Title\n\nSome **bold** text") == "markdown"


def test_detect_plain_unchanged():
    assert _detect_source_type("just some plain text") == "plain"

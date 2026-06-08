"""Behavioral tests for ploston_core.native_tools.data.

These call the real async functions and assert real output/error envelope shapes,
covering happy paths, malformed input, and edge cases.
"""

import json

import pytest

from ploston_core.native_tools.data import (
    transform_csv_to_json,
    transform_json_to_csv,
    transform_json_to_xml,
    transform_xml_to_json,
    validate_data_schema,
)

# ---------------------------------------------------------------------------
# validate_data_schema
# ---------------------------------------------------------------------------


async def test_validate_data_schema_valid():
    schema = {"type": "object", "properties": {"age": {"type": "integer"}}}
    result = await validate_data_schema({"age": 30}, schema)
    assert result["success"] is True
    assert result["valid"] is True
    assert result["is_valid"] is True
    assert result["errors"] == []


async def test_validate_data_schema_invalid_reports_errors():
    schema = {"type": "object", "properties": {"age": {"type": "integer"}}}
    result = await validate_data_schema({"age": "not-an-int"}, schema)
    # Validation runs successfully but data is invalid.
    assert result["success"] is True
    assert result["valid"] is False
    assert result["is_valid"] is False
    assert len(result["errors"]) == 1
    assert isinstance(result["errors"][0], str)


async def test_validate_data_schema_missing_data():
    result = await validate_data_schema(None, {"type": "object"})
    assert result["success"] is False
    assert result["error"] == "Data is required"


async def test_validate_data_schema_missing_schema():
    result = await validate_data_schema({"a": 1}, {})
    assert result["success"] is False
    assert result["error"] == "Schema is required"


async def test_validate_data_schema_malformed_schema():
    # A schema that jsonschema cannot compile -> caught and returned as error.
    result = await validate_data_schema({"a": 1}, {"type": 12345})
    assert result["success"] is False
    assert "Validation failed" in result["error"]


# ---------------------------------------------------------------------------
# transform_json_to_csv
# ---------------------------------------------------------------------------


async def test_json_to_csv_list_of_dicts_with_headers():
    data = [{"name": "alice", "age": 30}, {"name": "bob", "age": 25}]
    result = await transform_json_to_csv(data)
    assert result["success"] is True
    assert result["row_count"] == 2
    assert result["column_count"] == 2
    assert result["headers"] == ["name", "age"]
    lines = result["csv_data"].splitlines()
    assert lines[0] == "name,age"
    assert "alice,30" in result["csv_data"]


async def test_json_to_csv_from_json_string():
    data = json.dumps([{"x": 1}])
    result = await transform_json_to_csv(data)
    assert result["success"] is True
    assert result["headers"] == ["x"]


async def test_json_to_csv_single_dict_coerced_to_list():
    result = await transform_json_to_csv({"only": "one"})
    assert result["success"] is True
    assert result["row_count"] == 1
    assert result["headers"] == ["only"]


async def test_json_to_csv_no_headers_flag():
    data = [{"a": 1}]
    result = await transform_json_to_csv(data, include_headers=False)
    assert result["success"] is True
    # Header line should be omitted; only the data row present.
    assert "a" not in result["csv_data"].splitlines()[0]


async def test_json_to_csv_invalid_json_string():
    result = await transform_json_to_csv("{not valid json")
    assert result["success"] is False
    assert "Invalid JSON" in result["error"]


async def test_json_to_csv_empty_list():
    result = await transform_json_to_csv([])
    assert result["success"] is False
    assert "non-empty list" in result["error"]


async def test_json_to_csv_non_list_non_dict():
    result = await transform_json_to_csv("123")  # parses to int 123
    assert result["success"] is False
    assert "non-empty list" in result["error"]


async def test_json_to_csv_extra_keys_raise_error_envelope():
    # Second row has a key not in the header set -> DictWriter raises ValueError,
    # caught by the outer handler.
    data = [{"a": 1}, {"a": 2, "b": 3}]
    result = await transform_json_to_csv(data)
    assert result["success"] is False
    assert "transformation failed" in result["error"]


# ---------------------------------------------------------------------------
# transform_csv_to_json
# ---------------------------------------------------------------------------


async def test_csv_to_json_with_headers():
    csv_data = "name,age\nalice,30\nbob,25\n"
    result = await transform_csv_to_json(csv_data)
    assert result["success"] is True
    assert result["row_count"] == 2
    assert result["record_count"] == 2
    assert result["column_count"] == 2
    assert result["headers"] == ["name", "age"]
    assert result["json_data"][0]["name"] == "alice"


async def test_csv_to_json_without_headers():
    csv_data = "alice,30\nbob,25\n"
    result = await transform_csv_to_json(csv_data, has_headers=False)
    assert result["success"] is True
    assert result["headers"] == ["column_0", "column_1"]
    assert result["json_data"][0] == {"column_0": "alice", "column_1": "30"}


async def test_csv_to_json_empty_input():
    result = await transform_csv_to_json("")
    assert result["success"] is False
    assert result["error"] == "CSV data is required"


async def test_csv_to_json_no_headers_blank_line_yields_empty_record():
    # A lone newline parses to a single empty row -> zero generated columns.
    result = await transform_csv_to_json("\n", has_headers=False)
    assert result["success"] is True
    assert result["headers"] == []
    assert result["json_data"] == [{}]


# ---------------------------------------------------------------------------
# transform_json_to_xml
# ---------------------------------------------------------------------------


async def test_json_to_xml_dict():
    result = await transform_json_to_xml({"person": {"name": "alice"}})
    assert result["success"] is True
    assert result["root_element"] == "root"
    assert "<person>" in result["xml_data"]
    assert "<name>alice</name>" in result["xml_data"]
    assert result["element_count"] >= 3


async def test_json_to_xml_list_uses_item_element():
    result = await transform_json_to_xml([1, 2, 3], root_element="numbers", item_element="num")
    assert result["success"] is True
    assert result["root_element"] == "numbers"
    assert result["xml_data"].count("<num>") == 3


async def test_json_to_xml_from_string():
    result = await transform_json_to_xml('{"k": "v"}')
    assert result["success"] is True
    assert "<k>v</k>" in result["xml_data"]


async def test_json_to_xml_invalid_json_string():
    result = await transform_json_to_xml("{bad json")
    assert result["success"] is False
    assert "Invalid JSON" in result["error"]


async def test_json_to_xml_nested_list_of_dicts():
    # Exercise the recursive dict+list path together.
    result = await transform_json_to_xml({"items": [{"id": 1}, {"id": 2}]})
    assert result["success"] is True
    assert result["xml_data"].count("<item>") == 2
    assert result["xml_data"].count("<id>") == 2


async def test_json_to_xml_scalar_value():
    # A bare scalar becomes the root element's text.
    result = await transform_json_to_xml('"hello"')  # JSON string scalar
    assert result["success"] is True
    assert "hello" in result["xml_data"]


# ---------------------------------------------------------------------------
# transform_xml_to_json
# ---------------------------------------------------------------------------


async def test_xml_to_json_basic():
    xml = "<root><name>alice</name></root>"
    result = await transform_xml_to_json(xml)
    assert result["success"] is True
    assert result["root_element"] == "root"
    assert result["json_data"] == {"root": {"name": "alice"}}


async def test_xml_to_json_attributes_and_text():
    xml = '<root id="1">hello<child>c</child></root>'
    result = await transform_xml_to_json(xml)
    assert result["success"] is True
    root = result["json_data"]["root"]
    assert root["@attributes"] == {"id": "1"}
    assert root["#text"] == "hello"
    assert root["child"] == "c"


async def test_xml_to_json_repeated_tags_become_list():
    xml = "<root><item>a</item><item>b</item></root>"
    result = await transform_xml_to_json(xml)
    assert result["success"] is True
    assert result["json_data"]["root"]["item"] == ["a", "b"]


async def test_xml_to_json_empty_input():
    result = await transform_xml_to_json("")
    assert result["success"] is False
    assert result["error"] == "XML data is required"


async def test_xml_to_json_malformed():
    result = await transform_xml_to_json("<root><unclosed></root>")
    assert result["success"] is False
    assert "Invalid XML" in result["error"]


async def test_xml_to_json_empty_element_returns_none():
    xml = "<root></root>"
    result = await transform_xml_to_json(xml)
    assert result["success"] is True
    assert result["json_data"] == {"root": None}


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

"""Core data transformation and validation functions."""

import csv
import io
import json
import xml.etree.ElementTree as ET
from typing import Any


async def validate_data_schema(data: Any, schema: dict[str, Any]) -> dict[str, Any]:
    """Validate data against a JSON schema.

    Args:
        data: The data to validate (dict, list, string, or any JSON-serializable object)
        schema: JSON schema defining expected data structure, types, and validation rules

    Returns:
        Dictionary containing:
        - success: Whether validation passed
        - valid: Boolean indicating if data is valid
        - errors: List of validation errors (if any)
        - error: Error message if validation couldn't be performed
    """
    try:
        if data is None:
            return {"success": False, "error": "Data is required"}

        if not schema:
            return {"success": False, "error": "Schema is required"}

        # Try to import jsonschema
        try:
            from jsonschema import ValidationError, validate
        except ImportError:
            return {
                "success": False,
                "error": "jsonschema library not available. Install with: pip install jsonschema",
            }

        # Validate data against schema
        try:
            validate(instance=data, schema=schema)
            return {
                "success": True,
                "valid": True,
                "is_valid": True,  # Alias for valid
                "errors": [],
            }
        except ValidationError as e:
            return {
                "success": True,
                "valid": False,
                "is_valid": False,  # Alias for valid
                "errors": [str(e)],
            }

    except Exception as e:
        return {"success": False, "error": f"Validation failed: {str(e)}"}


async def transform_json_to_csv(
    json_data: str | list[dict] | dict, include_headers: bool = True
) -> dict[str, Any]:
    """Transform JSON data to CSV format.

    Args:
        json_data: JSON data as string, list of dicts, or single dict
        include_headers: Whether to include column headers in CSV

    Returns:
        Dictionary containing:
        - success: Whether transformation was successful
        - csv_data: The CSV formatted string
        - row_count: Number of data rows
        - column_count: Number of columns
        - error: Error message if transformation failed
    """
    try:
        # Parse JSON if string
        if isinstance(json_data, str):
            try:
                json_data = json.loads(json_data)
            except json.JSONDecodeError as e:
                return {"success": False, "error": f"Invalid JSON: {str(e)}"}

        # Convert single dict to list
        if isinstance(json_data, dict):
            json_data = [json_data]

        if not isinstance(json_data, list) or not json_data:
            return {"success": False, "error": "JSON data must be a non-empty list of objects"}

        # Extract headers from first object
        headers = list(json_data[0].keys())

        # Create CSV
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=headers)

        if include_headers:
            writer.writeheader()

        writer.writerows(json_data)
        csv_data = output.getvalue()

        return {
            "success": True,
            "csv_data": csv_data,
            "row_count": len(json_data),
            "column_count": len(headers),
            "headers": headers,
        }

    except Exception as e:
        return {"success": False, "error": f"JSON to CSV transformation failed: {str(e)}"}


async def transform_csv_to_json(csv_data: str, has_headers: bool = True) -> dict[str, Any]:
    """Transform CSV data to JSON format.

    Args:
        csv_data: CSV formatted string
        has_headers: Whether the first row contains column headers

    Returns:
        Dictionary containing:
        - success: Whether transformation was successful
        - json_data: List of dictionaries representing the data
        - row_count: Number of data rows
        - column_count: Number of columns
        - error: Error message if transformation failed
    """
    try:
        if not csv_data:
            return {"success": False, "error": "CSV data is required"}

        # Parse CSV
        input_stream = io.StringIO(csv_data)

        if has_headers:
            reader = csv.DictReader(input_stream)
            json_data = list(reader)
            headers = reader.fieldnames
        else:
            reader = csv.reader(input_stream)
            rows = list(reader)
            if not rows:
                return {"success": False, "error": "CSV data is empty"}

            # Generate column names
            headers = [f"column_{i}" for i in range(len(rows[0]))]
            json_data = [dict(zip(headers, row)) for row in rows]

        return {
            "success": True,
            "json_data": json_data,
            "row_count": len(json_data),
            "record_count": len(json_data),  # Alias for row_count
            "column_count": len(headers) if headers else 0,
            "headers": list(headers) if headers else [],
        }

    except Exception as e:
        return {"success": False, "error": f"CSV to JSON transformation failed: {str(e)}"}


async def transform_json_to_xml(
    json_data: str | dict | list, root_element: str = "root", item_element: str = "item"
) -> dict[str, Any]:
    """Transform JSON data to XML format.

    Args:
        json_data: JSON data as string, dict, or list
        root_element: Name of the root XML element
        item_element: Name for list item elements

    Returns:
        Dictionary containing:
        - success: Whether transformation was successful
        - xml_data: The XML formatted string
        - element_count: Number of XML elements created
        - error: Error message if transformation failed
    """
    try:
        # Parse JSON if string
        if isinstance(json_data, str):
            try:
                json_data = json.loads(json_data)
            except json.JSONDecodeError as e:
                return {"success": False, "error": f"Invalid JSON: {str(e)}"}

        # Create root element
        root = ET.Element(root_element)
        element_count = [1]  # Use list to allow modification in nested function

        def add_to_element(parent, data, item_name=None):
            """Recursively add data to XML element."""
            if isinstance(data, dict):
                for key, value in data.items():
                    child = ET.SubElement(parent, key)
                    element_count[0] += 1
                    add_to_element(child, value)
            elif isinstance(data, list):
                for item in data:
                    child = ET.SubElement(parent, item_name or item_element)
                    element_count[0] += 1
                    add_to_element(child, item)
            else:
                parent.text = str(data)

        add_to_element(root, json_data)

        # Convert to string
        xml_data = ET.tostring(root, encoding="unicode")

        return {
            "success": True,
            "xml_data": xml_data,
            "element_count": element_count[0],
            "root_element": root_element,
        }

    except Exception as e:
        return {"success": False, "error": f"JSON to XML transformation failed: {str(e)}"}


async def transform_xml_to_json(xml_data: str) -> dict[str, Any]:
    """Transform XML data to JSON format.

    Args:
        xml_data: XML formatted string

    Returns:
        Dictionary containing:
        - success: Whether transformation was successful
        - json_data: Dictionary representing the XML structure
        - root_element: Name of the root element
        - error: Error message if transformation failed
    """
    try:
        if not xml_data:
            return {"success": False, "error": "XML data is required"}

        # Parse XML
        try:
            root = ET.fromstring(xml_data)
        except ET.ParseError as e:
            return {"success": False, "error": f"Invalid XML: {str(e)}"}

        def element_to_dict(element):
            """Recursively convert XML element to dictionary."""
            result = {}

            # Add attributes
            if element.attrib:
                result["@attributes"] = element.attrib

            # Add text content
            if element.text and element.text.strip():
                if len(element) == 0:  # No children
                    return element.text.strip()
                result["#text"] = element.text.strip()

            # Add children
            for child in element:
                child_data = element_to_dict(child)
                if child.tag in result:
                    # Convert to list if multiple children with same tag
                    if not isinstance(result[child.tag], list):
                        result[child.tag] = [result[child.tag]]
                    result[child.tag].append(child_data)
                else:
                    result[child.tag] = child_data

            return result if result else None

        json_data = {root.tag: element_to_dict(root)}

        return {"success": True, "json_data": json_data, "root_element": root.tag}

    except Exception as e:
        return {"success": False, "error": f"XML to JSON transformation failed: {str(e)}"}

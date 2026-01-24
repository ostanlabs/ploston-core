"""Core extraction functions for text, PDF, DOCX, and metadata extraction."""

import json
import re
from pathlib import Path
from typing import Any


async def extract_text_content(
    source: str, extraction_type: str = "auto", max_content_size: int = 52428800
) -> dict[str, Any]:
    """Extract text content from various sources (HTML, Markdown, JSON, XML, Plain Text).

    Args:
        source: The source content to extract text from
        extraction_type: Type of extraction - "auto", "html", "markdown", "json", "xml", "plain"
        max_content_size: Maximum content size in bytes (default: 50MB)

    Returns:
        Dictionary containing:
        - success: Whether extraction was successful
        - source_type: Detected or specified source type
        - extracted_text: The extracted plain text
        - text_length: Length of extracted text in characters
        - word_count: Number of words in extracted text
        - line_count: Number of lines in extracted text
        - error: Error message if extraction failed
    """
    try:
        if not source:
            return {"success": False, "error": "Source is required"}

        # Check content size
        if len(source) > max_content_size:
            return {
                "success": False,
                "error": f"Content size exceeds maximum ({max_content_size} bytes)",
            }

        # Auto-detect source type
        if extraction_type == "auto":
            extraction_type = _detect_source_type(source)

        # Extract text based on type
        if extraction_type == "html":
            extracted_text = _extract_from_html(source)
        elif extraction_type == "markdown":
            extracted_text = _extract_from_markdown(source)
        elif extraction_type == "json":
            extracted_text = _extract_from_json(source)
        elif extraction_type == "xml":
            extracted_text = _extract_from_xml(source)
        else:
            extracted_text = _extract_plain_text(source)

        return {
            "success": True,
            "source_type": extraction_type,
            "extracted_text": extracted_text,
            "text_length": len(extracted_text),
            "word_count": len(extracted_text.split()),
            "line_count": extracted_text.count("\n") + 1,
        }

    except Exception as e:
        return {"success": False, "error": f"Text extraction failed: {str(e)}"}


def _detect_source_type(source: str) -> str:
    """Auto-detect the source type."""
    source_stripped = source.strip()
    if source_stripped.startswith("<"):
        return "html"
    elif source_stripped.startswith("{") or source_stripped.startswith("["):
        return "json"
    elif source_stripped.startswith("<?xml"):
        return "xml"
    elif "#" in source and ("*" in source or "_" in source):
        return "markdown"
    else:
        return "plain"


def _extract_from_html(html: str) -> str:
    """Extract text from HTML content."""
    # Simple HTML tag removal
    text = re.sub(r"<[^>]+>", "", html)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_from_markdown(markdown: str) -> str:
    """Extract plain text from markdown."""
    # Remove markdown formatting
    text = re.sub(r"#+\s+", "", markdown)  # Headers
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)  # Bold
    text = re.sub(r"\*([^*]+)\*", r"\1", text)  # Italic
    text = re.sub(r"`([^`]+)`", r"\1", text)  # Code
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)  # Links
    return text.strip()


def _extract_from_json(json_str: str) -> str:
    """Extract text from JSON content."""
    try:
        data = json.loads(json_str)
        return _extract_text_from_object(data)
    except json.JSONDecodeError:
        return json_str


def _extract_text_from_object(obj: Any) -> str:
    """Recursively extract text from JSON objects."""
    if isinstance(obj, str):
        return obj
    elif isinstance(obj, dict):
        return " ".join(_extract_text_from_object(v) for v in obj.values())
    elif isinstance(obj, list):
        return " ".join(_extract_text_from_object(item) for item in obj)
    else:
        return str(obj)


def _extract_from_xml(xml: str) -> str:
    """Extract text from XML content."""
    # Simple XML tag removal
    text = re.sub(r"<[^>]+>", "", xml)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_plain_text(text: str) -> str:
    """Extract plain text (no processing needed)."""
    return text.strip()


async def extract_structured_data(
    source: str, patterns: dict[str, str], extraction_type: str = "regex"
) -> dict[str, Any]:
    """Extract structured data using patterns (regex or JSON paths).

    Args:
        source: The source content to extract from
        patterns: Dictionary of field names to extraction patterns
        extraction_type: Type of extraction - "regex" or "jsonpath"

    Returns:
        Dictionary containing:
        - success: Whether extraction was successful
        - extracted_data: Dictionary of extracted field values
        - fields_found: Number of fields successfully extracted
        - fields_missing: List of fields that couldn't be extracted
        - error: Error message if extraction failed
    """
    try:
        if not source:
            return {"success": False, "error": "Source is required"}

        if not patterns:
            return {"success": False, "error": "Patterns are required"}

        extracted_data = {}
        fields_missing = []

        if extraction_type == "regex":
            for field_name, pattern in patterns.items():
                matches = re.findall(pattern, source)
                if matches:
                    extracted_data[field_name] = matches
                else:
                    fields_missing.append(field_name)

        elif extraction_type == "jsonpath":
            try:
                data = json.loads(source)
                for field_name, path in patterns.items():
                    value = _extract_jsonpath(data, path)
                    if value is not None:
                        extracted_data[field_name] = value
                    else:
                        fields_missing.append(field_name)
            except json.JSONDecodeError as e:
                return {"success": False, "error": f"Invalid JSON: {str(e)}"}

        return {
            "success": True,
            "extracted_data": extracted_data,
            "fields_found": len(extracted_data),
            "fields_missing": fields_missing,
            "extraction_type": extraction_type,
        }

    except Exception as e:
        return {"success": False, "error": f"Structured data extraction failed: {str(e)}"}


def _extract_jsonpath(data: Any, path: str) -> Any:
    """Simple JSONPath extraction (supports dot notation)."""
    parts = path.split(".")
    current = data

    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            if 0 <= index < len(current):
                current = current[index]
            else:
                return None
        else:
            return None

    return current


async def extract_metadata(file_path: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Extract metadata from files (size, type, timestamps, etc.).

    Args:
        file_path: Path to the file (relative to workspace_dir)
        workspace_dir: Base workspace directory (default: current directory)

    Returns:
        Dictionary containing:
        - success: Whether extraction was successful
        - file_name: Name of the file
        - file_size: Size in bytes
        - file_type: File extension
        - created_time: File creation timestamp
        - modified_time: File modification timestamp
        - is_readable: Whether file is readable
        - is_writable: Whether file is writable
        - error: Error message if extraction failed
    """
    try:
        # Resolve workspace directory
        if workspace_dir:
            base_path = Path(workspace_dir).resolve()
        else:
            base_path = Path.cwd()

        # Resolve file path
        file_path_obj = Path(file_path)
        if file_path_obj.is_absolute():
            full_path = file_path_obj.resolve()
        else:
            full_path = (base_path / file_path).resolve()

        # Security check: ensure path is within workspace (only if workspace_dir is specified)
        if workspace_dir and not str(full_path).startswith(str(base_path)):
            return {"success": False, "error": f"Path {file_path} is outside workspace directory"}

        # Check if file exists
        if not full_path.exists():
            return {"success": False, "error": f"File not found: {file_path}"}

        # Extract metadata
        stat = full_path.stat()

        # Calculate relative path if workspace_dir is specified
        if workspace_dir:
            try:
                relative_path = str(full_path.relative_to(base_path))
            except ValueError:
                relative_path = str(full_path)
        else:
            relative_path = str(full_path)

        return {
            "success": True,
            "file_name": full_path.name,
            "file_path": relative_path,
            "file_size": stat.st_size,
            "file_type": full_path.suffix,
            "created_at": stat.st_ctime,
            "modified_at": stat.st_mtime,
            "is_file": full_path.is_file(),
            "is_dir": full_path.is_dir(),
            "is_readable": full_path.exists(),
            "is_writable": full_path.exists() and full_path.stat().st_mode & 0o200,
        }

    except Exception as e:
        return {"success": False, "error": f"Metadata extraction failed: {str(e)}"}

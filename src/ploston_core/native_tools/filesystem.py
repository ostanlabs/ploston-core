"""Core filesystem operations for native tools MCP server.

These functions provide the core logic for filesystem operations,
extracted from the agent's filesystem tools for use in the MCP server.
"""

import json
import os
from pathlib import Path
from typing import Any

import yaml


def read_file_content(
    path: str, workspace_dir: str | None = None, encoding: str = "utf-8", format: str = "text"
) -> dict[str, Any]:
    """Read content from a file with format parsing.

    Args:
        path: File path (relative to workspace or absolute)
        workspace_dir: Workspace directory for security validation
        encoding: Text encoding (default: utf-8)
        format: Output format - "text", "json", "yaml", or "auto"

    Returns:
        Dictionary with:
        - content: File content (parsed if json/yaml)
        - path: Resolved file path
        - size: File size in bytes
        - format: Detected/used format
        - encoding: Used encoding

    Raises:
        ValueError: If path is outside workspace or invalid
        FileNotFoundError: If file doesn't exist
        Exception: For other file operation errors
    """
    # Set default workspace
    if workspace_dir is None:
        workspace_dir = os.getcwd()
    workspace_path = Path(workspace_dir).resolve()

    # Resolve file path
    file_path = Path(path)
    if not file_path.is_absolute():
        file_path = workspace_path / file_path
    file_path = file_path.resolve()

    # Security check: ensure file is within workspace
    if not str(file_path).startswith(str(workspace_path)):
        raise ValueError(f"File path {file_path} is outside workspace directory {workspace_path}")

    # Check if file exists and is a file
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    if not file_path.is_file():
        raise ValueError(f"Path is not a file: {file_path}")

    # Auto-detect format if requested
    file_format = format
    if format == "auto":
        ext = file_path.suffix.lower()
        if ext == ".json":
            file_format = "json"
        elif ext in [".yaml", ".yml"]:
            file_format = "yaml"
        else:
            file_format = "text"

    # Read file content
    with open(file_path, encoding=encoding) as f:
        content = f.read()

    # Parse content based on format
    parsed_content = content
    if file_format == "json":
        try:
            parsed_content = json.loads(content)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON format: {e}")
    elif file_format == "yaml":
        try:
            parsed_content = yaml.safe_load(content)
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML format: {e}")

    return {
        "content": parsed_content,
        "path": str(file_path),
        "size": len(content),
        "format": file_format,
        "encoding": encoding,
    }


def write_file_content(
    path: str,
    content: Any,
    workspace_dir: str | None = None,
    format: str = "text",
    encoding: str = "utf-8",
    overwrite: bool = True,
    create_dirs: bool = True,
) -> dict[str, Any]:
    """Write content to a file with format serialization.

    Args:
        path: File path (relative to workspace or absolute)
        content: Content to write (string, dict, list, etc.)
        workspace_dir: Workspace directory for security validation
        format: Output format - "text", "json", or "yaml"
        encoding: Text encoding (default: utf-8)
        overwrite: Whether to overwrite existing files
        create_dirs: Whether to create parent directories

    Returns:
        Dictionary with:
        - path: Written file path
        - size: Written content size
        - created: Whether file was newly created
        - format: Used format

    Raises:
        ValueError: If path is outside workspace or file exists and overwrite=False
        Exception: For other file operation errors
    """
    # Set default workspace
    if workspace_dir is None:
        workspace_dir = os.getcwd()
    workspace_path = Path(workspace_dir).resolve()

    # Resolve file path
    file_path = Path(path)
    if not file_path.is_absolute():
        file_path = workspace_path / file_path
    file_path = file_path.resolve()

    # Security check: ensure file is within workspace
    if not str(file_path).startswith(str(workspace_path)):
        raise ValueError(f"File path {file_path} is outside workspace directory {workspace_path}")

    # Check if file exists and overwrite is disabled
    file_existed = file_path.exists()
    if file_existed and not overwrite:
        raise ValueError(f"File already exists and overwrite=False: {file_path}")

    # Create parent directories if requested
    if create_dirs:
        file_path.parent.mkdir(parents=True, exist_ok=True)

    # Serialize content based on format
    if format == "json":
        if not isinstance(content, str):
            content = json.dumps(content, indent=2, ensure_ascii=False)
    elif format == "yaml":
        if not isinstance(content, str):
            content = yaml.dump(content, default_flow_style=False, allow_unicode=True)
    else:  # text format
        content = str(content)

    # Write content to file
    with open(file_path, "w", encoding=encoding) as f:
        f.write(content)

    return {
        "path": str(file_path),
        "size": len(content),
        "created": not file_existed,
        "format": format,
        "encoding": encoding,
    }


def list_directory_content(
    path: str = ".",
    workspace_dir: str | None = None,
    recursive: bool = False,
    pattern: str | None = None,
    include_files: bool = True,
    include_dirs: bool = True,
    include_hidden: bool = False,
) -> dict[str, Any]:
    """List directory contents with filtering options.

    Args:
        path: Directory path (relative to workspace or absolute)
        workspace_dir: Workspace directory for security validation
        recursive: Whether to list recursively
        pattern: Glob pattern for filtering (e.g., "*.py")
        include_files: Whether to include files
        include_dirs: Whether to include directories
        include_hidden: Whether to include hidden files/dirs

    Returns:
        Dictionary with:
        - path: Listed directory path
        - items: List of items with metadata (name, type, size, modified)
        - total_files: Total file count
        - total_dirs: Total directory count

    Raises:
        ValueError: If path is outside workspace or not a directory
        FileNotFoundError: If directory doesn't exist
    """
    # Set default workspace
    if workspace_dir is None:
        workspace_dir = os.getcwd()
    workspace_path = Path(workspace_dir).resolve()

    # Resolve directory path
    dir_path = Path(path)
    if not dir_path.is_absolute():
        dir_path = workspace_path / dir_path
    dir_path = dir_path.resolve()

    # Security check: ensure directory is within workspace
    if not str(dir_path).startswith(str(workspace_path)):
        raise ValueError(
            f"Directory path {dir_path} is outside workspace directory {workspace_path}"
        )

    # Check if directory exists
    if not dir_path.exists():
        raise FileNotFoundError(f"Directory not found: {dir_path}")

    if not dir_path.is_dir():
        raise ValueError(f"Path is not a directory: {dir_path}")

    # Collect items
    items = []
    total_files = 0
    total_dirs = 0

    # Use glob for pattern matching or iterdir for simple listing
    if recursive and pattern:
        iterator = dir_path.rglob(pattern)
    elif recursive:
        iterator = dir_path.rglob("*")
    elif pattern:
        iterator = dir_path.glob(pattern)
    else:
        iterator = dir_path.iterdir()

    for item in iterator:
        # Skip hidden files if not included
        if not include_hidden and item.name.startswith("."):
            continue

        is_file = item.is_file()
        is_dir = item.is_dir()

        # Filter by type
        if is_file and not include_files:
            continue
        if is_dir and not include_dirs:
            continue

        # Get item metadata
        stat = item.stat()
        item_data = {
            "name": item.name,
            "path": str(item.relative_to(workspace_path)),
            "type": "file" if is_file else "directory",
            "size": stat.st_size if is_file else 0,
            "modified": stat.st_mtime,
        }
        items.append(item_data)

        if is_file:
            total_files += 1
        elif is_dir:
            total_dirs += 1

    return {
        "path": str(dir_path),
        "items": items,
        "total_files": total_files,
        "total_dirs": total_dirs,
    }


def delete_file_or_directory(
    path: str, workspace_dir: str | None = None, recursive: bool = False
) -> dict[str, Any]:
    """Delete a file or directory.

    Args:
        path: File/directory path (relative to workspace or absolute)
        workspace_dir: Workspace directory for security validation
        recursive: Whether to delete directories recursively

    Returns:
        Dictionary with:
        - path: Deleted path
        - type: "file" or "directory"
        - deleted: True if successful

    Raises:
        ValueError: If path is outside workspace or directory not empty and recursive=False
        FileNotFoundError: If path doesn't exist
    """
    # Set default workspace
    if workspace_dir is None:
        workspace_dir = os.getcwd()
    workspace_path = Path(workspace_dir).resolve()

    # Resolve path
    target_path = Path(path)
    if not target_path.is_absolute():
        target_path = workspace_path / target_path
    target_path = target_path.resolve()

    # Security check: ensure path is within workspace
    if not str(target_path).startswith(str(workspace_path)):
        raise ValueError(f"Path {target_path} is outside workspace directory {workspace_path}")

    # Check if path exists
    if not target_path.exists():
        raise FileNotFoundError(f"Path not found: {target_path}")

    # Delete based on type
    is_file = target_path.is_file()
    is_dir = target_path.is_dir()

    if is_file:
        target_path.unlink()
    elif is_dir:
        if recursive:
            import shutil

            shutil.rmtree(target_path)
        else:
            # Try to remove empty directory
            try:
                target_path.rmdir()
            except OSError:
                raise ValueError(f"Directory not empty and recursive=False: {target_path}")

    return {"path": str(target_path), "type": "file" if is_file else "directory", "deleted": True}

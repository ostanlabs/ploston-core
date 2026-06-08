"""Core filesystem operations for native tools MCP server.

These functions provide the core logic for filesystem operations,
extracted from the agent's filesystem tools for use in the MCP server.
"""

import json
import os
from pathlib import Path
from typing import Any

import yaml

# Default maximum file size for read/write operations (10 MB). This mirrors the
# FilesystemConfig.max_file_size default in the config schema and is used when a
# caller does not pass an explicit limit.
DEFAULT_MAX_FILE_SIZE = 10 * 1024 * 1024


def _resolve_workspace(workspace_dir: str | None) -> Path:
    """Resolve the workspace directory to an absolute, real path.

    NOTE: callers in the server pass an explicit workspace_dir derived from
    config. The ``None`` fallback here resolves CWD only as a last resort for
    direct library callers; the server-level default is fail-closed (see
    ploston.native_tools.config_manager).
    """
    if workspace_dir is None:
        workspace_dir = os.getcwd()
    return Path(workspace_dir).resolve()


def _validate_within_workspace(
    path: str,
    workspace_path: Path,
    *,
    allowed_paths: list[str] | None = None,
    denied_paths: list[str] | None = None,
) -> Path:
    """Resolve ``path`` and enforce that it is contained within the workspace.

    Security properties enforced (PL-C1/C2/C5):
    - Proper containment via ``Path.is_relative_to`` on the *resolved* path,
      so sibling-prefix escapes (e.g. ``/tmp/ws-secret`` vs ``/tmp/ws``) and
      ``..`` traversal are blocked rather than passing a string ``startswith``.
    - Symlink escape rejection: if any existing component of the requested path
      is a symlink that, once resolved, leaves the workspace, the path is
      rejected. (The is_relative_to check on the fully-resolved path already
      catches symlinks-to-outside; this loop additionally guards intermediate
      components and produces a clear error.)
    - denied_paths: the resolved path must not be inside any denied subtree.
    - allowed_paths: when non-empty, the resolved path MUST be inside at least
      one allowed subtree (in addition to being inside the workspace).

    Returns the resolved ``Path``. Raises ``ValueError`` on any violation.
    """
    requested = Path(path)
    if not requested.is_absolute():
        requested = workspace_path / requested

    # Reject symlink components that escape the workspace. We walk the chain of
    # parents (and the target itself) and, for any that exist and are symlinks,
    # ensure their resolved location stays within the workspace.
    for component in [requested, *requested.parents]:
        try:
            if component.is_symlink():
                resolved_component = component.resolve()
                if not _is_relative_to(resolved_component, workspace_path):
                    raise ValueError(
                        f"Path {path} traverses a symlink that escapes the "
                        f"workspace directory {workspace_path}"
                    )
        except OSError:
            # Broken symlink / permission issue mid-chain: treat conservatively.
            break
        # Stop once we reach the workspace root.
        if component == workspace_path:
            break

    resolved = requested.resolve()

    # Proper containment check (replaces the unsafe str.startswith check).
    if not _is_relative_to(resolved, workspace_path):
        raise ValueError(f"Path {resolved} is outside workspace directory {workspace_path}")

    # denied_paths: reject if inside any denied subtree.
    for denied in denied_paths or []:
        denied_resolved = Path(denied).resolve()
        if _is_relative_to(resolved, denied_resolved):
            raise ValueError(f"Path {resolved} is within a denied path: {denied}")

    # allowed_paths: when set, must be inside at least one allowed subtree.
    allowed = allowed_paths or []
    if allowed:
        if not any(_is_relative_to(resolved, Path(a).resolve()) for a in allowed):
            raise ValueError(f"Path {resolved} is not within any allowed path: {allowed}")

    return resolved


def _is_relative_to(path: Path, other: Path) -> bool:
    """Backport-safe ``Path.is_relative_to`` (available natively on 3.9+)."""
    try:
        path.relative_to(other)
        return True
    except ValueError:
        return False


def read_file_content(
    path: str,
    workspace_dir: str | None = None,
    encoding: str = "utf-8",
    format: str = "text",
    max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    allowed_paths: list[str] | None = None,
    denied_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Read content from a file with format parsing.

    Args:
        path: File path (relative to workspace or absolute)
        workspace_dir: Workspace directory for security validation
        encoding: Text encoding (default: utf-8)
        format: Output format - "text", "json", "yaml", or "auto"
        max_file_size: Maximum file size in bytes to read (PL-C3)
        allowed_paths: Optional allowlist of subtrees (PL-C5)
        denied_paths: Optional denylist of subtrees (PL-C5)

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
    # Set default workspace and validate confinement (PL-C1/C2/C5)
    workspace_path = _resolve_workspace(workspace_dir)
    file_path = _validate_within_workspace(
        path,
        workspace_path,
        allowed_paths=allowed_paths,
        denied_paths=denied_paths,
    )

    # Check if file exists and is a file
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    if not file_path.is_file():
        raise ValueError(f"Path is not a file: {file_path}")

    # Enforce max file size on read (PL-C3)
    actual_size = file_path.stat().st_size
    if max_file_size is not None and actual_size > max_file_size:
        raise ValueError(
            f"File size {actual_size} bytes exceeds maximum allowed "
            f"{max_file_size} bytes: {file_path}"
        )

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
    max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    allowed_paths: list[str] | None = None,
    denied_paths: list[str] | None = None,
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
        max_file_size: Maximum content size in bytes to write (PL-C3)
        allowed_paths: Optional allowlist of subtrees (PL-C5)
        denied_paths: Optional denylist of subtrees (PL-C5)

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
    # Set default workspace and validate confinement (PL-C1/C2/C5)
    workspace_path = _resolve_workspace(workspace_dir)
    file_path = _validate_within_workspace(
        path,
        workspace_path,
        allowed_paths=allowed_paths,
        denied_paths=denied_paths,
    )

    # Check if file exists and overwrite is disabled
    file_existed = file_path.exists()
    if file_existed and not overwrite:
        raise ValueError(f"File already exists and overwrite=False: {file_path}")

    # Serialize content based on format
    if format == "json":
        if not isinstance(content, str):
            content = json.dumps(content, indent=2, ensure_ascii=False)
    elif format == "yaml":
        if not isinstance(content, str):
            content = yaml.dump(content, default_flow_style=False, allow_unicode=True)
    else:  # text format
        content = str(content)

    # Enforce max file size on write BEFORE creating dirs / touching disk (PL-C3)
    content_size = len(content.encode(encoding))
    if max_file_size is not None and content_size > max_file_size:
        raise ValueError(
            f"Content size {content_size} bytes exceeds maximum allowed "
            f"{max_file_size} bytes: {file_path}"
        )

    # Create parent directories if requested
    if create_dirs:
        file_path.parent.mkdir(parents=True, exist_ok=True)

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
    allowed_paths: list[str] | None = None,
    denied_paths: list[str] | None = None,
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
        allowed_paths: Optional allowlist of subtrees (PL-C5)
        denied_paths: Optional denylist of subtrees (PL-C5)

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
    # Set default workspace and validate confinement (PL-C1/C2/C5)
    workspace_path = _resolve_workspace(workspace_dir)
    dir_path = _validate_within_workspace(
        path,
        workspace_path,
        allowed_paths=allowed_paths,
        denied_paths=denied_paths,
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
    path: str,
    workspace_dir: str | None = None,
    recursive: bool = False,
    allowed_paths: list[str] | None = None,
    denied_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Delete a file or directory.

    Args:
        path: File/directory path (relative to workspace or absolute)
        workspace_dir: Workspace directory for security validation
        recursive: Whether to delete directories recursively
        allowed_paths: Optional allowlist of subtrees (PL-C5)
        denied_paths: Optional denylist of subtrees (PL-C5)

    Returns:
        Dictionary with:
        - path: Deleted path
        - type: "file" or "directory"
        - deleted: True if successful

    Raises:
        ValueError: If path is outside workspace or directory not empty and recursive=False
        FileNotFoundError: If path doesn't exist
    """
    # Set default workspace and validate confinement (PL-C1/C2/C5)
    workspace_path = _resolve_workspace(workspace_dir)
    target_path = _validate_within_workspace(
        path,
        workspace_path,
        allowed_paths=allowed_paths,
        denied_paths=denied_paths,
    )

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

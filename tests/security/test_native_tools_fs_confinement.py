"""Security tests for native-tools filesystem path confinement (PL-C1/C2/C3).

These cover:
- sibling-prefix escape (`/tmp/ws-secret/...` vs workspace `/tmp/ws`) BLOCKED
- `..` traversal BLOCKED
- symlink pointing outside the workspace BLOCKED
- oversize read/write rejected (max_file_size)
- denied_path rejected
- normal in-workspace read/write still works
"""

from __future__ import annotations

import os

import pytest

from ploston_core.native_tools.filesystem import (
    delete_file_or_directory,
    list_directory_content,
    read_file_content,
    write_file_content,
)


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


# ---------------------------------------------------------------------------
# PL-C1: sibling-prefix escape via string startswith
# ---------------------------------------------------------------------------


def test_sibling_prefix_escape_blocked_read(workspace):
    """`/tmp/ws-secret/...` must NOT be treated as inside `/tmp/ws`."""
    sibling = workspace.parent / "ws-secret"
    sibling.mkdir()
    secret = sibling / "secret.txt"
    secret.write_text("top secret")

    with pytest.raises(ValueError):
        read_file_content(path=str(secret), workspace_dir=str(workspace))


def test_sibling_prefix_escape_blocked_write(workspace):
    sibling = workspace.parent / "ws-secret"
    sibling.mkdir()
    target = sibling / "evil.txt"

    with pytest.raises(ValueError):
        write_file_content(path=str(target), content="x", workspace_dir=str(workspace))


def test_sibling_prefix_escape_blocked_list(workspace):
    sibling = workspace.parent / "ws-secret"
    sibling.mkdir()

    with pytest.raises(ValueError):
        list_directory_content(path=str(sibling), workspace_dir=str(workspace))


def test_sibling_prefix_escape_blocked_delete(workspace):
    sibling = workspace.parent / "ws-secret"
    sibling.mkdir()
    target = sibling / "f.txt"
    target.write_text("x")

    with pytest.raises(ValueError):
        delete_file_or_directory(path=str(target), workspace_dir=str(workspace))


# ---------------------------------------------------------------------------
# PL-C1: `..` traversal
# ---------------------------------------------------------------------------


def test_dotdot_traversal_blocked_read(workspace):
    outside = workspace.parent / "outside.txt"
    outside.write_text("nope")

    with pytest.raises(ValueError):
        read_file_content(path="../outside.txt", workspace_dir=str(workspace))


def test_dotdot_traversal_blocked_write(workspace):
    with pytest.raises(ValueError):
        write_file_content(path="../escaped.txt", content="x", workspace_dir=str(workspace))


# ---------------------------------------------------------------------------
# PL-C2: symlink escape
# ---------------------------------------------------------------------------


def test_symlink_out_of_workspace_blocked_read(workspace):
    """A symlink inside the workspace pointing outside must be rejected."""
    outside = workspace.parent / "outside_target.txt"
    outside.write_text("secret-via-symlink")

    link = workspace / "link.txt"
    link.symlink_to(outside)

    with pytest.raises(ValueError):
        read_file_content(path="link.txt", workspace_dir=str(workspace))


def test_symlink_dir_out_of_workspace_blocked_write(workspace):
    """Writing through a symlinked dir that escapes the workspace is rejected."""
    outside_dir = workspace.parent / "outside_dir"
    outside_dir.mkdir()

    link_dir = workspace / "linkdir"
    link_dir.symlink_to(outside_dir, target_is_directory=True)

    with pytest.raises(ValueError):
        write_file_content(path="linkdir/pwn.txt", content="x", workspace_dir=str(workspace))


# ---------------------------------------------------------------------------
# PL-C3: max file size on read and write
# ---------------------------------------------------------------------------


def test_oversize_read_rejected(workspace):
    big = workspace / "big.txt"
    big.write_text("A" * 5000)

    with pytest.raises(ValueError):
        read_file_content(path="big.txt", workspace_dir=str(workspace), max_file_size=1000)


def test_oversize_write_rejected(workspace):
    with pytest.raises(ValueError):
        write_file_content(
            path="big.txt",
            content="A" * 5000,
            workspace_dir=str(workspace),
            max_file_size=1000,
        )
    # File must not have been created.
    assert not (workspace / "big.txt").exists()


# ---------------------------------------------------------------------------
# PL-C5: denied_paths / allowed_paths
# ---------------------------------------------------------------------------


def test_denied_path_rejected_read(workspace):
    secrets_dir = workspace / "secrets"
    secrets_dir.mkdir()
    secret = secrets_dir / "key.txt"
    secret.write_text("api-key")

    with pytest.raises(ValueError):
        read_file_content(
            path="secrets/key.txt",
            workspace_dir=str(workspace),
            denied_paths=[str(secrets_dir)],
        )


def test_denied_path_rejected_write(workspace):
    secrets_dir = workspace / "secrets"
    secrets_dir.mkdir()

    with pytest.raises(ValueError):
        write_file_content(
            path="secrets/new.txt",
            content="x",
            workspace_dir=str(workspace),
            denied_paths=[str(secrets_dir)],
        )


def test_allowed_paths_restricts_outside_subtree(workspace):
    """When allowed_paths is set, paths outside the allowlist are rejected."""
    allowed = workspace / "public"
    allowed.mkdir()
    other = workspace / "other.txt"
    other.write_text("x")

    with pytest.raises(ValueError):
        read_file_content(
            path="other.txt",
            workspace_dir=str(workspace),
            allowed_paths=[str(allowed)],
        )


# ---------------------------------------------------------------------------
# Happy path: normal in-workspace read/write still works
# ---------------------------------------------------------------------------


def test_normal_write_then_read(workspace):
    res = write_file_content(path="hello.txt", content="hello world", workspace_dir=str(workspace))
    assert res["created"] is True
    assert os.path.exists(workspace / "hello.txt")

    read = read_file_content(path="hello.txt", workspace_dir=str(workspace))
    assert read["content"] == "hello world"


def test_normal_within_size_limit(workspace):
    write_file_content(
        path="small.txt",
        content="abc",
        workspace_dir=str(workspace),
        max_file_size=1000,
    )
    read = read_file_content(path="small.txt", workspace_dir=str(workspace), max_file_size=1000)
    assert read["content"] == "abc"

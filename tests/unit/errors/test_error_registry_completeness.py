"""Area A (REMEDIATION_PLAN.md): error-model correctness.

These tests encode the correct behavior for the error registry/contract:

1. Every error code used at a ``create_error(...)`` / ``AELError(...)`` call site
   in the source tree MUST be registered. This is an AST-based, repo-wide scan
   (not a curated list), so multi-line calls like

       create_error(
           "EXECUTION_TIMEOUT",
           ...
       )

   are caught — the curated-list / single-line-regex guards missed exactly this
   (CR-5: ``engine.types.with_timeout`` raised the unregistered EXECUTION_TIMEOUT,
   which surfaced as a raw ``ValueError("Unknown error code")``).

2. ``with_timeout`` must raise a structured ``AELError`` (WORKFLOW_TIMEOUT), never
   a raw ``ValueError``.

3. An unknown error code must degrade to a structured ``AELError`` (INTERNAL_ERROR),
   never crash with ``ValueError`` (ERROR_REGISTRY_FULL_SPEC safe-fallback contract).
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
import re

import pytest

from ploston_core.errors import AELError, create_error
from ploston_core.errors.registry import ErrorRegistry

SRC_ROOT = pathlib.Path(__file__).resolve().parents[3] / "src" / "ploston_core"

# Error codes are UPPER_SNAKE_CASE; this filter avoids treating message strings
# passed to AELError(...) as codes.
_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
_CODE_CALLERS = {"create_error", "AELError"}


def _collect_used_codes() -> dict[str, list[str]]:
    """AST-scan the whole src tree for error codes used at call sites.

    Returns mapping of code -> ["relpath:lineno", ...].
    """
    used: dict[str, list[str]] = {}
    for py in SRC_ROOT.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if name not in _CODE_CALLERS:
                continue
            # The code may be the first positional arg OR a keyword `code=...`.
            # Scanning only positional args is a blind spot (caught L-1, where an
            # unregistered code slipped through as a keyword argument).
            candidate = node.args[0] if node.args else None
            if candidate is None:
                for kw in node.keywords:
                    if kw.arg == "code":
                        candidate = kw.value
                        break
            if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
                if _CODE_RE.match(candidate.value):
                    loc = f"{py.relative_to(SRC_ROOT)}:{candidate.lineno}"
                    used.setdefault(candidate.value, []).append(loc)
    return used


def test_all_used_error_codes_are_registered() -> None:
    """Every code used at a create_error/AELError call site is registered."""
    registry = ErrorRegistry()
    registered = set(registry.list_codes())
    used = _collect_used_codes()

    # Sanity: the scan must actually find call sites (guards against a broken scan
    # silently passing).
    assert used, "AST scan found no error-code call sites — scan is broken"

    missing = {code: locs for code, locs in used.items() if code not in registered}
    assert not missing, "Unregistered error codes used in src (code -> call sites):\n" + "\n".join(
        f"  {c}: {locs}" for c, locs in sorted(missing.items())
    )


@pytest.mark.asyncio
async def test_with_timeout_raises_aelerror_not_valueerror() -> None:
    """with_timeout on a timeout raises AELError(WORKFLOW_TIMEOUT), not ValueError."""
    from ploston_core.engine.types import with_timeout

    async def never() -> None:
        await asyncio.sleep(30)

    with pytest.raises(AELError) as excinfo:
        await with_timeout(never(), timeout_seconds=0)

    assert not isinstance(excinfo.value, ValueError)
    assert excinfo.value.code == "WORKFLOW_TIMEOUT"


def test_unknown_code_degrades_to_internal_error_not_valueerror() -> None:
    """An unregistered code yields a structured AELError, never a raw ValueError."""
    registry = ErrorRegistry()
    err = registry.create("DEFINITELY_NOT_A_REAL_CODE_XYZ")
    assert isinstance(err, AELError)
    assert not isinstance(err, ValueError)
    assert err.code == "INTERNAL_ERROR"


def test_create_error_helper_unknown_code_does_not_raise_valueerror() -> None:
    """The module-level create_error helper degrades too (no ValueError)."""
    err = create_error("DEFINITELY_NOT_A_REAL_CODE_XYZ")
    assert isinstance(err, AELError)
    assert not isinstance(err, ValueError)
    assert err.code == "INTERNAL_ERROR"

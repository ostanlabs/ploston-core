"""Spec tests for WorkflowEngine._extract_from_path attribute-traversal safety (L-9).

L-9: ``_extract_from_path`` traversed objects with ``hasattr``/``getattr``, so an
author-supplied output ``from_path`` could pull arbitrary attributes off objects
(e.g. ``steps.<id>.__class__``). The fix restricts object traversal to the known
``StepOutput`` fields and dict-key access; arbitrary attribute names resolve to
``None`` rather than leaking object internals.
"""

from __future__ import annotations

from ploston_core.engine.engine import WorkflowEngine
from ploston_core.engine.types import ExecutionContext
from ploston_core.types.execution import StepOutput


def _make_engine() -> WorkflowEngine:
    # _extract_from_path needs no collaborators; pass minimal stand-ins.
    return WorkflowEngine(
        workflow_registry=None,
        tool_invoker=None,
        template_engine=None,
        config=None,
    )


def _context_with_step(step_id: str, output: object) -> ExecutionContext:
    ctx = ExecutionContext(
        execution_id="exec-1",
        workflow=None,
        inputs={"name": "marc"},
        config={},
    )
    ctx.step_outputs[step_id] = StepOutput(
        output=output,
        success=True,
        duration_ms=1,
        step_id=step_id,
    )
    return ctx


def test_extract_legitimate_output_field_and_dict_key():
    """A normal ``steps.<id>.output.<key>`` path still resolves."""
    engine = _make_engine()
    ctx = _context_with_step("fetch", {"items": [1, 2, 3]})

    assert engine._extract_from_path("steps.fetch.output", ctx) == {"items": [1, 2, 3]}
    assert engine._extract_from_path("steps.fetch.output.items", ctx) == [1, 2, 3]
    assert engine._extract_from_path("steps.fetch.status", ctx) == "completed"


def test_extract_rejects_dunder_attribute_traversal():
    """``steps.<id>.__class__`` must NOT leak the object's class via getattr."""
    engine = _make_engine()
    ctx = _context_with_step("fetch", {"items": [1, 2, 3]})

    # Arbitrary dunder on the StepOutput object.
    assert engine._extract_from_path("steps.fetch.__class__", ctx) is None
    # Arbitrary dunder reachable on the inner dict object.
    assert engine._extract_from_path("steps.fetch.output.__class__", ctx) is None
    # A method/private attribute on the dict object must not be reachable either.
    assert engine._extract_from_path("steps.fetch.output.keys", ctx) is None


def test_extract_rejects_arbitrary_object_attribute():
    """Non-whitelisted attributes on the inner object resolve to None."""
    engine = _make_engine()

    class Leaky:
        secret = "do-not-leak"

    ctx = _context_with_step("fetch", Leaky())

    # ``secret`` is a real attribute on the inner object but not a dict key —
    # arbitrary getattr traversal must be rejected.
    assert engine._extract_from_path("steps.fetch.output.secret", ctx) is None

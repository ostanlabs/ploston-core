"""#3 validate_inputs must not mutate the caller-supplied inputs dict (TDD).

Defaults must still be applied to the copy used downstream, but the original
argument dict passed by the caller (and captured in telemetry snapshots) must
remain untouched.
"""

from unittest.mock import MagicMock

from ploston_core.engine.engine import WorkflowEngine
from ploston_core.workflow.types import InputDefinition, WorkflowDefinition


def _make_engine():
    return WorkflowEngine(
        workflow_registry=MagicMock(),
        tool_invoker=MagicMock(),
        template_engine=MagicMock(),
        config=MagicMock(),
    )


def _workflow_with_default():
    return WorkflowDefinition(
        name="wf",
        version="1.0.0",
        inputs=[
            InputDefinition(name="provided", type="string", required=True),
            InputDefinition(name="opt", type="string", required=False, default="DEFAULT"),
        ],
    )


def test_validate_inputs_does_not_mutate_caller_dict():
    engine = _make_engine()
    workflow = _workflow_with_default()

    original = {"provided": "x"}
    result = engine.validate_inputs(workflow, original)

    # The caller's dict must be untouched — no default leaked back in.
    assert original == {"provided": "x"}
    assert "opt" not in original

    # The returned/used copy must carry the applied default.
    assert result is not None, "validate_inputs must return the effective inputs copy"
    assert result["opt"] == "DEFAULT"
    assert result["provided"] == "x"
    # And it must be a distinct object from the caller's dict.
    assert result is not original

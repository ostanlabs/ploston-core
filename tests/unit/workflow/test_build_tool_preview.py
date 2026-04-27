"""F-088 · T-903 · BTP-01..BTP-05 -- _build_tool_preview emits outputs section."""

from ploston_core.workflow.tools import WorkflowToolsProvider
from ploston_core.workflow.types import (
    InputDefinition,
    OutputDefinition,
    StepDefinition,
    WorkflowDefinition,
)


def _workflow(
    *,
    outputs: list[OutputDefinition] | None = None,
    description: str = "runs the thing",
    inputs: list[InputDefinition] | None = None,
) -> WorkflowDefinition:
    return WorkflowDefinition(
        name="my_workflow",
        description=description,
        version="0.1",
        inputs=inputs or [],
        outputs=outputs or [],
        steps=[StepDefinition(id="s1", tool="noop", mcp="svc")],
    )


def test_btp01_outputs_section_included_when_defined():
    wf = _workflow(
        outputs=[
            OutputDefinition(
                name="items",
                from_path="steps.transform.output.items",
                description="array of transformed rows",
            ),
            OutputDefinition(name="count", from_path="steps.count.output.value"),
        ]
    )
    preview, warnings = WorkflowToolsProvider._build_tool_preview(wf)

    assert "outputs" in preview
    outputs = preview["outputs"]
    assert outputs["items"]["from"] == "steps.transform.output.items"
    assert outputs["items"]["description"] == "array of transformed rows"
    assert outputs["count"]["from"] == "steps.count.output.value"
    # No "missing outputs" warning should be emitted.
    assert not any("No outputs defined" in w for w in warnings)


def test_btp02_missing_outputs_emits_warning_and_placeholder():
    wf = _workflow(outputs=[])
    preview, warnings = WorkflowToolsProvider._build_tool_preview(wf)

    assert preview["outputs"] == "(no outputs defined)"
    assert any("No outputs defined" in w for w in warnings)


def test_btp03_output_with_only_name_gets_placeholder_metadata():
    wf = _workflow(outputs=[OutputDefinition(name="bare")])
    preview, _ = WorkflowToolsProvider._build_tool_preview(wf)
    assert preview["outputs"]["bare"] == "(no metadata)"


def test_btp04_existing_preview_fields_unchanged():
    # BTP-04: tool_name / tool_description / parameters carry through.
    wf = _workflow(
        inputs=[InputDefinition(name="repo", description="the repository", required=True)],
        outputs=[OutputDefinition(name="ok", from_path="steps.s1.output.ok")],
    )
    preview, _ = WorkflowToolsProvider._build_tool_preview(wf)

    assert preview["tool_name"] == "my_workflow"
    assert preview["tool_description"] == "runs the thing"
    assert preview["parameters"] == {"repo": "the repository"}
    assert preview["note"].startswith("Agents will see this tool")


def test_btp05_fallback_description_warning_coexists_with_outputs_warning():
    wf = _workflow(description="", outputs=[])
    preview, warnings = WorkflowToolsProvider._build_tool_preview(wf)

    # Both warnings should be present.
    assert any("description is empty" in w for w in warnings)
    assert any("No outputs defined" in w for w in warnings)
    # The fallback description still lands in tool_description.
    assert preview["tool_description"] == "Execute my_workflow workflow"

"""Property-based tests for workflow validation.

Tests circular dependency detection, unique step IDs, valid depends_on references,
and other validation rules using Hypothesis.
"""

from unittest.mock import MagicMock

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from ploston_core.workflow.types import (
    OutputDefinition,
    StepDefinition,
    WorkflowDefinition,
)
from ploston_core.workflow.validator import WorkflowValidator


def make_validator() -> WorkflowValidator:
    """Create a WorkflowValidator with a mock tool registry."""
    mock_registry = MagicMock()
    mock_registry.get.return_value = None  # No tools registered
    return WorkflowValidator(tool_registry=mock_registry)


# =============================================================================
# Strategies for generating workflow components
# =============================================================================

# Valid identifiers (step IDs, workflow names)
valid_identifier = st.from_regex(r'^[a-z][a-z0-9_]{0,15}$', fullmatch=True)

# Valid version strings
valid_version = st.from_regex(r'^[0-9]+\.[0-9]+(\.[0-9]+)?$', fullmatch=True)


def make_code_step(step_id: str, depends_on: list[str] | None = None) -> StepDefinition:
    """Create a simple code step."""
    return StepDefinition(
        id=step_id,
        code="result = 42",
        depends_on=depends_on,
    )


def make_workflow(
    name: str = "test-workflow",
    version: str = "1.0.0",
    steps: list[StepDefinition] | None = None,
) -> WorkflowDefinition:
    """Create a workflow definition."""
    return WorkflowDefinition(
        name=name,
        version=version,
        steps=steps or [make_code_step("step1")],
        outputs=[OutputDefinition(name="result", value="{{ steps.step1.output }}")],
    )


# =============================================================================
# Property Tests for Unique Step IDs
# =============================================================================

@pytest.mark.property
class TestUniqueStepIds:
    """Property tests for unique step ID validation."""

    @given(st.lists(valid_identifier, min_size=2, max_size=10, unique=True))
    @settings(max_examples=50)
    def test_unique_step_ids_are_valid(self, step_ids):
        """Workflows with unique step IDs should pass validation."""
        steps = [make_code_step(sid) for sid in step_ids]
        workflow = make_workflow(steps=steps)

        validator = make_validator()
        result = validator.validate(workflow, check_tools=False)

        # Should not have duplicate step ID errors
        duplicate_errors = [e for e in result.errors if "Duplicate step IDs" in e.message]
        assert len(duplicate_errors) == 0

    @given(
        valid_identifier,
        st.integers(min_value=2, max_value=5)
    )
    @settings(max_examples=50)
    def test_duplicate_step_ids_detected(self, step_id, count):
        """Workflows with duplicate step IDs should fail validation."""
        # Create multiple steps with the same ID
        steps = [make_code_step(step_id) for _ in range(count)]
        workflow = make_workflow(steps=steps)

        validator = make_validator()
        result = validator.validate(workflow, check_tools=False)

        # Should have duplicate step ID error
        duplicate_errors = [e for e in result.errors if "Duplicate step IDs" in e.message]
        assert len(duplicate_errors) == 1
        assert step_id in duplicate_errors[0].message

    @given(
        st.lists(valid_identifier, min_size=3, max_size=8, unique=True),
        st.integers(min_value=0, max_value=2)
    )
    @settings(max_examples=50)
    def test_partial_duplicates_detected(self, unique_ids, dup_index):
        """Workflows with some duplicate IDs should detect them."""
        assume(dup_index < len(unique_ids) - 1)

        # Create steps with one duplicate
        steps = [make_code_step(sid) for sid in unique_ids]
        # Add a duplicate of one step
        duplicate_id = unique_ids[dup_index]
        steps.append(make_code_step(duplicate_id))

        workflow = make_workflow(steps=steps)

        validator = make_validator()
        result = validator.validate(workflow, check_tools=False)

        # Should detect the duplicate
        duplicate_errors = [e for e in result.errors if "Duplicate step IDs" in e.message]
        assert len(duplicate_errors) == 1


# =============================================================================
# Property Tests for Depends On References
# =============================================================================

@pytest.mark.property
class TestDependsOnReferences:
    """Property tests for depends_on reference validation."""

    @given(st.lists(valid_identifier, min_size=2, max_size=6, unique=True))
    @settings(max_examples=50)
    def test_valid_depends_on_references(self, step_ids):
        """Valid depends_on references should pass validation."""
        # Create a chain: step2 depends on step1, step3 depends on step2, etc.
        steps = []
        for i, sid in enumerate(step_ids):
            depends_on = [step_ids[i-1]] if i > 0 else None
            steps.append(make_code_step(sid, depends_on=depends_on))

        workflow = make_workflow(steps=steps)

        validator = make_validator()
        result = validator.validate(workflow, check_tools=False)

        # Should not have dependency not found errors
        dep_errors = [e for e in result.errors if "not found" in e.message and "depends_on" in e.path]
        assert len(dep_errors) == 0

    @given(
        st.lists(valid_identifier, min_size=2, max_size=5, unique=True),
        valid_identifier
    )
    @settings(max_examples=50)
    def test_invalid_depends_on_detected(self, step_ids, invalid_ref):
        """Invalid depends_on references should be detected."""
        assume(invalid_ref not in step_ids)

        # Create steps where last step depends on non-existent step
        steps = [make_code_step(sid) for sid in step_ids[:-1]]
        steps.append(make_code_step(step_ids[-1], depends_on=[invalid_ref]))

        workflow = make_workflow(steps=steps)

        validator = make_validator()
        result = validator.validate(workflow, check_tools=False)

        # Should have dependency not found error
        dep_errors = [e for e in result.errors if "not found" in e.message]
        assert len(dep_errors) == 1
        assert invalid_ref in dep_errors[0].message

    @given(st.lists(valid_identifier, min_size=3, max_size=6, unique=True))
    @settings(max_examples=50)
    def test_multiple_valid_dependencies(self, step_ids):
        """Steps can depend on multiple other steps."""
        # First two steps have no dependencies
        # Third step depends on first two
        steps = [
            make_code_step(step_ids[0]),
            make_code_step(step_ids[1]),
            make_code_step(step_ids[2], depends_on=[step_ids[0], step_ids[1]]),
        ]
        # Add remaining steps depending on previous
        for i in range(3, len(step_ids)):
            steps.append(make_code_step(step_ids[i], depends_on=[step_ids[i-1]]))

        workflow = make_workflow(steps=steps)

        validator = make_validator()
        result = validator.validate(workflow, check_tools=False)

        dep_errors = [e for e in result.errors if "not found" in e.message]
        assert len(dep_errors) == 0


# =============================================================================
# Property Tests for Circular Dependencies
# =============================================================================

@pytest.mark.property
class TestCircularDependencies:
    """Property tests for circular dependency detection."""

    @given(st.lists(valid_identifier, min_size=2, max_size=6, unique=True))
    @settings(max_examples=50)
    def test_linear_chain_no_cycle(self, step_ids):
        """Linear dependency chains should not be detected as cycles."""
        # Create a linear chain
        steps = []
        for i, sid in enumerate(step_ids):
            depends_on = [step_ids[i-1]] if i > 0 else None
            steps.append(make_code_step(sid, depends_on=depends_on))

        workflow = make_workflow(steps=steps)

        # Should not raise circular dependency error
        try:
            order = workflow.get_execution_order()
            assert len(order) == len(step_ids)
        except ValueError as e:
            pytest.fail(f"Linear chain incorrectly detected as cycle: {e}")

    @given(st.lists(valid_identifier, min_size=2, max_size=4, unique=True))
    @settings(max_examples=50)
    def test_simple_cycle_detected(self, step_ids):
        """Simple A->B->A cycles should be detected."""
        assume(len(step_ids) >= 2)

        # Create a cycle: A depends on B, B depends on A
        steps = [
            make_code_step(step_ids[0], depends_on=[step_ids[1]]),
            make_code_step(step_ids[1], depends_on=[step_ids[0]]),
        ]

        workflow = make_workflow(steps=steps)

        with pytest.raises(ValueError, match="[Cc]ircular"):
            workflow.get_execution_order()

    @given(st.lists(valid_identifier, min_size=3, max_size=5, unique=True))
    @settings(max_examples=50)
    def test_longer_cycle_detected(self, step_ids):
        """Longer cycles (A->B->C->A) should be detected."""
        assume(len(step_ids) >= 3)

        # Create a cycle: A->B->C->A
        steps = []
        for i, sid in enumerate(step_ids):
            next_idx = (i + 1) % len(step_ids)
            steps.append(make_code_step(sid, depends_on=[step_ids[next_idx]]))

        workflow = make_workflow(steps=steps)

        with pytest.raises(ValueError, match="[Cc]ircular"):
            workflow.get_execution_order()

    @given(st.lists(valid_identifier, min_size=1, max_size=3, unique=True))
    @settings(max_examples=30)
    def test_self_dependency_detected(self, step_ids):
        """Self-dependencies (A depends on A) should be detected."""
        # Create a step that depends on itself
        steps = [make_code_step(step_ids[0], depends_on=[step_ids[0]])]

        workflow = make_workflow(steps=steps)

        with pytest.raises(ValueError, match="[Cc]ircular"):
            workflow.get_execution_order()


# =============================================================================
# Property Tests for Required Fields
# =============================================================================

@pytest.mark.property
class TestRequiredFields:
    """Property tests for required field validation."""

    @given(valid_identifier, valid_version)
    @settings(max_examples=30)
    def test_valid_name_and_version(self, name, version):
        """Valid name and version should pass validation."""
        workflow = make_workflow(name=name, version=version)

        validator = make_validator()
        result = validator.validate(workflow, check_tools=False)

        # Should not have name/version errors
        name_errors = [e for e in result.errors if "name" in e.path.lower()]
        version_errors = [e for e in result.errors if "version" in e.path.lower()]
        assert len(name_errors) == 0
        assert len(version_errors) == 0

    def test_empty_name_rejected(self):
        """Empty workflow name should be rejected."""
        workflow = make_workflow(name="")

        validator = make_validator()
        result = validator.validate(workflow, check_tools=False)

        name_errors = [e for e in result.errors if "name" in e.message.lower()]
        assert len(name_errors) == 1

    def test_empty_version_rejected(self):
        """Empty workflow version should be rejected."""
        workflow = make_workflow(version="")

        validator = make_validator()
        result = validator.validate(workflow, check_tools=False)

        version_errors = [e for e in result.errors if "version" in e.message.lower()]
        assert len(version_errors) == 1


# =============================================================================
# Property Tests for Tool XOR Code
# =============================================================================

@pytest.mark.property
class TestToolXorCode:
    """Property tests for tool XOR code validation."""

    @given(valid_identifier)
    @settings(max_examples=30)
    def test_code_only_step_valid(self, step_id):
        """Steps with only code should be valid."""
        step = StepDefinition(id=step_id, code="result = 42")
        workflow = make_workflow(steps=[step])

        validator = make_validator()
        result = validator.validate(workflow, check_tools=False)

        xor_errors = [e for e in result.errors if "either" in e.message.lower()]
        assert len(xor_errors) == 0

    @given(valid_identifier)
    @settings(max_examples=30)
    def test_tool_only_step_valid(self, step_id):
        """Steps with only tool should be valid (tool existence not checked)."""
        step = StepDefinition(id=step_id, tool="some_tool", params={"arg": "value"})
        workflow = make_workflow(steps=[step])

        validator = make_validator()
        result = validator.validate(workflow, check_tools=False)

        xor_errors = [e for e in result.errors if "either" in e.message.lower()]
        assert len(xor_errors) == 0

    @given(valid_identifier)
    @settings(max_examples=30)
    def test_both_tool_and_code_rejected(self, step_id):
        """Steps with both tool and code should be rejected."""
        step = StepDefinition(id=step_id, tool="some_tool", code="result = 42")
        workflow = make_workflow(steps=[step])

        validator = make_validator()
        result = validator.validate(workflow, check_tools=False)

        xor_errors = [e for e in result.errors if "not both" in e.message.lower()]
        assert len(xor_errors) == 1

    @given(valid_identifier)
    @settings(max_examples=30)
    def test_neither_tool_nor_code_rejected(self, step_id):
        """Steps with neither tool nor code should be rejected."""
        step = StepDefinition(id=step_id)
        workflow = make_workflow(steps=[step])

        validator = make_validator()
        result = validator.validate(workflow, check_tools=False)

        xor_errors = [e for e in result.errors if "either" in e.message.lower()]
        assert len(xor_errors) == 1

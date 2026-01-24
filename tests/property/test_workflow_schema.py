"""Property-based tests for workflow schema validation.

Uses Hypothesis to generate thousands of test cases automatically.
"""

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

from ploston_core.errors import AELError
from ploston_core.workflow.parser import parse_workflow_yaml

# =============================================================================
# Strategies for generating valid workflow components
# =============================================================================

# Strategy for valid workflow names (alphanumeric, starting with letter)
workflow_names = st.from_regex(r'^[a-zA-Z][a-zA-Z0-9_-]{0,30}$', fullmatch=True)

# Strategy for valid step IDs
step_ids = st.from_regex(r'^[a-zA-Z][a-zA-Z0-9_]{0,20}$', fullmatch=True)

# Strategy for simple safe code blocks
safe_code = st.sampled_from([
    'result = 42',
    'result = "hello"',
    'result = [1, 2, 3]',
    'result = {"key": "value"}',
    'import json\nresult = json.dumps({"a": 1})',
    'import math\nresult = math.sqrt(16)',
    'result = len("test")',
    'result = str(123)',
])

# Strategy for input types
input_types = st.sampled_from(['string', 'integer', 'number', 'boolean', 'array', 'object'])


# =============================================================================
# Test Classes
# =============================================================================

@pytest.mark.property
class TestWorkflowNameValidation:
    """Property tests for workflow name validation."""

    @given(name=workflow_names)
    @settings(max_examples=100)
    def test_valid_names_always_accepted(self, name):
        """Any name matching the valid pattern should be accepted."""
        workflow_yaml = f"""
name: {name}
version: "1.0"
steps:
  - id: step1
    code: result = 1
"""
        # Should not raise
        workflow = parse_workflow_yaml(workflow_yaml)
        assert workflow.name == name

    @given(name=st.text(
        alphabet=st.characters(
            whitelist_categories=('L', 'N'),  # Letters and numbers only
            whitelist_characters='_-'
        ),
        min_size=1,
        max_size=50
    ))
    @settings(max_examples=100)
    def test_names_either_valid_or_rejected(self, name):
        """Any alphanumeric string should either be valid or raise an error."""
        workflow_yaml = f"""
name: "{name}"
version: "1.0"
steps:
  - id: step1
    code: result = 1
"""
        try:
            workflow = parse_workflow_yaml(workflow_yaml)
            # If it passed, name was accepted
            assert workflow.name == name
        except (AELError, yaml.YAMLError):
            pass  # Expected for invalid names


@pytest.mark.property
class TestStepDependencies:
    """Property tests for step dependency resolution."""

    @given(st.lists(step_ids, min_size=1, max_size=5, unique=True))
    @settings(max_examples=50)
    def test_linear_dependencies_resolve_in_order(self, step_names):
        """Linear dependencies should execute in dependency order."""
        steps_yaml = []
        for i, name in enumerate(step_names):
            step = f'  - id: {name}\n    code: result = "{name}"'
            if i > 0:
                step += f'\n    depends_on:\n      - {step_names[i-1]}'
            steps_yaml.append(step)

        workflow_yaml = f"""
name: test-workflow
version: "1.0"
steps:
{chr(10).join(steps_yaml)}
"""
        parsed = parse_workflow_yaml(workflow_yaml)
        execution_order = parsed.get_execution_order()

        # Verify order respects dependencies
        for i, step_id in enumerate(execution_order):
            step = next(s for s in parsed.steps if s.id == step_id)
            if step.depends_on:
                for dep in step.depends_on:
                    assert execution_order.index(dep) < i, \
                        f"Dependency {dep} should come before {step_id}"

    @given(st.lists(step_ids, min_size=2, max_size=4, unique=True))
    @settings(max_examples=30)
    def test_circular_dependencies_detected(self, step_names):
        """Circular dependencies must be detected and rejected."""
        # Create circular dependency: A -> B -> C -> A
        steps_yaml = []
        for i, name in enumerate(step_names):
            next_idx = (i + 1) % len(step_names)
            steps_yaml.append(
                f'  - id: {name}\n    code: result = "{name}"\n    depends_on:\n      - {step_names[next_idx]}'
            )

        workflow_yaml = f"""
name: circular-workflow
version: "1.0"
steps:
{chr(10).join(steps_yaml)}
"""
        parsed = parse_workflow_yaml(workflow_yaml)

        with pytest.raises(ValueError, match='[Cc]ircular'):
            parsed.get_execution_order()


@pytest.mark.property
class TestYAMLParsing:
    """Property tests for YAML parsing robustness."""

    @given(st.binary(max_size=5000))
    @settings(max_examples=100)
    def test_arbitrary_bytes_handled(self, data):
        """Arbitrary bytes should either parse or raise clear error."""
        try:
            text = data.decode('utf-8', errors='replace')
            parse_workflow_yaml(text)
        except (AELError, yaml.YAMLError, ValueError, TypeError):
            pass  # Expected for invalid input
        except Exception as e:
            pytest.fail(f"Unexpected exception: {type(e).__name__}: {e}")

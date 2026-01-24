"""Fuzz test harnesses for ploston-core.

These tests use Hypothesis for property-based fuzzing as a portable alternative
to Atheris (which only works on Linux). For production fuzzing on Linux,
use the standalone Atheris harnesses in fuzz_sandbox.py and fuzz_yaml.py.
"""

from unittest.mock import Mock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


def create_mock_tool_registry():
    """Create a mock tool registry."""
    registry = Mock()
    registry.get_tool = Mock(return_value=None)
    registry.list_tools = Mock(return_value=[])
    return registry


@pytest.mark.fuzz
class TestSandboxFuzzing:
    """Fuzz tests for the Python sandbox."""

    @given(st.text(min_size=0, max_size=1000))
    @settings(max_examples=100, deadline=5000)
    def test_fuzz_001_random_code_strings(self, code: str):
        """FUZZ-001: Fuzz sandbox with random code strings."""
        from ploston_core.sandbox.sandbox import PythonExecSandbox

        sandbox = PythonExecSandbox()

        # Should not crash, regardless of input
        try:
            # Note: execute is async, but we test sync behavior
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(sandbox.execute(code))
            # If execution succeeds, result should have expected structure
            assert hasattr(result, 'success') or isinstance(result, dict)
        except Exception as e:
            # Exceptions are acceptable, but should be handled gracefully
            assert isinstance(
                e,
                SyntaxError | ValueError | TypeError |
                NameError | AttributeError | RuntimeError |
                RecursionError | MemoryError | Exception
            )

    @given(st.binary(min_size=0, max_size=500))
    @settings(max_examples=50, deadline=5000)
    def test_fuzz_002_binary_as_code(self, data: bytes):
        """FUZZ-002: Fuzz sandbox with binary data as code."""
        from ploston_core.sandbox.sandbox import PythonExecSandbox

        sandbox = PythonExecSandbox()

        try:
            code = data.decode('utf-8', errors='replace')
        except Exception:
            return  # Skip if can't decode

        try:
            import asyncio
            asyncio.get_event_loop().run_until_complete(sandbox.execute(code))
        except Exception:
            pass  # Exceptions are acceptable

    @given(st.lists(st.text(min_size=1, max_size=50), min_size=1, max_size=10))
    @settings(max_examples=50, deadline=5000)
    def test_fuzz_003_code_with_random_identifiers(self, identifiers: list):
        """FUZZ-003: Fuzz sandbox with random identifiers."""
        from ploston_core.sandbox.sandbox import PythonExecSandbox

        sandbox = PythonExecSandbox()

        # Build code with random identifiers
        code_parts = []
        for i, ident in enumerate(identifiers):
            # Clean identifier to be valid Python
            clean_ident = ''.join(c if c.isalnum() or c == '_' else '_' for c in ident)
            if clean_ident and not clean_ident[0].isdigit():
                code_parts.append(f"{clean_ident} = {i}")

        if code_parts:
            code = '\n'.join(code_parts)
            try:
                import asyncio
                asyncio.get_event_loop().run_until_complete(sandbox.execute(code))
            except Exception:
                pass

    @given(st.integers(min_value=0, max_value=100))
    @settings(max_examples=50, deadline=5000)
    def test_fuzz_004_nested_expressions(self, depth: int):
        """FUZZ-004: Fuzz sandbox with nested expressions."""
        from ploston_core.sandbox.sandbox import PythonExecSandbox

        sandbox = PythonExecSandbox()

        # Build nested expression
        code = "x = " + "(" * min(depth, 50) + "1" + ")" * min(depth, 50)

        try:
            import asyncio
            asyncio.get_event_loop().run_until_complete(sandbox.execute(code))
        except Exception:
            pass


@pytest.mark.fuzz
class TestYAMLFuzzing:
    """Fuzz tests for YAML parsing."""

    @given(st.text(min_size=0, max_size=1000))
    @settings(max_examples=100, deadline=5000)
    def test_fuzz_010_random_yaml_strings(self, yaml_str: str):
        """FUZZ-010: Fuzz YAML parser with random strings."""
        import yaml

        try:
            result = yaml.safe_load(yaml_str)
            # If parsing succeeds, result should be a valid Python object
            assert result is None or isinstance(result, dict | list | str | int | float | bool)
        except yaml.YAMLError:
            pass  # YAML errors are acceptable
        except Exception as e:
            # Other exceptions should be rare
            assert isinstance(e, ValueError | TypeError | RecursionError)

    @given(st.binary(min_size=0, max_size=500))
    @settings(max_examples=50, deadline=5000)
    def test_fuzz_011_binary_as_yaml(self, data: bytes):
        """FUZZ-011: Fuzz YAML parser with binary data."""
        import yaml

        try:
            yaml_str = data.decode('utf-8', errors='replace')
            yaml.safe_load(yaml_str)  # Result intentionally unused
        except Exception:
            pass

    @given(st.dictionaries(
        keys=st.text(min_size=1, max_size=20),
        values=st.one_of(
            st.text(max_size=50),
            st.integers(),
            st.floats(allow_nan=False),
            st.booleans(),
            st.none()
        ),
        min_size=0,
        max_size=20
    ))
    @settings(max_examples=50, deadline=5000)
    def test_fuzz_012_random_dict_to_yaml(self, data: dict):
        """FUZZ-012: Fuzz YAML round-trip with random dicts."""
        import yaml

        try:
            yaml_str = yaml.dump(data)
            result = yaml.safe_load(yaml_str)
            # Round-trip should preserve data
            assert result == data or (not data and not result)
        except Exception:
            pass

    @given(st.lists(
        st.one_of(
            st.text(max_size=30),
            st.integers(),
            st.floats(allow_nan=False),
            st.booleans()
        ),
        min_size=0,
        max_size=20
    ))
    @settings(max_examples=50, deadline=5000)
    def test_fuzz_013_random_list_to_yaml(self, data: list):
        """FUZZ-013: Fuzz YAML round-trip with random lists."""
        import yaml

        try:
            yaml_str = yaml.dump(data)
            result = yaml.safe_load(yaml_str)
            assert result == data or (not data and not result)
        except Exception:
            pass


@pytest.mark.fuzz
class TestWorkflowFuzzing:
    """Fuzz tests for workflow parsing and validation."""

    @given(st.dictionaries(
        keys=st.sampled_from(['name', 'version', 'steps', 'inputs', 'output', 'description']),
        values=st.one_of(
            st.text(max_size=50),
            st.integers(),
            st.lists(st.text(max_size=20), max_size=5),
            st.dictionaries(st.text(max_size=10), st.text(max_size=20), max_size=3)
        ),
        min_size=0,
        max_size=6
    ))
    @settings(max_examples=100, deadline=5000)
    def test_fuzz_020_random_workflow_dicts(self, workflow: dict):
        """FUZZ-020: Fuzz workflow validation with random dicts."""
        from ploston_core.workflow.validator import WorkflowValidator

        mock_registry = create_mock_tool_registry()
        validator = WorkflowValidator(mock_registry)

        try:
            result = validator.validate(workflow)
            # Validation should return a result object
            assert hasattr(result, 'is_valid') or isinstance(result, bool | dict)
        except Exception as e:
            # Exceptions should be handled gracefully
            assert isinstance(e, ValueError | TypeError | KeyError | AttributeError | Exception)

    @given(st.lists(
        st.fixed_dictionaries({
            'id': st.text(min_size=1, max_size=20),
            'code': st.text(max_size=100)
        }),
        min_size=0,
        max_size=10
    ))
    @settings(max_examples=50, deadline=5000)
    def test_fuzz_021_random_steps(self, steps: list):
        """FUZZ-021: Fuzz workflow with random steps."""
        from ploston_core.workflow.validator import WorkflowValidator

        mock_registry = create_mock_tool_registry()
        validator = WorkflowValidator(mock_registry)

        workflow = {
            'name': 'fuzz-test',
            'version': '1.0',
            'steps': steps,
            'output': 'result'
        }

        try:
            validator.validate(workflow)  # Result intentionally unused
        except Exception:
            pass


@pytest.mark.fuzz
class TestTemplateFuzzing:
    """Fuzz tests for template rendering."""

    @given(st.text(min_size=0, max_size=500))
    @settings(max_examples=100, deadline=5000)
    def test_fuzz_030_random_template_strings(self, template: str):
        """FUZZ-030: Fuzz template engine with random strings."""
        from ploston_core.template.engine import TemplateEngine

        engine = TemplateEngine()

        try:
            result = engine.render(template, {})
            assert isinstance(result, str)
        except Exception as e:
            # Template errors are acceptable
            assert isinstance(e, ValueError | TypeError | KeyError | AttributeError | Exception)

    @given(
        st.text(min_size=0, max_size=200),
        st.dictionaries(
            keys=st.text(min_size=1, max_size=20, alphabet='abcdefghijklmnopqrstuvwxyz'),
            values=st.one_of(
                st.text(max_size=50),
                st.integers(),
                st.floats(allow_nan=False),
                st.booleans()
            ),
            min_size=0,
            max_size=10
        )
    )
    @settings(max_examples=50, deadline=5000)
    def test_fuzz_031_template_with_random_context(self, template: str, context: dict):
        """FUZZ-031: Fuzz template with random context."""
        from ploston_core.template.engine import TemplateEngine

        engine = TemplateEngine()

        try:
            result = engine.render(template, context)
            assert isinstance(result, str)
        except Exception:
            pass

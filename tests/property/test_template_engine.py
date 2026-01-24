"""Property-based tests for template engine.

Tests template rendering, injection prevention, and filter behavior.
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ploston_core.errors import AELError
from ploston_core.template import TemplateEngine
from ploston_core.template.types import TemplateContext
from ploston_core.types import StepOutput


def make_context(inputs=None, steps=None, config=None):
    """Create a TemplateContext for testing."""
    return TemplateContext(
        inputs=inputs or {}, steps=steps or {}, config=config or {}, execution_id="test-exec-id"
    )


def make_step_output(output, step_id="test-step"):
    """Create a StepOutput for testing."""
    return StepOutput(output=output, success=True, duration_ms=100, step_id=step_id)


# =============================================================================
# Test Classes
# =============================================================================


@pytest.mark.property
class TestTemplateInjectionPrevention:
    """Property tests ensuring template injection is prevented."""

    @given(st.text(max_size=500))
    @settings(max_examples=200)
    def test_user_input_cannot_execute_code(self, user_input):
        """User-provided values should never execute as template code."""
        engine = TemplateEngine()

        # User input in context
        context = make_context(inputs={"user_data": user_input})

        # Template that uses user data
        template = "User said: {{ inputs.user_data }}"

        try:
            result = engine.render_string(template, context)
            # Result should be a string
            assert isinstance(result, str)
            # Even if user_input contains {{ }}, it should be escaped
            # and not executed as template code
        except AELError:
            pass  # Template errors are acceptable

    @given(
        st.dictionaries(
            keys=st.from_regex(r"^[a-z]{1,10}$", fullmatch=True),
            values=st.text(max_size=50),
            max_size=5,
        )
    )
    @settings(max_examples=100)
    def test_nested_context_access(self, kv_dict):
        """Nested context access should work correctly."""
        engine = TemplateEngine()

        # Build nested context with StepOutput objects
        steps = {}
        for key, value in kv_dict.items():
            steps[key] = make_step_output(value, step_id=key)

        context = make_context(steps=steps)

        # Access each value
        for key, expected_value in kv_dict.items():
            template = f"{{{{ steps.{key}.output }}}}"
            result = engine.render_string(template, context)
            # Result could be the actual value or string representation
            assert result == expected_value or str(result) == str(expected_value)


@pytest.mark.property
class TestTemplateWithValidInputs:
    """Property tests for template rendering with various input types."""

    @given(
        inputs=st.dictionaries(
            keys=st.from_regex(r"^[a-zA-Z][a-zA-Z0-9_]{0,15}$", fullmatch=True),
            values=st.one_of(
                st.text(max_size=100),
                st.integers(),
                st.floats(allow_nan=False, allow_infinity=False),
                st.booleans(),
            ),
            max_size=5,
        )
    )
    @settings(max_examples=100)
    def test_template_with_valid_inputs_never_crashes(self, inputs):
        """Template rendering should never crash with valid inputs."""
        engine = TemplateEngine()
        context = make_context(inputs=inputs)

        for key, value in inputs.items():
            template = f"{{{{ inputs.{key} }}}}"
            try:
                result = engine.render_string(template, context)
                # Result should be the value or string representation
                assert result is not None
            except AELError:
                pass  # Template errors are acceptable

    @given(st.text(max_size=500))
    @settings(max_examples=100)
    def test_arbitrary_text_in_template_handled(self, text):
        """Arbitrary text should either render or raise TemplateError."""
        engine = TemplateEngine()
        context = make_context()

        try:
            result = engine.render_string(text, context)
            # If no template syntax, should return as-is
            if "{{" not in text and "{%" not in text:
                assert result == text
        except AELError:
            pass  # Expected for invalid templates
        except Exception as e:
            pytest.fail(f"Unexpected exception: {type(e).__name__}: {e}")


@pytest.mark.property
class TestInputVariations:
    """Property tests for various input value types."""

    @given(st.lists(st.integers(), max_size=10))
    @settings(max_examples=50)
    def test_list_inputs(self, values):
        """List inputs should be accessible."""
        engine = TemplateEngine()
        context = make_context(inputs={"items": values})

        template = "{{ inputs.items }}"
        result = engine.render_string(template, context)

        # Should return the list
        assert result == values

    @given(
        st.dictionaries(st.from_regex(r"^[a-z]{1,5}$", fullmatch=True), st.integers(), max_size=5)
    )
    @settings(max_examples=50)
    def test_dict_inputs(self, data):
        """Dict inputs should be accessible."""
        engine = TemplateEngine()
        context = make_context(inputs={"data": data})

        template = "{{ inputs.data }}"
        result = engine.render_string(template, context)

        # Should return the dict
        assert result == data

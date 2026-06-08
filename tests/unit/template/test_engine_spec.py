"""Spec tests for the restricted TemplateEngine.

The engine renders ``{{ ... }}`` expressions over a TemplateContext and is
deliberately *restricted*: it supports variable access, dotted/indexed
navigation, and a fixed filter set, and MUST reject arbitrary Python,
control flow, arithmetic, and function calls (per the class docstring).

Tests assert intended behaviour + error contracts:
- pure templates preserve type; mixed content yields strings
- undefined variables / unknown namespaces / unknown filters raise
  AELError(TEMPLATE_ERROR)
- restricted constructs are rejected
- builtin methods on primitives are not accessible
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from ploston_core.errors import AELError
from ploston_core.template.engine import TemplateEngine
from ploston_core.template.types import RenderResult, TemplateContext
from ploston_core.types import StepOutput


def _ctx(**overrides) -> TemplateContext:
    base = dict(
        inputs={},
        steps={},
        config={},
        execution_id="exec-1",
        workflow=None,
    )
    base.update(overrides)
    return TemplateContext(**base)


@pytest.fixture
def engine() -> TemplateEngine:
    return TemplateEngine()


# ---------------------------------------------------------------------------
# render() — structure traversal + RenderResult contract
# ---------------------------------------------------------------------------


class TestRender:
    def test_plain_string_no_templates(self, engine: TemplateEngine) -> None:
        r = engine.render("hello", _ctx())
        assert isinstance(r, RenderResult)
        assert r.value == "hello"
        assert r.had_templates is False
        assert r.templates_rendered == []

    def test_simple_input_substitution(self, engine: TemplateEngine) -> None:
        r = engine.render("{{ inputs.name }}", _ctx(inputs={"name": "Ada"}))
        assert r.value == "Ada"
        assert r.had_templates is True
        assert "inputs.name" in r.templates_rendered

    def test_nested_dict_and_list_rendered(self, engine: TemplateEngine) -> None:
        ctx = _ctx(inputs={"a": 1, "b": "two"})
        template = {
            "x": "{{ inputs.a }}",
            "y": ["{{ inputs.b }}", "static"],
        }
        r = engine.render(template, ctx)
        assert r.value == {"x": 1, "y": ["two", "static"]}
        assert r.had_templates is True

    def test_primitive_passthrough(self, engine: TemplateEngine) -> None:
        for prim in (42, 3.14, True, None):
            r = engine.render(prim, _ctx())
            assert r.value == prim
            assert r.had_templates is False

    def test_render_propagates_syntax_error(self, engine: TemplateEngine) -> None:
        with pytest.raises(AELError) as ei:
            engine.render("{{ a + b }}", _ctx(inputs={"a": 1}))
        assert ei.value.code == "TEMPLATE_ERROR"


# ---------------------------------------------------------------------------
# render_string() — type preservation vs interpolation
# ---------------------------------------------------------------------------


class TestRenderString:
    def test_pure_template_preserves_int(self, engine: TemplateEngine) -> None:
        out = engine.render_string("{{ inputs.count }}", _ctx(inputs={"count": 5}))
        assert out == 5
        assert isinstance(out, int)

    def test_pure_template_preserves_list(self, engine: TemplateEngine) -> None:
        out = engine.render_string("{{ inputs.items }}", _ctx(inputs={"items": [1, 2]}))
        assert out == [1, 2]

    def test_mixed_content_returns_string(self, engine: TemplateEngine) -> None:
        out = engine.render_string("Hi {{ inputs.n }}", _ctx(inputs={"n": "Bob"}))
        assert out == "Hi Bob"

    def test_mixed_content_none_becomes_empty(self, engine: TemplateEngine) -> None:
        out = engine.render_string("x={{ inputs.n }}", _ctx(inputs={"n": None}))
        assert out == "x="

    def test_no_templates_returns_unchanged(self, engine: TemplateEngine) -> None:
        assert engine.render_string("nothing here", _ctx()) == "nothing here"

    def test_interpolation_without_spaces(self, engine: TemplateEngine) -> None:
        out = engine.render_string("v={{inputs.n}}", _ctx(inputs={"n": "Q"}))
        assert out == "v=Q"


# ---------------------------------------------------------------------------
# Variable resolution: namespaces + navigation
# ---------------------------------------------------------------------------


class TestVariableResolution:
    def test_steps_output_navigation(self, engine: TemplateEngine) -> None:
        step = StepOutput(
            output={"data": {"value": 99}},
            success=True,
            duration_ms=10,
            step_id="fetch",
        )
        out = engine.render_string(
            "{{ steps.fetch.output.data.value }}", _ctx(steps={"fetch": step})
        )
        assert out == 99

    def test_step_success_field(self, engine: TemplateEngine) -> None:
        step = StepOutput(output=1, success=True, duration_ms=5, step_id="s")
        out = engine.render_string("{{ steps.s.success }}", _ctx(steps={"s": step}))
        assert out is True

    def test_config_namespace(self, engine: TemplateEngine) -> None:
        out = engine.render_string("{{ config.timeout }}", _ctx(config={"timeout": 30}))
        assert out == 30

    def test_execution_id_namespace(self, engine: TemplateEngine) -> None:
        out = engine.render_string("{{ execution_id }}", _ctx(execution_id="run-77"))
        assert out == "run-77"

    def test_workflow_namespace(self, engine: TemplateEngine) -> None:
        out = engine.render_string("{{ workflow.name }}", _ctx(workflow={"name": "wf1"}))
        assert out == "wf1"

    def test_workflow_namespace_none_defaults_to_empty(self, engine: TemplateEngine) -> None:
        # workflow is None -> treated as {} -> missing key raises TEMPLATE_ERROR.
        with pytest.raises(AELError) as ei:
            engine.render_string("{{ workflow.name }}", _ctx(workflow=None))
        assert ei.value.code == "TEMPLATE_ERROR"

    def test_array_indexing(self, engine: TemplateEngine) -> None:
        out = engine.render_string("{{ inputs.items[1] }}", _ctx(inputs={"items": ["a", "b", "c"]}))
        assert out == "b"

    def test_nested_key_then_index(self, engine: TemplateEngine) -> None:
        out = engine.render_string(
            "{{ inputs.data.list[0] }}",
            _ctx(inputs={"data": {"list": [10, 20]}}),
        )
        assert out == 10


# ---------------------------------------------------------------------------
# Error contracts: undefined / unknown namespaces / bad index
# ---------------------------------------------------------------------------


class TestResolutionErrors:
    def test_unknown_namespace_raises(self, engine: TemplateEngine) -> None:
        with pytest.raises(AELError) as ei:
            engine.render_string("{{ bogus.x }}", _ctx())
        assert ei.value.code == "TEMPLATE_ERROR"

    def test_missing_input_key_raises(self, engine: TemplateEngine) -> None:
        with pytest.raises(AELError) as ei:
            engine.render_string("{{ inputs.missing }}", _ctx(inputs={"a": 1}))
        assert ei.value.code == "TEMPLATE_ERROR"

    def test_template_error_carries_failing_variable_metadata(self, engine: TemplateEngine) -> None:
        """S-292 P4d: failing variable + full expression are stashed on the
        exception for downstream structured error metadata."""
        with pytest.raises(AELError) as ei:
            engine.render_string("{{ inputs.missing | default(0) }}", _ctx(inputs={}))
        err = ei.value
        assert getattr(err, "_template_variable") == "inputs.missing"
        assert getattr(err, "_template_expression") == "inputs.missing | default(0)"

    def test_index_into_non_list_raises_typeerror(self, engine: TemplateEngine) -> None:
        # Per _resolve_variable contract, indexing a non-list raises TypeError
        # which is NOT caught by _evaluate_expression (only Key/Attr/Index).
        with pytest.raises(TypeError):
            engine.render_string("{{ inputs.val[0] }}", _ctx(inputs={"val": {"not": "a list"}}))

    def test_invalid_array_index_raises_template_error(self, engine: TemplateEngine) -> None:
        with pytest.raises(AELError) as ei:
            engine.render_string("{{ inputs.items[x] }}", _ctx(inputs={"items": [1, 2]}))
        assert ei.value.code == "TEMPLATE_ERROR"


# ---------------------------------------------------------------------------
# Attribute access safety: builtin methods on primitives rejected
# ---------------------------------------------------------------------------


class TestAttributeSafety:
    def test_string_builtin_method_rejected(self, engine: TemplateEngine) -> None:
        # {{ inputs.name.upper }} must NOT resolve to str.upper bound method.
        with pytest.raises(AELError) as ei:
            engine.render_string("{{ inputs.name.upper }}", _ctx(inputs={"name": "x"}))
        assert ei.value.code == "TEMPLATE_ERROR"

    def test_list_builtin_method_rejected(self, engine: TemplateEngine) -> None:
        with pytest.raises(AELError) as ei:
            engine.render_string("{{ inputs.items.append }}", _ctx(inputs={"items": [1]}))
        assert ei.value.code == "TEMPLATE_ERROR"

    def test_dataclass_field_access(self, engine: TemplateEngine) -> None:
        @dataclass
        class Obj:
            field_a: int

        ctx = _ctx(inputs={"obj": Obj(field_a=7)})
        assert engine.render_string("{{ inputs.obj.field_a }}", ctx) == 7

    def test_dataclass_missing_attr_raises(self, engine: TemplateEngine) -> None:
        @dataclass
        class Obj:
            field_a: int

        ctx = _ctx(inputs={"obj": Obj(field_a=7)})
        with pytest.raises(AELError):
            engine.render_string("{{ inputs.obj.nope }}", ctx)

    def test_custom_object_dict_attr_access(self, engine: TemplateEngine) -> None:
        class Plain:
            def __init__(self) -> None:
                self.value = "hi"

        ctx = _ctx(inputs={"o": Plain()})
        assert engine.render_string("{{ inputs.o.value }}", ctx) == "hi"


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


class TestFilters:
    def test_length_filter(self, engine: TemplateEngine) -> None:
        assert (
            engine.render_string("{{ inputs.items | length }}", _ctx(inputs={"items": [1, 2, 3]}))
            == 3
        )

    def test_default_filter_applies_on_none(self, engine: TemplateEngine) -> None:
        assert (
            engine.render_string("{{ inputs.maybe | default(42) }}", _ctx(inputs={"maybe": None}))
            == 42
        )

    def test_default_filter_keeps_value(self, engine: TemplateEngine) -> None:
        assert (
            engine.render_string("{{ inputs.maybe | default(42) }}", _ctx(inputs={"maybe": 7})) == 7
        )

    def test_json_filter(self, engine: TemplateEngine) -> None:
        out = engine.render_string("{{ inputs.d | json }}", _ctx(inputs={"d": {"a": 1}}))
        assert out == '{"a": 1}'

    def test_chained_filters(self, engine: TemplateEngine) -> None:
        out = engine.render_string("{{ inputs.x | default(5) | string }}", _ctx(inputs={"x": None}))
        assert out == "5"

    def test_string_arg_single_quotes(self, engine: TemplateEngine) -> None:
        out = engine.render_string("{{ inputs.x | default('fallback') }}", _ctx(inputs={"x": None}))
        assert out == "fallback"

    def test_bool_arg_true(self, engine: TemplateEngine) -> None:
        out = engine.render_string("{{ inputs.x | default(true) }}", _ctx(inputs={"x": None}))
        assert out is True

    def test_float_arg(self, engine: TemplateEngine) -> None:
        out = engine.render_string("{{ inputs.x | default(1.5) }}", _ctx(inputs={"x": None}))
        assert out == 1.5

    def test_unknown_filter_raises(self, engine: TemplateEngine) -> None:
        with pytest.raises(AELError) as ei:
            engine.render_string("{{ inputs.x | nonexistent }}", _ctx(inputs={"x": 1}))
        assert ei.value.code == "TEMPLATE_ERROR"

    def test_join_filter_with_arg(self, engine: TemplateEngine) -> None:
        out = engine.render_string(
            "{{ inputs.items | join(',') }}", _ctx(inputs={"items": ["a", "b"]})
        )
        assert out == "a,b"


# ---------------------------------------------------------------------------
# Restricted-construct rejection (validate + render)
# ---------------------------------------------------------------------------


class TestRestrictedConstructs:
    @pytest.mark.parametrize(
        "tmpl",
        [
            "{{ a + b }}",
            "{{ a - b }}",
            "{{ a * b }}",
            "{{ a / b }}",
            "{{ a % b }}",
            "{{ a ** b }}",
        ],
    )
    def test_arithmetic_rejected(self, engine: TemplateEngine, tmpl: str) -> None:
        errors = engine.validate(tmpl)
        assert errors, f"expected arithmetic rejection for {tmpl}"

    @pytest.mark.parametrize(
        "tmpl",
        [
            "{{ if x }}",
            "{{ for i in items }}",
            "{{ while True }}",
            "{{ import os }}",
        ],
    )
    def test_control_flow_rejected(self, engine: TemplateEngine, tmpl: str) -> None:
        errors = engine.validate(tmpl)
        assert errors, f"expected control-flow rejection for {tmpl}"

    def test_function_call_rejected(self, engine: TemplateEngine) -> None:
        errors = engine.validate("{{ foo() }}")
        assert errors

    def test_valid_template_has_no_errors(self, engine: TemplateEngine) -> None:
        assert engine.validate("{{ inputs.x | default(0) }}") == []

    def test_validate_recurses_into_structures(self, engine: TemplateEngine) -> None:
        errors = engine.validate({"a": ["{{ x + y }}"]})
        assert errors

    def test_render_rejects_control_flow(self, engine: TemplateEngine) -> None:
        with pytest.raises(AELError) as ei:
            engine.render("{{ for i in items }}", _ctx())
        assert ei.value.code == "TEMPLATE_ERROR"


# ---------------------------------------------------------------------------
# render_params + extract_references
# ---------------------------------------------------------------------------


class TestParamsAndReferences:
    def test_render_params_returns_dict(self, engine: TemplateEngine) -> None:
        out = engine.render_params(
            {"url": "{{ inputs.u }}", "n": "{{ inputs.n }}"},
            _ctx(inputs={"u": "http://x", "n": 3}),
        )
        assert out == {"url": "http://x", "n": 3}

    def test_extract_references_simple(self, engine: TemplateEngine) -> None:
        refs = engine.extract_references("{{ inputs.url }}")
        assert refs == ["inputs.url"]

    def test_extract_references_strips_filters(self, engine: TemplateEngine) -> None:
        refs = engine.extract_references("{{ steps.a.output | length }}")
        assert refs == ["steps.a.output"]

    def test_extract_references_recursive(self, engine: TemplateEngine) -> None:
        refs = engine.extract_references({"a": "{{ inputs.x }}", "b": ["{{ config.y }}"]})
        assert set(refs) == {"inputs.x", "config.y"}

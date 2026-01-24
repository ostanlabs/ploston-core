"""Property-based tests for template filters.

Tests the json, length, and default filters with various input types.
"""

import json
import pytest
from hypothesis import given, strategies as st, settings, assume

from ploston_core.template import TemplateEngine
from ploston_core.template.types import TemplateContext
from ploston_core.errors import AELError


def make_context(inputs=None, steps=None, config=None):
    """Create a TemplateContext for testing."""
    return TemplateContext(
        inputs=inputs or {},
        steps=steps or {},
        config=config or {},
        execution_id="test-exec-id"
    )


@pytest.mark.property
class TestJsonFilter:
    """Property tests for the json filter."""
    
    @given(st.dictionaries(
        st.from_regex(r'^[a-z]{1,10}$', fullmatch=True),
        st.integers(),
        max_size=10
    ))
    @settings(max_examples=100)
    def test_json_filter_produces_valid_json(self, data):
        """json filter should produce valid JSON."""
        engine = TemplateEngine()
        context = make_context(inputs={'data': data})
        
        template = "{{ inputs.data | json }}"
        result = engine.render_string(template, context)
        
        # Should be valid JSON
        parsed = json.loads(result)
        assert parsed == data
    
    @given(st.lists(st.integers(), max_size=20))
    @settings(max_examples=50)
    def test_json_filter_with_lists(self, items):
        """json filter should work with lists."""
        engine = TemplateEngine()
        context = make_context(inputs={'items': items})
        
        template = "{{ inputs.items | json }}"
        result = engine.render_string(template, context)
        
        parsed = json.loads(result)
        assert parsed == items
    
    @given(st.text(max_size=100).filter(lambda x: '"' not in x and '\\' not in x))
    @settings(max_examples=50)
    def test_json_filter_with_strings(self, text):
        """json filter should properly escape strings."""
        engine = TemplateEngine()
        context = make_context(inputs={'text': text})
        
        template = "{{ inputs.text | json }}"
        result = engine.render_string(template, context)
        
        parsed = json.loads(result)
        assert parsed == text
    
    @given(st.recursive(
        st.one_of(st.integers(), st.text(max_size=20), st.booleans(), st.none()),
        lambda children: st.lists(children, max_size=5) | st.dictionaries(
            st.from_regex(r'^[a-z]{1,5}$', fullmatch=True),
            children,
            max_size=5
        ),
        max_leaves=20
    ))
    @settings(max_examples=50)
    def test_json_filter_with_nested_structures(self, data):
        """json filter should handle nested structures."""
        engine = TemplateEngine()
        context = make_context(inputs={'data': data})
        
        template = "{{ inputs.data | json }}"
        result = engine.render_string(template, context)
        
        parsed = json.loads(result)
        assert parsed == data


@pytest.mark.property
class TestLengthFilter:
    """Property tests for the length filter."""
    
    @given(st.lists(st.integers(), max_size=100))
    @settings(max_examples=50)
    def test_length_filter_with_lists(self, items):
        """length filter should return list length."""
        engine = TemplateEngine()
        context = make_context(inputs={'items': items})
        
        template = "{{ inputs.items | length }}"
        result = engine.render_string(template, context)
        
        assert result == len(items)
    
    @given(st.text(max_size=100))
    @settings(max_examples=50)
    def test_length_filter_with_strings(self, text):
        """length filter should return string length."""
        engine = TemplateEngine()
        context = make_context(inputs={'text': text})
        
        template = "{{ inputs.text | length }}"
        result = engine.render_string(template, context)
        
        assert result == len(text)
    
    @given(st.dictionaries(
        st.from_regex(r'^[a-z]{1,5}$', fullmatch=True),
        st.integers(),
        max_size=20
    ))
    @settings(max_examples=50)
    def test_length_filter_with_dicts(self, data):
        """length filter should return dict key count."""
        engine = TemplateEngine()
        context = make_context(inputs={'data': data})
        
        template = "{{ inputs.data | length }}"
        result = engine.render_string(template, context)
        
        assert result == len(data)


@pytest.mark.property
class TestDefaultFilter:
    """Property tests for the default filter."""
    
    @given(st.one_of(st.none(), st.text(max_size=50)))
    @settings(max_examples=50)
    def test_default_filter_with_none(self, value):
        """default filter should return fallback for None."""
        engine = TemplateEngine()
        context = make_context(inputs={'value': value})
        
        template = "{{ inputs.value | default('fallback') }}"
        result = engine.render_string(template, context)
        
        if value is None:
            assert result == 'fallback'
        else:
            assert result == value
    
    @given(
        value=st.one_of(st.integers(), st.text(max_size=20), st.booleans()),
        default=st.from_regex(r'^[a-zA-Z0-9_]{0,10}$', fullmatch=True)
    )
    @settings(max_examples=50)
    def test_default_filter_with_values(self, value, default):
        """default filter should return value when not None."""
        engine = TemplateEngine()
        context = make_context(inputs={'value': value})

        # Use safe default values (no special characters that break template)
        template = f"{{{{ inputs.value | default('{default}') }}}}"
        result = engine.render_string(template, context)

        # Value should be returned, not default
        assert result == value or str(result) == str(value)

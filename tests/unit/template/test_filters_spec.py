"""Specification tests for template filters.

Each filter has a clear docstring contract; these tests assert the documented
behavior including the explicitly-promised None / wrong-type edge cases, not
just the happy path.
"""

import json

import pytest

from ploston_core.template import filters as F  # noqa: N812


class TestFilterLength:
    def test_string(self):
        assert F.filter_length("hello") == 5

    def test_list(self):
        assert F.filter_length([1, 2, 3]) == 3

    def test_dict(self):
        assert F.filter_length({"a": 1, "b": 2}) == 2

    def test_empty(self):
        assert F.filter_length("") == 0
        assert F.filter_length([]) == 0

    def test_unsupported_type_raises_typeerror(self):
        # Docstring: raises TypeError if value doesn't support len().
        with pytest.raises(TypeError):
            F.filter_length(5)
        with pytest.raises(TypeError):
            F.filter_length(None)


class TestFilterDefault:
    def test_returns_value_when_not_none(self):
        assert F.filter_default("x", "fallback") == "x"

    def test_returns_default_when_none(self):
        assert F.filter_default(None, "fallback") == "fallback"

    def test_falsy_but_not_none_is_kept(self):
        # Contract: only None triggers the default, not other falsy values.
        assert F.filter_default(0, 99) == 0
        assert F.filter_default("", "fallback") == ""
        assert F.filter_default([], ["d"]) == []
        assert F.filter_default(False, True) is False


class TestFilterJsonAndTojson:
    def test_json_dict(self):
        assert F.filter_json({"a": 1}) == json.dumps({"a": 1})

    def test_json_list(self):
        assert F.filter_json([1, 2]) == "[1, 2]"

    def test_json_string(self):
        assert F.filter_json("hi") == '"hi"'

    def test_json_none(self):
        assert F.filter_json(None) == "null"

    def test_tojson_is_alias_of_json(self):
        for v in ({"a": 1}, [1, 2], "hi", None, 3):
            assert F.filter_tojson(v) == F.filter_json(v)


class TestFilterString:
    def test_basic(self):
        assert F.filter_string(123) == "123"

    def test_none_becomes_empty_string(self):
        assert F.filter_string(None) == ""

    def test_bool(self):
        assert F.filter_string(True) == "True"


class TestFilterInt:
    def test_int_passthrough(self):
        assert F.filter_int(5) == 5

    def test_numeric_string(self):
        assert F.filter_int("42") == 42

    def test_float_to_int(self):
        assert F.filter_int(3.9) == 3

    def test_none_returns_none(self):
        assert F.filter_int(None) is None

    def test_invalid_string_returns_none(self):
        assert F.filter_int("not a number") is None

    def test_unconvertible_type_returns_none(self):
        assert F.filter_int([1, 2]) is None


class TestFilterFloat:
    def test_float_passthrough(self):
        assert F.filter_float(2.5) == 2.5

    def test_numeric_string(self):
        assert F.filter_float("3.14") == 3.14

    def test_int_to_float(self):
        assert F.filter_float(7) == 7.0

    def test_none_returns_none(self):
        assert F.filter_float(None) is None

    def test_invalid_string_returns_none(self):
        assert F.filter_float("abc") is None

    def test_unconvertible_type_returns_none(self):
        assert F.filter_float({"a": 1}) is None


class TestFilterJoin:
    def test_join_strings(self):
        assert F.filter_join(["a", "b", "c"], ",") == "a,b,c"

    def test_default_separator_is_empty(self):
        assert F.filter_join(["a", "b"]) == "ab"

    def test_coerces_non_strings(self):
        assert F.filter_join([1, 2, 3], "-") == "1-2-3"

    def test_none_returns_empty_string(self):
        assert F.filter_join(None, ",") == ""

    def test_empty_iterable(self):
        assert F.filter_join([], ",") == ""


class TestFilterKeysValues:
    def test_keys_of_dict(self):
        assert F.filter_keys({"a": 1, "b": 2}) == ["a", "b"]

    def test_keys_non_dict_returns_empty(self):
        assert F.filter_keys([1, 2]) == []
        assert F.filter_keys(None) == []
        assert F.filter_keys("string") == []

    def test_values_of_dict(self):
        assert F.filter_values({"a": 1, "b": 2}) == [1, 2]

    def test_values_non_dict_returns_empty(self):
        assert F.filter_values([1, 2]) == []
        assert F.filter_values(None) == []


class TestFilterFirstLast:
    def test_first_of_list(self):
        assert F.filter_first([10, 20, 30]) == 10

    def test_last_of_list(self):
        assert F.filter_last([10, 20, 30]) == 30

    def test_first_of_string(self):
        assert F.filter_first("abc") == "a"

    def test_last_of_string(self):
        assert F.filter_last("abc") == "c"

    def test_first_of_empty_returns_none(self):
        assert F.filter_first([]) is None
        assert F.filter_first("") is None

    def test_last_of_empty_returns_none(self):
        assert F.filter_last([]) is None

    def test_first_of_none_returns_none(self):
        assert F.filter_first(None) is None
        assert F.filter_last(None) is None

    def test_first_of_non_indexable_returns_none(self):
        # A non-empty, non-indexable value: contract says return None.
        assert F.filter_first(123) is None
        assert F.filter_last(123) is None


class TestFilterRegistry:
    def test_all_documented_filters_registered(self):
        expected = {
            "length",
            "default",
            "json",
            "string",
            "int",
            "float",
            "tojson",
            "join",
            "keys",
            "values",
            "first",
            "last",
        }
        assert expected == set(F.FILTERS.keys())

    def test_registry_maps_to_callables(self):
        for name, fn in F.FILTERS.items():
            assert callable(fn), name


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

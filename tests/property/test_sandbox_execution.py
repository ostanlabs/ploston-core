"""Property-based tests for sandbox execution.

Tests sandbox execution with generated code and inputs.
"""

import asyncio

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ploston_core.sandbox import PythonExecSandbox, SandboxConfig


def run_async(coro):
    """Run async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.mark.property
class TestSandboxBasicExecution:
    """Property tests for basic sandbox execution."""

    @given(value=st.integers(min_value=-1000000, max_value=1000000))
    @settings(max_examples=50)
    def test_integer_assignment(self, value):
        """Integer assignment should work correctly."""
        sandbox = PythonExecSandbox(SandboxConfig())
        code = f"result = {value}"

        res = run_async(sandbox.execute(code))

        assert res.success
        assert res.result == value

    @given(value=st.floats(allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6))
    @settings(max_examples=50)
    def test_float_assignment(self, value):
        """Float assignment should work correctly."""
        sandbox = PythonExecSandbox(SandboxConfig())
        code = f"result = {value}"

        res = run_async(sandbox.execute(code))

        assert res.success
        assert abs(res.result - value) < 1e-9

    @given(value=st.booleans())
    @settings(max_examples=20)
    def test_boolean_assignment(self, value):
        """Boolean assignment should work correctly."""
        sandbox = PythonExecSandbox(SandboxConfig())
        code = f"result = {value}"

        res = run_async(sandbox.execute(code))

        assert res.success
        assert res.result == value

    @given(text=st.text(min_size=0, max_size=100, alphabet=st.characters(
        whitelist_categories=('L', 'N', 'P', 'S'),
        blacklist_characters='"\'\\`'
    )))
    @settings(max_examples=50)
    def test_string_assignment(self, text):
        """String assignment should work correctly."""
        sandbox = PythonExecSandbox(SandboxConfig())
        # Use repr to safely escape the string
        code = f"result = {repr(text)}"

        res = run_async(sandbox.execute(code))

        assert res.success
        assert res.result == text


@pytest.mark.property
class TestSandboxArithmetic:
    """Property tests for arithmetic operations in sandbox."""

    @given(
        a=st.integers(min_value=-1000, max_value=1000),
        b=st.integers(min_value=-1000, max_value=1000)
    )
    @settings(max_examples=50)
    def test_addition(self, a, b):
        """Addition should work correctly."""
        sandbox = PythonExecSandbox(SandboxConfig())
        code = f"result = {a} + {b}"

        res = run_async(sandbox.execute(code))

        assert res.success
        assert res.result == a + b

    @given(
        a=st.integers(min_value=-1000, max_value=1000),
        b=st.integers(min_value=-1000, max_value=1000)
    )
    @settings(max_examples=50)
    def test_multiplication(self, a, b):
        """Multiplication should work correctly."""
        sandbox = PythonExecSandbox(SandboxConfig())
        code = f"result = {a} * {b}"

        res = run_async(sandbox.execute(code))

        assert res.success
        assert res.result == a * b

    @given(
        a=st.integers(min_value=-1000, max_value=1000),
        b=st.integers(min_value=1, max_value=1000)  # Avoid division by zero
    )
    @settings(max_examples=50)
    def test_integer_division(self, a, b):
        """Integer division should work correctly."""
        sandbox = PythonExecSandbox(SandboxConfig())
        code = f"result = {a} // {b}"

        res = run_async(sandbox.execute(code))

        assert res.success
        assert res.result == a // b

    @given(
        base=st.integers(min_value=1, max_value=10),
        exp=st.integers(min_value=0, max_value=5)
    )
    @settings(max_examples=30)
    def test_exponentiation(self, base, exp):
        """Exponentiation should work correctly."""
        sandbox = PythonExecSandbox(SandboxConfig())
        code = f"result = {base} ** {exp}"

        res = run_async(sandbox.execute(code))

        assert res.success
        assert res.result == base ** exp


@pytest.mark.property
class TestSandboxDataStructures:
    """Property tests for data structures in sandbox."""

    @given(items=st.lists(st.integers(min_value=-100, max_value=100), max_size=20))
    @settings(max_examples=50)
    def test_list_creation(self, items):
        """List creation should work correctly."""
        sandbox = PythonExecSandbox(SandboxConfig())
        code = f"result = {items}"

        res = run_async(sandbox.execute(code))

        assert res.success
        assert res.result == items

    @given(items=st.lists(st.integers(min_value=-100, max_value=100), min_size=1, max_size=20))
    @settings(max_examples=30)
    def test_list_sum(self, items):
        """List sum should work correctly."""
        sandbox = PythonExecSandbox(SandboxConfig())
        code = f"result = sum({items})"

        res = run_async(sandbox.execute(code))

        assert res.success
        assert res.result == sum(items)

    @given(items=st.lists(st.integers(min_value=-100, max_value=100), min_size=1, max_size=20))
    @settings(max_examples=30)
    def test_list_len(self, items):
        """List length should work correctly."""
        sandbox = PythonExecSandbox(SandboxConfig())
        code = f"result = len({items})"

        res = run_async(sandbox.execute(code))

        assert res.success
        assert res.result == len(items)

    @given(
        keys=st.lists(st.text(min_size=1, max_size=10, alphabet=st.characters(
            whitelist_categories=('L',)
        )), min_size=1, max_size=5, unique=True),
        values=st.lists(st.integers(min_value=-100, max_value=100), min_size=1, max_size=5)
    )
    @settings(max_examples=30)
    def test_dict_creation(self, keys, values):
        """Dict creation should work correctly."""
        # Ensure same length
        min_len = min(len(keys), len(values))
        keys = keys[:min_len]
        values = values[:min_len]

        sandbox = PythonExecSandbox(SandboxConfig())
        pairs = ", ".join(f'"{k}": {v}' for k, v in zip(keys, values))
        code = f"result = {{{pairs}}}"

        res = run_async(sandbox.execute(code))

        assert res.success
        expected = dict(zip(keys, values))
        assert res.result == expected


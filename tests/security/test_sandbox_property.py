"""Property-based security tests for Python sandbox.

Uses Hypothesis to generate attack patterns.
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ploston_core.sandbox import PythonExecSandbox


@pytest.mark.security
@pytest.mark.property
class TestImportPatternGeneration:
    """Generate and test import patterns."""

    DANGEROUS_MODULES = [
        'os', 'sys', 'subprocess', 'socket', 'http', 'urllib',
        'pickle', 'marshal', 'ctypes', 'multiprocessing', 'shutil',
        'tempfile', 'glob', 'pathlib', 'inspect', 'gc'
    ]

    @given(module=st.sampled_from(DANGEROUS_MODULES))
    @settings(max_examples=50)
    @pytest.mark.asyncio
    async def test_import_variations(self, module):
        """Various import syntaxes should all be blocked."""
        sandbox = PythonExecSandbox(timeout=5)

        variations = [
            f"import {module}",
            f"from {module} import *",
        ]

        for code in variations:
            full_code = f"{code}\nresult = 'escaped'"
            result = await sandbox.execute(full_code, {})
            assert not result.success, f"Import should be blocked: {code}"


@pytest.mark.security
@pytest.mark.property
class TestCodePatternGeneration:
    """Generate and test code patterns."""

    @given(
        builtin=st.sampled_from(['eval', 'exec', 'compile', 'open', '__import__']),
        string_value=st.text(max_size=30).filter(lambda x: '"' not in x and "'" not in x and '\n' not in x)
    )
    @settings(max_examples=50)
    @pytest.mark.asyncio
    async def test_builtin_call_blocked(self, builtin, string_value):
        """Calls to dangerous builtins should be blocked regardless of args."""
        sandbox = PythonExecSandbox(timeout=5)
        code = f'result = {builtin}("{string_value}")'

        result = await sandbox.execute(code, {})
        assert not result.success, f"Builtin {builtin} should be blocked"


@pytest.mark.security
@pytest.mark.property
class TestArbitraryCodeHandling:
    """Test handling of arbitrary code strings."""

    @given(st.text(min_size=1, max_size=200))
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_arbitrary_code_doesnt_crash(self, code):
        """Arbitrary text should never crash the sandbox itself."""
        sandbox = PythonExecSandbox(timeout=2)

        # Sandbox should either:
        # 1. Execute safely and return result
        # 2. Return an error result
        # It should NEVER:
        # 1. Crash the process
        # 2. Hang indefinitely (without timeout)

        try:
            result = await sandbox.execute(code, {})
            # If it executed, that's fine - check it returned a result
            assert hasattr(result, 'success')
        except Exception as e:
            # Unexpected exception - this is concerning
            pytest.fail(f"Sandbox crashed with unexpected error: {type(e).__name__}: {e}")

    @given(st.binary(max_size=200))
    @settings(max_examples=50)
    @pytest.mark.asyncio
    async def test_binary_input_handled(self, data):
        """Binary data should be handled gracefully."""
        sandbox = PythonExecSandbox(timeout=2)

        try:
            code = data.decode('utf-8', errors='replace')
            result = await sandbox.execute(code, {})
            # Should not crash
            assert hasattr(result, 'success')
        except Exception as e:
            pytest.fail(f"Sandbox crashed with binary input: {type(e).__name__}: {e}")


@pytest.mark.security
@pytest.mark.property
class TestAttributeChainGeneration:
    """Generate and test attribute chain patterns."""

    @given(
        depth=st.integers(min_value=1, max_value=5),
        attr=st.sampled_from(['__class__', '__bases__', '__mro__', '__dict__'])
    )
    @settings(max_examples=50)
    @pytest.mark.asyncio
    async def test_attribute_chain_handled(self, depth, attr):
        """Deep attribute chains should be handled safely."""
        sandbox = PythonExecSandbox(timeout=5)

        # Build attribute chain
        chain = "()"
        for _ in range(depth):
            chain += f".{attr}"

        code = f"result = {chain}"

        try:
            result = await sandbox.execute(code, {})
            # Should not crash - may succeed or fail
            assert hasattr(result, 'success')
        except Exception as e:
            pytest.fail(f"Sandbox crashed with attribute chain: {type(e).__name__}: {e}")

    @given(
        base_obj=st.sampled_from(['()', '""', '[]', '{}', '0', 'True']),
        attrs=st.lists(
            st.sampled_from(['__class__', '__bases__', '__mro__', '__subclasses__', '__init__', '__globals__']),
            min_size=1,
            max_size=4
        )
    )
    @settings(max_examples=50)
    @pytest.mark.asyncio
    async def test_various_attribute_chains(self, base_obj, attrs):
        """Various attribute chains should be handled safely."""
        sandbox = PythonExecSandbox(timeout=5)

        chain = base_obj
        for attr in attrs:
            if attr == '__subclasses__':
                chain += f".{attr}()"
            else:
                chain += f".{attr}"

        code = f"result = {chain}"

        try:
            result = await sandbox.execute(code, {})
            assert hasattr(result, 'success')
        except Exception as e:
            pytest.fail(f"Sandbox crashed: {type(e).__name__}: {e}")


@pytest.mark.security
@pytest.mark.property
class TestDunderMethodGeneration:
    """Generate and test dunder method access patterns."""

    DUNDER_METHODS = [
        '__init__', '__new__', '__del__', '__repr__', '__str__',
        '__call__', '__getattr__', '__setattr__', '__delattr__',
        '__getattribute__', '__get__', '__set__', '__delete__',
        '__slots__', '__dict__', '__class__', '__bases__', '__mro__',
        '__subclasses__', '__module__', '__name__', '__qualname__',
        '__code__', '__globals__', '__closure__', '__annotations__',
        '__builtins__', '__doc__', '__file__', '__loader__',
    ]

    @given(
        dunder=st.sampled_from(DUNDER_METHODS),
        obj=st.sampled_from(['object', 'type', 'str', 'int', 'list', 'dict'])
    )
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_dunder_access_handled(self, dunder, obj):
        """Dunder method access should be handled safely."""
        sandbox = PythonExecSandbox(timeout=5)

        code = f"result = {obj}.{dunder}"

        try:
            result = await sandbox.execute(code, {})
            assert hasattr(result, 'success')
        except Exception as e:
            pytest.fail(f"Sandbox crashed accessing {obj}.{dunder}: {type(e).__name__}: {e}")


@pytest.mark.security
@pytest.mark.property
class TestStringManipulationAttacks:
    """Generate and test string manipulation attack patterns."""

    @given(
        module=st.sampled_from(['os', 'sys', 'subprocess', 'socket']),
        obfuscation=st.sampled_from([
            lambda m: f"'{m}'",  # Direct string
            lambda m: f"''.join(['{m[0]}', '{m[1:]}'])",  # Join
            lambda m: f"chr({ord(m[0])}) + '{m[1:]}'",  # chr()
            lambda m: f"'{m[::-1]}'[::-1]",  # Reverse
        ])
    )
    @settings(max_examples=50)
    @pytest.mark.asyncio
    async def test_string_obfuscation_blocked(self, module, obfuscation):
        """String obfuscation attempts should not bypass import blocking."""
        sandbox = PythonExecSandbox(timeout=5)

        obfuscated = obfuscation(module)
        code = f"result = __import__({obfuscated})"

        result = await sandbox.execute(code, {})
        assert not result.success, "Import via string obfuscation should be blocked"


@pytest.mark.security
@pytest.mark.property
class TestNestedStructureAttacks:
    """Generate and test nested structure attack patterns."""

    @given(
        depth=st.integers(min_value=1, max_value=10),
        structure=st.sampled_from(['list', 'dict', 'tuple'])
    )
    @settings(max_examples=30)
    @pytest.mark.asyncio
    async def test_deeply_nested_structures(self, depth, structure):
        """Deeply nested structures should be handled safely."""
        sandbox = PythonExecSandbox(timeout=5)

        if structure == 'list':
            code = "x = " + "[" * depth + "1" + "]" * depth + "\nresult = x"
        elif structure == 'dict':
            code = "x = " + "{'a': " * depth + "1" + "}" * depth + "\nresult = x"
        else:  # tuple
            code = "x = " + "(" * depth + "1," + ")" * depth + "\nresult = x"

        try:
            result = await sandbox.execute(code, {})
            assert hasattr(result, 'success')
        except Exception as e:
            pytest.fail(f"Sandbox crashed with nested {structure}: {type(e).__name__}: {e}")

    @given(st.integers(min_value=100, max_value=500))
    @settings(max_examples=20)
    @pytest.mark.asyncio
    async def test_large_list_creation(self, size):
        """Large list creation should be handled safely."""
        sandbox = PythonExecSandbox(timeout=5)

        code = f"result = list(range({size}))"

        try:
            result = await sandbox.execute(code, {})
            assert hasattr(result, 'success')
        except Exception as e:
            pytest.fail(f"Sandbox crashed with large list: {type(e).__name__}: {e}")


@pytest.mark.security
@pytest.mark.property
class TestExceptionHandlingAttacks:
    """Generate and test exception handling attack patterns."""

    @given(
        exception=st.sampled_from([
            'Exception', 'BaseException', 'SystemExit', 'KeyboardInterrupt',
            'GeneratorExit', 'StopIteration', 'RuntimeError', 'RecursionError'
        ])
    )
    @settings(max_examples=30)
    @pytest.mark.asyncio
    async def test_exception_raising_handled(self, exception):
        """Raising various exceptions should be handled safely."""
        sandbox = PythonExecSandbox(timeout=5)

        code = f"""
try:
    raise {exception}("test")
except:
    result = "caught"
"""

        try:
            result = await sandbox.execute(code, {})
            assert hasattr(result, 'success')
        except Exception as e:
            pytest.fail(f"Sandbox crashed raising {exception}: {type(e).__name__}: {e}")

    @pytest.mark.xfail(reason="Sandbox gap: SystemExit/KeyboardInterrupt escape the sandbox")
    @given(st.sampled_from(['SystemExit', 'KeyboardInterrupt', 'GeneratorExit']))
    @settings(max_examples=10)
    @pytest.mark.asyncio
    async def test_system_exceptions_contained(self, exception):
        """System exceptions should not escape the sandbox.

        NOTE: This test documents a security gap - SystemExit and KeyboardInterrupt
        currently escape the sandbox. This should be fixed in sandbox hardening.
        """
        sandbox = PythonExecSandbox(timeout=5)

        code = f"raise {exception}()"

        try:
            result = await sandbox.execute(code, {})
            # Should return error result, not propagate exception
            assert hasattr(result, 'success')
            assert not result.success
        except (SystemExit, KeyboardInterrupt, GeneratorExit):
            pytest.fail(f"{exception} escaped the sandbox!")
        except Exception as e:
            pytest.fail(f"Unexpected exception: {type(e).__name__}: {e}")

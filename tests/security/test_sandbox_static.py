"""Static security tests for Python sandbox.

Tests known attack patterns and dangerous operations.
"""

import pytest

from ploston_core.sandbox import PythonExecSandbox


@pytest.mark.security
class TestBlockedImports:
    """Test that dangerous imports are blocked."""

    BLOCKED_IMPORTS = [
        # System access
        ("import os", "os module"),
        ("import sys", "sys module"),
        ("import subprocess", "subprocess module"),
        ("import shutil", "shutil module"),
        ("import pathlib", "pathlib module"),
        # Network access
        ("import socket", "socket module"),
        ("import http", "http module"),
        ("import urllib", "urllib module"),
        ("import requests", "requests module"),
        ("import httpx", "httpx module"),
        ("import aiohttp", "aiohttp module"),
        # Code execution
        ("import ctypes", "ctypes module"),
        ("import cffi", "cffi module"),
        ("import importlib", "importlib module"),
        # Serialization (code execution risk)
        ("import pickle", "pickle module"),
        ("import marshal", "marshal module"),
        ("import shelve", "shelve module"),
        # Multiprocessing (sandbox escape)
        ("import multiprocessing", "multiprocessing module"),
        ("import threading", "threading module - direct"),
        ("import concurrent", "concurrent module"),
        # Introspection (sandbox inspection)
        ("import inspect", "inspect module"),
        ("import gc", "gc module"),
        ("import traceback", "traceback module"),
        # File operations
        ("import tempfile", "tempfile module"),
        ("import glob", "glob module"),
        ("import fnmatch", "fnmatch module"),
        # Indirect imports
        ("from os import path", "os.path"),
        ("from sys import modules", "sys.modules"),
        ("from subprocess import run", "subprocess.run"),
        ("from pathlib import Path", "pathlib.Path"),
    ]

    @pytest.fixture
    def sandbox(self):
        return PythonExecSandbox(timeout=5)

    @pytest.mark.parametrize("code,description", BLOCKED_IMPORTS)
    @pytest.mark.asyncio
    async def test_blocked_import(self, sandbox, code, description):
        """Verify dangerous import is blocked."""
        full_code = f"{code}\nresult = 'escaped'"

        result = await sandbox.execute(full_code, {})

        # Should fail with security error
        assert not result.success, f"Import should be blocked: {description}"
        assert result.error is not None
        # Error should mention import or blocked
        error_lower = result.error.lower()
        assert (
            "import" in error_lower or "blocked" in error_lower or "not allowed" in error_lower
        ), f"Error should mention import blocking: {result.error}"


@pytest.mark.security
class TestBlockedBuiltins:
    """Test that dangerous builtins are blocked."""

    BLOCKED_BUILTINS = [
        # Code execution
        ("eval('1+1')", "eval"),
        ("exec('x=1')", "exec"),
        ("compile('x=1', '', 'exec')", "compile"),
        # File access
        ("open('/etc/passwd')", "open"),
        ("open('/etc/passwd', 'r')", "open read"),
        # Dynamic import
        ("__import__('os')", "__import__"),
        # Namespace access
        ("globals()", "globals"),
        ("locals()", "locals"),
        # Attribute manipulation
        ("getattr(object, '__class__')", "getattr"),
        ("setattr(object, 'x', 1)", "setattr"),
        ("delattr(object, 'x')", "delattr"),
        # Debugging
        ("breakpoint()", "breakpoint"),
        # Input (hangs execution)
        ("input('prompt')", "input"),
    ]

    @pytest.fixture
    def sandbox(self):
        return PythonExecSandbox(timeout=5)

    @pytest.mark.parametrize("code,description", BLOCKED_BUILTINS)
    @pytest.mark.asyncio
    async def test_blocked_builtin(self, sandbox, code, description):
        """Verify dangerous builtin is blocked."""
        full_code = f"result = {code}"

        result = await sandbox.execute(full_code, {})

        # Should fail
        assert not result.success, f"Builtin should be blocked: {description}"


@pytest.mark.security
class TestResourceLimits:
    """Test resource limit enforcement.

    Note: Python's asyncio.wait_for cannot interrupt synchronous code.
    Infinite loops without await points will not be interrupted by timeout.
    This is a known limitation of the in-process sandbox.
    """

    @pytest.fixture
    def sandbox(self):
        return PythonExecSandbox(timeout=2)  # Short timeout for tests

    @pytest.mark.asyncio
    async def test_recursive_depth(self, sandbox):
        """Deep recursion should be caught."""
        code = """
def recurse(n):
    return recurse(n+1)
result = recurse(0)
"""
        result = await sandbox.execute(code, {})

        assert not result.success
        assert "recursion" in result.error.lower() or "depth" in result.error.lower()


@pytest.mark.security
class TestSafeOperations:
    """Test that safe operations work correctly."""

    @pytest.fixture
    def sandbox(self):
        return PythonExecSandbox(timeout=5)

    @pytest.mark.asyncio
    async def test_basic_math(self, sandbox):
        """Basic math operations should work."""
        code = "result = 2 + 2"
        result = await sandbox.execute(code, {})

        assert result.success
        assert result.result == 4

    @pytest.mark.asyncio
    async def test_string_operations(self, sandbox):
        """String operations should work."""
        code = 'result = "hello" + " " + "world"'
        result = await sandbox.execute(code, {})

        assert result.success
        assert result.result == "hello world"

    @pytest.mark.asyncio
    async def test_list_operations(self, sandbox):
        """List operations should work."""
        code = """
items = [1, 2, 3]
items.append(4)
result = sum(items)
"""
        result = await sandbox.execute(code, {})

        assert result.success
        assert result.result == 10

    @pytest.mark.asyncio
    async def test_dict_operations(self, sandbox):
        """Dict operations should work."""
        code = """
data = {"a": 1, "b": 2}
data["c"] = 3
result = data
"""
        result = await sandbox.execute(code, {})

        assert result.success
        assert result.result == {"a": 1, "b": 2, "c": 3}

    @pytest.mark.asyncio
    async def test_safe_imports(self, sandbox):
        """Safe imports should work."""
        code = """
import json
import math
result = json.dumps({"pi": math.pi})
"""
        result = await sandbox.execute(code, {})

        assert result.success
        assert "pi" in result.result


@pytest.mark.security
class TestAsyncCodeExecution:
    """Test that sandbox supports top-level await for async tool calls.

    Regression tests for the bug where exec() ran synchronously,
    causing ``await context.tools.call(...)`` to either fail or
    return unawaited coroutine objects.
    """

    @pytest.fixture
    def sandbox(self):
        return PythonExecSandbox(timeout=5)

    @pytest.mark.asyncio
    async def test_top_level_await_basic(self, sandbox):
        """Top-level await on a simple coroutine should work."""

        # Provide an async function in context that code can await
        async def async_double(x):
            return x * 2

        result = await sandbox.execute(
            "result = await async_double(21)",
            {"async_double": async_double},
        )

        assert result.success, f"Expected success, got error: {result.error}"
        assert result.result == 42

    @pytest.mark.asyncio
    async def test_top_level_await_tool_call_interface(self):
        """Await on ToolCallInterface.call() should return actual data."""
        from unittest.mock import AsyncMock, MagicMock

        from ploston_core.sandbox.types import ToolCallInterface

        # Create a mock tool caller implementing ToolCallerProtocol
        mock_caller = MagicMock()
        mock_caller.call = AsyncMock(return_value={"value": 99})
        tool_interface = ToolCallInterface(tool_caller=mock_caller, max_calls=10)

        sandbox = PythonExecSandbox(timeout=5)

        code = """
data = await tools.call("my_tool", {"key": "test"})
result = data["value"]
"""
        result = await sandbox.execute(code, {"tools": tool_interface})

        assert result.success, f"Expected success, got error: {result.error}"
        assert result.result == 99
        mock_caller.call.assert_called_once_with("my_tool", {"key": "test"})

    @pytest.mark.asyncio
    async def test_sync_code_still_works(self, sandbox):
        """Normal synchronous code should continue to work unchanged."""
        code = "result = 2 + 2"
        result = await sandbox.execute(code, {})

        assert result.success
        assert result.result == 4

    @pytest.mark.asyncio
    async def test_mixed_sync_and_async(self, sandbox):
        """Code mixing sync operations and await should work."""

        async def fetch(key):
            return {"key": key, "value": len(key)}

        code = """
x = 10
data = await fetch("hello")
result = x + data["value"]
"""
        result = await sandbox.execute(code, {"fetch": fetch})

        assert result.success, f"Expected success, got error: {result.error}"
        assert result.result == 15

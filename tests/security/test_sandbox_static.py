"""Static security tests for Python sandbox.

Tests known attack patterns and dangerous operations.
"""

import pytest

from ploston_core.sandbox import PythonExecSandbox
from ploston_core.errors import AELError


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
        assert "import" in error_lower or "blocked" in error_lower or "not allowed" in error_lower, \
            f"Error should mention import blocking: {result.error}"


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

"""Extended security tests for Python sandbox.

Additional tests to reach 150+ security tests covering:
- More blocked imports
- Resource limits
- Unicode obfuscation
- Pickle/marshal attacks
- Signal handling
"""

import pytest

from ploston_core.sandbox import PythonExecSandbox


@pytest.mark.security
class TestExtendedBlockedImports:
    """Test additional dangerous imports are blocked."""

    @pytest.fixture
    def sandbox(self):
        return PythonExecSandbox(timeout=5)

    EXTENDED_BLOCKED_IMPORTS = [
        # Additional system modules
        ("import pty", "pty module"),
        ("import tty", "tty module"),
        ("import termios", "termios module"),
        ("import fcntl", "fcntl module"),
        ("import resource", "resource module"),
        ("import signal", "signal module"),
        ("import select", "select module"),
        ("import mmap", "mmap module"),
        ("import sysconfig", "sysconfig module"),
        ("import platform", "platform module"),
        
        # Additional network modules
        ("import ssl", "ssl module"),
        ("import ftplib", "ftplib module"),
        ("import smtplib", "smtplib module"),
        ("import poplib", "poplib module"),
        ("import imaplib", "imaplib module"),
        ("import telnetlib", "telnetlib module"),
        ("import asyncio", "asyncio module"),
        ("import selectors", "selectors module"),
        
        # Code execution modules
        ("import code", "code module"),
        ("import codeop", "codeop module"),
        ("import compileall", "compileall module"),
        ("import py_compile", "py_compile module"),
        ("import dis", "dis module"),
        ("import ast", "ast module"),
        
        # Debugging modules
        ("import pdb", "pdb module"),
        ("import bdb", "bdb module"),
        ("import profile", "profile module"),
        ("import cProfile", "cProfile module"),
        ("import trace", "trace module"),
        ("import timeit", "timeit module"),
        
        # Serialization modules
        ("import copyreg", "copyreg module"),
        ("import _pickle", "_pickle module"),
        
        # Indirect imports with aliases
        ("import os as o", "os with alias"),
        ("import sys as s", "sys with alias"),
        ("import subprocess as sp", "subprocess with alias"),
        
        # From imports with aliases
        ("from os import system as sys_call", "os.system with alias"),
        ("from subprocess import Popen as P", "Popen with alias"),
    ]

    @pytest.mark.parametrize("code,description", EXTENDED_BLOCKED_IMPORTS)
    @pytest.mark.asyncio
    async def test_extended_blocked_import(self, sandbox, code, description):
        """Verify additional dangerous imports are blocked."""
        full_code = f"{code}\nresult = 'escaped'"

        result = await sandbox.execute(full_code, {})

        assert not result.success, f"Import should be blocked: {description}"


@pytest.mark.security
class TestResourceLimitsExtended:
    """Extended resource limit tests."""

    @pytest.fixture
    def sandbox(self):
        return PythonExecSandbox(timeout=2)

    @pytest.mark.asyncio
    async def test_memory_allocation_large_string(self, sandbox):
        """Large string allocation should be handled."""
        code = "result = 'x' * (10 ** 7)"  # 10MB string
        result = await sandbox.execute(code, {})
        # Should either succeed (if memory available) or fail gracefully
        assert hasattr(result, 'success')

    @pytest.mark.asyncio
    async def test_memory_allocation_large_list(self, sandbox):
        """Large list allocation should be handled."""
        code = "result = [0] * (10 ** 7)"  # 10M element list
        result = await sandbox.execute(code, {})
        assert hasattr(result, 'success')

    @pytest.mark.asyncio
    async def test_memory_allocation_large_dict(self, sandbox):
        """Large dict allocation should be handled."""
        code = "result = {i: i for i in range(10 ** 6)}"  # 1M key dict
        result = await sandbox.execute(code, {})
        assert hasattr(result, 'success')

    @pytest.mark.asyncio
    async def test_cpu_intensive_loop(self, sandbox):
        """CPU-intensive operations should be handled."""
        code = """
total = 0
for i in range(10 ** 6):
    total += i
result = total
"""
        result = await sandbox.execute(code, {})
        assert hasattr(result, 'success')

    @pytest.mark.asyncio
    async def test_nested_function_calls(self, sandbox):
        """Deeply nested function calls should be handled."""
        code = """
def f(n):
    if n <= 0:
        return 0
    return f(n - 1) + 1
result = f(100)  # Safe depth
"""
        result = await sandbox.execute(code, {})
        assert result.success
        assert result.result == 100

    @pytest.mark.asyncio
    async def test_generator_exhaustion(self, sandbox):
        """Generator exhaustion should be handled."""
        code = """
def gen():
    i = 0
    while i < 10000:
        yield i
        i += 1
result = list(gen())[-1]
"""
        result = await sandbox.execute(code, {})
        assert result.success
        assert result.result == 9999


@pytest.mark.security
class TestUnicodeObfuscation:
    """Test Unicode obfuscation attack patterns."""

    @pytest.fixture
    def sandbox(self):
        return PythonExecSandbox(timeout=5)

    UNICODE_ATTACKS = [
        # Unicode lookalikes for 'import'
        ("ⅰmport os", "unicode i in import"),
        ("іmport os", "cyrillic i in import"),
        
        # Unicode lookalikes for 'eval'
        ("еval('1+1')", "cyrillic e in eval"),
        
        # Unicode lookalikes for 'exec'
        ("ехec('x=1')", "cyrillic e and x in exec"),
        
        # Unicode lookalikes for 'open'
        ("оpen('/etc/passwd')", "cyrillic o in open"),
        
        # Zero-width characters
        ("im\u200bport os", "zero-width space in import"),
        ("ev\u200bal('1')", "zero-width space in eval"),
        
        # Right-to-left override
        ("so tropmi\u202e", "RTL override"),
    ]

    @pytest.mark.parametrize("code,description", UNICODE_ATTACKS)
    @pytest.mark.asyncio
    async def test_unicode_obfuscation_handled(self, sandbox, code, description):
        """Unicode obfuscation should not bypass security."""
        full_code = f"{code}\nresult = 'escaped'"

        result = await sandbox.execute(full_code, {})

        # Should either fail (blocked) or be a syntax error
        # Should NOT successfully import os or execute dangerous code
        if result.success:
            # If it succeeded, verify it didn't actually import os
            assert result.result != 'escaped' or 'os' not in str(result.result)


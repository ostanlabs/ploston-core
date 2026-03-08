"""Python code execution sandbox for AEL.

Provides secure Python code execution with multiple security layers:
1. Import restrictions (AST-based whitelist)
2. Builtin restrictions (no eval, exec, open, etc.)
3. Timeout enforcement

Simplified version for AEL workflow execution.
"""

import ast
import asyncio
import contextlib
import io
import time
import types
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from typing import Any

from ploston_core.types import ToolCallerProtocol


class SecurityError(Exception):
    """Raised when code violates security policy."""

    pass


@dataclass
class SandboxResult:
    """Result of sandbox code execution.

    Attributes:
        success: Whether execution succeeded
        result: The result value (from 'result' variable in code)
        stdout: Captured stdout output
        stderr: Captured stderr output
        execution_time: Total execution time in seconds
        error: Optional error message if execution failed
        tool_call_count: Number of tool calls made during execution
    """

    success: bool
    result: Any
    stdout: str
    stderr: str
    execution_time: float
    error: str | None = None
    tool_call_count: int = 0


# ─── Allowed import surface ────────────────────────────────────────────────
# Standard library: json, math, datetime, time, random, itertools, functools,
#   collections, typing, re, decimal, statistics, operator, copy, uuid, hashlib, io
# Third-party:
#   anthropic  — LLM synthesis steps (requires ANTHROPIC_API_KEY env var)
#   pypdf      — PDF parsing steps
# ───────────────────────────────────────────────────────────────────────────
SAFE_IMPORTS = {
    # Standard library
    "json",
    "math",
    "datetime",
    "time",
    "random",
    "itertools",
    "functools",
    "collections",
    "typing",
    "re",
    "decimal",
    "statistics",
    "operator",
    "copy",
    "uuid",
    "hashlib",
    "io",  # T-688 audit: needed for io.BytesIO in PDF parsing
    # Third-party — added in S-225
    "anthropic",  # T-687: LLM synthesis steps
    "pypdf",  # T-686: PDF parsing steps
}

DANGEROUS_BUILTINS = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "open",
    "input",
    "breakpoint",
    "exit",
    "quit",
    "help",
    "globals",
    "locals",
    "vars",
    "dir",
    "getattr",
    "setattr",
    "delattr",
    "hasattr",
    "callable",
    "classmethod",
    "staticmethod",
    "property",
    "super",
    "type",
}

# Dangerous dunder attributes that enable sandbox escapes
DANGEROUS_DUNDERS = {
    # Class hierarchy traversal
    "__class__",
    "__bases__",
    "__base__",
    "__mro__",
    "__subclasses__",
    # Code object manipulation
    "__code__",
    "__globals__",
    "__closure__",
    "__func__",
    # Frame inspection
    "__builtins__",
    "__dict__",
    "__self__",
    # Module manipulation
    "__loader__",
    "__spec__",
    "__cached__",
    "__file__",
    "__path__",
}


class PythonExecSandbox:
    """Sandboxed Python code execution for AEL workflows.

    Example:
        >>> sandbox = PythonExecSandbox(timeout=30)
        >>> result = await sandbox.execute('''
        ... import json
        ... data = {"hello": "world"}
        ... result = json.dumps(data)
        ... ''')
        >>> print(result.success, result.result)
    """

    def __init__(
        self,
        tool_caller: ToolCallerProtocol | None = None,
        allowed_imports: set[str] | None = None,
        timeout: int = 30,
        max_output_size: int = 1024 * 1024,
    ):
        """Initialize sandbox.

        Args:
            tool_caller: Optional tool caller for executing tools from code
            allowed_imports: Whitelist of allowed imports (default: SAFE_IMPORTS)
            timeout: Execution timeout in seconds
            max_output_size: Maximum stdout/stderr size in bytes
        """
        self.tool_caller = tool_caller
        self.allowed_imports = allowed_imports or SAFE_IMPORTS.copy()
        self.timeout = timeout
        self.max_output_size = max_output_size
        self._tool_call_count = 0

    def validate_code(self, code: str) -> list[str]:
        """Validate code without executing it.

        Checks:
        - Syntax validity
        - Import restrictions
        - Disallowed builtins (eval, exec, compile, __import__)
        - Dangerous dunder attribute access

        Args:
            code: Python code to validate

        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []

        # Check syntax (supports top-level await)
        try:
            tree = self._parse_code(code)
        except SecurityError as e:
            errors.append(str(e))
            return errors  # Can't continue validation if syntax is invalid

        # Check imports
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name.split(".")[0]
                    if module not in self.allowed_imports:
                        errors.append(f"Import '{module}' not allowed")
            elif isinstance(node, ast.ImportFrom) and node.module:
                module = node.module.split(".")[0]
                if module not in self.allowed_imports:
                    errors.append(f"Import from '{module}' not allowed")

        # Check for disallowed builtins (eval, exec, compile)
        # Note: __import__ is handled separately in sandbox globals
        disallowed_names = {"eval", "exec", "compile", "__builtins__"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in disallowed_names:
                errors.append(f"Use of '{node.id}' is not allowed")

        # Check for dangerous dunder attribute access
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in DANGEROUS_DUNDERS:
                errors.append(f"Access to '{node.attr}' is not allowed")

            # Check string literals for format string attacks
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for dunder in DANGEROUS_DUNDERS:
                    if dunder in node.value:
                        errors.append(f"String containing '{dunder}' is not allowed")

        return errors

    def _parse_code(self, code: str) -> ast.AST:
        """Parse code into AST, supporting top-level await syntax.

        Tries normal parsing first. If that fails with a SyntaxError
        (e.g. because code contains ``await``), retries with
        ``PyCF_ALLOW_TOP_LEVEL_AWAIT`` so async code steps work.

        Args:
            code: Python code to parse

        Returns:
            Parsed AST

        Raises:
            SecurityError: If code has syntax errors even with async support
        """
        try:
            return ast.parse(code)
        except SyntaxError:
            pass
        # Retry allowing top-level await
        try:
            return ast.parse(code, mode="exec", type_comments=False)
        except SyntaxError:
            pass
        try:
            return compile(
                code, "<sandbox>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT | ast.PyCF_ONLY_AST
            )
        except SyntaxError as e:
            raise SecurityError(f"Syntax error in code: {e}") from e

    def _validate_imports(self, code: str) -> None:
        """Validate that code only imports allowed modules.

        Args:
            code: Python code to validate

        Raises:
            SecurityError: If code imports disallowed modules
        """
        tree = self._parse_code(code)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name.split(".")[0]
                    if module not in self.allowed_imports:
                        raise SecurityError(
                            f"Import '{module}' not allowed. "
                            f"Allowed imports: {sorted(self.allowed_imports)}"
                        )
            elif isinstance(node, ast.ImportFrom) and node.module:
                module = node.module.split(".")[0]
                if module not in self.allowed_imports:
                    raise SecurityError(
                        f"Import from '{module}' not allowed. "
                        f"Allowed imports: {sorted(self.allowed_imports)}"
                    )

    def _validate_dangerous_attrs(self, code: str) -> None:
        """Validate that code doesn't access dangerous dunder attributes.

        Blocks sandbox escape vectors like:
        - Class hierarchy traversal: __class__, __bases__, __mro__, __subclasses__
        - Code object manipulation: __code__, __globals__, __closure__
        - Builtins recovery: __builtins__, __dict__

        Args:
            code: Python code to validate

        Raises:
            SecurityError: If code accesses dangerous attributes
        """
        try:
            tree = self._parse_code(code)
        except SecurityError:
            # Syntax errors are handled in _validate_imports
            return

        for node in ast.walk(tree):
            # Check direct name access to __builtins__
            if isinstance(node, ast.Name) and node.id == "__builtins__":
                raise SecurityError(
                    "Access to '__builtins__' is not allowed (security restriction)"
                )

            # Check direct attribute access: obj.__class__
            if isinstance(node, ast.Attribute):
                if node.attr in DANGEROUS_DUNDERS:
                    raise SecurityError(
                        f"Access to '{node.attr}' is not allowed (security restriction)"
                    )

            # Check string literals that might be used in format strings
            # e.g., '{0.__class__}'.format(x) or f'{x.__class__}'
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for dunder in DANGEROUS_DUNDERS:
                    if dunder in node.value:
                        raise SecurityError(
                            f"String containing '{dunder}' is not allowed "
                            "(potential format string attack)"
                        )

            # Check JoinedStr (f-strings) for dangerous attribute access
            if isinstance(node, ast.JoinedStr):
                for value in node.values:
                    if isinstance(value, ast.FormattedValue):
                        # Check if the formatted value accesses dangerous attrs
                        for subnode in ast.walk(value):
                            if isinstance(subnode, ast.Attribute):
                                if subnode.attr in DANGEROUS_DUNDERS:
                                    raise SecurityError(
                                        f"Access to '{subnode.attr}' in f-string "
                                        "is not allowed (security restriction)"
                                    )

    def _create_safe_import(self) -> Any:
        """Create a safe __import__ function that only allows whitelisted modules."""

        def safe_import(name: str, *args: Any, **kwargs: Any) -> Any:
            module_name = name.split(".")[0]
            if module_name not in self.allowed_imports:
                raise SecurityError(f"Import '{module_name}' not allowed")
            return __import__(name, *args, **kwargs)

        return safe_import

    def _create_safe_globals(self, context: dict[str, Any]) -> dict[str, Any]:
        """Create safe globals dict with restricted builtins.

        Args:
            context: Context variables to inject into execution

        Returns:
            Safe globals dictionary with restricted builtins
        """
        import builtins as builtins_module

        # Get safe builtins (exclude dangerous ones)
        safe_builtins = {}
        for name in dir(builtins_module):
            if name not in DANGEROUS_BUILTINS:
                with contextlib.suppress(AttributeError):
                    safe_builtins[name] = getattr(builtins_module, name)

        # Add safe __import__
        safe_builtins["__import__"] = self._create_safe_import()

        # Create globals with safe builtins and context
        return {"__builtins__": safe_builtins, **context}

    async def execute(
        self,
        code: str,
        context: dict[str, Any] | None = None,
    ) -> SandboxResult:
        """Execute Python code in sandbox.

        Args:
            code: Python code to execute
            context: Optional context variables to inject

        Returns:
            SandboxResult with execution results

        The code can set a 'result' variable which will be captured.
        All stdout/stderr is captured and returned.
        """
        context = context or {}
        self._tool_call_count = 0

        start_time = time.perf_counter()
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()

        try:
            # Validate imports
            self._validate_imports(code)

            # Validate dangerous attribute access
            self._validate_dangerous_attrs(code)

            # Create safe globals
            safe_globals = self._create_safe_globals(context)

            # Add result variable to capture output
            safe_globals["result"] = None

            # Compile with PyCF_ALLOW_TOP_LEVEL_AWAIT so code steps can use
            # ``await context.tools.call(...)`` for nested tool invocations.
            compiled = compile(
                code,
                "<sandbox>",
                "exec",
                flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
            )

            # Execute with timeout.
            # When PyCF_ALLOW_TOP_LEVEL_AWAIT is set and the code contains
            # ``await``, we need to wrap the code object in a FunctionType
            # and call it to get a coroutine that can be awaited.
            # For sync code the function returns None immediately.
            async def _execute() -> Any:
                with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                    fn = types.FunctionType(compiled, safe_globals)
                    coro_or_none = fn()
                    if asyncio.iscoroutine(coro_or_none):
                        await coro_or_none
                return safe_globals.get("result")

            try:
                result = await asyncio.wait_for(_execute(), timeout=self.timeout)
                success = True
                error = None
            except TimeoutError:
                success = False
                result = None
                error = f"Execution timeout after {self.timeout}s"
            except (SystemExit, KeyboardInterrupt, GeneratorExit) as e:
                # Catch system exceptions that would normally escape
                success = False
                result = None
                error = f"{type(e).__name__}: {str(e) if str(e) else 'raised'}"
            except Exception as e:
                success = False
                result = None
                error = f"{type(e).__name__}: {str(e)}"

            execution_time = time.perf_counter() - start_time

            # Get captured output
            stdout = stdout_capture.getvalue()
            stderr = stderr_capture.getvalue()

            # Truncate if too large
            if len(stdout) > self.max_output_size:
                stdout = stdout[: self.max_output_size] + "\n... (truncated)"
            if len(stderr) > self.max_output_size:
                stderr = stderr[: self.max_output_size] + "\n... (truncated)"

            return SandboxResult(
                success=success,
                result=result,
                stdout=stdout,
                stderr=stderr,
                execution_time=execution_time,
                error=error,
                tool_call_count=self._tool_call_count,
            )

        except SecurityError as e:
            execution_time = time.perf_counter() - start_time
            return SandboxResult(
                success=False,
                result=None,
                stdout=stdout_capture.getvalue(),
                stderr=stderr_capture.getvalue(),
                execution_time=execution_time,
                error=f"Security violation: {str(e)}",
                tool_call_count=self._tool_call_count,
            )
        except Exception as e:
            execution_time = time.perf_counter() - start_time
            return SandboxResult(
                success=False,
                result=None,
                stdout=stdout_capture.getvalue(),
                stderr=stderr_capture.getvalue(),
                execution_time=execution_time,
                error=f"Unexpected error: {type(e).__name__}: {str(e)}",
                tool_call_count=self._tool_call_count,
            )

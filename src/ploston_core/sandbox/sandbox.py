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


# Security constants
SAFE_IMPORTS = {
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
    "hashlib",  # For hashing
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

        Args:
            code: Python code to validate

        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []

        # Check syntax
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            errors.append(f"Syntax error at line {e.lineno}: {e.msg}")
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
        disallowed_names = {"eval", "exec", "compile"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in disallowed_names:
                errors.append(f"Use of '{node.id}' is not allowed")

        return errors

    def _validate_imports(self, code: str) -> None:
        """Validate that code only imports allowed modules.

        Args:
            code: Python code to validate

        Raises:
            SecurityError: If code imports disallowed modules
        """
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            raise SecurityError(f"Syntax error in code: {e}") from e

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

            # Create safe globals
            safe_globals = self._create_safe_globals(context)

            # Add result variable to capture output
            safe_globals["result"] = None

            # Execute with timeout
            async def _execute() -> Any:
                with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                    exec(code, safe_globals)
                return safe_globals.get("result")

            try:
                result = await asyncio.wait_for(_execute(), timeout=self.timeout)
                success = True
                error = None
            except TimeoutError:
                success = False
                result = None
                error = f"Execution timeout after {self.timeout}s"
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

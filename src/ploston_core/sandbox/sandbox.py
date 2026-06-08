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
import multiprocessing
import pickle
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ploston_core.types import ToolCallerProtocol

if TYPE_CHECKING:
    from ploston_core.sandbox import worker as _worker_types


class SecurityError(Exception):
    """Raised when code violates security policy."""

    pass


class _PlostonStepExit(BaseException):
    """Internal control-flow signal raised when agent code uses top-level
    ``return``. Inherits from :class:`BaseException` (not :class:`Exception`)
    so user ``except Exception:`` blocks don't swallow it. Caught only at
    the sandbox wrapper boundary inside ``PythonExecSandbox.execute()``.
    """


class _ReturnRewriter(ast.NodeTransformer):
    """Rewrite top-level ``return X`` into ``result = X; raise __ploston_step_exit__()``.

    Nested function/lambda bodies are left alone — those are real Python
    functions whose ``return`` keeps standard semantics.

    Implements DEC-189 / S-293.
    """

    def __init__(self) -> None:
        self._depth = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        self._depth += 1
        node = self.generic_visit(node)  # type: ignore[assignment]
        self._depth -= 1
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        self._depth += 1
        node = self.generic_visit(node)  # type: ignore[assignment]
        self._depth -= 1
        return node

    def visit_Lambda(self, node: ast.Lambda) -> ast.AST:
        # Lambdas can't contain Return statements, but recurse defensively.
        self._depth += 1
        node = self.generic_visit(node)  # type: ignore[assignment]
        self._depth -= 1
        return node

    def visit_Return(self, node: ast.Return) -> Any:
        if self._depth > 0:
            return node

        raise_exit = ast.Raise(
            exc=ast.Call(
                func=ast.Name(id="__ploston_step_exit__", ctx=ast.Load()),
                args=[],
                keywords=[],
            ),
            cause=None,
        )
        ast.copy_location(raise_exit, node)

        if node.value is None:
            return raise_exit

        # Self-assignment optimization: ``return result`` skips the redundant
        # ``result = result`` and emits only the raise.
        if isinstance(node.value, ast.Name) and node.value.id == "result":
            return raise_exit

        assign = ast.Assign(
            targets=[ast.Name(id="result", ctx=ast.Store())],
            value=node.value,
        )
        ast.copy_location(assign, node)
        return [assign, raise_exit]


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
    "_strptime",  # S-272 T-865: required by datetime.strptime() (lazy import)
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
# Sync-required with PythonExecConfig.default_imports (config/models.py — PRODUCTION GATE)
# and SandboxConfig defaults (sandbox/types.py).

# ─── H-2: fail-closed builtins allowlist ───────────────────────────────────
# Previously the sandbox copied ALL builtins minus a denylist (DANGEROUS_BUILTINS),
# which is fail-OPEN: any newly-added or overlooked dangerous builtin leaks in.
# We now expose an explicit ALLOWLIST of known-safe builtins (fail-CLOSED). The
# AST dunder guard remains as defense-in-depth. ``__import__`` is added
# separately as a controlled, allowlist-aware wrapper (not from this set).
#
# Deliberately EXCLUDED (kept blocked): eval, exec, compile, open, input,
# breakpoint, exit, quit, help, globals, locals, vars, dir, getattr, setattr,
# delattr, hasattr, getattr-family, super, type, classmethod, staticmethod,
# property, memoryview, object, __import__, __build_class__, copyright, license.
SAFE_BUILTINS = {
    # ── safe constants ──
    "True",
    "False",
    "None",
    "NotImplemented",
    "Ellipsis",
    "__debug__",
    # ── core scalar/collection constructors & numeric ──
    "abs",
    "bool",
    "bytearray",
    "bytes",
    "complex",
    "dict",
    "divmod",
    "float",
    "frozenset",
    "int",
    "list",
    "pow",
    "round",
    "set",
    "slice",
    "str",
    "tuple",
    # ── iteration / functional ──
    "all",
    "any",
    "enumerate",
    "filter",
    "iter",
    "len",
    "map",
    "max",
    "min",
    "next",
    "range",
    "reversed",
    "sorted",
    "sum",
    "zip",
    # ── string / repr / encoding helpers ──
    "ascii",
    "bin",
    "chr",
    "format",
    "hex",
    "oct",
    "ord",
    "repr",
    "print",
    # ── safe introspection ──
    "isinstance",
    "issubclass",
    "hash",
    "id",
    # ── exception & warning classes (needed for try/except in user code) ──
    "ArithmeticError",
    "AssertionError",
    "AttributeError",
    "BaseException",
    "BaseExceptionGroup",
    "BlockingIOError",
    "BrokenPipeError",
    "BufferError",
    "BytesWarning",
    "ChildProcessError",
    "ConnectionAbortedError",
    "ConnectionError",
    "ConnectionRefusedError",
    "ConnectionResetError",
    "DeprecationWarning",
    "EOFError",
    "EncodingWarning",
    "EnvironmentError",
    "Exception",
    "ExceptionGroup",
    "FileExistsError",
    "FileNotFoundError",
    "FloatingPointError",
    "FutureWarning",
    "GeneratorExit",
    "IOError",
    "ImportError",
    "ImportWarning",
    "IndentationError",
    "IndexError",
    "InterruptedError",
    "IsADirectoryError",
    "KeyError",
    "KeyboardInterrupt",
    "LookupError",
    "MemoryError",
    "ModuleNotFoundError",
    "NameError",
    "NotADirectoryError",
    "NotImplementedError",
    "OSError",
    "OverflowError",
    "PendingDeprecationWarning",
    "PermissionError",
    "ProcessLookupError",
    "RecursionError",
    "ReferenceError",
    "ResourceWarning",
    "RuntimeError",
    "RuntimeWarning",
    "StopAsyncIteration",
    "StopIteration",
    "SyntaxError",
    "SyntaxWarning",
    "SystemError",
    "SystemExit",
    "TabError",
    "TimeoutError",
    "TypeError",
    "UnboundLocalError",
    "UnicodeDecodeError",
    "UnicodeEncodeError",
    "UnicodeError",
    "UnicodeTranslateError",
    "UnicodeWarning",
    "UserWarning",
    "ValueError",
    "Warning",
    "ZeroDivisionError",
}

# Denylist of builtins explicitly called out as dangerous. As of H-2 this is no
# longer the ENFORCEMENT mechanism (SAFE_BUILTINS is the fail-closed gate); it is
# retained because workflow authoring / schema generation surfaces it to users as
# "builtins you may not use", and the static ``check_forbidden_builtins`` lint
# reports their presence. Enforcement = allowlist; this = documentation/lint.
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

# Matches format-string patterns that reference dunders, e.g.:
#   {0.__class__}  {x.__dict__}  {.__bases__}  {foo.__globals__}
# Used to distinguish dangerous format strings from plain string literals
# that merely mention a dunder name in prose.
_FORMAT_DUNDER_RE = re.compile(r"\{[^}]*\.__[a-z]+__")


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
            # Only flag strings that contain format-string patterns like
            # {0.__class__} or {x.__dict__} — plain strings that happen
            # to mention a dunder (e.g. "the __dict__ attribute") are harmless.
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for dunder in DANGEROUS_DUNDERS:
                    if dunder in node.value and _FORMAT_DUNDER_RE.search(node.value):
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
            tree: ast.AST = compile(
                code, "<sandbox>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT | ast.PyCF_ONLY_AST
            )
            return tree
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
            # Only flag strings with format-string patterns ({...dunder...}),
            # not plain strings that happen to mention a dunder name.
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for dunder in DANGEROUS_DUNDERS:
                    if dunder in node.value and _FORMAT_DUNDER_RE.search(node.value):
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

    def _compile_step(self, code: str) -> Any:
        """Parse → return-rewrite → compile the (already-validated) code.

        Returns a code object compiled with ``PyCF_ALLOW_TOP_LEVEL_AWAIT`` so
        code steps can ``await context.tools.call(...)``. Runs in the PARENT.
        """
        # S-293 / DEC-189: rewrite top-level ``return X`` into
        # ``result = X; raise __ploston_step_exit__()``.
        try:
            tree = ast.parse(code, mode="exec", type_comments=False)
        except SyntaxError:
            tree = compile(
                code,
                "<sandbox>",
                "exec",
                flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT | ast.PyCF_ONLY_AST,
            )
        tree = _ReturnRewriter().visit(tree)
        ast.fix_missing_locations(tree)
        return compile(
            tree,
            "<sandbox>",
            "exec",
            flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
        )

    async def execute(
        self,
        code: str,
        context: dict[str, Any] | None = None,
    ) -> SandboxResult:
        """Execute Python code in a sandboxed CHILD PROCESS (CR-1).

        Security model:
        - Static AST validation (imports allowlist + dangerous dunders) runs
          HERE, in the parent, before anything is executed.
        - The validated code runs in a child process so a wall-clock timeout
          enforced by the parent can HARD-KILL (SIGKILL) CPU-bound / blocking
          synchronous code (``while True: pass``) — which the previous
          in-process ``asyncio.wait_for`` design could never interrupt.
        - Non-picklable context objects (the live ToolCallInterface /
          SandboxContext, async test closures) stay in the parent; the child
          gets IPC proxies. Tool whitelist / rate-limit / blocked-tools are
          therefore enforced on the parent side, where a malicious child
          cannot bypass them.

        Args:
            code: Python code to execute
            context: Optional context variables to inject

        Returns:
            SandboxResult with execution results

        The code can set a ``result`` variable (or top-level ``return``) which
        is captured. stdout/stderr are captured and returned.
        """
        context = context or {}
        self._tool_call_count = 0
        start_time = time.perf_counter()

        # ── Static validation (parent side, before any execution) ──
        try:
            self._validate_imports(code)
            self._validate_dangerous_attrs(code)
        except SecurityError as e:
            return SandboxResult(
                success=False,
                result=None,
                stdout="",
                stderr="",
                execution_time=time.perf_counter() - start_time,
                error=f"Security violation: {str(e)}",
                tool_call_count=self._tool_call_count,
            )

        # ── Compile (parent side) ──
        try:
            compiled = self._compile_step(code)
        except SecurityError as e:
            return SandboxResult(
                success=False,
                result=None,
                stdout="",
                stderr="",
                execution_time=time.perf_counter() - start_time,
                error=f"Security violation: {str(e)}",
                tool_call_count=self._tool_call_count,
            )
        except (SyntaxError, ValueError) as e:
            return SandboxResult(
                success=False,
                result=None,
                stdout="",
                stderr="",
                execution_time=time.perf_counter() - start_time,
                error=f"SyntaxError: {str(e)}",
                tool_call_count=self._tool_call_count,
            )

        try:
            outcome, stdout, stderr = await self._run_in_child(compiled, context)
        except Exception as e:
            return SandboxResult(
                success=False,
                result=None,
                stdout="",
                stderr="",
                execution_time=time.perf_counter() - start_time,
                error=f"Unexpected error: {type(e).__name__}: {str(e)}",
                tool_call_count=self._tool_call_count,
            )

        execution_time = time.perf_counter() - start_time

        if len(stdout) > self.max_output_size:
            stdout = stdout[: self.max_output_size] + "\n... (truncated)"
        if len(stderr) > self.max_output_size:
            stderr = stderr[: self.max_output_size] + "\n... (truncated)"

        return SandboxResult(
            success=outcome.success,
            result=outcome.result,
            stdout=stdout,
            stderr=stderr,
            execution_time=execution_time,
            error=outcome.error,
            tool_call_count=self._tool_call_count,
        )

    async def _run_in_child(
        self,
        compiled: Any,
        context: dict[str, Any],
    ) -> tuple[Any, str, str]:
        """Spawn a child, drive the tool-call IPC, and enforce the timeout.

        Returns ``(ExecOutcome, stdout, stderr)``. On timeout the child is
        hard-killed and a timeout ExecOutcome is returned.
        """
        import marshal

        from ploston_core.sandbox import worker as _worker

        # Partition context: picklable values travel as plain data; everything
        # else stays in the parent behind a proxy. ``can_pickle`` is the gate.
        registry: dict[int, Any] = {}
        context_plain: dict[str, Any] = {}
        proxy_refs: dict[str, _worker.ProxyRef] = {}
        for key, value in context.items():
            ref = _make_proxy_ref(value, registry)
            if ref is None:
                context_plain[key] = value
            else:
                proxy_refs[key] = ref

        code_bytes = marshal.dumps(compiled)

        ctx = _get_mp_context()
        parent_conn, child_conn = ctx.Pipe()
        proc: multiprocessing.process.BaseProcess = ctx.Process(  # type: ignore[attr-defined]
            target=_worker._worker_main,
            args=(child_conn,),
            kwargs={
                "code_bytes": code_bytes,
                "context_plain": context_plain,
                "proxy_refs": proxy_refs,
                "safe_builtins_names": tuple(sorted(SAFE_BUILTINS)),
                "extra_imports": tuple(sorted(self.allowed_imports)),
            },
        )
        proc.start()
        child_conn.close()  # parent keeps only its end

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout

        outcome: _worker.ExecOutcome | None = None
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break  # timeout → kill below

                # Wait for a message without blocking the event loop. The
                # blocking ``poll`` runs in a thread; on each wakeup we either
                # service a tool-call request or collect the final outcome.
                has_msg = await loop.run_in_executor(None, parent_conn.poll, min(remaining, 0.5))
                if not has_msg:
                    if not proc.is_alive():
                        # Child died without sending an outcome.
                        break
                    continue

                try:
                    msg = parent_conn.recv()
                except EOFError:
                    break

                if msg == _worker._DONE:
                    outcome = parent_conn.recv()
                    break

                # Otherwise it's an OpRequest from the child — service it.
                resp = await _service_op(msg, registry)
                parent_conn.send(resp)
        finally:
            if proc.is_alive():
                proc.kill()
            await loop.run_in_executor(None, proc.join, 5)
            with contextlib.suppress(Exception):
                parent_conn.close()

        if outcome is None:
            # Timed out or child crashed before sending a result.
            if proc.exitcode is not None and proc.exitcode != 0 and proc.exitcode != -9:
                return (
                    _worker.ExecOutcome(
                        success=False,
                        error=f"Child process exited with code {proc.exitcode}",
                    ),
                    "",
                    "",
                )
            return (
                _worker.ExecOutcome(
                    success=False,
                    error=f"Execution timeout after {self.timeout}s",
                ),
                "",
                "",
            )

        return outcome, outcome.stdout, outcome.stderr


# ─────────────────────────────────────────────────────────────────
# CR-1 parent-side IPC helpers (module-level so spawn/forkserver works)
# ─────────────────────────────────────────────────────────────────

_MP_CONTEXT: multiprocessing.context.BaseContext | None = None


def _get_mp_context() -> multiprocessing.context.BaseContext:
    """Return (and memoize) the multiprocessing context for code-step children.

    Prefers ``forkserver`` (CR-1): children are forked from a minimal server
    process that pre-imports the sandbox modules, giving CLEAN module state
    (no parent objects, no event loop) — the spirit of ``spawn`` — while being
    ~1000x cheaper than ``spawn`` (which re-imports ``ploston_core`` per call,
    making the property/fuzz suites infeasible). Falls back to ``spawn`` where
    ``forkserver`` is unavailable.
    """
    global _MP_CONTEXT
    if _MP_CONTEXT is not None:
        return _MP_CONTEXT

    methods = multiprocessing.get_all_start_methods()
    if "forkserver" in methods:
        ctx = multiprocessing.get_context("forkserver")
        with contextlib.suppress(Exception):
            # Warm the forkserver so the first real call is fast and children
            # inherit the imported sandbox modules.
            ctx.set_forkserver_preload(
                [
                    "ploston_core.sandbox.worker",
                    "ploston_core.sandbox.sandbox",
                    "ploston_core.sandbox.types",
                ]
            )
            # Eagerly start + warm the forkserver so the FIRST real execute()
            # doesn't pay the one-time server-boot + preload-import cost (which
            # otherwise shows up as a multi-hundred-ms latency spike on call 1).
            with contextlib.suppress(Exception):
                from multiprocessing import forkserver as _fs

                _fs.ensure_running()
        _MP_CONTEXT = ctx
    else:  # pragma: no cover - platform dependent
        _MP_CONTEXT = multiprocessing.get_context("spawn")
    return _MP_CONTEXT


def _can_pickle(value: Any) -> bool:
    """True if ``value`` round-trips through pickle (so it can travel as data)."""
    try:
        pickle.dumps(value)
        return True
    except Exception:
        return False


def _make_proxy_ref(value: Any, registry: dict[int, Any]) -> "_worker_types.ProxyRef | None":
    """Return a ProxyRef for a non-picklable value (registering it), else None.

    Picklable values travel across the pipe as plain data and need no proxy.
    Non-picklable values (live ToolCallInterface / SandboxContext, async test
    closures) stay in the parent's ``registry`` keyed by ``id`` and are
    referenced from the child via the returned :class:`ProxyRef`.
    """
    from ploston_core.sandbox import worker as _worker

    if _can_pickle(value):
        return None
    obj_id = id(value)
    registry[obj_id] = value
    return _worker.ProxyRef(
        obj_id=obj_id,
        is_callable=callable(value),
        is_coroutine=asyncio.iscoroutinefunction(value),
        repr_hint=_safe_repr(value),
    )


def _safe_repr(value: Any) -> str:
    try:
        return repr(value)
    except Exception:
        return f"<{type(value).__name__}>"


async def _service_op(req: "_worker_types.OpRequest", registry: dict[int, Any]) -> Any:
    """Execute one OpRequest against a live parent-held object.

    Returns an :class:`OpResponse`. Coroutine results are awaited HERE, on the
    parent's real event loop, so ``await context.tools.call(...)`` runs against
    the genuine ToolCallInterface (whitelist + rate-limit + blocked-tools all
    enforced parent-side). Exceptions — including ToolError — are sent back so
    the child can re-raise them with original semantics.
    """
    from ploston_core.sandbox import worker as _worker

    target = registry.get(req.target_id)
    if target is None:
        return _worker.OpResponse(error=RuntimeError(f"Unknown remote object {req.target_id}"))

    try:
        if req.op == "getattr":
            assert req.name is not None
            value = getattr(target, req.name)
            return _wrap_value(value, registry)

        if req.op == "call":
            value = target(*req.args, **req.kwargs)
            if asyncio.iscoroutine(value):
                value = await value
            return _wrap_value(value, registry)

        return _worker.OpResponse(error=RuntimeError(f"Unknown op {req.op!r}"))
    except BaseException as e:  # noqa: BLE001 — forward to child for re-raise
        return _wrap_error(e, registry)


def _wrap_value(value: Any, registry: dict[int, Any]) -> "_worker_types.OpResponse":
    """Return an OpResponse carrying ``value`` inline, or as a fresh proxy."""
    from ploston_core.sandbox import worker as _worker

    if _can_pickle(value):
        return _worker.OpResponse(value=value)
    ref = _make_proxy_ref(value, registry)
    return _worker.OpResponse(proxy=ref)


def _wrap_error(exc: BaseException, registry: dict[int, Any]) -> "_worker_types.OpResponse":
    """Return an OpResponse carrying an exception the child can re-raise.

    If the exception itself can't be pickled, fall back to a RuntimeError that
    preserves the type name and message.
    """
    from ploston_core.sandbox import worker as _worker

    if _can_pickle(exc):
        return _worker.OpResponse(error=exc)
    return _worker.OpResponse(error=RuntimeError(f"{type(exc).__name__}: {exc}"))

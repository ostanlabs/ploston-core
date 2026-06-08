"""CR-1: child-process worker entrypoint + parent/child IPC bridge.

The code-step sandbox executes user code in a **child process** (multiprocessing
``spawn`` context) so the PARENT can enforce a wall-clock timeout by hard-killing
the child (``SIGKILL``). This interrupts CPU-bound / blocking *synchronous* code
(``while True: pass``) that the previous in-process ``asyncio.wait_for`` design
could never abort.

Non-picklable context objects (the live ``ToolCallInterface`` / ``SandboxContext``,
arbitrary async closures used in tests) stay in the PARENT. The child receives
lightweight :class:`RemoteProxy` stand-ins. Any attribute access or call on a
proxy is serialized into an :class:`OpRequest` and sent up the pipe; the parent's
service loop resolves it against the live object, executes it (awaiting any
coroutine on the parent's real event loop), and sends back the result. This is
what keeps ``await context.tools.call(...)`` working — and keeps the tool
whitelist / rate-limit / blocked-tools enforcement on the *parent* side where a
malicious child cannot bypass it.

Everything that crosses the pipe is a plain dataclass of plain data. Tool
params/results are already required to be JSON-serializable, so pickling them is
safe.

CAVEAT (spawn): the worker entrypoint :func:`_worker_main` is module-level (not a
closure) so ``spawn`` can re-import and pickle it. macOS uses ``spawn`` by
default; we request it explicitly for deterministic, clean-state children.
"""

from __future__ import annotations

import asyncio
import io
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from multiprocessing.connection import Connection


# ─────────────────────────────────────────────────────────────────
# Wire protocol — plain dataclasses, all fields plain/picklable data
# ─────────────────────────────────────────────────────────────────


@dataclass
class OpRequest:
    """Child → parent: perform an operation against a live parent object.

    ``op`` is one of:
      * ``"getattr"``  — read ``getattr(target, name)``; scalar/None results come
        back inline, anything else comes back as a fresh proxy ref.
      * ``"call"``     — call ``target(*args, **kwargs)``; if the result is a
        coroutine it is awaited on the parent loop before returning.

    ``proxy.method(...)`` is expressed as a ``getattr`` (yielding a callable
    proxy for the bound method) followed by a ``call`` on that proxy.
    """

    op: str
    target_id: int
    name: str | None = None
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass
class OpResponse:
    """Parent → child: the result of an :class:`OpRequest`.

    Exactly one of ``value`` / ``proxy`` is meaningful, unless ``error`` is set.
    ``error`` carries a reconstructed exception so the child can re-raise it
    (preserving ``ToolError`` semantics across the boundary).
    """

    value: Any = None
    proxy: ProxyRef | None = None
    error: BaseException | None = None


@dataclass
class ProxyRef:
    """A reference to a live object that remains in the parent process.

    ``obj_id`` keys the parent's object registry. ``repr_hint`` lets the child
    produce a stable ``repr()`` (used by the "unawaited coroutine" detection
    tests) without a round-trip. ``is_coroutine`` says whether calling the
    referent returns a coroutine (e.g. ``tools.call``) — in which case the
    child proxy's ``__call__`` must itself return a coroutine so user code
    ``await``s it — versus a plain sync call (e.g. ``context.log``) that
    returns its value directly.
    """

    obj_id: int
    is_callable: bool = False
    is_coroutine: bool = False
    repr_hint: str = "<remote object>"


@dataclass
class ExecOutcome:
    """Final result sent child → parent over the result pipe."""

    success: bool
    result: Any = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None


# ─────────────────────────────────────────────────────────────────
# Child-side proxy
# ─────────────────────────────────────────────────────────────────


class _ChildBridge:
    """Holds the child's end of the pipe; (de)proxies values."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def request(self, req: OpRequest) -> Any:
        self._conn.send(req)
        resp: OpResponse = self._conn.recv()
        if resp.error is not None:
            raise resp.error
        if resp.proxy is not None:
            return RemoteProxy(self, resp.proxy)
        return resp.value


class RemoteProxy:
    """Child-side stand-in for a live object kept in the parent process.

    Attribute reads and calls are forwarded to the parent over the pipe. Calls
    are exposed as ``async`` methods so user code must ``await`` them — which
    both matches the real ``ToolCallInterface`` API and preserves the documented
    "forgot to await ⇒ get a coroutine object" behaviour: an un-awaited call
    constructs a coroutine that never drives any IPC.
    """

    __slots__ = ("_bridge", "_ref")

    def __init__(self, bridge: _ChildBridge, ref: ProxyRef) -> None:
        object.__setattr__(self, "_bridge", bridge)
        object.__setattr__(self, "_ref", ref)

    def __getattr__(self, name: str) -> Any:
        # Resolve the attribute in the parent. Methods come back as callable
        # proxies; data attributes come back as values (or nested proxies).
        return self._bridge.request(OpRequest(op="getattr", target_id=self._ref.obj_id, name=name))

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        # Async referents (e.g. ``tools.call``) must return a coroutine so the
        # user ``await``s it — awaiting is what drives the IPC round-trip, and
        # NOT awaiting yields a coroutine object (preserving the documented
        # "forgot to await" behaviour). Sync referents (e.g. ``context.log``)
        # do the IPC immediately and return their value.
        if self._ref.is_coroutine:
            return self._async_call(args, kwargs)
        return self._do_call(args, kwargs)

    def _do_call(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        return self._bridge.request(
            OpRequest(
                op="call",
                target_id=self._ref.obj_id,
                args=args,
                kwargs=kwargs,
            )
        )

    async def _async_call(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        return self._do_call(args, kwargs)

    def __repr__(self) -> str:
        ref: ProxyRef = object.__getattribute__(self, "_ref")
        return ref.repr_hint


# ─────────────────────────────────────────────────────────────────
# Child worker entrypoint (must be module-level for spawn pickling)
# ─────────────────────────────────────────────────────────────────


def _worker_main(
    conn: Connection,
    *,
    code_bytes: bytes,
    context_plain: dict[str, Any],
    proxy_refs: dict[str, ProxyRef],
    safe_builtins_names: tuple[str, ...],
    extra_imports: tuple[str, ...],
) -> None:
    """Entrypoint run inside the spawned child process.

    Args:
        conn: child end of the duplex pipe (tool-call IPC + final result).
        code_bytes: marshalled, already-validated + return-rewritten code object.
        context_plain: picklable context values injected as globals.
        proxy_refs: name → ProxyRef for non-picklable context values.
        safe_builtins_names: allowlisted builtin names to expose.
        extra_imports: import allowlist for the controlled ``__import__``.
    """
    import marshal

    bridge = _ChildBridge(conn)

    # Rebuild globals: plain context + proxies for parent-held objects.
    glb: dict[str, Any] = dict(context_plain)
    for name, ref in proxy_refs.items():
        glb[name] = RemoteProxy(bridge, ref)

    glb["__builtins__"] = _build_safe_builtins(safe_builtins_names, extra_imports)

    # Injected names (mirror the in-process sandbox).
    from ploston_core.sandbox.sandbox import _PlostonStepExit
    from ploston_core.sandbox.types import ToolError

    glb["ToolError"] = ToolError
    glb["__ploston_step_exit__"] = _PlostonStepExit
    glb["result"] = None

    code_obj = marshal.loads(code_bytes)

    stdout_cap = io.StringIO()
    stderr_cap = io.StringIO()

    outcome = ExecOutcome(success=False)
    try:
        import types as _types

        async def _run() -> Any:
            with redirect_stdout(stdout_cap), redirect_stderr(stderr_cap):
                fn = _types.FunctionType(code_obj, glb)
                try:
                    coro_or_none = fn()
                    if asyncio.iscoroutine(coro_or_none):
                        await coro_or_none
                except _PlostonStepExit:
                    pass
            return glb.get("result")

        result = asyncio.run(_run())
        outcome.success = True
        outcome.result = result
    except BaseException as e:  # noqa: BLE001 — report any failure to parent
        outcome.success = False
        # Find <sandbox> frame line number, mirroring the in-process impl.
        lineno = None
        tb = e.__traceback__
        while tb is not None:
            if tb.tb_frame.f_code.co_filename == "<sandbox>":
                lineno = tb.tb_lineno
            tb = tb.tb_next
        if lineno is not None:
            outcome.error = f"{type(e).__name__} at line {lineno}: {e}"
        else:
            outcome.error = f"{type(e).__name__}: {e}"
    finally:
        outcome.stdout = stdout_cap.getvalue()
        outcome.stderr = stderr_cap.getvalue()

    # Signal "done" then send the outcome. The leading None lets the parent's
    # service loop distinguish a final-result message from an OpRequest.
    try:
        conn.send(_DONE)
        conn.send(outcome)
    except (BrokenPipeError, OSError):
        pass


def _build_safe_builtins(
    allow_names: tuple[str, ...],
    extra_imports: tuple[str, ...],
) -> dict[str, Any]:
    """Build a fail-closed builtins dict from an explicit allowlist (H-2)."""
    import builtins as builtins_module

    safe: dict[str, Any] = {}
    for name in allow_names:
        if hasattr(builtins_module, name):
            safe[name] = getattr(builtins_module, name)

    allowed = set(extra_imports)

    def _safe_import(name: str, *args: Any, **kwargs: Any) -> Any:
        module_name = name.split(".")[0]
        if module_name not in allowed:
            raise ImportError(f"Import '{module_name}' not allowed")
        return __import__(name, *args, **kwargs)

    safe["__import__"] = _safe_import
    return safe


# Sentinel marking the final-result handoff on the pipe.
_DONE = "__ploston_worker_done__"

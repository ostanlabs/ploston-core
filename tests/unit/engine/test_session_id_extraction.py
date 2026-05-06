"""S-304/M-082 — session_id extraction in WorkflowEngine.

Verifies that ``_extract_bridge_runner_session`` returns the *real*
``BridgeContext.session_id`` (per-conversation) and not the long-lived
``bridge_id`` it used to silently substitute.
"""

from __future__ import annotations

from ploston_core.engine.engine import WorkflowEngine
from ploston_core.mcp_frontend.http_transport import (
    BridgeContext,
    bridge_context,
)

# The helper is pure: bind it to a stub object to dodge the real
# WorkflowEngine constructor (which demands template_engine / config).
_extract = WorkflowEngine._extract_bridge_runner_session


class _Stub:
    pass


def test_session_id_returned_when_set() -> None:
    ctx = BridgeContext(
        bridge_id="bridge-A",
        runner_name="laptop",
        session_id="conv-42",
    )
    token = bridge_context.set(ctx)
    try:
        b, r, s = _extract(_Stub())
    finally:
        bridge_context.reset(token)
    assert b == "bridge-A"
    assert r == "laptop"
    assert s == "conv-42"


def test_session_id_none_when_unset() -> None:
    """No silent fallback to bridge_id when session_id is None.

    Regression: previously this helper returned ``bridge_id`` whenever
    ``session_id`` was missing, conflating bridge identity with session.
    """
    ctx = BridgeContext(bridge_id="bridge-A", runner_name="laptop")
    token = bridge_context.set(ctx)
    try:
        b, r, s = _extract(_Stub())
    finally:
        bridge_context.reset(token)
    assert b == "bridge-A"
    assert r == "laptop"
    assert s is None


def test_all_none_when_no_bridge_context() -> None:
    # Default ContextVar is None outside any request scope.
    b, r, s = _extract(_Stub())
    assert (b, r, s) == (None, None, None)

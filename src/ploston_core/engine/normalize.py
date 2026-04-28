"""MCP response envelope normalization.

Strips transport-level wrappers from tool-call outputs so workflow steps see
clean data. Applied to tool step outputs and to sandbox call()/call_mcp()
return values. NOT applied to workflow final outputs.

Handled shapes:
    1. {"status": "...", "result": X}             → unwrap result
    2. {"result": {"content": X}}                  → unwrap to X
    3. [{"type": "text", "text": "<json|str>"}]   → parse JSON if possible
    4. {"content": X} with only that key           → unwrap to X
    5. {"content": X, "error": None}               → unwrap to X (S-289 P1)

Bare {"result": X} without a "status" sibling is left alone — a tool
legitimately returning {"result": ..., "warnings": [...]} must keep its
shape.

Shape 5 unwraps the transport envelope only when the dict has *exactly*
the keys {"content", "error"} and error is None. Tool payloads that happen
to have an "error" key alongside other application keys are not affected.
A non-null "error" is left as-is — sandbox call sites inspect the envelope
before normalization and raise ToolError on non-null errors.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["normalize_mcp_response"]


def normalize_mcp_response(raw: Any) -> Any:
    """Normalize an MCP tool response by stripping transport envelopes.

    Idempotent: an already-normalized value is returned unchanged.

    Args:
        raw: Raw tool output as returned by a runner or CP-direct caller.

    Returns:
        Normalized payload with transport wrappers removed.
    """
    if isinstance(raw, dict):
        # Shape 1: {"status": "...", "result": X} → unwrap result
        if "result" in raw and isinstance(raw.get("status"), str):
            raw = raw["result"]
        # Shape 2: {"result": {"content": X}} → unwrap only when content is nested in result
        elif "result" in raw and isinstance(raw["result"], dict) and "content" in raw["result"]:
            raw = raw["result"]
        # Otherwise: bare {"result": X} is a legitimate tool response — leave it alone.

        # Shape 4: single-key {"content": X} envelope
        if isinstance(raw, dict) and "content" in raw and len(raw) == 1:
            raw = raw["content"]
        # Shape 5: {"content": X, "error": None} transport envelope (S-289 P1).
        # Unwrap only when keys are exactly {"content", "error"} and error is None.
        elif (
            isinstance(raw, dict)
            and set(raw.keys()) == {"content", "error"}
            and raw["error"] is None
        ):
            raw = raw["content"]

    # Shape 3: [{"type": "text", "text": "<json|str>"}] → parse JSON if possible
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        if raw[0].get("type") == "text":
            text_val = raw[0].get("text", "")
            try:
                return json.loads(text_val)
            except (json.JSONDecodeError, TypeError):
                return text_val

    return raw

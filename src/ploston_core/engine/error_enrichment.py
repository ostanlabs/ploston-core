"""S-292 P4d: structured error metadata builders for ``workflow_run``.

The engine's per-step failure response is enriched with enough context
that an agent can construct a ``workflow_patch`` call without first
fetching the workflow YAML:

- code-step failures get a ``code_context`` window, ``line_in_step``
  (1-based, mapped back to the YAML ``code:`` block) and a snapshot of
  ``step_inputs.prior_step_output_keys``;
- tool-step failures get the ``params_sent`` dict (the actual values
  that reached the tool) and, for deterministic errors, a
  ``suggested_fix`` op;
- skipped-due-to-failed-dependency steps get the ``root_cause_step_id``
  pointing at the step whose failure cascaded.

Token budgets for prior-output introspection are bounded — see
``_summarize_prior_outputs``.
"""

from __future__ import annotations

import traceback
from typing import Any

# Cap for the number of keys reported per prior-step output. The
# remainder is summarized as ``"...truncated"``. Aligned with the spec
# §workflow_run failure response design.
_MAX_PRIOR_KEYS_PER_STEP = 50


def build_code_context(code: str, error_line: int, window: int = 2) -> list[dict[str, Any]]:
    """Return the ±window lines around ``error_line`` from ``code``.

    ``error_line`` is 1-indexed against the raw ``code:`` block as
    written in YAML (after stripping leading/trailing blank lines).
    """
    lines = code.splitlines()
    start = max(1, error_line - window)
    end = min(len(lines), error_line + window)
    out: list[dict[str, Any]] = []
    for ln in range(start, end + 1):
        entry: dict[str, Any] = {"line": ln, "text": lines[ln - 1]}
        if ln == error_line:
            entry["is_error_line"] = True
        out.append(entry)
    return out


def extract_line_in_step(
    exc: BaseException, step_code: str, wrapper_offset: int = 0
) -> tuple[int | None, str | None]:
    """Walk the traceback of ``exc`` to find the line that fired inside the user code.

    Returns ``(line_in_step, source_line)``: 1-based line within the
    step's ``code:`` body, plus the matching source text. Returns
    ``(None, None)`` when no frame inside the step body can be
    isolated.

    ``wrapper_offset`` is the number of synthetic lines the engine
    prepends before the user's code (e.g. ``async def _step(...):``).
    """
    tb = traceback.extract_tb(exc.__traceback__)
    if not tb:
        return None, None
    code_lines = step_code.splitlines()
    # The sandbox compiles the user source with a virtual filename like
    # ``<step:diagnose>``. Walk the traceback in reverse and pick the
    # innermost frame whose filename starts with ``<step:`` — that's
    # always the user-authored line.
    for frame in reversed(tb):
        fname = frame.filename or ""
        if not fname.startswith("<step"):
            continue
        line_in_step = frame.lineno - wrapper_offset if frame.lineno else None
        if line_in_step is None or line_in_step < 1 or line_in_step > len(code_lines):
            return None, None
        return line_in_step, code_lines[line_in_step - 1]
    # Fall back to the last frame (engine internals) — surface line+text
    # from the step body if it lines up.
    last = tb[-1]
    if last.lineno and 1 <= last.lineno <= len(code_lines):
        return last.lineno, code_lines[last.lineno - 1]
    return None, None


def summarize_prior_outputs(
    step_results: dict[str, Any],
    *,
    max_keys: int = _MAX_PRIOR_KEYS_PER_STEP,
) -> dict[str, Any]:
    """Summarize prior step outputs without leaking values.

    - dict outputs → list of top-level keys (capped at ``max_keys``)
    - list outputs → ``"<list, length=N>"``
    - scalar outputs → ``"<scalar:type>"``

    Only completed prior steps are included; skipped/failed steps are
    omitted so the agent isn't pointed at unreliable outputs.
    """
    summary: dict[str, Any] = {}
    for sid, result in step_results.items():
        if getattr(result, "status", None) is None:
            continue
        # Defer the import to keep the helper free of engine cycles.
        from ploston_core.types import StepStatus

        if result.status != StepStatus.COMPLETED:
            continue
        out = result.output
        if isinstance(out, dict):
            keys = list(out.keys())
            if len(keys) > max_keys:
                summary[sid] = keys[:max_keys] + ["...truncated"]
            else:
                summary[sid] = keys
        elif isinstance(out, list):
            summary[sid] = f"<list, length={len(out)}>"
        elif out is None:
            summary[sid] = "<scalar:NoneType>"
        else:
            summary[sid] = f"<scalar:{type(out).__name__}>"
    return summary


def build_skipped_metadata(root_cause_step_id: str, root_cause_error: Any) -> dict[str, Any]:
    """Return ``error_metadata`` for a step skipped due to a failed dep.

    ``root_cause_error`` is stringified — clients can pull the full
    structured detail off the corresponding ``execution.steps[<root>]``
    entry in the response.
    """
    return {
        "step_type": "skipped",
        "root_cause_step_id": root_cause_step_id,
        "root_cause_error": str(root_cause_error) if root_cause_error else None,
    }

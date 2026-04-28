"""Workflow validation."""

from __future__ import annotations

import ast
import difflib
import re
from typing import TYPE_CHECKING, Any

from ploston_core.template import TemplateEngine
from ploston_core.types import ValidationIssue, ValidationResult

from .types import StepDefinition, WorkflowDefinition

if TYPE_CHECKING:
    from ploston_core.registry import ToolRegistry
    from ploston_core.runner_management.registry import RunnerRegistry


# S-291 P3: Input names that conflict with reserved Python keywords or
# template engine internals. Surfaced by ``enrich_with_suggested_fixes`` —
# detection is best-effort since the parser/validator doesn't currently
# block reserved names, but the enrichment surfaces them with
# ``requires_agent_decision: true`` when seen.
_RESERVED_INPUT_NAMES = frozenset(
    {
        # Python keywords most likely to collide with template variables.
        "class",
        "def",
        "for",
        "if",
        "else",
        "elif",
        "while",
        "return",
        "import",
        "from",
        "as",
        "with",
        "try",
        "except",
        "finally",
        "raise",
        "yield",
        "lambda",
        "global",
        "nonlocal",
        "pass",
        "break",
        "continue",
        "and",
        "or",
        "not",
        "is",
        "in",
        "True",
        "False",
        "None",
        # Reserved by the workflow context object inside code steps.
        "context",
        "inputs",
        "steps",
        "outputs",
        "result",
        "self",
    }
)


# Cutoff used by ``difflib.get_close_matches`` for nearest-name suggestions.
# 0.6 matches the spec (line 248) — high enough to avoid noise on short
# tokens, low enough to catch single-character typos.
_FUZZY_CUTOFF = 0.6


# ── Static checks (S-291 P3) ─────────────────────────────────────────


def check_return_in_code(workflow: WorkflowDefinition) -> list[ValidationIssue]:
    """Flag ``return X`` statements in code steps.

    Code steps run as the body of an ``async def`` in the sandbox; agents
    are required to assign their output to ``result`` instead of using
    ``return``. The runtime sandbox does enforce this, but surfacing it at
    validation time turns a runtime failure into an authoring-time one
    that ``workflow_create`` can fix via ``suggested_fix``.

    Best-effort: any per-step parse failure degrades to a no-op for that
    step (the Python parser raises when the user wrote unrelated syntax
    errors, in which case other validation pathways will already complain).
    """
    issues: list[ValidationIssue] = []
    for step in workflow.steps:
        if not step.code:
            continue
        try:
            tree = ast.parse(step.code)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Return):
                # Capture the source slice for the suggested_fix `old` field.
                lines = step.code.splitlines()
                line_idx = (node.lineno - 1) if node.lineno else 0
                line_text = lines[line_idx] if 0 <= line_idx < len(lines) else ""
                issues.append(
                    ValidationIssue(
                        path=f"steps.{step.id}.code",
                        message=(
                            "code steps must assign to 'result', not use 'return'. "
                            "Replace 'return X' with 'result = X'."
                        ),
                        severity="error",
                        line=node.lineno,
                    )
                )
                # One issue per step is enough — the suggested_fix will
                # cover the first offending line; agents can re-validate
                # to surface any remainder. Avoids spamming the response
                # for the common case of a single trailing ``return``.
                _ = line_text
                break
    return issues


def check_forbidden_imports(
    workflow: WorkflowDefinition,
    allowed_imports: frozenset[str] | set[str],
) -> list[ValidationIssue]:
    """Flag ``import X`` / ``from X import ...`` for modules outside the allowlist.

    Mirrors what the sandbox enforces at execution time (sandbox.py
    SAFE_IMPORTS), but raised at validation time so ``workflow_create``
    can produce a ``suggested_fix`` directing the agent to remove the
    line. There is no useful auto-replacement (the agent must choose what
    to do without that import), so the fix is a deletion of the offending
    line.
    """
    issues: list[ValidationIssue] = []
    for step in workflow.steps:
        if not step.code:
            continue
        try:
            tree = ast.parse(step.code)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            module: str | None = None
            line_no: int = 0
            if isinstance(node, ast.Import):
                # Only flag the first alias — typical pattern is one
                # module per import statement; if the agent wrote
                # ``import a, b`` we'll catch ``a`` first.
                module = node.names[0].name.split(".", 1)[0] if node.names else None
                line_no = node.lineno
            elif isinstance(node, ast.ImportFrom):
                module = (node.module or "").split(".", 1)[0]
                line_no = node.lineno
            if not module or module in allowed_imports:
                continue
            issues.append(
                ValidationIssue(
                    path=f"steps.{step.id}.code",
                    message=(
                        f"forbidden import '{module}' — sandbox only allows "
                        f"{sorted(allowed_imports)}. Remove the import or "
                        "switch to an allowed alternative."
                    ),
                    severity="error",
                    line=line_no,
                )
            )
    return issues


def check_forbidden_builtins(
    workflow: WorkflowDefinition,
    dangerous_builtins: frozenset[str] | set[str],
) -> list[ValidationIssue]:
    """Flag direct calls to dangerous builtins (eval, exec, open, ...).

    The sandbox blocks these at runtime; we surface them at validation
    time with a suggested replacement when one is well-known
    (``type(x)`` → ``isinstance(x, T)``), and otherwise emit a
    ``requires_agent_decision`` error.
    """
    issues: list[ValidationIssue] = []
    for step in workflow.steps:
        if not step.code:
            continue
        try:
            tree = ast.parse(step.code)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                name = node.func.id
                if name in dangerous_builtins:
                    issues.append(
                        ValidationIssue(
                            path=f"steps.{step.id}.code",
                            message=(
                                f"forbidden builtin '{name}()' — sandbox blocks this at runtime."
                            ),
                            severity="error",
                            line=node.lineno,
                        )
                    )
    return issues


class WorkflowValidator:
    """Validate workflow definitions."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        runner_registry: RunnerRegistry | None = None,
    ):
        """Initialize validator.

        Args:
            tool_registry: Tool registry for CP-direct tool existence checks
            runner_registry: Optional runner registry for runner-hosted tool checks
        """
        self._tool_registry = tool_registry
        self._runner_registry = runner_registry
        self._template_engine = TemplateEngine()

    def validate(
        self,
        workflow: WorkflowDefinition,
        check_tools: bool = True,
    ) -> ValidationResult:
        """Validate workflow definition.

        Checks:
        - Required fields present
        - Valid types
        - Unique step IDs
        - Valid depends_on references
        - No circular dependencies
        - Tool XOR code per step
        - Tool exists (if check_tools=True)
        - Valid template syntax in params

        Args:
            workflow: Workflow to validate
            check_tools: Whether to check tool existence

        Returns:
            ValidationResult with errors and warnings
        """
        errors: list[ValidationIssue] = []
        warnings: list[ValidationIssue] = []

        # Check required fields
        if not workflow.name:
            errors.append(
                ValidationIssue(
                    path="name",
                    message="Workflow name is required",
                    severity="error",
                )
            )

        if not workflow.version:
            errors.append(
                ValidationIssue(
                    path="version",
                    message="Workflow version is required",
                    severity="error",
                )
            )

        # Check unique step IDs
        step_ids = [step.id for step in workflow.steps]
        input_names = [inp.name for inp in (workflow.inputs or [])]
        duplicates = [sid for sid in step_ids if step_ids.count(sid) > 1]
        if duplicates:
            errors.append(
                ValidationIssue(
                    path="steps",
                    message=f"Duplicate step IDs: {', '.join(set(duplicates))}",
                    severity="error",
                )
            )

        # Validate each step
        for step in workflow.steps:
            # Tool XOR code
            if step.tool and step.code:
                errors.append(
                    ValidationIssue(
                        path=f"steps.{step.id}",
                        message="Step must have either 'tool' or 'code', not both",
                        severity="error",
                    )
                )
            elif not step.tool and not step.code:
                errors.append(
                    ValidationIssue(
                        path=f"steps.{step.id}",
                        message="Step must have either 'tool' or 'code'",
                        severity="error",
                    )
                )

            # Check mcp is set for tool steps
            if step.tool and not step.mcp:
                errors.append(
                    ValidationIssue(
                        path=f"steps.{step.id}.mcp",
                        message=(
                            f"Tool step '{step.id}' is missing the 'mcp' field. "
                            "The 'mcp' field is required for tool steps — it identifies "
                            "which MCP server hosts the tool."
                        ),
                        severity="error",
                    )
                )

            # Check tool exists via (mcp, tool, runner) resolution
            if check_tools and step.tool and step.mcp:
                resolved, resolve_error = self._resolve_tool(
                    step, workflow.defaults.runner if workflow.defaults else None
                )
                if not resolved:
                    errors.append(
                        ValidationIssue(
                            path=f"steps.{step.id}.tool",
                            message=resolve_error or f"Tool '{step.tool}' not found",
                            severity="error",
                        )
                    )

            # Validate depends_on references
            if step.depends_on:
                for dep in step.depends_on:
                    if dep not in step_ids:
                        errors.append(
                            ValidationIssue(
                                path=f"steps.{step.id}.depends_on",
                                message=f"Dependency '{dep}' not found",
                                severity="error",
                            )
                        )

            # Validate template syntax in params
            if step.params:
                template_errors = self._template_engine.validate(step.params)
                for error in template_errors:
                    errors.append(
                        ValidationIssue(
                            path=f"steps.{step.id}.params",
                            message=f"Template error: {error}",
                            severity="error",
                        )
                    )
                # Reference-existence check: flag unknown ``inputs.<name>`` and
                # ``steps.<id>`` refs (S-292 P3 catalog: template_unknown).
                for ref in self._template_engine.extract_references(step.params):
                    head = ref.split(".", 2)
                    if len(head) < 2:
                        continue
                    root, name = head[0], head[1]
                    if root == "inputs" and name not in input_names:
                        errors.append(
                            ValidationIssue(
                                path=f"steps.{step.id}.params",
                                message=(
                                    f"Template error: unknown input '{name}'. "
                                    f"Available inputs: {sorted(input_names)}."
                                ),
                                severity="error",
                            )
                        )
                    elif root == "steps" and name not in step_ids:
                        errors.append(
                            ValidationIssue(
                                path=f"steps.{step.id}.params",
                                message=(
                                    f"Template error: unknown step '{name}'. "
                                    f"Available steps: {sorted(step_ids)}."
                                ),
                                severity="error",
                            )
                        )

            # Validate when expression syntax (must be a valid Jinja2 expression)
            if step.when:
                when_errors = self._template_engine.validate({"_when": "{{ " + step.when + " }}"})
                for error in when_errors:
                    errors.append(
                        ValidationIssue(
                            path=f"steps.{step.id}.when",
                            message=f"Template error in when expression: {error}",
                            severity="error",
                        )
                    )

        # Check for circular dependencies
        try:
            workflow.get_execution_order()
        except ValueError as e:
            errors.append(
                ValidationIssue(
                    path="steps",
                    message=str(e),
                    severity="error",
                )
            )

        # Validate outputs
        for output in workflow.outputs:
            if output.from_path and output.value:
                errors.append(
                    ValidationIssue(
                        path=f"outputs.{output.name}",
                        message="Output must have either 'from_path' or 'value', not both",
                        severity="error",
                    )
                )
            elif not output.from_path and not output.value:
                errors.append(
                    ValidationIssue(
                        path=f"outputs.{output.name}",
                        message="Output must have either 'from_path' or 'value'",
                        severity="error",
                    )
                )

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    # ── Tool resolution ──────────────────────────────────────────

    def _resolve_tool(
        self,
        step: StepDefinition,
        default_runner: str | None,
    ) -> tuple[bool, str | None]:
        """Resolve a tool step to verify the tool exists.

        Resolution chain (per WORKFLOW_TOOL_RESOLUTION_SPEC DEC-157):
        1. Determine runner: default_runner (from workflow defaults)
           or bridge context (handled at execution time — skipped here).
        2. If runner found: check {runner}__{mcp}__{tool} on runner.
        3. If no runner: check tool by (server_name, name) on CP-direct registry.
        4. If not found: return error with available tools hint.

        Returns:
            (True, None) if found, (False, error_message) if not.
        """
        assert step.tool is not None
        assert step.mcp is not None

        # Determine effective runner: explicit default > inference from registry
        runner = default_runner

        # Path 1: Runner inference — if no explicit runner, check if exactly
        # one connected runner hosts this MCP server (spec step 3, DEC-157).
        if runner is None and self._runner_registry:
            matching_runners = []
            for r in self._runner_registry.list():
                if r.status.value != "connected":
                    continue
                for tool_entry in r.available_tools:
                    name = self._runner_registry._get_tool_name(tool_entry)
                    if name.startswith(f"{step.mcp}__"):
                        matching_runners.append(r)
                        break
            if len(matching_runners) == 1:
                runner = matching_runners[0].name
            elif len(matching_runners) > 1:
                names = sorted(r.name for r in matching_runners)
                return False, (
                    f"MCP server '{step.mcp}' is hosted on multiple runners: {names}. "
                    f"Add 'defaults.runner' to the workflow to disambiguate."
                )

        # Path 2: Runner-hosted tool lookup (explicit or inferred runner)
        if runner and self._runner_registry:
            canonical = f"{runner}__{step.mcp}__{step.tool}"
            if self._runner_registry.has_tool(runner, canonical):
                return True, None
            # Build hint
            runner_obj = self._runner_registry.get_by_name(runner)
            if runner_obj:
                avail = [
                    self._runner_registry._get_tool_name(t)
                    for t in runner_obj.available_tools
                    if self._runner_registry._get_tool_name(t).startswith(f"{step.mcp}__")
                ]
                hint = (
                    f" Available tools on '{step.mcp}' server (runner '{runner}'): {avail}"
                    if avail
                    else ""
                )
            else:
                hint = f" Runner '{runner}' not found in registry."
            return False, (
                f"Tool '{step.tool}' not found on MCP server '{step.mcp}' "
                f"(runner '{runner}').{hint}"
            )

        # Path 3: CP-direct tool — match by (server_name, tool name)
        matching = self._tool_registry.list_tools(server_name=step.mcp)
        for tool_def in matching:
            if tool_def.name == step.tool:
                return True, None

        # Build hint for CP-direct
        avail_names = [t.name for t in matching] if matching else []
        if avail_names:
            hint = f" Available tools on '{step.mcp}' server: {avail_names}"
        else:
            # List all known server names
            all_tools = self._tool_registry.list_tools()
            servers = sorted({t.server_name for t in all_tools if t.server_name})
            hint = f" No MCP server named '{step.mcp}' found. Known servers: {servers}"
        return False, (f"Tool '{step.tool}' not found on MCP server '{step.mcp}'.{hint}")


# ── Suggested-fix enrichment (S-291 P3) ─────────────────────────────


def _fuzzy_best(word: str, candidates: list[str]) -> tuple[str | None, list[str]]:
    """Return (best_match, alternatives) using ``difflib.get_close_matches``.

    Spec line 248: cutoff=0.6, n=1 for the best match. ``alternatives``
    captures the next two candidates (cutoff=0.5) so the agent can fall
    back when the top suggestion is wrong.
    """
    if not candidates:
        return None, []
    primary = difflib.get_close_matches(word, candidates, n=1, cutoff=_FUZZY_CUTOFF)
    if not primary:
        return None, []
    # Wider cutoff for alternatives — these are advisory, not the chosen fix.
    extra = difflib.get_close_matches(word, candidates, n=3, cutoff=0.4)
    alternatives = [c for c in extra if c != primary[0]][:2]
    return primary[0], alternatives


# Compiled patterns used to extract the offending value from validator
# messages. Kept module-level so they cost nothing to re-use across calls.
_RE_TOOL_NOT_FOUND = re.compile(r"^Tool '([^']+)' not found on MCP server '([^']+)'")
_RE_NO_MCP_SERVER = re.compile(r"No MCP server named '([^']+)'")
_RE_DEP_NOT_FOUND = re.compile(r"^Dependency '([^']+)' not found$")
_RE_DUPLICATE_STEPS = re.compile(r"^Duplicate step IDs: (.+)$")


def _classify_issue(issue: ValidationIssue) -> str:
    """Map a ValidationIssue to one of the spec catalog error types.

    Returns one of: ``unknown_tool``, ``unknown_mcp``, ``missing_mcp``,
    ``missing_tool_or_code``, ``tool_xor_code``, ``return_in_code``,
    ``forbidden_import``, ``forbidden_builtin``, ``missing_required``,
    ``invalid_type``, ``template_unknown``, ``duplicate_step``,
    ``bad_depends_on``, ``output_missing_value``, ``cycle``,
    ``parse_error``, or ``other``.
    """
    path = issue.path
    msg = issue.message

    if path == "yaml":
        return "parse_error"
    if path == "name":
        return "missing_required"
    if path == "version":
        return "missing_required"
    if path.endswith(".mcp"):
        return "missing_mcp" if "missing the 'mcp' field" in msg else "unknown_mcp"
    if path.endswith(".tool"):
        # Tool resolution surfaces "No MCP server named '...'" via the same
        # path — distinguish by message body.
        if _RE_NO_MCP_SERVER.search(msg):
            return "unknown_mcp"
        return "unknown_tool"
    if "must have either 'tool' or 'code', not both" in msg:
        return "tool_xor_code"
    if "must have either 'tool' or 'code'" in msg:
        return "missing_tool_or_code"
    if path.endswith(".depends_on"):
        return "bad_depends_on"
    if ".params" in path or path.endswith(".when"):
        return "template_unknown"
    if path.startswith("outputs."):
        return "output_missing_value"
    if path == "steps":
        if _RE_DUPLICATE_STEPS.match(msg):
            return "duplicate_step"
        if "Circular" in msg or "cycle" in msg.lower():
            return "cycle"
    if "must assign to 'result'" in msg:
        return "return_in_code"
    if msg.startswith("forbidden import "):
        return "forbidden_import"
    if msg.startswith("forbidden builtin "):
        return "forbidden_builtin"
    return "other"


def _step_id_from_path(path: str) -> str | None:
    """Extract step id from a path like ``steps.{id}.tool``.

    Returns ``None`` if the path doesn't follow the ``steps.<id>`` shape.
    """
    if not path.startswith("steps."):
        return None
    parts = path.split(".", 2)
    if len(parts) < 2:
        return None
    return parts[1] or None


def _output_name_from_path(path: str) -> str | None:
    if not path.startswith("outputs."):
        return None
    parts = path.split(".", 1)
    return parts[1] if len(parts) == 2 else None


def _find_step(workflow: WorkflowDefinition, step_id: str) -> StepDefinition | None:
    return workflow.get_step(step_id)


def _step_first_return_line(code: str) -> tuple[str, str] | None:
    """Find the first ``return X`` statement in ``code``.

    Returns ``(line_text, value_expr)`` tuple where ``line_text`` is the
    full source line (used as the ``old`` of the ``replace`` op) and
    ``value_expr`` is everything after the ``return `` keyword (used to
    construct ``result = ...``).
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Return):
            lines = code.splitlines()
            line_idx = (node.lineno - 1) if node.lineno else 0
            if not (0 <= line_idx < len(lines)):
                return None
            line_text = lines[line_idx]
            stripped = line_text.lstrip()
            if not stripped.startswith("return"):
                # ``return`` on a different line than the AST source — bail.
                return None
            # Slice off the "return" keyword (and an optional value).
            after = stripped[len("return") :]
            value_expr = after.lstrip()
            return line_text, value_expr
    return None


def _step_first_import_line(
    code: str, allowed: frozenset[str] | set[str]
) -> tuple[str, str] | None:
    """Find the first forbidden ``import`` line.

    Returns ``(line_text, module_name)`` or None.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        module: str | None = None
        line_no = 0
        if isinstance(node, ast.Import):
            module = node.names[0].name.split(".", 1)[0] if node.names else None
            line_no = node.lineno
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").split(".", 1)[0]
            line_no = node.lineno
        if not module or module in allowed:
            continue
        lines = code.splitlines()
        line_idx = (line_no - 1) if line_no else 0
        if 0 <= line_idx < len(lines):
            return lines[line_idx], module
    return None


def _step_first_builtin_call(
    code: str, dangerous: frozenset[str] | set[str]
) -> tuple[str, str] | None:
    """Find the first dangerous-builtin call line.

    Returns ``(line_text, builtin_name)`` or None.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in dangerous:
                lines = code.splitlines()
                line_idx = (node.lineno - 1) if node.lineno else 0
                if 0 <= line_idx < len(lines):
                    return lines[line_idx], node.func.id
    return None


def _extract_template_var(message: str) -> str | None:
    """Extract the variable name from a Jinja2 template-error message.

    Best effort — Jinja2 errors are heterogeneous. We pick out the first
    ``'identifier'`` token we see; if none is found, we return None and
    the enricher falls back to ``requires_agent_decision``.
    """
    m = re.search(r"'([A-Za-z_][A-Za-z0-9_]*)'", message)
    return m.group(1) if m else None


def enrich_validation_result(
    result: ValidationResult,
    workflow: WorkflowDefinition | None,
    *,
    available_tools_by_mcp: dict[str, list[str]] | None = None,
    available_mcps: list[str] | None = None,
    safe_imports: frozenset[str] | set[str] | None = None,
    dangerous_builtins: frozenset[str] | set[str] | None = None,
) -> list[dict[str, Any]]:
    """Convert ``ValidationResult.errors`` into the spec-shaped error dicts.

    Each returned dict is a superset of ``{path, message}``. Optional keys
    per spec catalog (lines 233–246):

    - ``current_value``: the offending value pulled from the workflow.
    - ``line_in_step``: 1-based line inside the step's ``code`` block (when
      relevant).
    - ``suggested_fix``: a dict shaped like a future ``workflow_patch`` op
      (``{op: "set", path: "...", value: ...}`` or
      ``{op: "replace", step_id: "...", old: "...", new: "..."}``), or
      ``None`` when no deterministic fix is available.
    - ``requires_agent_decision``: ``true`` when the agent must choose
      (duplicate IDs, reserved names, no fuzzy candidate above cutoff).
    - ``alternatives``, ``available_steps``, ``available_inputs``,
      ``reserved_words``: extra context for agent decisions.

    The function is pure — no side effects, no calls into the runtime
    registries beyond what was passed in.
    """
    enriched: list[dict[str, Any]] = []
    available_tools_by_mcp = available_tools_by_mcp or {}
    available_mcps = available_mcps or list(available_tools_by_mcp.keys())
    safe_imports = safe_imports or frozenset()
    dangerous_builtins = dangerous_builtins or frozenset()

    step_ids = [s.id for s in workflow.steps] if workflow else []
    input_names = [i.name for i in workflow.inputs] if workflow else []

    for issue in result.errors:
        kind = _classify_issue(issue)
        base: dict[str, Any] = {
            "path": issue.path,
            "message": issue.message,
            "suggested_fix": None,
            "requires_agent_decision": False,
        }
        if issue.line is not None:
            base["line_in_step"] = issue.line

        if workflow is not None:
            _enrich_one(
                base,
                issue,
                kind,
                workflow,
                step_ids,
                input_names,
                available_tools_by_mcp,
                available_mcps,
                safe_imports,
                dangerous_builtins,
            )

        # Default: when no fix could be derived, mark for agent decision.
        if base["suggested_fix"] is None and not base.get("requires_agent_decision"):
            base["requires_agent_decision"] = True
        enriched.append(base)
    return enriched


def _enrich_one(
    out: dict[str, Any],
    issue: ValidationIssue,
    kind: str,
    workflow: WorkflowDefinition,
    step_ids: list[str],
    input_names: list[str],
    available_tools_by_mcp: dict[str, list[str]],
    available_mcps: list[str],
    safe_imports: frozenset[str] | set[str],
    dangerous_builtins: frozenset[str] | set[str],
) -> None:
    """Mutates ``out`` in place with the per-kind enrichment fields."""
    step_id = _step_id_from_path(issue.path)
    step = _find_step(workflow, step_id) if step_id else None

    if kind == "unknown_tool" and step and step.mcp:
        out["current_value"] = step.tool
        candidates = available_tools_by_mcp.get(step.mcp, [])
        best, alternatives = _fuzzy_best(step.tool or "", candidates)
        if best:
            out["suggested_fix"] = {
                "op": "set",
                "path": f"steps.{step_id}.tool",
                "value": best,
            }
            if alternatives:
                out["alternatives"] = alternatives
        else:
            out["alternatives"] = sorted(candidates)[:5]

    elif kind == "unknown_mcp" and step:
        # Validator emits this kind under ``.tool`` path when the MCP
        # server itself is unknown (the resolver hint reads "No MCP
        # server named ..."). Rewrite the path to ``.mcp`` in the
        # enriched output so agents see the correct field — the original
        # ``message`` is preserved verbatim.
        if issue.path.endswith(".tool"):
            out["path"] = issue.path[: -len(".tool")] + ".mcp"
        out["current_value"] = step.mcp
        best, alternatives = _fuzzy_best(step.mcp or "", available_mcps)
        if best:
            out["suggested_fix"] = {
                "op": "set",
                "path": f"steps.{step_id}.mcp",
                "value": best,
            }
            if alternatives:
                out["alternatives"] = alternatives
        else:
            out["alternatives"] = sorted(available_mcps)[:5]

    elif kind == "missing_mcp" and step:
        # No deterministic value — agent must supply.
        out["requires_agent_decision"] = True
        out["available_mcps"] = sorted(available_mcps)
        out["suggested_fix"] = {
            "op": "set",
            "path": f"steps.{step_id}.mcp",
            "value": None,
        }

    elif kind == "return_in_code" and step and step.code:
        found = _step_first_return_line(step.code)
        if found:
            line_text, value_expr = found
            indent = line_text[: len(line_text) - len(line_text.lstrip())]
            new_line = f"{indent}result = {value_expr}" if value_expr else f"{indent}result = None"
            out["current_value"] = line_text.strip()
            out["suggested_fix"] = {
                "op": "replace",
                "step_id": step_id,
                "old": line_text,
                "new": new_line,
            }

    elif kind == "forbidden_import" and step and step.code:
        found = _step_first_import_line(step.code, safe_imports)
        if found:
            line_text, module = found
            out["current_value"] = line_text.strip()
            out["suggested_fix"] = {
                "op": "replace",
                "step_id": step_id,
                "old": line_text + "\n",
                "new": "",
            }
            out["alternatives"] = sorted(safe_imports)

    elif kind == "forbidden_builtin" and step and step.code:
        found = _step_first_builtin_call(step.code, dangerous_builtins)
        if found:
            line_text, name = found
            out["current_value"] = name
            # Well-known replacements; otherwise agent decides.
            replacements = {"type": "isinstance"}
            if name in replacements:
                out["suggested_fix"] = {
                    "op": "replace",
                    "step_id": step_id,
                    "old": line_text,
                    "new": line_text.replace(f"{name}(", f"{replacements[name]}(", 1),
                }
            else:
                out["requires_agent_decision"] = True

    elif kind == "bad_depends_on" and step:
        m = _RE_DEP_NOT_FOUND.match(issue.message)
        if m:
            out["current_value"] = m.group(1)
        # Spec: suggested_fix removes the bad reference; lists available_steps.
        valid_deps = [d for d in (step.depends_on or []) if d in step_ids]
        out["suggested_fix"] = {
            "op": "set",
            "path": f"steps.{step_id}.depends_on",
            "value": valid_deps,
        }
        out["available_steps"] = step_ids

    elif kind == "template_unknown" and step:
        var = _extract_template_var(issue.message)
        if var:
            out["current_value"] = var
        # Suggest correction by fuzzy-matching the offending name against
        # both step IDs (for ``steps.<id>.output`` references) and input
        # names (for ``inputs.<name>`` references). The agent gets both
        # lists so they can pick the right side.
        if var:
            best, _alts = _fuzzy_best(var, step_ids + input_names)
            if best:
                out["suggested_fix"] = {
                    "op": "set",
                    "path": issue.path,
                    "value": None,  # actual replacement requires agent edit of expression
                }
                out["alternatives"] = [best]
        out["available_steps"] = step_ids
        out["available_inputs"] = input_names
        out["requires_agent_decision"] = True

    elif kind == "duplicate_step":
        m = _RE_DUPLICATE_STEPS.match(issue.message)
        if m:
            out["current_value"] = m.group(1)
        out["requires_agent_decision"] = True
        out["available_steps"] = step_ids

    elif kind == "missing_required":
        # Top-level required field missing (name/version) — agent decision.
        out["requires_agent_decision"] = True
        out["suggested_fix"] = {
            "op": "set",
            "path": issue.path,
            "value": None,
        }

    elif kind == "missing_tool_or_code":
        out["requires_agent_decision"] = True

    elif kind == "tool_xor_code":
        out["requires_agent_decision"] = True

    elif kind == "output_missing_value":
        output_name = _output_name_from_path(issue.path)
        out["requires_agent_decision"] = True
        if output_name:
            out["suggested_fix"] = {
                "op": "set",
                "path": f"outputs.{output_name}.from_path",
                "value": None,
            }

    elif kind == "cycle":
        out["requires_agent_decision"] = True
        out["available_steps"] = step_ids

    # All other kinds fall through with suggested_fix=None,
    # requires_agent_decision left at False; the caller sets it to True
    # in enrich_validation_result.


def detect_reserved_input_names(workflow: WorkflowDefinition) -> list[ValidationIssue]:
    """Surface input names that conflict with reserved Python/template words.

    Best-effort static check that complements ``WorkflowValidator.validate``.
    Emits one warning-severity issue per offending input. ``enrich`` will
    set ``requires_agent_decision=True`` and surface ``reserved_words``.
    """
    issues: list[ValidationIssue] = []
    for inp in workflow.inputs:
        if inp.name in _RESERVED_INPUT_NAMES:
            issues.append(
                ValidationIssue(
                    path=f"inputs.{inp.name}",
                    message=(
                        f"Input name '{inp.name}' conflicts with a reserved "
                        "Python keyword or workflow context name. Rename it "
                        "to something unambiguous."
                    ),
                    severity="error",
                )
            )
    return issues

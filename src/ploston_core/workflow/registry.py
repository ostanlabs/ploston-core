"""Workflow Registry implementation."""

import asyncio
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ploston_core.errors import create_error
from ploston_core.types import LogLevel, ValidationResult

from .parser import parse_workflow_yaml
from .types import WorkflowDefinition, WorkflowEntry
from .validator import WorkflowValidator

# Reserved bare names that would collide with workflow management tools.
# A workflow named any of these would be permanently shadowed by the
# management tool at routing step 3 (e.g. workflow_run).
WORKFLOW_RESERVED_NAMES = frozenset(
    {
        "schema",
        "list",
        "get",
        "create",
        "update",
        "delete",
        "validate",
        "tool_schema",
        "run",  # DEC-171
    }
)

if TYPE_CHECKING:
    from ploston_core.config import WorkflowsConfig
    from ploston_core.config.redis_store import RedisConfigStore
    from ploston_core.logging import AELLogger
    from ploston_core.registry import ToolRegistry
    from ploston_core.runner_management.registry import RunnerRegistry
    from ploston_core.telemetry.metrics import AELMetrics


class WorkflowRegistry:
    """Registry of workflow definitions.

    Loads workflows from directory, validates them,
    and provides hot-reload on file changes.
    """

    def __init__(
        self,
        tool_registry: "ToolRegistry",
        config: "WorkflowsConfig",
        logger: "AELLogger | None" = None,
        redis_store: "RedisConfigStore | None" = None,
        runner_registry: "RunnerRegistry | None" = None,
        on_tools_changed: Callable[[], Awaitable[None]] | None = None,
    ):
        """Initialize workflow registry.

        Args:
            tool_registry: Tool registry for validation
            config: Workflows configuration
            logger: Optional logger
            redis_store: Optional Redis config store for Premium persistence
            runner_registry: Optional runner registry for runner-hosted tool validation
            on_tools_changed: Optional async callback fired when the workflow
                set changes (register/unregister/initialize). Workflows surface
                as MCP tools, so mutations must trigger
                notifications/tools/list_changed via this hook.
        """
        self._workflows: dict[str, WorkflowEntry] = {}
        self._tool_registry = tool_registry
        self._config = config
        self._logger = logger
        self._redis_store = redis_store
        self._runner_registry = runner_registry
        self._validator = WorkflowValidator(tool_registry, runner_registry=runner_registry)
        self._watching = False
        self._watch_task: asyncio.Task[None] | None = None
        self._metrics: AELMetrics | None = None
        self._on_tools_changed = on_tools_changed
        # S-291 P3: in-memory draft store for failed-validation workflows.
        # TTL is sourced from ``WorkflowsConfig.draft_ttl_seconds`` (default
        # 1800s). The attribute lookup uses ``getattr`` so callers passing
        # an older mock config still work; non-int values (e.g. a MagicMock
        # attribute auto-generated in tests) fall back to the default.
        ttl = getattr(config, "draft_ttl_seconds", 1800)
        if not isinstance(ttl, int) or ttl <= 0:
            ttl = 1800
        self._draft_store = DraftStore(ttl_seconds=ttl)

    @property
    def draft_store(self) -> "DraftStore":
        """Expose the draft store (used by ``WorkflowToolsProvider``)."""
        return self._draft_store

    def _fire_tools_changed(self) -> None:
        """Schedule the on_tools_changed callback on the running event loop."""
        if self._on_tools_changed:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._on_tools_changed())
            except RuntimeError:
                pass  # No running event loop — skip notification

    def set_metrics(self, metrics: "AELMetrics") -> None:
        """Set the metrics instance for telemetry.

        Args:
            metrics: AELMetrics instance
        """
        self._metrics = metrics

    def _update_metrics(self) -> None:
        """Update telemetry metrics based on current workflow count."""
        if self._metrics:
            self._metrics.update_registered_workflows(len(self._workflows))

    async def _persist(self, name: str, yaml_content: str) -> None:
        """Persist API-registered workflow YAML.

        Always writes to disk so workflows survive container teardown.
        When Redis is available, also writes there for runtime consistency.
        """
        # Always write to disk — this is the durable copy that survives
        # container restarts and Redis data wipes during bootstrap teardown.
        workflows_dir = Path(self._config.directory)
        workflows_dir.mkdir(parents=True, exist_ok=True)
        target = workflows_dir / f"{name}.yaml"
        target.write_text(yaml_content, encoding="utf-8")

        # Also write to Redis when available for runtime consistency.
        if self._redis_store and self._redis_store.connected:
            await self._redis_store.set_value(f"workflows:{name}", yaml_content)

    async def _delete_persisted(self, name: str, source: str) -> None:
        """Remove persisted storage for an API-registered workflow.

        File-loaded workflows (source=file) are never touched.
        Removes from both disk and Redis to stay consistent with dual-write.
        """
        if source != "api":
            return
        # Remove from disk
        target = Path(self._config.directory) / f"{name}.yaml"
        if target.exists():
            target.unlink()
        # Remove from Redis
        if self._redis_store and self._redis_store.connected:
            await self._redis_store.delete_value(f"workflows:{name}")

    async def initialize(self) -> int:
        """Initialize registry by loading workflows from directory.

        Returns:
            Number of workflows loaded
        """
        if self._logger:
            self._logger._log(
                LogLevel.INFO,
                "workflow",
                "Initializing workflow registry",
                {"directory": str(self._config.directory)},
            )

        # Step 1: load from disk (existing logic)
        # Skip tool validation — tools aren't available yet at startup
        # (runners/bridges haven't connected). These workflows were already
        # validated when originally registered via the API.
        count = 0
        workflows_dir = Path(self._config.directory)
        if workflows_dir.exists():
            for yaml_file in workflows_dir.glob("*.yaml"):
                try:
                    yaml_content = yaml_file.read_text()
                    self.register_from_yaml(
                        yaml_content,
                        source_path=yaml_file,
                        validate=False,
                    )
                    count += 1
                except Exception as e:
                    if self._logger:
                        self._logger._log(
                            LogLevel.ERROR,
                            "workflow",
                            "Failed to load workflow",
                            {"file": str(yaml_file), "error": str(e)},
                        )
        else:
            if self._logger:
                self._logger._log(
                    LogLevel.WARN,
                    "workflow",
                    "Workflows directory does not exist",
                    {"directory": str(workflows_dir)},
                )

        # Step 2: load from Redis — Premium only. Redis wins on name collision.
        # Also skip tool validation for the same reason as disk loading.
        if self._redis_store and self._redis_store.connected:
            redis_keys = await self._redis_store.scan_keys("workflows:*")
            for key in redis_keys:
                yaml_content = await self._redis_store.get_value(key)
                if yaml_content:
                    try:
                        self.register_from_yaml(
                            yaml_content,
                            source_path=None,
                            validate=False,
                        )
                        count += 1
                    except Exception as e:
                        if self._logger:
                            self._logger._log(
                                LogLevel.ERROR,
                                "workflow",
                                "Failed to load workflow from Redis",
                                {"key": key, "error": str(e)},
                            )

        if self._logger:
            self._logger._log(
                LogLevel.INFO,
                "workflow",
                "Workflow registry initialized",
                {"count": count},
            )

        self._update_metrics()
        if count > 0:
            self._fire_tools_changed()
        return count

    def register(
        self,
        workflow: WorkflowDefinition,
        validate: bool = True,
    ) -> ValidationResult:
        """Register a workflow.

        Args:
            workflow: Workflow to register
            validate: Whether to validate before registering

        Returns:
            ValidationResult (always valid if validate=False)

        Raises:
            AELError(INPUT_INVALID) if validation fails or name is reserved/collides
        """
        # DEC-169: Check reserved names (would be shadowed by management tools)
        if workflow.name in WORKFLOW_RESERVED_NAMES:
            raise create_error(
                "INPUT_INVALID",
                detail=(
                    f"Workflow name '{workflow.name}' is reserved. "
                    f"Reserved names: {', '.join(sorted(WORKFLOW_RESERVED_NAMES))}"
                ),
            )

        # DEC-169: Check collision with CP tools in ToolRegistry
        if self._tool_registry and self._tool_registry.get(workflow.name) is not None:
            raise create_error(
                "INPUT_INVALID",
                detail=(
                    f"Workflow name '{workflow.name}' collides with an existing "
                    f"CP tool of the same name. Choose a different name."
                ),
            )

        if validate:
            result = self._validator.validate(workflow)
            if not result.valid:
                error_msgs = [f"{e.path}: {e.message}" for e in result.errors]
                raise create_error(
                    "INPUT_INVALID",
                    detail="; ".join(error_msgs),
                )
        else:
            result = ValidationResult(valid=True, errors=[], warnings=[])

        entry = WorkflowEntry(
            workflow=workflow,
            registered_at=datetime.now(UTC).isoformat(),
            source="api",
        )
        self._workflows[workflow.name] = entry

        if self._logger:
            self._logger._log(
                LogLevel.INFO,
                "workflow",
                "Workflow registered",
                {"name": workflow.name},
            )

        self._update_metrics()
        self._fire_tools_changed()
        return result

    def register_from_yaml(
        self,
        yaml_content: str,
        source_path: Path | None = None,
        persist: bool = False,
        validate: bool = True,
    ) -> ValidationResult:
        """Parse and register workflow from YAML.

        Args:
            yaml_content: YAML content
            source_path: Optional source file path
            persist: If True, persist the workflow (disk or Redis). Set True
                     only by REST/MCP API callers.
            validate: If True (default), validate tool references against the
                      tool registry. Set False when loading persisted workflows
                      at startup before runners have connected.

        Returns:
            ValidationResult

        Raises:
            AELError(INPUT_INVALID) if parsing or validation fails
        """
        workflow = parse_workflow_yaml(yaml_content, source_path)
        result = self.register(workflow, validate=validate)

        # Update source in entry
        if workflow.name in self._workflows:
            self._workflows[workflow.name].source = "file" if source_path else "api"

        if persist and not source_path:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._persist(workflow.name, yaml_content))
            except RuntimeError:
                asyncio.run(self._persist(workflow.name, yaml_content))

        return result

    def register_validated(
        self,
        workflow: WorkflowDefinition,
        yaml_content: str,
        persist: bool = True,
    ) -> WorkflowEntry:
        """Register a pre-validated workflow definition.

        S-291 P3: Splits the validation/registration coupling that previously
        lived inside ``register_from_yaml``. The caller is expected to have
        already produced a successful ``ValidationResult`` via
        ``validate_yaml`` (or equivalent) — this method skips
        ``WorkflowValidator.validate`` entirely and only enforces the
        registry-level invariants (reserved names, CP tool collisions,
        in-memory write, persistence).

        Args:
            workflow: Pre-validated workflow definition.
            yaml_content: Original YAML text to persist.
            persist: If True (default), persist the workflow asynchronously.

        Returns:
            The newly created ``WorkflowEntry``.

        Raises:
            AELError(INPUT_INVALID) if the name is reserved or collides with
                an existing CP tool. Validation errors are NOT raised here —
                the caller is responsible for those before invoking this.
        """
        if workflow.name in WORKFLOW_RESERVED_NAMES:
            raise create_error(
                "INPUT_INVALID",
                detail=(
                    f"Workflow name '{workflow.name}' is reserved. "
                    f"Reserved names: {', '.join(sorted(WORKFLOW_RESERVED_NAMES))}"
                ),
            )

        if self._tool_registry and self._tool_registry.get(workflow.name) is not None:
            raise create_error(
                "INPUT_INVALID",
                detail=(
                    f"Workflow name '{workflow.name}' collides with an existing "
                    f"CP tool of the same name. Choose a different name."
                ),
            )

        # The yaml_content lives on WorkflowDefinition itself — the parser
        # populates it for round-trip patch/get flows. Caller may have
        # produced ``workflow`` from ``parse_workflow_yaml(yaml_content)``
        # already, but rewriting it here keeps the contract explicit.
        workflow.yaml_content = yaml_content

        entry = WorkflowEntry(
            workflow=workflow,
            registered_at=datetime.now(UTC).isoformat(),
            source="api",
        )
        self._workflows[workflow.name] = entry

        if self._logger:
            self._logger._log(
                LogLevel.INFO,
                "workflow",
                "Workflow registered (validated)",
                {"name": workflow.name},
            )

        if persist:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._persist(workflow.name, yaml_content))
            except RuntimeError:
                asyncio.run(self._persist(workflow.name, yaml_content))

        self._update_metrics()
        self._fire_tools_changed()
        return entry

    def unregister(self, name: str) -> bool:
        """Unregister a workflow.

        Args:
            name: Workflow name

        Returns:
            True if workflow was registered, False if not found
        """
        if name in self._workflows:
            entry = self._workflows.pop(name)
            if self._logger:
                self._logger._log(
                    LogLevel.INFO,
                    "workflow",
                    "Workflow unregistered",
                    {"name": name},
                )
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._delete_persisted(name, entry.source))
            except RuntimeError:
                asyncio.run(self._delete_persisted(name, entry.source))
            self._update_metrics()
            self._fire_tools_changed()
            return True
        return False

    def get(self, name: str) -> WorkflowDefinition | None:
        """Get workflow by name.

        Args:
            name: Workflow name

        Returns:
            Workflow definition or None if not found
        """
        entry = self._workflows.get(name)
        return entry.workflow if entry else None

    def get_or_raise(self, name: str) -> WorkflowDefinition:
        """Get workflow by name, raise if not found.

        Args:
            name: Workflow name

        Returns:
            Workflow definition

        Raises:
            AELError(WORKFLOW_NOT_FOUND)
        """
        workflow = self.get(name)
        if not workflow:
            raise create_error("WORKFLOW_NOT_FOUND", workflow_name=name)
        return workflow

    def list_workflows(self) -> list[WorkflowDefinition]:
        """List all registered workflows.

        Returns:
            List of workflow definitions
        """
        return [entry.workflow for entry in self._workflows.values()]

    def validate_yaml(self, yaml_content: str) -> ValidationResult:
        """Validate YAML without registering.

        Useful for CLI `ael validate` command.

        Args:
            yaml_content: YAML content to validate

        Returns:
            ValidationResult
        """
        try:
            workflow = parse_workflow_yaml(yaml_content)
            return self._validator.validate(workflow)
        except Exception as e:
            from ploston_core.types import ValidationIssue

            return ValidationResult(
                valid=False,
                errors=[
                    ValidationIssue(
                        path="yaml",
                        message=str(e),
                        severity="error",
                    )
                ],
                warnings=[],
            )

    def get_for_mcp_exposure(self) -> list[dict[str, Any]]:
        """Get workflows formatted as MCP tools.

        Returns list of tool definitions using bare names (DEC-169).
        Includes _ploston_tags for pre-serialization filtering.

        Returns:
            List of MCP tool definitions
        """
        tools: list[dict[str, Any]] = []

        for workflow in self.list_workflows():
            # Build input schema
            properties: dict[str, Any] = {}
            required: list[str] = []

            for inp in workflow.inputs:
                prop: dict[str, Any] = {"type": inp.type}
                if inp.description:
                    prop["description"] = inp.description
                if inp.enum:
                    prop["enum"] = inp.enum
                if inp.pattern:
                    prop["pattern"] = inp.pattern
                if inp.minimum is not None:
                    prop["minimum"] = inp.minimum
                if inp.maximum is not None:
                    prop["maximum"] = inp.maximum

                properties[inp.name] = prop
                if inp.required:
                    required.append(inp.name)

            input_schema: dict[str, Any] = {
                "type": "object",
                "properties": properties,
            }
            if required:
                input_schema["required"] = required

            # Create tool definition — bare name (DEC-169)
            tool = {
                "name": workflow.name,
                "description": workflow.description or f"Execute {workflow.name} workflow",
                "inputSchema": input_schema,
                "_ploston_tags": {"kind:workflow"},
            }
            tools.append(tool)

        return tools

    def snapshot(self, name: str) -> dict[str, Any]:
        """Get workflow snapshot for execution.

        Returns frozen copy of workflow definition.

        Args:
            name: Workflow name

        Returns:
            Workflow snapshot as dict

        Raises:
            AELError(WORKFLOW_NOT_FOUND)
        """
        workflow = self.get_or_raise(name)

        # Return a dict representation (frozen copy)
        return {
            "name": workflow.name,
            "version": workflow.version,
            "description": workflow.description,
            "packages": {
                "profile": workflow.packages.profile if workflow.packages else "standard",
                "additional": workflow.packages.additional if workflow.packages else [],
            },
            "defaults": {
                "timeout": workflow.defaults.timeout if workflow.defaults else 30,
                "on_error": workflow.defaults.on_error.value if workflow.defaults else "fail",
                "retry": (
                    {
                        "max_attempts": workflow.defaults.retry.max_attempts,
                        "backoff": workflow.defaults.retry.backoff.value,
                        "delay_seconds": workflow.defaults.retry.delay_seconds,
                    }
                    if workflow.defaults and workflow.defaults.retry
                    else None
                ),
            },
            "inputs": [
                {
                    "name": inp.name,
                    "type": inp.type,
                    "required": inp.required,
                    "default": inp.default,
                    "description": inp.description,
                }
                for inp in workflow.inputs
            ],
            "steps": [
                {
                    "id": step.id,
                    "tool": step.tool,
                    "code": step.code,
                    "params": step.params,
                    "depends_on": step.depends_on,
                    "on_error": step.on_error.value if step.on_error else None,
                    "timeout": step.timeout,
                    "retry": (
                        {
                            "max_attempts": step.retry.max_attempts,
                            "backoff": step.retry.backoff.value,
                            "delay_seconds": step.retry.delay_seconds,
                        }
                        if step.retry
                        else None
                    ),
                }
                for step in workflow.steps
            ],
            "outputs": [
                {
                    "name": out.name,
                    "from_path": out.from_path,
                    "value": out.value,
                    "description": out.description,
                }
                for out in workflow.outputs
            ],
        }

    def start_watching(self) -> None:
        """Start file watcher for hot-reload."""
        if self._watching:
            return

        self._watching = True
        if self._logger:
            self._logger._log(
                LogLevel.INFO,
                "workflow",
                "Starting workflow file watcher",
                {},
            )

        # Note: Actual file watching implementation would go here
        # For now, this is a placeholder

    def stop_watching(self) -> None:
        """Stop file watcher."""
        if not self._watching:
            return

        self._watching = False
        if self._watch_task:
            self._watch_task.cancel()
            self._watch_task = None

        if self._logger:
            self._logger._log(
                LogLevel.INFO,
                "workflow",
                "Stopped workflow file watcher",
                {},
            )

    async def _on_file_change(self, path: Path) -> None:
        """Handle workflow file change.

        Args:
            path: Path to changed file
        """
        if self._logger:
            self._logger._log(
                LogLevel.INFO,
                "workflow",
                "Workflow file changed",
                {"file": str(path)},
            )

        try:
            yaml_content = path.read_text()
            self.register_from_yaml(yaml_content, source_path=path)
        except Exception as e:
            if self._logger:
                self._logger._log(
                    LogLevel.ERROR,
                    "workflow",
                    "Failed to reload workflow",
                    {"file": str(path), "error": str(e)},
                )


@dataclass
class DraftEntry:
    """A failed-validation workflow held for follow-up patching.

    S-291 P3: When ``workflow_create`` validation fails (and ``dry_run`` is
    not set), the YAML and structured validation result are stashed under a
    ``draft_id`` so a subsequent ``workflow_patch`` (or re-create) can
    address the errors without retransmitting the full YAML.
    """

    draft_id: str
    yaml_content: str
    name: str
    version: str
    validation: ValidationResult
    created_at: float = field(default_factory=time.monotonic)


class DraftStore:
    """In-memory TTL store for failed-validation workflow drafts.

    Drafts older than ``ttl_seconds`` are evicted lazily on every access.
    The store is intentionally simple: process-local, no persistence, no
    background sweeper. Capacity is bounded by ``max_size`` — once the cap
    is hit the oldest entry is dropped before the new one is inserted.
    """

    def __init__(self, ttl_seconds: int = 1800, max_size: int = 256) -> None:
        if ttl_seconds <= 0:
            raise ValueError(f"DraftStore ttl_seconds must be positive, got {ttl_seconds}")
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._drafts: dict[str, DraftEntry] = {}

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    def _now(self) -> float:
        # Indirection to make tests able to monkeypatch the clock.
        return time.monotonic()

    def _evict_expired(self) -> None:
        now = self._now()
        stale = [
            draft_id
            for draft_id, entry in self._drafts.items()
            if now - entry.created_at > self._ttl
        ]
        for draft_id in stale:
            self._drafts.pop(draft_id, None)

    def put(
        self,
        yaml_content: str,
        name: str,
        version: str,
        validation: ValidationResult,
    ) -> str:
        """Store a draft and return its ``draft_id``.

        ``draft_id`` is a short URL-safe random token prefixed with
        ``draft-`` for visual recognition by agents reading tool output.
        """
        self._evict_expired()
        if len(self._drafts) >= self._max_size:
            # Drop the oldest entry to make room.
            oldest = min(self._drafts.items(), key=lambda kv: kv[1].created_at)
            self._drafts.pop(oldest[0], None)

        draft_id = f"draft-{secrets.token_urlsafe(8)}"
        self._drafts[draft_id] = DraftEntry(
            draft_id=draft_id,
            yaml_content=yaml_content,
            name=name,
            version=version,
            validation=validation,
        )
        return draft_id

    def get(self, draft_id: str) -> DraftEntry | None:
        self._evict_expired()
        return self._drafts.get(draft_id)

    def pop(self, draft_id: str) -> DraftEntry | None:
        self._evict_expired()
        return self._drafts.pop(draft_id, None)

    def replace_yaml(self, draft_id: str, yaml_content: str) -> DraftEntry | None:
        """Replace the YAML body of an existing draft in place.

        Used by ``workflow_patch`` when the patch target is a draft —
        successful patches mutate the draft's YAML so subsequent calls see
        the latest state. ``created_at`` is left untouched (TTL still
        anchored to original draft creation).
        """
        entry = self.get(draft_id)
        if entry is None:
            return None
        entry.yaml_content = yaml_content
        return entry

    def __len__(self) -> int:
        self._evict_expired()
        return len(self._drafts)

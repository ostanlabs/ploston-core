"""Workflow engine for executing workflows."""

import asyncio
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ploston_core.errors import create_error
from ploston_core.errors.errors import AELError
from ploston_core.logging import AELLogger
from ploston_core.sandbox import SandboxContext
from ploston_core.telemetry import (
    TokenEstimator,
    instrument_step,
    instrument_workflow,
    record_tool_result,
)
from ploston_core.template import TemplateEngine
from ploston_core.types import (
    ExecutionStatus,
    LogLevel,
    OnError,
    OnMissingTool,
    StepStatus,
    StepType,
)
from ploston_core.workflow import WorkflowDefinition

from .types import (
    ExecutionContext,
    ExecutionResult,
    StepExecutionConfig,
    StepResult,
    calculate_retry_delay,
    generate_execution_id,
    with_timeout,
)

if TYPE_CHECKING:
    from ploston_core.plugins import PluginRegistry


class _WorkflowSourceLogger:
    """Thin wrapper that injects workflow context into every log record.

    Automatically adds ``source``, ``execution_id``, and ``step_id`` to the
    OTEL log attributes so that Loki can promote them to stream labels
    (``ael_source``, ``ael_execution_id``, ``ael_step_id``).

    The engine sets ``execution_id`` once at workflow start and updates
    ``step_id`` before each step.  The wrapper merges these into every
    ``_log()`` call without the caller needing to remember.
    """

    def __init__(self, inner: "AELLogger") -> None:
        self._inner = inner
        self._execution_id: str | None = None
        self._step_id: str | None = None

    def set_execution_id(self, execution_id: str) -> None:
        self._execution_id = execution_id

    def set_step_id(self, step_id: str | None) -> None:
        self._step_id = step_id

    def _log(
        self,
        level: "LogLevel",
        component: str,
        message: str,
        context: dict | None = None,
    ) -> None:
        ctx = dict(context) if context else {}
        ctx.setdefault("source", "workflow")
        if self._execution_id:
            ctx.setdefault("execution_id", self._execution_id)
        if self._step_id:
            ctx.setdefault("step_id", self._step_id)
        self._inner._log(level, component, message, ctx)

    # Forward attribute access so callers that read other logger properties
    # (e.g. ``self._logger.config``) still work.
    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


class WorkflowEngine:
    """
    Execute workflow definitions.

    Core execution loop:
    1. Validate inputs
    2. For each step in order:
       a. Render parameters
       b. Execute (tool or code)
       c. Handle errors (fail/skip/retry)
       d. Store output
    3. Compute outputs
    4. Return result
    """

    def __init__(
        self,
        workflow_registry: Any,  # WorkflowRegistry
        tool_invoker: Any,  # ToolInvoker
        template_engine: TemplateEngine,
        config: Any,  # ExecutionConfig
        logger: AELLogger | None = None,
        error_factory: Any = None,  # ErrorFactory
        plugin_registry: "PluginRegistry | None" = None,
        token_estimator: TokenEstimator | None = None,
        runner_registry: Any = None,  # RunnerRegistry (optional)
        tool_registry: Any = None,  # ToolRegistry (optional, for call_mcp CP-first)
        max_tool_calls: int = 10,  # from config.python_exec.max_tool_calls
    ):
        """Initialize workflow engine.

        Args:
            workflow_registry: Registry for fetching workflows
            tool_invoker: Invoker for executing tools
            template_engine: Engine for rendering templates
            config: Execution configuration
            logger: Optional logger
            error_factory: Optional error factory
            plugin_registry: Optional plugin registry for hook execution
            token_estimator: Optional token estimator for savings metrics
            runner_registry: Optional runner registry for tool name resolution
            tool_registry: Optional tool registry for call_mcp CP-direct resolution
            max_tool_calls: Max tool calls per code step (sandbox rate limit)
        """
        self._workflow_registry = workflow_registry
        self._tool_invoker = tool_invoker
        self._template_engine = template_engine
        self._config = config
        self._logger = _WorkflowSourceLogger(logger) if logger else None
        self._error_factory = error_factory
        self._plugin_registry = plugin_registry
        self._token_estimator = token_estimator
        self._runner_registry = runner_registry
        self._tool_registry = tool_registry
        self._max_tool_calls = max_tool_calls

    async def execute(
        self,
        workflow_id: str,
        inputs: dict[str, Any],
        timeout_seconds: int | None = None,
    ) -> ExecutionResult:
        """
        Execute a workflow.

        Args:
            workflow_id: Name of workflow to execute
            inputs: Workflow inputs
            timeout_seconds: Optional timeout override (seconds)

        Returns:
            ExecutionResult with outputs or error

        Raises:
            AELError(WORKFLOW_NOT_FOUND) if workflow doesn't exist
            AELError(INPUT_INVALID) if inputs invalid
            AELError(WORKFLOW_TIMEOUT) if execution times out
        """
        # Fetch workflow from registry
        workflow = self._workflow_registry.get_or_raise(workflow_id)

        # Execute with timeout if specified
        if timeout_seconds:
            return await with_timeout(
                self.execute_workflow(workflow, inputs),
                timeout_seconds,
            )
        else:
            return await self.execute_workflow(workflow, inputs)

    async def execute_workflow(
        self,
        workflow: WorkflowDefinition,
        inputs: dict[str, Any],
    ) -> ExecutionResult:
        """
        Execute a workflow definition directly.

        Useful for testing or ad-hoc execution.

        Args:
            workflow: Workflow definition
            inputs: Workflow inputs

        Returns:
            ExecutionResult
        """
        execution_id = generate_execution_id()
        started_at = datetime.now()

        if self._logger:
            self._logger.set_execution_id(execution_id)
            self._logger.set_step_id(None)
            self._logger._log(
                LogLevel.INFO,
                "engine",
                "Starting workflow execution",
                {"execution_id": execution_id, "workflow": workflow.name},
            )

        # Execute REQUEST_RECEIVED plugin hook
        current_inputs = inputs
        if self._plugin_registry:
            from ploston_core.plugins import RequestContext

            request_ctx = RequestContext(
                workflow_id=workflow.name,
                inputs=inputs,
                execution_id=execution_id,
                timestamp=started_at,
            )
            hook_result = self._plugin_registry.execute_request_received(request_ctx)
            current_inputs = hook_result.data.inputs

        # Instrument workflow execution with telemetry
        async with instrument_workflow(workflow.name) as telemetry_result:
            # Validate inputs
            try:
                self.validate_inputs(workflow, current_inputs)
            except Exception as e:
                record_tool_result(telemetry_result, success=False, error_code="INPUT_INVALID")
                return ExecutionResult(
                    execution_id=execution_id,
                    workflow_id=workflow.name,
                    workflow_version=workflow.version,
                    status=ExecutionStatus.FAILED,
                    started_at=started_at,
                    completed_at=datetime.now(),
                    duration_ms=int((datetime.now() - started_at).total_seconds() * 1000),
                    inputs=current_inputs,
                    error=e,
                )

            # Create execution context
            context = ExecutionContext(
                execution_id=execution_id,
                workflow=workflow,
                inputs=current_inputs,
                config={},
                started_at=started_at.isoformat(),
            )

            # Execute steps
            try:
                await self._execute_steps(context)
                status = ExecutionStatus.COMPLETED
                error = None
                record_tool_result(telemetry_result, success=True)
            except Exception as e:
                status = ExecutionStatus.FAILED
                error = e
                record_tool_result(telemetry_result, success=False, error_code=type(e).__name__)

            # Compute outputs
            outputs = self._compute_outputs(workflow, context)

            # Build result
            completed_at = datetime.now()
            duration_ms = int((completed_at - started_at).total_seconds() * 1000)

            # Count step statuses
            steps_completed = sum(
                1 for r in context.step_results.values() if r.status == StepStatus.COMPLETED
            )
            steps_failed = sum(
                1 for r in context.step_results.values() if r.status == StepStatus.FAILED
            )
            steps_skipped = sum(
                1 for r in context.step_results.values() if r.status == StepStatus.SKIPPED
            )

            # Execute RESPONSE_READY plugin hook
            final_outputs = outputs
            if self._plugin_registry:
                from ploston_core.plugins import ResponseContext

                response_ctx = ResponseContext(
                    workflow_id=workflow.name,
                    execution_id=execution_id,
                    inputs=current_inputs,
                    outputs=outputs,
                    success=(status == ExecutionStatus.COMPLETED),
                    error=error,
                    duration_ms=duration_ms,
                    step_count=len(context.step_results),
                )
                hook_result = self._plugin_registry.execute_response_ready(response_ctx)
                final_outputs = hook_result.data.outputs

            result = ExecutionResult(
                execution_id=execution_id,
                workflow_id=workflow.name,
                workflow_version=workflow.version,
                status=status,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                inputs=current_inputs,
                outputs=final_outputs,
                steps=list(context.step_results.values()),
                error=error,
                steps_completed=steps_completed,
                steps_failed=steps_failed,
                steps_skipped=steps_skipped,
            )

            if self._logger:
                self._logger.set_step_id(None)  # workflow-level log
                self._logger._log(
                    LogLevel.INFO,
                    "engine",
                    "Workflow execution completed",
                    {
                        "execution_id": execution_id,
                        "status": status.value,
                        "duration_ms": duration_ms,
                    },
                )

            # Record token savings metrics (T-397)
            if self._token_estimator and status == ExecutionStatus.COMPLETED:
                self._token_estimator.record_workflow_savings(result)

            return result

    def validate_inputs(
        self,
        workflow: WorkflowDefinition,
        inputs: dict[str, Any],
    ) -> None:
        """
        Validate inputs against workflow schema.

        Args:
            workflow: Workflow definition
            inputs: Input values

        Raises:
            AELError(INPUT_INVALID) if validation fails
        """
        errors = []

        for input_def in workflow.inputs:
            if input_def.name not in inputs:
                # Input not provided - check if we have a default or if it's required
                if input_def.default is not None:
                    # Apply default value
                    inputs[input_def.name] = input_def.default
                elif input_def.required:
                    # Required input missing with no default
                    errors.append(f"Missing required input: {input_def.name}")

        if errors:
            raise create_error("INPUT_INVALID", detail="; ".join(errors))

    async def _execute_steps(
        self,
        context: ExecutionContext,
    ) -> None:
        """Execute all steps in order.

        Args:
            context: Execution context
        """
        execution_order = context.workflow.get_execution_order()
        total_steps = len(execution_order)

        for step_index, step_id in enumerate(execution_order):
            step = context.workflow.get_step(step_id)
            if not step:
                continue

            result = await self._execute_step(step, context, step_index, total_steps)
            context.add_step_result(result)

            # Stop on failure if on_error is FAIL or RETRY (after retries exhausted)
            # Only SKIP allows the workflow to continue after a step failure
            if result.status == StepStatus.FAILED:
                step_config = self._get_step_config(step, context.workflow)
                if step_config.on_error != OnError.SKIP:
                    raise (
                        result.error
                        if result.error
                        else create_error("STEP_FAILED", step_id=step_id)
                    )

    async def _execute_step(
        self,
        step: Any,  # StepDefinition
        context: ExecutionContext,
        step_index: int = 0,
        total_steps: int = 1,
    ) -> StepResult:
        """
        Execute a single step.

        Handles:
        - Parameter rendering
        - Tool vs code execution
        - Error handling (fail/skip)
        - Retry logic

        Args:
            step: Step definition
            context: Execution context
            step_index: Index of current step (0-based)
            total_steps: Total number of steps in workflow

        Returns:
            StepResult
        """
        step_config = self._get_step_config(step, context.workflow)

        # Set step context on wrapper logger so all subsequent log calls
        # automatically include ael_step_id for Loki label promotion.
        if self._logger:
            self._logger.set_step_id(step.id)

        # Retry logic
        for attempt in range(1, (step_config.retry.max_attempts if step_config.retry else 1) + 1):
            result = await self._execute_step_once(step, context, step_index, total_steps)
            result.attempt = attempt
            result.max_attempts = step_config.retry.max_attempts if step_config.retry else 1

            if result.status == StepStatus.COMPLETED:
                return result

            # Handle failure
            if step_config.on_error == OnError.SKIP:
                result.status = StepStatus.SKIPPED
                result.skip_reason = f"Skipped due to error: {result.error}"
                return result

            # Retry if configured and not last attempt
            if step_config.retry and attempt < step_config.retry.max_attempts:
                delay = calculate_retry_delay(attempt, step_config.retry)
                if self._logger:
                    self._logger._log(
                        LogLevel.INFO,
                        "engine",
                        f"Retrying step {step.id} after {delay}s",
                        {"attempt": attempt, "max_attempts": step_config.retry.max_attempts},
                    )
                await asyncio.sleep(delay)
                continue

            # No more retries, return failure
            return result

        # Should not reach here
        return result

    async def _execute_step_once(
        self,
        step: Any,  # StepDefinition
        context: ExecutionContext,
        step_index: int = 0,
        total_steps: int = 1,
    ) -> StepResult:
        """Execute step without retry logic.

        Args:
            step: Step definition
            context: Execution context
            step_index: Index of current step (0-based)
            total_steps: Total number of steps in workflow

        Returns:
            StepResult
        """
        started_at = datetime.now()
        start_time = time.time()

        # Execute STEP_BEFORE plugin hook
        current_params = dict(step.params) if step.params else {}
        if self._plugin_registry:
            from ploston_core.plugins import StepContext as PluginStepContext

            step_ctx = PluginStepContext(
                workflow_id=context.workflow.name,
                execution_id=context.execution_id,
                step_id=step.id,
                step_type=step.step_type.value
                if hasattr(step.step_type, "value")
                else str(step.step_type),
                step_index=step_index,
                total_steps=total_steps,
                tool_name=step.tool if hasattr(step, "tool") else None,
                params=current_params,
            )
            hook_result = self._plugin_registry.execute_step_before(step_ctx)
            current_params = hook_result.data.params

        # Evaluate when condition (skip step if falsy)
        if step.when:
            template_context = context.get_template_context()
            when_expr = "{{ " + step.when + " }}"
            when_result = self._template_engine.render_string(when_expr, template_context)
            if not when_result:
                completed_at = datetime.now()
                duration_ms = int((time.time() - start_time) * 1000)
                return StepResult(
                    step_id=step.id,
                    status=StepStatus.SKIPPED,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_ms=duration_ms,
                    skip_reason=f"when condition not met: {step.when}",
                )

        # Instrument step execution with telemetry
        async with instrument_step(context.workflow.name, step.id) as telemetry_result:
            try:
                # Execute based on step type
                step_debug_log: list[str] = []
                if step.step_type == StepType.TOOL:
                    output = await self._execute_tool_step(step, context, current_params)
                else:  # CODE
                    output, step_debug_log = await self._execute_code_step(step, context)

                # Success
                completed_at = datetime.now()
                duration_ms = int((time.time() - start_time) * 1000)
                record_tool_result(telemetry_result, success=True)

                # Execute STEP_AFTER plugin hook
                final_output = output
                if self._plugin_registry:
                    from ploston_core.plugins import StepResultContext

                    result_ctx = StepResultContext(
                        workflow_id=context.workflow.name,
                        execution_id=context.execution_id,
                        step_id=step.id,
                        step_type=step.step_type.value
                        if hasattr(step.step_type, "value")
                        else str(step.step_type),
                        success=True,
                        output=output,
                        duration_ms=duration_ms,
                    )
                    hook_result = self._plugin_registry.execute_step_after(result_ctx)
                    final_output = hook_result.data.output

                return StepResult(
                    step_id=step.id,
                    status=StepStatus.COMPLETED,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_ms=duration_ms,
                    output=final_output,
                    debug_log=step_debug_log,
                )

            except Exception as e:
                # Failure
                completed_at = datetime.now()
                duration_ms = int((time.time() - start_time) * 1000)

                # Check if this is a tool-unavailable error and on_missing_tool: skip is set
                is_tool_unavailable = isinstance(e, AELError) and e.code == "TOOL_UNAVAILABLE"
                if (
                    is_tool_unavailable
                    and step.step_type == StepType.TOOL
                    and getattr(step, "on_missing_tool", None) == OnMissingTool.SKIP
                ):
                    record_tool_result(
                        telemetry_result, success=False, error_code="TOOL_UNAVAILABLE"
                    )
                    return StepResult(
                        step_id=step.id,
                        status=StepStatus.SKIPPED,
                        started_at=started_at,
                        completed_at=completed_at,
                        duration_ms=duration_ms,
                        skip_reason=f"Tool '{step.tool}' not registered (on_missing_tool: skip)",
                    )

                record_tool_result(telemetry_result, success=False, error_code=type(e).__name__)

                # Execute STEP_AFTER plugin hook for failure
                if self._plugin_registry:
                    from ploston_core.plugins import StepResultContext

                    result_ctx = StepResultContext(
                        workflow_id=context.workflow.name,
                        execution_id=context.execution_id,
                        step_id=step.id,
                        step_type=step.step_type.value
                        if hasattr(step.step_type, "value")
                        else str(step.step_type),
                        success=False,
                        error=e,
                        duration_ms=duration_ms,
                    )
                    self._plugin_registry.execute_step_after(result_ctx)

                return StepResult(
                    step_id=step.id,
                    status=StepStatus.FAILED,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_ms=duration_ms,
                    error=e,
                )

    async def _execute_tool_step(
        self,
        step: Any,  # StepDefinition
        context: ExecutionContext,
        plugin_params: dict[str, Any] | None = None,
    ) -> Any:
        """Execute a tool step.

        Args:
            step: Step definition
            context: Execution context
            plugin_params: Optional params from plugin hook (overrides step.params)

        Returns:
            Tool output
        """
        # Render parameters using template engine
        template_context = context.get_template_context()

        # Use plugin params if provided, otherwise use step params
        params_to_render = plugin_params if plugin_params is not None else step.params

        # Use render_params which handles nested dicts/lists and extracts .value
        rendered_params = self._template_engine.render_params(params_to_render, template_context)

        # Get step config for timeout
        step_config = self._get_step_config(step, context.workflow)

        # Resolve canonical tool name (DEC-157 / T-726)
        invoke_name = self._resolve_invoke_name(step, context.workflow)

        if self._logger:
            self._logger._log(
                LogLevel.INFO,
                "engine",
                f"[TOOL_STEP] step={step.id} invoking tool_name={invoke_name!r} "
                f"(original tool={step.tool!r}, mcp={getattr(step, 'mcp', None)!r})",
                {},
            )

        # Invoke tool
        result = await self._tool_invoker.invoke(
            tool_name=invoke_name,
            params=rendered_params,
            timeout_seconds=step_config.timeout_seconds,
        )

        if not result.success:
            raise result.error if result.error else create_error("TOOL_FAILED", tool_name=step.tool)

        return result.output

    def _get_effective_runner(self, workflow: WorkflowDefinition) -> str | None:
        """Resolve effective runner for tool step invocation.

        Priority: workflow defaults.runner > bridge_context.runner_name
        Used by _resolve_invoke_name() for tool steps only.
        """
        if workflow.defaults and workflow.defaults.runner:
            return workflow.defaults.runner
        try:
            from ploston_core.mcp_frontend.http_transport import bridge_context

            ctx = bridge_context.get()
            if ctx:
                return getattr(ctx, "runner_name", None)
        except Exception:
            pass
        return None

    def _resolve_invoke_name(self, step: Any, workflow: WorkflowDefinition) -> str:
        """Resolve the canonical tool name for invocation.

        Priority: workflow defaults.runner → bridge context runner → inference → bare name.
        If mcp is not set (legacy/system tools), returns step.tool as-is.
        """
        if self._logger:
            self._logger._log(
                LogLevel.DEBUG,
                "engine",
                f"[RESOLVE] step={step.id} tool={step.tool!r} mcp={getattr(step, 'mcp', None)!r}",
                {},
            )

        if not getattr(step, "mcp", None):
            # Legacy workflow or system tool — bare name
            if self._logger:
                self._logger._log(
                    LogLevel.DEBUG,
                    "engine",
                    f"[RESOLVE] step={step.id} → no mcp field, using bare tool name: {step.tool!r}",
                    {},
                )
            return step.tool

        # Determine runner via shared helper
        runner = self._get_effective_runner(workflow)
        runner_source: str = "none"

        if runner:
            # Determine source for logging
            if workflow.defaults and workflow.defaults.runner == runner:
                runner_source = "defaults.runner"
            else:
                runner_source = "bridge_context"
        else:
            # Step 3: single-match inference from RunnerRegistry
            if self._runner_registry:
                matches = [
                    r
                    for r in self._runner_registry.list()
                    if r.status.value == "connected"
                    and any(
                        self._runner_registry._get_tool_name(e).startswith(f"{step.mcp}__")
                        for e in (r.available_tools or [])
                    )
                ]
                if len(matches) == 1:
                    runner = matches[0].name
                    runner_source = "inference"
                elif len(matches) > 1:
                    names = sorted(r.name for r in matches)
                    if self._logger:
                        self._logger._log(
                            LogLevel.WARN,
                            "engine",
                            f"[RESOLVE] step={step.id} ambiguous runners for mcp={step.mcp!r}: {names}",
                            {},
                        )
                    # Fall through to bare tool name; will TOOL_UNAVAILABLE at invoke time

        if self._logger:
            self._logger._log(
                LogLevel.INFO,
                "engine",
                f"[RESOLVE] step={step.id} runner={runner!r} (source={runner_source})",
                {},
            )

        if runner:
            canonical = f"{runner}__{step.mcp}__{step.tool}"
            if self._logger:
                self._logger._log(
                    LogLevel.INFO,
                    "engine",
                    f"[RESOLVE] step={step.id} → canonical name: {canonical!r}",
                    {},
                )
            return canonical

        # CP-direct — bare tool name
        if self._logger:
            self._logger._log(
                LogLevel.WARN,
                "engine",
                f"[RESOLVE] step={step.id} → no runner resolved, falling back to bare tool name: {step.tool!r}",
                {},
            )
        return step.tool

    async def _execute_code_step(
        self,
        step: Any,  # StepDefinition
        context: ExecutionContext,
    ) -> tuple[Any, list[str]]:
        """Execute a code step.

        Creates SandboxContext from ExecutionContext with a properly wrapped
        ToolCallInterface (rate limiting, recursion prevention, call_mcp support).

        Args:
            step: Step definition
            context: Execution context

        Returns:
            Tuple of (output, debug_log) where debug_log is from context.log() calls
        """
        # Local imports to avoid circular deps.
        # SandboxContext is already imported at module level (line 11).
        from ploston_core.sandbox.types import RunnerContext, ToolCallInterface, WorkflowMeta

        # Populate RunnerContext with raw source values (not pre-resolved)
        bridge_ctx = None
        try:
            from ploston_core.mcp_frontend.http_transport import bridge_context

            bridge_ctx = bridge_context.get()
        except Exception:
            pass

        runner_ctx = RunnerContext(
            runner_name=getattr(bridge_ctx, "runner_name", None) if bridge_ctx else None,
            defaults_runner=(
                context.workflow.defaults.runner if context.workflow.defaults else None
            ),
            step_id=step.id,
            execution_id=context.execution_id,
        )

        tool_interface = ToolCallInterface(
            tool_caller=self._tool_invoker,
            max_calls=self._max_tool_calls,
            logger=self._logger,
            tool_registry=self._tool_registry,
            runner_registry=self._runner_registry,
            runner_context=runner_ctx,
        )

        # Build workflow metadata for sandbox context
        workflow_meta = WorkflowMeta(
            name=getattr(context.workflow, "name", ""),
            version=getattr(context.workflow, "version", ""),
            execution_id=context.execution_id,
            start_time=context.started_at,
        )

        sandbox_context = SandboxContext(
            inputs=context.inputs,
            steps=context.step_outputs,
            config=context.config,
            tools=tool_interface,
            runner_context=runner_ctx,
            workflow=workflow_meta,
        )

        # Get step config for timeout
        step_config = self._get_step_config(step, context.workflow)

        # Execute code via tool invoker (python_exec)
        result = await self._tool_invoker.invoke(
            tool_name="python_exec",
            params={"code": step.code, "context": sandbox_context},
            timeout_seconds=step_config.timeout_seconds,
        )

        # Capture debug_log from sandbox context (only available on success path)
        debug_log = list(sandbox_context._debug_log)

        if not result.success:
            # Use CODE_RUNTIME — CODE_EXECUTION_FAILED is not in ErrorRegistry
            raise (result.error if result.error else create_error("CODE_RUNTIME", step_id=step.id))

        return result.output, debug_log

    def _compute_outputs(
        self,
        workflow: WorkflowDefinition,
        context: ExecutionContext,
    ) -> dict[str, Any]:
        """Compute workflow outputs from step results.

        Args:
            workflow: Workflow definition
            context: Execution context

        Returns:
            Output values
        """
        outputs = {}

        for output_def in workflow.outputs:
            if output_def.from_path:
                # Extract from step output using path
                # e.g., "steps.fetch.output.items"
                value = self._extract_from_path(output_def.from_path, context)
            elif output_def.value:
                # Render template expression
                template_context = context.get_template_context()
                render_result = self._template_engine.render(output_def.value, template_context)
                # Extract value from RenderResult
                value = render_result.value if hasattr(render_result, "value") else render_result
            else:
                value = None

            outputs[output_def.name] = value

        return outputs

    def _extract_from_path(self, path: str, context: ExecutionContext) -> Any:
        """Extract value from context using dot-notation path.

        Args:
            path: Dot-notation path (e.g., "steps.fetch.output.items")
            context: Execution context

        Returns:
            Extracted value
        """
        parts = path.split(".")
        value: Any = None

        if parts[0] == "steps" and len(parts) >= 2:
            step_id = parts[1]
            if step_id in context.step_outputs:
                value = context.step_outputs[step_id]
                # Navigate remaining path
                for part in parts[2:]:
                    if hasattr(value, part):
                        value = getattr(value, part)
                    elif isinstance(value, dict) and part in value:
                        value = value[part]
                    else:
                        value = None
                        break
        elif parts[0] == "inputs" and len(parts) >= 2:
            input_name = parts[1]
            value = context.inputs.get(input_name)

        return value

    def _get_step_config(
        self,
        step: Any,  # StepDefinition
        workflow: WorkflowDefinition,
    ) -> StepExecutionConfig:
        """
        Get effective configuration for a step.

        Merges: step → workflow.defaults → system config

        Args:
            step: Step definition
            workflow: Workflow definition

        Returns:
            Effective step configuration
        """
        # Timeout: step > workflow.defaults > system config
        timeout = step.timeout
        if timeout is None and workflow.defaults:
            timeout = workflow.defaults.timeout
        if timeout is None:
            timeout = getattr(self._config, "default_timeout", 30)

        # on_error: step > workflow.defaults > FAIL
        on_error = step.on_error
        if on_error is None and workflow.defaults:
            on_error = workflow.defaults.on_error
        if on_error is None:
            on_error = OnError.FAIL

        # retry: step > workflow.defaults > None
        retry = step.retry
        if retry is None and workflow.defaults:
            retry = workflow.defaults.retry

        return StepExecutionConfig(
            timeout_seconds=timeout,
            on_error=on_error,
            retry=retry,
        )

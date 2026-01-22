"""Error registry for creating errors from templates."""

from typing import Any

from .errors import AELError, ErrorCategory, ErrorTemplate


class ErrorRegistry:
    """Registry of error templates. Creates errors from templates + context."""

    def __init__(self) -> None:
        """Initialize error registry with built-in templates."""
        self._templates: dict[str, ErrorTemplate] = {}
        self._load_builtin_templates()

    def get_template(self, code: str) -> ErrorTemplate | None:
        """Get template by error code.

        Args:
            code: Error code to look up

        Returns:
            ErrorTemplate if found, None otherwise
        """
        return self._templates.get(code)

    def list_codes(self) -> list[str]:
        """List all registered error codes.

        Returns:
            List of error codes
        """
        return list(self._templates.keys())

    def create(
        self,
        code: str,
        context: dict[str, Any] | None = None,
        cause: AELError | None = None,
    ) -> AELError:
        """Create error instance from template + context.

        Args:
            code: Error code
            context: Context variables for template interpolation
            cause: Optional cause error

        Returns:
            AELError instance

        Raises:
            ValueError: If error code not found
        """
        template = self.get_template(code)
        if not template:
            msg = f"Unknown error code: {code}"
            raise ValueError(msg)

        context = context or {}

        # Interpolate templates
        message = self._interpolate(template.message_template, context)
        detail = self._interpolate(template.detail_template, context)
        suggestion = self._interpolate(template.suggestion_template, context)

        # Ensure message is not None
        if message is None:
            message = f"Error {code}"

        return AELError(
            code=template.code,
            category=template.category,
            message=message,
            detail=detail,
            suggestion=suggestion,
            retryable=template.default_retryable,
            http_status=template.default_http_status,
            step_id=context.get("step_id"),
            tool_name=context.get("tool_name"),
            execution_id=context.get("execution_id"),
            cause=cause,
        )

    def _interpolate(
        self,
        template: str | None,
        context: dict[str, Any],
    ) -> str | None:
        """Safe string interpolation.

        Args:
            template: Template string with {var} placeholders
            context: Context variables

        Returns:
            Interpolated string or None if template is None
        """
        if template is None:
            return None

        try:
            return template.format(**context)
        except KeyError:
            # Missing context variable - return template as-is
            return template

    def _load_builtin_templates(self) -> None:
        """Load hardcoded built-in templates."""
        # TOOL Errors
        self._templates["TOOL_UNAVAILABLE"] = ErrorTemplate(
            code="TOOL_UNAVAILABLE",
            category=ErrorCategory.TOOL,
            message_template="Tool '{tool_name}' is unavailable",
            detail_template="The requested tool could not be reached or is not responding",
            suggestion_template="Check that the MCP server is running and the tool is registered",
            default_retryable=True,
            default_http_status=503,
        )

        self._templates["TOOL_TIMEOUT"] = ErrorTemplate(
            code="TOOL_TIMEOUT",
            category=ErrorCategory.TOOL,
            message_template="Tool '{tool_name}' timed out after {timeout_seconds}s",
            detail_template="The tool did not respond within the configured timeout",
            suggestion_template="Increase the timeout or check if the tool is stuck",
            default_retryable=True,
            default_http_status=504,
        )

        self._templates["TOOL_REJECTED"] = ErrorTemplate(
            code="TOOL_REJECTED",
            category=ErrorCategory.TOOL,
            message_template="Tool '{tool_name}' rejected the request",
            detail_template="The tool refused to execute with the provided parameters",
            suggestion_template="Check the tool parameters and try again",
            default_retryable=False,
            default_http_status=400,
        )

        self._templates["TOOL_FAILED"] = ErrorTemplate(
            code="TOOL_FAILED",
            category=ErrorCategory.TOOL,
            message_template="{message}",
            detail_template="Tool '{tool_name}' encountered an error during execution",
            suggestion_template="Check the tool logs for more details",
            default_retryable=False,
            default_http_status=502,
        )

        # EXECUTION Errors
        self._templates["CODE_SYNTAX"] = ErrorTemplate(
            code="CODE_SYNTAX",
            category=ErrorCategory.EXECUTION,
            message_template="Syntax error in code block",
            detail_template="The Python code contains syntax errors",
            suggestion_template="Fix the syntax errors and try again",
            default_retryable=False,
            default_http_status=400,
        )

        self._templates["CODE_RUNTIME"] = ErrorTemplate(
            code="CODE_RUNTIME",
            category=ErrorCategory.EXECUTION,
            message_template="Runtime error in code block",
            detail_template="The Python code raised an exception during execution",
            suggestion_template="Check the code logic and error message",
            default_retryable=False,
            default_http_status=500,
        )

        self._templates["CODE_TIMEOUT"] = ErrorTemplate(
            code="CODE_TIMEOUT",
            category=ErrorCategory.EXECUTION,
            message_template="Code execution timed out after {timeout_seconds}s",
            detail_template="The code block did not complete within the timeout",
            suggestion_template="Optimize the code or increase the timeout",
            default_retryable=False,
            default_http_status=504,
        )

        self._templates["CODE_SECURITY"] = ErrorTemplate(
            code="CODE_SECURITY",
            category=ErrorCategory.EXECUTION,
            message_template="Security violation in code block",
            detail_template="The code attempted to use forbidden imports or builtins",
            suggestion_template="Remove dangerous imports or operations",
            default_retryable=False,
            default_http_status=403,
        )

        self._templates["TEMPLATE_ERROR"] = ErrorTemplate(
            code="TEMPLATE_ERROR",
            category=ErrorCategory.EXECUTION,
            message_template="Template rendering failed",
            detail_template="Failed to render Jinja2 template",
            suggestion_template="Check template syntax and variable names",
            default_retryable=False,
            default_http_status=400,
        )

        # VALIDATION Errors
        self._templates["INPUT_INVALID"] = ErrorTemplate(
            code="INPUT_INVALID",
            category=ErrorCategory.VALIDATION,
            message_template="Invalid workflow input",
            detail_template="The workflow input does not match the expected schema",
            suggestion_template="Check the input schema and provide valid data",
            default_retryable=False,
            default_http_status=400,
        )

        self._templates["PARAM_INVALID"] = ErrorTemplate(
            code="PARAM_INVALID",
            category=ErrorCategory.VALIDATION,
            message_template="Invalid parameters for tool '{tool_name}'",
            detail_template="The tool parameters do not match the expected schema",
            suggestion_template="Check the tool schema and provide valid parameters",
            default_retryable=False,
            default_http_status=400,
        )

        self._templates["OUTPUT_INVALID"] = ErrorTemplate(
            code="OUTPUT_INVALID",
            category=ErrorCategory.VALIDATION,
            message_template="Step '{step_id}' output doesn't match contract",
            detail_template="The step output does not match the expected schema",
            suggestion_template="Check the step output schema",
            default_retryable=False,
            default_http_status=500,
        )

        # WORKFLOW Errors
        self._templates["STEP_NOT_FOUND"] = ErrorTemplate(
            code="STEP_NOT_FOUND",
            category=ErrorCategory.WORKFLOW,
            message_template="Step '{step_id}' not found",
            detail_template="The referenced step does not exist in the workflow",
            suggestion_template="Check the step ID and workflow definition",
            default_retryable=False,
            default_http_status=400,
        )

        self._templates["CIRCULAR_DEPENDENCY"] = ErrorTemplate(
            code="CIRCULAR_DEPENDENCY",
            category=ErrorCategory.WORKFLOW,
            message_template="Circular dependency detected",
            detail_template="The workflow contains circular step dependencies",
            suggestion_template="Remove circular dependencies from the workflow",
            default_retryable=False,
            default_http_status=400,
        )

        self._templates["WORKFLOW_NOT_FOUND"] = ErrorTemplate(
            code="WORKFLOW_NOT_FOUND",
            category=ErrorCategory.WORKFLOW,
            message_template="Workflow '{workflow_id}' not found",
            detail_template="The requested workflow does not exist",
            suggestion_template="Check the workflow ID and registry",
            default_retryable=False,
            default_http_status=404,
        )

        self._templates["WORKFLOW_TIMEOUT"] = ErrorTemplate(
            code="WORKFLOW_TIMEOUT",
            category=ErrorCategory.WORKFLOW,
            message_template="Workflow timed out after {timeout_seconds}s",
            detail_template="The workflow did not complete within the timeout",
            suggestion_template="Increase the timeout or optimize the workflow",
            default_retryable=False,
            default_http_status=504,
        )

        # SYSTEM Errors
        self._templates["INTERNAL_ERROR"] = ErrorTemplate(
            code="INTERNAL_ERROR",
            category=ErrorCategory.SYSTEM,
            message_template="Internal AEL error",
            detail_template="An unexpected error occurred in the AEL engine",
            suggestion_template="Check the logs and report this issue",
            default_retryable=False,
            default_http_status=500,
        )

        self._templates["RESOURCE_EXHAUSTED"] = ErrorTemplate(
            code="RESOURCE_EXHAUSTED",
            category=ErrorCategory.SYSTEM,
            message_template="Resource exhausted: {resource}",
            detail_template="A system resource has been exhausted",
            suggestion_template="Free up resources or increase limits",
            default_retryable=True,
            default_http_status=503,
        )

        self._templates["CONFIG_INVALID"] = ErrorTemplate(
            code="CONFIG_INVALID",
            category=ErrorCategory.SYSTEM,
            message_template="Invalid configuration",
            detail_template="The AEL configuration is invalid",
            suggestion_template="Check the configuration file and fix errors",
            default_retryable=False,
            default_http_status=500,
        )

        self._templates["MCP_CONNECTION_FAILED"] = ErrorTemplate(
            code="MCP_CONNECTION_FAILED",
            category=ErrorCategory.SYSTEM,
            message_template="Failed to connect to MCP server",
            detail_template="Could not establish connection to the MCP server",
            suggestion_template="Check that the MCP server is running and accessible",
            default_retryable=True,
            default_http_status=503,
        )

        # CONFIG Errors (Phase 1 additions)
        self._templates["CONFIG_PATH_INVALID"] = ErrorTemplate(
            code="CONFIG_PATH_INVALID",
            category=ErrorCategory.VALIDATION,
            message_template="Invalid configuration path: {path}",
            detail_template="The configuration path '{path}' is not valid",
            suggestion_template="Use dot notation for nested paths (e.g., 'logging.level')",
            default_retryable=False,
            default_http_status=400,
        )

        self._templates["CONFIG_VALIDATION_FAILED"] = ErrorTemplate(
            code="CONFIG_VALIDATION_FAILED",
            category=ErrorCategory.VALIDATION,
            message_template="Configuration validation failed",
            detail_template="The configuration contains {error_count} error(s)",
            suggestion_template="Fix the validation errors and try again",
            default_retryable=False,
            default_http_status=400,
        )

        self._templates["CONFIG_WRITE_FAILED"] = ErrorTemplate(
            code="CONFIG_WRITE_FAILED",
            category=ErrorCategory.SYSTEM,
            message_template="Failed to write configuration file",
            detail_template="Could not write configuration to '{path}'",
            suggestion_template="Check file permissions and disk space",
            default_retryable=False,
            default_http_status=500,
        )

        self._templates["CONFIG_MCP_CONNECTION_FAILED"] = ErrorTemplate(
            code="CONFIG_MCP_CONNECTION_FAILED",
            category=ErrorCategory.SYSTEM,
            message_template="Failed to connect to MCP server '{server_name}'",
            detail_template="Could not establish connection during config validation",
            suggestion_template="Check the server command and ensure it's installed",
            default_retryable=True,
            default_http_status=500,
        )

        self._templates["TOOL_NOT_AVAILABLE"] = ErrorTemplate(
            code="TOOL_NOT_AVAILABLE",
            category=ErrorCategory.TOOL,
            message_template="Tool '{tool_name}' not available in current mode",
            detail_template="The tool cannot be used in {mode} mode",
            suggestion_template="Switch to running mode to use this tool",
            default_retryable=False,
            default_http_status=400,
        )

        self._templates["WORKFLOW_NOT_AVAILABLE"] = ErrorTemplate(
            code="WORKFLOW_NOT_AVAILABLE",
            category=ErrorCategory.WORKFLOW,
            message_template="Workflows not available in configuration mode",
            detail_template="Cannot start workflows while in configuration mode",
            suggestion_template="Complete configuration with config_done to enable workflows",
            default_retryable=False,
            default_http_status=400,
        )

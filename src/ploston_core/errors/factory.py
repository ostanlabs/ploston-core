"""Error factory for creating AELErrors from any exception type."""

from typing import Any

from .errors import AELError
from .matchers import ErrorMatcherChain
from .registry import ErrorRegistry


class ErrorFactory:
    """Creates AELErrors from any exception type."""

    def __init__(
        self,
        registry: ErrorRegistry | None = None,
        matcher_chain: ErrorMatcherChain | None = None,
    ):
        """Initialize error factory.

        Args:
            registry: Error registry (defaults to new ErrorRegistry())
            matcher_chain: Matcher chain (defaults to new ErrorMatcherChain())
        """
        self.registry = registry or ErrorRegistry()
        self.matcher_chain = matcher_chain or ErrorMatcherChain()
        self._max_cause_depth = 3

    def from_exception(
        self,
        error: Exception | dict[str, Any],
        step_id: str | None = None,
        tool_name: str | None = None,
        execution_id: str | None = None,
    ) -> AELError:
        """Convert any exception to AELError.

        Args:
            error: Exception or error dict to convert
            step_id: Optional step identifier
            tool_name: Optional tool name
            execution_id: Optional execution identifier

        Returns:
            AELError instance
        """
        # If already an AELError, just add context
        if isinstance(error, AELError):
            return error.with_context(
                step_id=step_id,
                tool_name=tool_name,
                execution_id=execution_id,
            )

        # Try to match the exception
        match_result = self.matcher_chain.match(error)

        # Create error from template
        context = match_result.context.copy()
        if step_id:
            context["step_id"] = step_id
        if tool_name:
            context["tool_name"] = tool_name
        if execution_id:
            context["execution_id"] = execution_id

        ael_error = self.registry.create(
            code=match_result.ael_code,
            context=context,
        )

        # Override retryable if specified in match result
        if match_result.retryable is not None:
            ael_error.retryable = match_result.retryable

        return ael_error

    def create(
        self,
        code: str,
        context: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AELError:
        """Create AELError directly from code.

        Args:
            code: Error code
            context: Context variables for template interpolation
            **kwargs: Additional context variables

        Returns:
            AELError instance
        """
        # Merge context and kwargs
        merged_context = context or {}
        merged_context.update(kwargs)

        return self.registry.create(code=code, context=merged_context)


# Convenience singleton
_default_factory: ErrorFactory | None = None


def get_error_factory() -> ErrorFactory:
    """Get default error factory singleton.

    Returns:
        Default ErrorFactory instance
    """
    global _default_factory  # noqa: PLW0603
    if _default_factory is None:
        _default_factory = ErrorFactory()
    return _default_factory


def create_error(code: str, **context: Any) -> AELError:
    """Convenience function to create error.

    Args:
        code: Error code
        **context: Context variables for template interpolation

    Returns:
        AELError instance
    """
    return get_error_factory().create(code, context)

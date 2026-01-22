"""AEL Error handling - Structured errors with context."""

from .errors import AELError, ErrorCategory, ErrorMatcher, ErrorTemplate, MatchResult
from .factory import ErrorFactory, create_error, get_error_factory
from .matchers import ErrorMatcherChain
from .registry import ErrorRegistry

__all__ = [
    # Core error types
    "AELError",
    "ErrorCategory",
    "ErrorTemplate",
    "MatchResult",
    # Registry and factory
    "ErrorRegistry",
    "ErrorFactory",
    "ErrorMatcherChain",
    "ErrorMatcher",
    # Convenience functions
    "get_error_factory",
    "create_error",
]

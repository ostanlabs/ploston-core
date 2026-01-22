"""Error matchers for converting exceptions to AELErrors."""

import asyncio
from typing import Any

from .errors import ErrorMatcher, MatchResult


class TimeoutErrorMatcher(ErrorMatcher):
    """Matches timeout errors."""

    def matches(self, error: Exception | dict[str, Any]) -> bool:
        """Check if error is a timeout error.

        Args:
            error: Exception or error dict to check

        Returns:
            True if error is a timeout error
        """
        if isinstance(error, dict):
            return error.get("type") == "timeout"
        return isinstance(error, (asyncio.TimeoutError, TimeoutError))

    def extract(self, error: Exception | dict[str, Any]) -> MatchResult:
        """Extract timeout error info.

        Args:
            error: Exception or error dict to extract from

        Returns:
            MatchResult with CODE_TIMEOUT code
        """
        context: dict[str, Any] = {}

        if isinstance(error, dict):
            context["timeout_seconds"] = error.get("timeout", "unknown")
        else:
            context["timeout_seconds"] = "unknown"

        return MatchResult(
            ael_code="CODE_TIMEOUT",
            context=context,
            retryable=False,
        )


class SyntaxErrorMatcher(ErrorMatcher):
    """Matches Python syntax errors."""

    def matches(self, error: Exception | dict[str, Any]) -> bool:
        """Check if error is a syntax error.

        Args:
            error: Exception or error dict to check

        Returns:
            True if error is a syntax error
        """
        if isinstance(error, dict):
            return error.get("type") == "SyntaxError"
        return isinstance(error, SyntaxError)

    def extract(self, error: Exception | dict[str, Any]) -> MatchResult:
        """Extract syntax error info.

        Args:
            error: Exception or error dict to extract from

        Returns:
            MatchResult with CODE_SYNTAX code
        """
        context: dict[str, Any] = {}

        if isinstance(error, dict):
            context["detail"] = error.get("message", str(error))
        else:
            context["detail"] = str(error)

        return MatchResult(
            ael_code="CODE_SYNTAX",
            context=context,
            retryable=False,
        )


class GenericErrorMatcher(ErrorMatcher):
    """Fallback matcher for any exception."""

    def matches(self, error: Exception | dict[str, Any]) -> bool:
        """Always matches.

        Args:
            error: Exception or error dict to check

        Returns:
            Always True (fallback matcher)
        """
        return True

    def extract(self, error: Exception | dict[str, Any]) -> MatchResult:
        """Extract generic error info.

        Args:
            error: Exception or error dict to extract from

        Returns:
            MatchResult with INTERNAL_ERROR code
        """
        context: dict[str, Any] = {}

        if isinstance(error, dict):
            context["detail"] = error.get("message", str(error))
            context["error_type"] = error.get("type", "unknown")
        else:
            context["detail"] = str(error)
            context["error_type"] = type(error).__name__

        return MatchResult(
            ael_code="INTERNAL_ERROR",
            context=context,
            retryable=False,
        )


class ErrorMatcherChain:
    """Ordered chain of matchers. First match wins."""

    def __init__(self) -> None:
        """Initialize matcher chain with built-in matchers."""
        self.matchers: list[ErrorMatcher] = []
        self._load_builtin_matchers()

    def match(self, error: Exception | dict[str, Any]) -> MatchResult:
        """Find first matching matcher and extract result.

        Args:
            error: Exception or error dict to match

        Returns:
            MatchResult from first matching matcher
        """
        for matcher in self.matchers:
            if matcher.matches(error):
                return matcher.extract(error)

        # Should never reach here due to GenericErrorMatcher
        return MatchResult(
            ael_code="INTERNAL_ERROR",
            context={"detail": str(error)},
            retryable=False,
        )

    def _load_builtin_matchers(self) -> None:
        """Load built-in matchers in priority order."""
        # Order matters - more specific matchers first
        self.matchers = [
            TimeoutErrorMatcher(),
            SyntaxErrorMatcher(),
            # Add more specific matchers here
            GenericErrorMatcher(),  # Fallback - must be last
        ]

"""L-1 (core/mcp_frontend): tool-execution error codes must go through the registry.

The MCP frontend constructed ``AELError`` directly with the codes
``TOOL_EXECUTION_FAILED`` / ``TOOL_ERROR``. Those codes were never registered in
the error registry, so:

* routing construction through ``create_error`` silently degraded them to
  ``INTERNAL_ERROR`` (wrong code, and category flips TOOL -> SYSTEM), and
* the direct ``AELError(...)`` call site set ``http_status``/``retryable``
  ad-hoc, outside the error contract.

These tests encode the CORRECT behavior via the public path (registry /
``create_error``): both codes are registered as TOOL-category errors whose
``http_status``/``retryable``/``message`` follow the contract. They deliberately
assert against the contract, not the previous buggy output.
"""

from __future__ import annotations

from ploston_core.errors import AELError, create_error
from ploston_core.errors.errors import ErrorCategory
from ploston_core.errors.registry import ErrorRegistry


def test_tool_execution_failed_is_registered() -> None:
    """TOOL_EXECUTION_FAILED is a registered TOOL-category template."""
    registry = ErrorRegistry()
    assert "TOOL_EXECUTION_FAILED" in registry.list_codes()
    template = registry.get_template("TOOL_EXECUTION_FAILED")
    assert template is not None
    assert template.category == ErrorCategory.TOOL


def test_tool_error_is_registered() -> None:
    """TOOL_ERROR is a registered TOOL-category template."""
    registry = ErrorRegistry()
    assert "TOOL_ERROR" in registry.list_codes()
    template = registry.get_template("TOOL_ERROR")
    assert template is not None
    assert template.category == ErrorCategory.TOOL


def test_tool_execution_failed_contract_via_create_error() -> None:
    """create_error(TOOL_EXECUTION_FAILED) keeps the code (no INTERNAL_ERROR degrade).

    Contract: a tool/runner execution failure is a server-side failure
    (http_status 500), non-retryable by default (matching the TOOL_FAILED /
    TOOL_REJECTED convention — only TOOL_UNAVAILABLE / TOOL_TIMEOUT are
    retryable), and TOOL category.
    """
    err = create_error("TOOL_EXECUTION_FAILED", message="boom")
    assert isinstance(err, AELError)
    # Did NOT silently degrade to INTERNAL_ERROR.
    assert err.code == "TOOL_EXECUTION_FAILED"
    assert err.category == ErrorCategory.TOOL
    assert err.http_status == 500
    assert err.retryable is False
    # Explicit message override is preserved (F-1 precedence).
    assert err.message == "boom"


def test_tool_error_contract_via_create_error() -> None:
    """create_error(TOOL_ERROR) keeps the code and follows the TOOL contract."""
    err = create_error("TOOL_ERROR", message="kaput")
    assert isinstance(err, AELError)
    assert err.code == "TOOL_ERROR"
    assert err.category == ErrorCategory.TOOL
    assert err.http_status == 500
    assert err.retryable is False
    assert err.message == "kaput"

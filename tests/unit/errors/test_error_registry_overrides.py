"""F-1: explicit context overrides for message/detail/suggestion.

`ErrorRegistry.create(code, context=...)` previously ALWAYS rendered
`message`/`detail`/`suggestion` from the template, silently discarding any
explicit value passed by the caller (e.g.
`create_error("PARAM_INVALID", message="'mcp' parameter is required")`).

Precedence contract:

    explicit context override  >  template render

Identity fields (code/category/http_status/retryable) always come from the
template. Unknown codes still degrade to INTERNAL_ERROR (Area A) and the
override precedence still applies on top of the fallback template.
"""

from __future__ import annotations

from ploston_core.errors import AELError, create_error
from ploston_core.errors.registry import ErrorRegistry


def test_explicit_message_overrides_template() -> None:
    """An explicit `message` in context wins over the template render."""
    err = create_error("PARAM_INVALID", message="custom X")
    assert isinstance(err, AELError)
    assert err.message == "custom X"
    # Identity still from the template.
    assert err.code == "PARAM_INVALID"
    assert err.category.value == "VALIDATION"
    assert err.http_status == 400


def test_no_override_uses_template_message() -> None:
    """Without an override the template-rendered message is preserved."""
    err = create_error("PARAM_INVALID", tool_name="search")
    assert err.message == "Invalid parameters for tool 'search'"


def test_explicit_detail_overrides_template() -> None:
    """An explicit `detail` in context wins over the template render."""
    err = create_error("PARAM_INVALID", detail="custom detail Y")
    assert err.detail == "custom detail Y"
    # Untouched fields still come from the template.
    assert err.code == "PARAM_INVALID"
    assert err.category.value == "VALIDATION"


def test_explicit_suggestion_overrides_template() -> None:
    """An explicit `suggestion` in context wins over the template render."""
    err = create_error("PARAM_INVALID", suggestion="custom suggestion Z")
    assert err.suggestion == "custom suggestion Z"
    assert err.code == "PARAM_INVALID"


def test_overrides_combine_with_template_identity() -> None:
    """Overriding message/detail/suggestion leaves identity from the template."""
    err = create_error(
        "PARAM_INVALID",
        message="m1",
        detail="d1",
        suggestion="s1",
    )
    assert err.message == "m1"
    assert err.detail == "d1"
    assert err.suggestion == "s1"
    assert err.code == "PARAM_INVALID"
    assert err.category.value == "VALIDATION"
    assert err.http_status == 400
    assert err.retryable is False


def test_unknown_code_still_degrades_to_internal_error() -> None:
    """Unknown code degrades to INTERNAL_ERROR (no Area-A regression)."""
    registry = ErrorRegistry()
    err = registry.create("DEFINITELY_NOT_A_REAL_CODE_XYZ")
    assert isinstance(err, AELError)
    assert err.code == "INTERNAL_ERROR"


def test_unknown_code_with_message_override_keeps_override() -> None:
    """Override precedence applies on top of the INTERNAL_ERROR fallback."""
    registry = ErrorRegistry()
    err = registry.create(
        "DEFINITELY_NOT_A_REAL_CODE_XYZ",
        context={"message": "explicit fallback msg"},
    )
    assert err.code == "INTERNAL_ERROR"
    assert err.message == "explicit fallback msg"
    # The unknown-code preservation in detail is an Area-A invariant; an
    # explicit detail override is not passed here, so detail records the code.
    assert "DEFINITELY_NOT_A_REAL_CODE_XYZ" in (err.detail or "")

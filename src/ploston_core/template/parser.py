"""Template parsing utilities."""

import re
from typing import Any

# Regex to find {{ }} expressions
TEMPLATE_PATTERN = re.compile(r"\{\{(.+?)\}\}")


def extract_templates(text: str) -> list[str]:
    """Extract all {{ }} template expressions from text.

    Args:
        text: Text to search

    Returns:
        List of template expressions (without {{ }})
    """
    return [match.group(1).strip() for match in TEMPLATE_PATTERN.finditer(text)]


def has_templates(text: str) -> bool:
    """Check if text contains any {{ }} templates.

    Args:
        text: Text to check

    Returns:
        True if templates found
    """
    return bool(TEMPLATE_PATTERN.search(text))


def is_pure_template(text: str) -> bool:
    """Check if text is entirely a single template.

    E.g., "{{ inputs.x }}" is pure, "Hello {{ name }}" is not.

    Args:
        text: Text to check

    Returns:
        True if text is a single template with no surrounding text
    """
    text = text.strip()
    if not text.startswith("{{") or not text.endswith("}}"):
        return False

    # Check if there's only one template and it spans the entire string
    templates = extract_templates(text)
    if len(templates) != 1:
        return False

    # Reconstruct and compare
    reconstructed = "{{ " + templates[0] + " }}"
    return text == reconstructed or text == "{{" + templates[0] + "}}"


def extract_all_references(value: Any) -> list[str]:
    """Extract all variable references from a value (recursively).

    Args:
        value: Value to extract from (can be str, dict, list, or primitive)

    Returns:
        List of variable references (e.g., ["inputs.url", "steps.fetch.output"])
    """
    references: list[str] = []

    if isinstance(value, str):
        templates = extract_templates(value)
        for template in templates:
            # Extract the variable part (before any filter)
            var_part = template.split("|")[0].strip()
            references.append(var_part)

    elif isinstance(value, dict):
        for v in value.values():
            references.extend(extract_all_references(v))

    elif isinstance(value, list):
        for item in value:
            references.extend(extract_all_references(item))

    return references


def validate_syntax(text: str) -> list[str]:
    """Validate template syntax without rendering.

    Args:
        text: Text to validate

    Returns:
        List of error messages (empty if valid)
    """
    errors: list[str] = []

    templates = extract_templates(text)
    for template in templates:
        # Check for disallowed patterns
        if "(" in template and ")" in template:
            # Check if it's a filter call (allowed) or function call (not allowed)
            parts = template.split("|")
            if len(parts) > 1:
                # Has filters - check filter syntax
                for part in parts[1:]:
                    filter_part = part.strip()
                    if not filter_part:
                        errors.append(f"Empty filter in template: {template}")
            else:
                # No filters but has parentheses - likely function call
                if "(" in parts[0]:
                    errors.append(f"Function calls not supported: {template}")

        # Check for arithmetic operators
        var_part = template.split("|")[0].strip()
        if any(op in var_part for op in ["+", "-", "*", "/", "%", "**"]):
            errors.append(f"Arithmetic expressions not supported: {template}")

        # Check for control flow - use word boundaries to avoid false positives
        # (e.g., "transform" contains "for" but is not control flow)
        import re

        for kw in ["if", "for", "while", "import"]:
            # Match keyword as a whole word (not part of another word)
            if re.search(rf"\b{kw}\b", template):
                errors.append(f"Control flow not supported: {template}")
                break

    return errors

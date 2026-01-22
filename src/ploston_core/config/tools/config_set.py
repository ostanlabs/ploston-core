"""config_set tool handler - stage configuration changes."""

import re
from typing import Any

from ploston_core.config import StagedConfig


# Patterns that might indicate plaintext secrets
SECRET_PATTERNS = [
    (r"password", "password"),
    (r"secret", "secret"),
    (r"api[_-]?key", "API key"),
    (r"token", "token"),
    (r"credential", "credential"),
]


def detect_plaintext_secrets(path: str, value: Any) -> list[dict[str, str]]:
    """Detect potential plaintext secrets in config values.

    Args:
        path: Config path being set
        value: Value being set

    Returns:
        List of warnings about potential secrets
    """
    warnings = []

    # Check if path suggests a secret
    path_lower = path.lower()
    for pattern, name in SECRET_PATTERNS:
        if re.search(pattern, path_lower):
            # Check if value looks like plaintext (not env var reference)
            if isinstance(value, str) and not value.startswith("${"):
                warnings.append({
                    "path": path,
                    "warning": f"Potential plaintext {name} detected",
                    "suggestion": f"Consider using environment variable: ${{{path.upper().replace('.', '_')}}}",
                })
            break

    return warnings


async def handle_config_set(
    arguments: dict[str, Any],
    staged_config: StagedConfig,
) -> dict[str, Any]:
    """Handle config_set tool call.

    Args:
        arguments: Tool arguments with 'path' and 'value'
        staged_config: StagedConfig instance

    Returns:
        Result with staging confirmation and validation
    """
    path = arguments.get("path")
    value = arguments.get("value")

    if not path:
        return {
            "staged": False,
            "error": "path is required",
        }

    # Stage the change
    staged_config.set(path, value)

    # Validate the merged config
    validation_result = staged_config.validate()

    # Convert ValidationIssue objects to dicts
    errors = [{"path": e.path, "error": e.message} for e in validation_result.errors]
    validation_warnings = [{"path": w.path, "warning": w.message} for w in validation_result.warnings]

    # Check for plaintext secrets
    warnings = detect_plaintext_secrets(path, value)
    warnings.extend(validation_warnings)

    return {
        "staged": True,
        "path": path,
        "validation": {
            "valid": validation_result.valid,
            "errors": errors,
            "warnings": warnings,
        },
    }

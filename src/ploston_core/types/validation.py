"""Shared validation types for AEL."""

from dataclasses import dataclass, field


@dataclass
class ValidationIssue:
    """Single validation issue (error or warning).

    Used by:
    - ConfigLoader (config validation)
    - WorkflowRegistry (workflow validation)
    """

    path: str  # e.g., "steps[0].tool" or "tools.mcp_servers.x"
    message: str  # Human-readable description
    severity: str = "error"  # "error" | "warning"
    line: int | None = None  # Line number in source file (if available)


@dataclass
class ValidationResult:
    """Result of validation (config or workflow).

    Used by:
    - ConfigLoader.validate()
    - WorkflowValidator.validate()
    - CLI validate command
    """

    valid: bool
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Ensure valid is False if there are errors."""
        if self.errors:
            self.valid = False

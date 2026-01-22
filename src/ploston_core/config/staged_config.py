"""Staged configuration for in-memory config changes."""

import dataclasses
import difflib
import re
from pathlib import Path
from typing import Any

import yaml

from ploston_core.types import ValidationIssue, ValidationResult

from .loader import ConfigLoader, deep_merge
from .models import AELConfig


# Patterns that look like secrets (should use ${VAR} instead)
SECRET_PATTERNS = [
    r"^ghp_[a-zA-Z0-9]{36}$",  # GitHub personal access token
    r"^sk-[a-zA-Z0-9]{48}$",  # OpenAI API key
    r"^xoxb-[a-zA-Z0-9-]+$",  # Slack bot token
    r"^[a-zA-Z0-9]{32,}$",  # Long alphanumeric (potential secret)
]


class StagedConfig:
    """
    In-memory buffer for configuration changes.

    Changes are staged here, then written atomically by config_done().
    Preserves ${VAR} syntax for environment variable references.
    """

    def __init__(self, config_loader: ConfigLoader):
        """Initialize staged config.

        Args:
            config_loader: ConfigLoader to get base config from
        """
        self._loader = config_loader
        self._base: dict[str, Any] = {}
        self._changes: dict[str, Any] = {}
        self._target_path: Path | None = None
        self._load_base()

    def _load_base(self) -> None:
        """Load current config as base, or use empty dict."""
        try:
            config = self._loader.get()
            self._base = self._config_to_dict(config)
        except Exception:
            # No config loaded yet - start with empty base
            self._base = {}

    def _config_to_dict(self, config: AELConfig) -> dict[str, Any]:
        """Convert AELConfig dataclass to dict.

        Args:
            config: AELConfig instance

        Returns:
            Dictionary representation
        """
        return dataclasses.asdict(config)

    def set(self, path: str, value: Any) -> None:
        """
        Stage a change at dot-notation path.

        Auto-creates parent paths if they don't exist.
        Value is stored as-is (${VAR} syntax preserved).

        Args:
            path: Dot-notation path (e.g., "mcp.servers.github.command")
            value: Value to set
        """
        keys = path.split(".")
        current = self._changes

        # Auto-create parent dicts
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            elif not isinstance(current[key], dict):
                # Overwrite non-dict with dict
                current[key] = {}
            current = current[key]

        current[keys[-1]] = value

    def get(self, path: str | None = None) -> Any:
        """Get value from merged config (base + changes).

        Args:
            path: Optional dot-notation path. If None, returns full config.

        Returns:
            Value at path, or None if not found
        """
        merged = self.get_merged()
        if path is None:
            return merged

        keys = path.split(".")
        current = merged
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return None
        return current

    def get_merged(self) -> dict[str, Any]:
        """Return base config with staged changes applied.

        Returns:
            Merged configuration dictionary
        """
        return deep_merge(self._base, self._changes)

    def get_diff(self) -> str:
        """Return unified diff between base and merged.

        Returns:
            Unified diff string
        """
        base_yaml = yaml.dump(self._base, default_flow_style=False, sort_keys=False)
        merged_yaml = yaml.dump(self.get_merged(), default_flow_style=False, sort_keys=False)

        diff = difflib.unified_diff(
            base_yaml.splitlines(keepends=True),
            merged_yaml.splitlines(keepends=True),
            fromfile="current",
            tofile="staged",
        )
        return "".join(diff)

    def validate(self) -> ValidationResult:
        """
        Validate merged config.

        Checks:
        - Required fields present
        - Type correctness
        - Enum values valid
        - Warns on ${VAR} if env var not set
        - Warns on plaintext that looks like secrets
        - Warns on incomplete MCP server definitions

        Returns:
            ValidationResult with errors and warnings
        """
        merged = self.get_merged()

        # Get base validation from loader
        result = self._loader.validate(merged)

        # Add additional warnings for staged config
        additional_warnings = self._check_secrets(merged)
        additional_warnings.extend(self._check_incomplete_mcp_servers(merged))

        # Combine warnings
        all_warnings = list(result.warnings) + additional_warnings

        return ValidationResult(
            valid=result.valid and len(result.errors) == 0,
            errors=result.errors,
            warnings=all_warnings,
        )

    def _check_secrets(
        self, data: dict[str, Any], path: str = ""
    ) -> list[ValidationIssue]:
        """Check for plaintext values that look like secrets.

        Args:
            data: Config data to check
            path: Current path prefix

        Returns:
            List of warning issues
        """
        warnings: list[ValidationIssue] = []

        for key, value in data.items():
            current_path = f"{path}.{key}" if path else key

            if isinstance(value, dict):
                warnings.extend(self._check_secrets(value, current_path))
            elif isinstance(value, str):
                # Skip if already using ${VAR} syntax
                if "${" in value:
                    continue

                # Check against secret patterns
                for pattern in SECRET_PATTERNS:
                    if re.match(pattern, value):
                        warnings.append(
                            ValidationIssue(
                                path=current_path,
                                message=f"Value looks like a secret. Consider using ${{VAR}} syntax.",
                                severity="warning",
                            )
                        )
                        break

        return warnings

    def _check_incomplete_mcp_servers(
        self, data: dict[str, Any]
    ) -> list[ValidationIssue]:
        """Check for incomplete MCP server definitions.

        Args:
            data: Config data to check

        Returns:
            List of warning issues
        """
        warnings: list[ValidationIssue] = []

        mcp = data.get("mcp", {})
        servers = mcp.get("servers", {})

        for name, server in servers.items():
            if not isinstance(server, dict):
                continue

            # Check for required fields
            if "command" not in server:
                warnings.append(
                    ValidationIssue(
                        path=f"mcp.servers.{name}",
                        message=f"MCP server '{name}' missing 'command' field",
                        severity="warning",
                    )
                )

        return warnings

    def get_full_config_with_defaults(self) -> dict[str, Any]:
        """
        Return complete config with all defaults filled in.

        Used by config_done to write verbose config file.

        Returns:
            Full configuration with defaults
        """
        merged = self.get_merged()
        # Load as AELConfig (applies dataclass defaults)
        config = self._loader._dict_to_config(merged)
        # Convert back to dict (now has all defaults)
        return self._config_to_dict(config)

    def set_target_path(self, path: Path | str) -> None:
        """Set where config_done will write.

        Args:
            path: Target file path
        """
        self._target_path = Path(path) if isinstance(path, str) else path

    @property
    def target_path(self) -> Path:
        """Get write target, defaulting to ./ael-config.yaml

        Returns:
            Target path for config file
        """
        if self._target_path:
            return self._target_path
        return Path("ael-config.yaml")

    def write(self) -> Path:
        """
        Write full config (with defaults) to target path.

        Preserves ${VAR} syntax - does not resolve env vars.

        Returns:
            Path written to
        """
        full_config = self.get_full_config_with_defaults()

        # Ensure parent directory exists
        self.target_path.parent.mkdir(parents=True, exist_ok=True)

        with self.target_path.open("w") as f:
            f.write("# ael-config.yaml - Generated by AEL\n")
            f.write("# All values shown, defaults included for reference\n\n")
            yaml.dump(full_config, f, default_flow_style=False, sort_keys=False)

        return self.target_path

    def clear(self) -> None:
        """Discard all staged changes."""
        self._changes = {}

    def has_changes(self) -> bool:
        """Check if there are any staged changes.

        Returns:
            True if there are staged changes
        """
        return len(self._changes) > 0

    @property
    def changes(self) -> dict[str, Any]:
        """Get the staged changes dictionary.

        Returns:
            Dictionary of staged changes
        """
        return self._changes.copy()

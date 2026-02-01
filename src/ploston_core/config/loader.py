"""AEL Configuration loader."""

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from ploston_core.errors import create_error
from ploston_core.types import ValidationIssue, ValidationResult

from .models import AELConfig


def resolve_env_vars(value: str) -> str:
    """Resolve environment variable references in string.

    Supports:
    - ${VAR} - Required, error if not set
    - ${VAR:-default} - With default value
    - ${VAR:?error message} - Required with custom error

    Args:
        value: String with potential env var references

    Returns:
        String with env vars resolved

    Raises:
        AELError: If required var not set
    """
    # Pattern: ${VAR}, ${VAR:-default}, ${VAR:?error}
    pattern = r"\$\{([^}:]+)(?::([?-])([^}]*))?\}"

    def replacer(match: re.Match[str]) -> str:
        var_name = match.group(1)
        operator = match.group(2)  # '-' or '?' or None
        operand = match.group(3)  # default value or error message

        env_value = os.environ.get(var_name)

        if env_value is not None:
            return env_value

        # Variable not set
        if operator == "-":
            # Use default value
            return operand or ""
        elif operator == "?":
            # Required with custom error
            error_msg = operand or f"Required environment variable {var_name} not set"
            raise create_error("CONFIG_INVALID", detail=error_msg)
        else:
            # Required without default
            raise create_error(
                "CONFIG_INVALID",
                detail=f"Required environment variable {var_name} not set",
            )

    return re.sub(pattern, replacer, value)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dictionaries.

    Args:
        base: Base dictionary
        override: Override dictionary

    Returns:
        Merged dictionary
    """
    result = base.copy()

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value

    return result


def _resolve_env_vars_recursive(data: Any) -> Any:
    """Recursively resolve env vars in data structure.

    Args:
        data: Data structure (dict, list, str, etc.)

    Returns:
        Data with env vars resolved
    """
    if isinstance(data, dict):
        return {k: _resolve_env_vars_recursive(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_resolve_env_vars_recursive(item) for item in data]
    elif isinstance(data, str):
        return resolve_env_vars(data)
    else:
        return data


class ConfigLoader:
    """Load and validate AEL configuration."""

    def __init__(self, logger: Any = None):
        """Initialize config loader.

        Args:
            logger: Optional AELLogger instance
        """
        self._config: AELConfig | None = None
        self._config_path: Path | None = None
        self._logger = logger
        self._change_callbacks: list[Callable[[AELConfig], None]] = []

    def load(self, path: str | Path | None = None, use_defaults: bool = True) -> AELConfig:
        """Load configuration from file.

        Resolution order if path not specified:
        1. PLOSTON_CONFIG_PATH or AEL_CONFIG_PATH environment variable
        2. ./ael-config.yaml
        3. ~/.ael/config.yaml
        4. If use_defaults=True and no file found, use default configuration

        Args:
            path: Optional path to config file
            use_defaults: If True, use default config when no file found (default: True)

        Returns:
            Loaded AELConfig instance

        Raises:
            AELError: If file not found (when use_defaults=False) or invalid
        """
        # Resolve config path
        if path is None:
            path = self._resolve_config_path()

        config_path = Path(path)

        if not config_path.exists():
            if use_defaults:
                # Use default configuration when no file found
                if self._logger:
                    self._logger.info("No config file found, using default configuration")
                return self.load_defaults()
            raise create_error(
                "CONFIG_INVALID",
                detail=f"Configuration file not found: {config_path}",
            )

        # Load YAML
        try:
            with config_path.open() as f:
                data = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise create_error(
                "CONFIG_INVALID",
                detail=f"Invalid YAML in config file: {e}",
            ) from e

        # Resolve environment variables
        data = _resolve_env_vars_recursive(data)

        # Load from dict
        return self.load_from_dict(data, config_path)

    def load_defaults(self) -> AELConfig:
        """Load default configuration without a file.

        Creates an AELConfig with all default values from the dataclass.

        Returns:
            AELConfig with default values
        """
        return self.load_from_dict({})

    def load_from_dict(self, data: dict[str, Any], config_path: Path | None = None) -> AELConfig:
        """Load configuration from dictionary.

        Args:
            data: Configuration dictionary
            config_path: Optional path to config file (for tracking)

        Returns:
            Loaded AELConfig instance

        Raises:
            AELError: If configuration is invalid
        """
        # Validate
        validation = self.validate(data)
        if not validation.valid:
            error_messages = [f"- {issue.message}" for issue in validation.errors]
            raise create_error(
                "CONFIG_INVALID",
                detail="Configuration validation failed:\n" + "\n".join(error_messages),
            )

        # Convert to AELConfig
        try:
            config = self._dict_to_config(data)
        except Exception as e:
            raise create_error(
                "CONFIG_INVALID",
                detail=f"Failed to parse configuration: {e}",
            ) from e

        self._config = config
        self._config_path = config_path

        if self._logger:
            self._logger._log("INFO", "config", "Configuration loaded successfully")

        return config

    def validate(self, data: dict[str, Any]) -> ValidationResult:
        """Validate config data without loading.

        Args:
            data: Configuration dictionary

        Returns:
            ValidationResult with errors and warnings
        """
        errors: list[ValidationIssue] = []
        warnings: list[ValidationIssue] = []

        # Basic validation - check for unknown top-level keys
        valid_keys = {
            "server",
            "mcp",
            "tools",
            "workflows",
            "execution",
            "python_exec",
            "logging",
            "plugins",
            "security",
            "telemetry",
        }

        for key in data:
            if key not in valid_keys:
                warnings.append(
                    ValidationIssue(
                        path=key,
                        message=f"Unknown configuration key: {key}",
                        severity="warning",
                    )
                )

        # Validate server config
        if "server" in data:
            server = data["server"]
            if not isinstance(server, dict):
                errors.append(
                    ValidationIssue(
                        path="server",
                        message="server must be a dictionary",
                        severity="error",
                    )
                )
            else:
                if "port" in server and not isinstance(server["port"], int):
                    errors.append(
                        ValidationIssue(
                            path="server.port",
                            message="port must be an integer",
                            severity="error",
                        )
                    )

        # Validate execution config
        if "execution" in data:
            execution = data["execution"]
            if isinstance(execution, dict):
                for timeout_key in ["default_timeout", "step_timeout"]:
                    if timeout_key in execution:
                        value = execution[timeout_key]
                        if not isinstance(value, int) or value <= 0:
                            errors.append(
                                ValidationIssue(
                                    path=f"execution.{timeout_key}",
                                    message=f"{timeout_key} must be a positive integer",
                                    severity="error",
                                )
                            )

        return ValidationResult(valid=True, errors=errors, warnings=warnings)

    def get(self) -> AELConfig:
        """Get current configuration.

        Returns:
            Current AELConfig instance

        Raises:
            AELError: If configuration not loaded
        """
        if self._config is None:
            raise create_error("CONFIG_INVALID", detail="Configuration not loaded")
        return self._config

    def reload(self) -> AELConfig:
        """Reload configuration from file.

        Only reloads if file has changed.
        Notifies registered callbacks on change.

        Returns:
            Reloaded AELConfig instance

        Raises:
            AELError: If no config path set or reload fails
        """
        if self._config_path is None:
            raise create_error("CONFIG_INVALID", detail="No config path set, cannot reload")

        # Load new config
        new_config = self.load(self._config_path)

        # Notify callbacks
        for callback in self._change_callbacks:
            try:
                callback(new_config)
            except Exception as e:
                if self._logger:
                    self._logger._log(
                        "ERROR",
                        "config",
                        f"Config change callback failed: {e}",
                    )

        return new_config

    def on_change(self, callback: Callable[[AELConfig], None]) -> None:
        """Register callback for config changes.

        Args:
            callback: Function to call when config changes
        """
        self._change_callbacks.append(callback)

    def start_watching(self) -> None:
        """Start file watcher for hot-reload.

        Note: Not implemented yet - placeholder for future enhancement.
        """
        if self._logger:
            self._logger._log("WARN", "config", "Hot-reload watching not yet implemented")

    def stop_watching(self) -> None:
        """Stop file watcher.

        Note: Not implemented yet - placeholder for future enhancement.
        """
        pass

    def _resolve_config_path(self) -> Path:
        """Resolve config file path using resolution order.

        Returns:
            Path to config file

        Raises:
            AELError: If no config file found
        """
        # 1. PLOSTON_CONFIG_PATH or AEL_CONFIG_PATH environment variable
        env_path = os.environ.get("PLOSTON_CONFIG_PATH") or os.environ.get("AEL_CONFIG_PATH")
        if env_path:
            return Path(env_path)

        # 2. ./ael-config.yaml
        local_path = Path("ael-config.yaml")
        if local_path.exists():
            return local_path

        # 3. ~/.ael/config.yaml
        home_path = Path.home() / ".ael" / "config.yaml"
        if home_path.exists():
            return home_path

        # Not found - use local path as default
        return local_path

    def _dict_to_config(self, data: dict[str, Any]) -> AELConfig:
        """Convert dictionary to AELConfig.

        Args:
            data: Configuration dictionary

        Returns:
            AELConfig instance
        """
        # Use dataclass defaults for missing sections
        from dataclasses import fields

        kwargs: dict[str, Any] = {}

        for field in fields(AELConfig):
            if field.name in data:
                # Recursively convert nested dicts to dataclasses
                kwargs[field.name] = self._convert_field(field.type, data[field.name])

        return AELConfig(**kwargs)

    def _convert_field(self, field_type: Any, value: Any) -> Any:
        """Convert field value to appropriate type.

        Args:
            field_type: Expected field type
            value: Value to convert

        Returns:
            Converted value
        """
        # Handle None
        if value is None:
            return None

        # Get origin type (for generics like list[str])
        import typing

        origin = typing.get_origin(field_type)

        # Handle lists
        if origin is list:
            if not isinstance(value, list):
                return value
            args = typing.get_args(field_type)
            if args:
                return [self._convert_field(args[0], item) for item in value]
            return value

        # Handle dicts
        if origin is dict:
            if not isinstance(value, dict):
                return value
            args = typing.get_args(field_type)
            if args and len(args) == 2:
                # dict[KeyType, ValueType] - convert values
                value_type = args[1]
                return {k: self._convert_field(value_type, v) for k, v in value.items()}
            return value

        # Handle dataclasses
        if hasattr(field_type, "__dataclass_fields__"):
            if isinstance(value, dict):
                from dataclasses import fields

                kwargs = {}
                for f in fields(field_type):
                    if f.name in value:
                        kwargs[f.name] = self._convert_field(f.type, value[f.name])
                return field_type(**kwargs)
            return value

        # Handle enums
        if hasattr(field_type, "__mro__") and any(
            base.__name__ == "Enum" for base in field_type.__mro__
        ):
            if isinstance(value, str):
                return field_type(value)
            return value

        # Return as-is for primitives
        return value


# Convenience singleton
_default_loader: ConfigLoader | None = None


def get_config_loader() -> ConfigLoader:
    """Get default config loader singleton.

    Returns:
        Default ConfigLoader instance
    """
    global _default_loader  # noqa: PLW0603
    if _default_loader is None:
        _default_loader = ConfigLoader()
    return _default_loader


def load_config(path: str | Path | None = None) -> AELConfig:
    """Convenience function to load config.

    Args:
        path: Optional path to config file

    Returns:
        Loaded AELConfig instance
    """
    return get_config_loader().load(path)

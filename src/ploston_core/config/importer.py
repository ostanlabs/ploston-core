"""Config Importer - Convert configurations from Claude Desktop and Cursor to Ploston format."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .secrets import SecretDetector


@dataclass
class SecretConversion:
    """Record of a secret that was converted."""

    server: str
    field: str  # e.g., "env.GITHUB_TOKEN"
    original: str  # Masked value
    converted_to: str  # e.g., "${GITHUB_TOKEN}"
    action_required: str  # e.g., "Set GITHUB_TOKEN environment variable"


@dataclass
class ImportError:
    """Error during import."""

    server: str
    error: str


@dataclass
class ImportResult:
    """Result of config import."""

    servers: dict[str, dict[str, Any]] = field(default_factory=dict)  # Converted server configs
    imported: list[str] = field(default_factory=list)  # Successfully imported server names
    skipped: list[str] = field(default_factory=list)  # Skipped server names
    secrets_detected: list[SecretConversion] = field(default_factory=list)
    errors: list[ImportError] = field(default_factory=list)


class ConfigImporter:
    """
    Convert configurations from Claude Desktop and Cursor to Ploston format.

    Handles:
    - Structure conversion (mcpServers -> tools.mcp_servers)
    - Secret detection and conversion to ${VAR} syntax
    - Transport type inference
    """

    def __init__(self, secret_detector: SecretDetector | None = None):
        """Initialize importer.

        Args:
            secret_detector: SecretDetector instance (creates one if not provided)
        """
        self.secret_detector = secret_detector or SecretDetector()

    def import_config(
        self,
        source: Literal["claude_desktop", "cursor"],
        config: dict[str, Any],
        convert_secrets: bool = True,
        secret_mappings: dict[str, str] | None = None,
        skip_servers: list[str] | None = None,
    ) -> ImportResult:
        """
        Convert source config to Ploston format.

        Args:
            source: Source format identifier ("claude_desktop" or "cursor")
            config: The mcpServers object from source config file
            convert_secrets: Whether to convert literal secrets to ${VAR} syntax
            secret_mappings: Manual mappings from literal value to env var name
            skip_servers: Server names to skip during import

        Returns:
            ImportResult with converted servers and detected secrets
        """
        result = ImportResult()
        secret_mappings = secret_mappings or {}
        skip_servers = skip_servers or []

        # Both Claude Desktop and Cursor use the same format
        # The config passed in should be the mcpServers object
        for name, server_config in config.items():
            # Skip if in skip list
            if name in skip_servers:
                result.skipped.append(name)
                continue

            try:
                converted, secrets = self._convert_server(
                    name=name,
                    server_config=server_config,
                    convert_secrets=convert_secrets,
                    secret_mappings=secret_mappings,
                )
                result.servers[name] = converted
                result.imported.append(name)
                result.secrets_detected.extend(secrets)
            except Exception as e:
                result.errors.append(ImportError(server=name, error=str(e)))

        return result

    def _convert_server(
        self,
        name: str,
        server_config: dict[str, Any],
        convert_secrets: bool,
        secret_mappings: dict[str, str],
    ) -> tuple[dict[str, Any], list[SecretConversion]]:
        """
        Convert a single server definition.

        Args:
            name: Server name
            server_config: Source server configuration
            convert_secrets: Whether to convert secrets
            secret_mappings: Manual secret mappings

        Returns:
            Tuple of (converted config, list of secret conversions)
        """
        secrets_detected: list[SecretConversion] = []
        converted: dict[str, Any] = {}

        # Copy command if present
        if "command" in server_config:
            converted["command"] = server_config["command"]

        # Copy args if present
        if "args" in server_config:
            converted["args"] = list(server_config["args"])

        # Infer transport type
        if "command" in server_config:
            converted["transport"] = "stdio"
        elif "url" in server_config:
            converted["transport"] = "http"
            converted["url"] = server_config["url"]
        else:
            # Default to stdio if command is present
            converted["transport"] = "stdio"

        # Process environment variables
        if "env" in server_config:
            converted["env"] = {}
            for key, value in server_config["env"].items():
                if not isinstance(value, str):
                    converted["env"][key] = value
                    continue

                # Check if value is already using ${VAR} syntax
                if "${" in value:
                    converted["env"][key] = value
                    continue

                # Check manual mappings first
                if value in secret_mappings:
                    env_var = secret_mappings[value]
                    converted["env"][key] = f"${{{env_var}}}"
                    secrets_detected.append(
                        SecretConversion(
                            server=name,
                            field=f"env.{key}",
                            original=self.secret_detector.mask_value(value),
                            converted_to=f"${{{env_var}}}",
                            action_required=f"Set {env_var} environment variable",
                        )
                    )
                    continue

                # Try to detect secrets
                if convert_secrets:
                    detection = self.secret_detector.detect(key, value)
                    if detection:
                        env_var = detection.suggested_env_var
                        converted["env"][key] = f"${{{env_var}}}"
                        secrets_detected.append(
                            SecretConversion(
                                server=name,
                                field=f"env.{key}",
                                original=detection.masked_value,
                                converted_to=f"${{{env_var}}}",
                                action_required=f"Set {env_var} environment variable",
                            )
                        )
                        continue

                # No conversion needed
                converted["env"][key] = value

        # Copy timeout if present
        if "timeout" in server_config:
            converted["timeout"] = server_config["timeout"]

        return converted, secrets_detected

    def get_source_config_path(self, source: Literal["claude_desktop", "cursor"]) -> str | None:
        """
        Get the default config file path for a source on the current platform.

        Args:
            source: Source identifier

        Returns:
            Expanded path to config file, or None if not found
        """
        import os
        import platform

        system = platform.system().lower()

        paths = {
            "claude_desktop": {
                "darwin": "~/Library/Application Support/Claude/claude_desktop_config.json",
                "windows": "%APPDATA%\\Claude\\claude_desktop_config.json",
                "linux": "~/.config/Claude/claude_desktop_config.json",
            },
            "cursor": {
                "darwin": "~/.cursor/mcp.json",
                "windows": "%USERPROFILE%\\.cursor\\mcp.json",
                "linux": "~/.cursor/mcp.json",
            },
        }

        platform_key = (
            "darwin" if system == "darwin" else ("windows" if system == "windows" else "linux")
        )

        if source in paths and platform_key in paths[source]:
            path = paths[source][platform_key]
            # Expand ~ and environment variables
            path = os.path.expanduser(path)
            path = os.path.expandvars(path)
            return path

        return None

    def load_source_config(
        self, source: Literal["claude_desktop", "cursor"], path: str | None = None
    ) -> dict[str, Any] | None:
        """
        Load and parse a source config file.

        Args:
            source: Source identifier
            path: Optional custom path (uses default if not provided)

        Returns:
            The mcpServers object from the config, or None if not found
        """
        import json
        import os

        config_path = path or self.get_source_config_path(source)
        if not config_path or not os.path.exists(config_path):
            return None

        try:
            with open(config_path) as f:
                data = json.load(f)

            # Extract mcpServers
            return data.get("mcpServers", {})
        except Exception:
            return None

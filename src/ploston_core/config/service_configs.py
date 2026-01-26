"""Service configuration builders for Ploston.

This module provides functions to extract and build configuration
for dependent services (like native-tools) from the main Ploston config.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_native_tools_config(ploston_config: dict[str, Any]) -> dict[str, Any]:
    """Build native-tools configuration from Ploston config.

    Extracts relevant configuration for native-tools from the main
    Ploston configuration. Uses ${VAR} syntax for secrets that should
    be resolved by native-tools from its environment.

    Args:
        ploston_config: The full Ploston configuration dictionary

    Returns:
        Configuration dictionary for native-tools
    """
    tools_config = ploston_config.get("tools", {})
    native_tools_config = tools_config.get("native_tools", {})

    # Build the native-tools config structure
    config: dict[str, Any] = {
        "kafka": _build_kafka_config(native_tools_config),
        "firecrawl": _build_firecrawl_config(native_tools_config),
        "ollama": _build_ollama_config(native_tools_config),
        "filesystem": _build_filesystem_config(native_tools_config, ploston_config),
        "network": _build_network_config(native_tools_config),
        "data": _build_data_config(native_tools_config),
    }

    return config


def _build_kafka_config(native_tools_config: dict[str, Any]) -> dict[str, Any]:
    """Build Kafka configuration."""
    kafka = native_tools_config.get("kafka", {})
    return {
        "enabled": kafka.get("enabled", False),
        "bootstrap_servers": kafka.get(
            "bootstrap_servers", "${KAFKA_BOOTSTRAP_SERVERS:-}"
        ),
        "producer": kafka.get("producer", {"acks": "all", "retries": 3}),
        "consumer": kafka.get("consumer", {"auto_offset_reset": "earliest"}),
        "security_protocol": kafka.get("security_protocol", "PLAINTEXT"),
        "sasl_mechanism": kafka.get("sasl_mechanism"),
        "sasl_username": kafka.get("sasl_username"),
        "sasl_password": kafka.get("sasl_password", "${KAFKA_SASL_PASSWORD:-}"),
    }


def _build_firecrawl_config(native_tools_config: dict[str, Any]) -> dict[str, Any]:
    """Build Firecrawl configuration."""
    firecrawl = native_tools_config.get("firecrawl", {})
    return {
        "enabled": firecrawl.get("enabled", False),
        "base_url": firecrawl.get("base_url", "${FIRECRAWL_BASE_URL:-}"),
        "api_key": firecrawl.get("api_key", "${FIRECRAWL_API_KEY:-}"),
        "timeout": firecrawl.get("timeout", 30),
        "max_retries": firecrawl.get("max_retries", 3),
    }


def _build_ollama_config(native_tools_config: dict[str, Any]) -> dict[str, Any]:
    """Build Ollama configuration."""
    ollama = native_tools_config.get("ollama", {})
    return {
        "enabled": ollama.get("enabled", False),
        "host": ollama.get("host", "${OLLAMA_HOST:-http://localhost:11434}"),
        "default_model": ollama.get("default_model", "llama3.2"),
        "timeout": ollama.get("timeout", 120),
    }


def _build_filesystem_config(
    native_tools_config: dict[str, Any], ploston_config: dict[str, Any]
) -> dict[str, Any]:
    """Build filesystem configuration."""
    filesystem = native_tools_config.get("filesystem", {})
    return {
        "enabled": filesystem.get("enabled", True),
        "workspace_dir": filesystem.get(
            "workspace_dir",
            ploston_config.get("workspace_dir", "${WORKSPACE_DIR:-/workspace}"),
        ),
        "allowed_paths": filesystem.get("allowed_paths", []),
        "denied_paths": filesystem.get("denied_paths", []),
        "max_file_size": filesystem.get("max_file_size", 10 * 1024 * 1024),  # 10MB
    }


def _build_network_config(native_tools_config: dict[str, Any]) -> dict[str, Any]:
    """Build network tools configuration."""
    network = native_tools_config.get("network", {})
    return {
        "enabled": network.get("enabled", True),
        "timeout": network.get("timeout", 30),
        "max_retries": network.get("max_retries", 3),
        "allowed_hosts": network.get("allowed_hosts", []),
        "denied_hosts": network.get("denied_hosts", []),
    }


def _build_data_config(native_tools_config: dict[str, Any]) -> dict[str, Any]:
    """Build data tools configuration."""
    data = native_tools_config.get("data", {})
    return {
        "enabled": data.get("enabled", True),
        "max_data_size": data.get("max_data_size", 50 * 1024 * 1024),  # 50MB
    }


def merge_configs(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two configuration dictionaries.

    Values from override take precedence over base.

    Args:
        base: Base configuration
        override: Override configuration

    Returns:
        Merged configuration
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_configs(result[key], value)
        else:
            result[key] = value
    return result

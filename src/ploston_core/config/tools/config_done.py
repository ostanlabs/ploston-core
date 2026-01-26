"""config_done tool handler - apply config and switch to running mode."""

import logging
from typing import Any

from ploston_core.config import ConfigLoader, Mode, StagedConfig
from ploston_core.config.redis_store import RedisConfigStore
from ploston_core.config.service_configs import build_native_tools_config

logger = logging.getLogger(__name__)


async def handle_config_done(
    arguments: dict[str, Any],
    staged_config: StagedConfig,
    config_loader: ConfigLoader,
    mode_manager: Any,
    mcp_manager: Any,
    write_location: str | None,
    redis_store: RedisConfigStore | None = None,
) -> dict[str, Any]:
    """Handle config_done tool call.

    Args:
        arguments: Tool arguments (none required)
        staged_config: StagedConfig instance
        config_loader: ConfigLoader instance
        mode_manager: ModeManager instance
        mcp_manager: MCPClientManager instance
        write_location: Target path for writing config
        redis_store: Optional RedisConfigStore for publishing config

    Returns:
        Success/failure result with capabilities or errors
    """
    # Step 1: Validate staged config
    validation_result = staged_config.validate()
    if not validation_result.valid:
        errors = [{"path": e.path, "error": e.message} for e in validation_result.errors]
        return {
            "success": False,
            "mode": "configuration",
            "errors": errors,
        }

    # Step 2: Get merged config
    merged_config = staged_config.get_merged()

    # Step 3: Try to connect to MCP servers
    mcp_results = {}
    errors = []

    # merged_config is a dict, not a dataclass
    mcp_config = merged_config.get("mcp", {})
    if mcp_manager and mcp_config:
        servers = mcp_config.get("servers", {}) or {}
        for server_name, server_config in servers.items():
            try:
                # Try to connect
                await mcp_manager.connect(server_name, server_config)
                tools = await mcp_manager.list_tools(server_name)
                mcp_results[server_name] = {
                    "status": "connected",
                    "tools": len(tools) if tools else 0,
                }
            except Exception as e:
                errors.append(
                    {
                        "path": f"mcp.servers.{server_name}",
                        "error": str(e),
                        "suggestion": "Check server command and environment variables",
                    }
                )
                mcp_results[server_name] = {
                    "status": "failed",
                    "error": str(e),
                }

    # If any MCP connection failed, stay in config mode
    if errors:
        return {
            "success": False,
            "mode": "configuration",
            "errors": errors,
            "partial_results": mcp_results,
        }

    # Step 4: Write config to file
    target_path = write_location or "./ael-config.yaml"
    try:
        staged_config.set_target_path(target_path)
        staged_config.write()
    except Exception as e:
        return {
            "success": False,
            "mode": "configuration",
            "errors": [
                {
                    "path": "(write)",
                    "error": f"Failed to write config: {e}",
                    "suggestion": "Check file permissions",
                }
            ],
        }

    # Step 5: Publish config to Redis (if connected)
    redis_published = False
    if redis_store and redis_store.connected:
        try:
            # Publish ploston config
            await redis_store.publish_config("ploston", merged_config)

            # Build and publish native-tools config
            native_tools_config = build_native_tools_config(merged_config)
            await redis_store.publish_config("native-tools", native_tools_config)

            # Set mode in Redis
            await redis_store.set_mode("RUNNING")

            redis_published = True
            logger.info("Published config to Redis")
        except Exception as e:
            logger.error(f"Failed to publish config to Redis: {e}")
            # Don't fail the whole operation - file was written successfully
            errors.append({
                "path": "(redis)",
                "error": f"Failed to publish to Redis: {e}",
                "suggestion": "Config was written to file but Redis publish failed",
            })

    # Step 6: Switch to running mode
    if mode_manager:
        mode_manager.set_mode(Mode.RUNNING)

    # Step 7: Clear staged changes
    staged_config.clear()

    # Calculate total tools
    total_tools = sum(r.get("tools", 0) for r in mcp_results.values())

    result = {
        "success": True,
        "mode": "running",
        "config_written_to": target_path,
        "redis_published": redis_published,
        "capabilities": {
            "workflows": [],  # Would be populated from workflow registry
            "mcp_servers": mcp_results,
            "total_tools": total_tools,
        },
    }

    # Include any non-fatal errors (like Redis publish failure)
    if errors:
        result["warnings"] = errors

    return result

"""stdio I/O helpers for MCP protocol."""

import asyncio
import json
import sys
from typing import Any


async def read_message() -> dict[str, Any] | None:
    """Read JSON-RPC message from stdin.

    Returns:
        Parsed JSON message or None if EOF
    """
    loop = asyncio.get_event_loop()

    try:
        # Read line from stdin asynchronously
        line = await loop.run_in_executor(None, sys.stdin.readline)

        if not line:
            return None

        # Parse JSON
        message: dict[str, Any] = json.loads(line.strip())
        return message

    except json.JSONDecodeError:
        # Return error message for malformed JSON
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {
                "code": -32700,
                "message": "Parse error: Invalid JSON",
            },
        }
    except Exception:
        return None


async def write_message(message: dict[str, Any]) -> None:
    """Write JSON-RPC message to stdout.

    Args:
        message: JSON-RPC message to write
    """
    loop = asyncio.get_event_loop()

    try:
        # Serialize to JSON
        line = json.dumps(message) + "\n"

        # Write to stdout asynchronously
        await loop.run_in_executor(None, sys.stdout.write, line)
        await loop.run_in_executor(None, sys.stdout.flush)

    except Exception:
        # Silently ignore write errors (client may have disconnected)
        pass

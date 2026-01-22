"""JSON-RPC protocol helpers for MCP communication."""

import json
from typing import Any


class JSONRPCMessage:
    """JSON-RPC 2.0 message builder and parser."""

    @staticmethod
    def request(method: str, params: dict[str, Any] | None = None, id: int = 1) -> dict[str, Any]:
        """Build a JSON-RPC request.

        Args:
            method: Method name (e.g., "initialize", "tools/list", "tools/call")
            params: Optional parameters
            id: Request ID

        Returns:
            JSON-RPC request dict
        """
        msg: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "id": id,
        }
        if params is not None:
            msg["params"] = params
        return msg

    @staticmethod
    def notification(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Build a JSON-RPC notification (no response expected).

        Args:
            method: Method name
            params: Optional parameters

        Returns:
            JSON-RPC notification dict
        """
        msg: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            msg["params"] = params
        return msg

    @staticmethod
    def success_response(id: int, result: Any) -> dict[str, Any]:
        """Build a JSON-RPC success response.

        Args:
            id: Request ID
            result: Result data

        Returns:
            JSON-RPC response dict
        """
        return {
            "jsonrpc": "2.0",
            "id": id,
            "result": result,
        }

    @staticmethod
    def error_response(id: int, code: int, message: str, data: Any = None) -> dict[str, Any]:
        """Build a JSON-RPC error response.

        Args:
            id: Request ID
            code: Error code
            message: Error message
            data: Optional error data

        Returns:
            JSON-RPC error response dict
        """
        error: dict[str, Any] = {
            "code": code,
            "message": message,
        }
        if data is not None:
            error["data"] = data

        return {
            "jsonrpc": "2.0",
            "id": id,
            "error": error,
        }

    @staticmethod
    def parse(message: str | bytes) -> dict[str, Any]:
        """Parse a JSON-RPC message.

        Args:
            message: JSON string or bytes

        Returns:
            Parsed message dict

        Raises:
            ValueError: If message is not valid JSON
        """
        if isinstance(message, bytes):
            message = message.decode("utf-8")
        parsed: dict[str, Any] = json.loads(message)
        return parsed

    @staticmethod
    def is_response(message: dict[str, Any]) -> bool:
        """Check if message is a response (has 'result' or 'error').

        Args:
            message: Parsed message dict

        Returns:
            True if response, False if request/notification
        """
        return "result" in message or "error" in message

    @staticmethod
    def is_error(message: dict[str, Any]) -> bool:
        """Check if message is an error response.

        Args:
            message: Parsed message dict

        Returns:
            True if error response
        """
        return "error" in message

    @staticmethod
    def get_result(message: dict[str, Any]) -> dict[str, Any]:
        """Extract result from response message.

        Args:
            message: Parsed response message

        Returns:
            Result data

        Raises:
            KeyError: If message has no result
        """
        result: dict[str, Any] = message["result"]
        return result

    @staticmethod
    def get_error(message: dict[str, Any]) -> dict[str, Any]:
        """Extract error from error response.

        Args:
            message: Parsed error response

        Returns:
            Error dict with code, message, data

        Raises:
            KeyError: If message has no error
        """
        error: dict[str, Any] = message["error"]
        return error

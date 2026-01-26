"""Property-based tests for MCP protocol.

Tests JSON-RPC message serialization, parsing, and protocol compliance.
"""

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ploston_core.mcp.protocol import JSONRPCMessage
from ploston_core.mcp.types import MCPCallResult, ServerStatus, ToolSchema
from ploston_core.types import ConnectionStatus

# =============================================================================
# Strategies for generating MCP data
# =============================================================================

# Valid method names
valid_method = st.from_regex(r'^[a-z][a-z0-9_/]{0,30}$', fullmatch=True)

# Valid request IDs
valid_id = st.integers(min_value=1, max_value=2**31)

# JSON-serializable values
json_primitives = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=100),
)

json_values = st.recursive(
    json_primitives,
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.dictionaries(
            st.from_regex(r'^[a-z][a-z0-9_]{0,10}$', fullmatch=True),
            children,
            max_size=5
        )
    ),
    max_leaves=20
)

# Valid params dict
valid_params = st.dictionaries(
    st.from_regex(r'^[a-z][a-z0-9_]{0,15}$', fullmatch=True),
    json_values,
    max_size=5
)


# =============================================================================
# Property Tests for JSON-RPC Request Building
# =============================================================================

@pytest.mark.property
class TestJSONRPCRequest:
    """Property tests for JSON-RPC request building."""

    @given(valid_method, valid_id)
    @settings(max_examples=50)
    def test_request_has_required_fields(self, method, req_id):
        """Requests must have jsonrpc, method, and id fields."""
        request = JSONRPCMessage.request(method, id=req_id)

        assert "jsonrpc" in request
        assert request["jsonrpc"] == "2.0"
        assert "method" in request
        assert request["method"] == method
        assert "id" in request
        assert request["id"] == req_id

    @given(valid_method, valid_params, valid_id)
    @settings(max_examples=50)
    def test_request_with_params(self, method, params, req_id):
        """Requests with params should include them."""
        request = JSONRPCMessage.request(method, params=params, id=req_id)

        assert "params" in request
        assert request["params"] == params

    @given(valid_method, valid_id)
    @settings(max_examples=30)
    def test_request_without_params(self, method, req_id):
        """Requests without params should not have params field."""
        request = JSONRPCMessage.request(method, params=None, id=req_id)

        assert "params" not in request

    @given(valid_method, valid_params, valid_id)
    @settings(max_examples=50)
    def test_request_is_json_serializable(self, method, params, req_id):
        """All requests must be JSON serializable."""
        request = JSONRPCMessage.request(method, params=params, id=req_id)

        # Should not raise
        json_str = json.dumps(request)
        parsed = json.loads(json_str)

        assert parsed == request


# =============================================================================
# Property Tests for JSON-RPC Notification Building
# =============================================================================

@pytest.mark.property
class TestJSONRPCNotification:
    """Property tests for JSON-RPC notification building."""

    @given(valid_method)
    @settings(max_examples=30)
    def test_notification_has_no_id(self, method):
        """Notifications must not have an id field."""
        notification = JSONRPCMessage.notification(method)

        assert "id" not in notification
        assert "jsonrpc" in notification
        assert notification["jsonrpc"] == "2.0"
        assert "method" in notification

    @given(valid_method, valid_params)
    @settings(max_examples=30)
    def test_notification_with_params(self, method, params):
        """Notifications with params should include them."""
        notification = JSONRPCMessage.notification(method, params=params)

        assert "params" in notification
        assert notification["params"] == params


# =============================================================================
# Property Tests for JSON-RPC Response Building
# =============================================================================

@pytest.mark.property
class TestJSONRPCResponse:
    """Property tests for JSON-RPC response building."""

    @given(valid_id, json_values)
    @settings(max_examples=50)
    def test_success_response_structure(self, req_id, result):
        """Success responses must have jsonrpc, id, and result."""
        response = JSONRPCMessage.success_response(req_id, result)

        assert response["jsonrpc"] == "2.0"
        assert response["id"] == req_id
        assert "result" in response
        assert response["result"] == result
        assert "error" not in response

    @given(valid_id, st.integers(), st.text(max_size=100))
    @settings(max_examples=50)
    def test_error_response_structure(self, req_id, code, message):
        """Error responses must have jsonrpc, id, and error."""
        response = JSONRPCMessage.error_response(req_id, code, message)

        assert response["jsonrpc"] == "2.0"
        assert response["id"] == req_id
        assert "error" in response
        assert response["error"]["code"] == code
        assert response["error"]["message"] == message
        assert "result" not in response

    @given(valid_id, st.integers(), st.text(max_size=100), json_values.filter(lambda x: x is not None))
    @settings(max_examples=30)
    def test_error_response_with_data(self, req_id, code, message, data):
        """Error responses can include additional data when provided."""
        response = JSONRPCMessage.error_response(req_id, code, message, data=data)

        assert "data" in response["error"]
        assert response["error"]["data"] == data

    @given(valid_id, st.integers(), st.text(max_size=100))
    @settings(max_examples=30)
    def test_error_response_without_data(self, req_id, code, message):
        """Error responses without data should not have data field."""
        response = JSONRPCMessage.error_response(req_id, code, message, data=None)

        assert "data" not in response["error"]


# =============================================================================
# Property Tests for JSON-RPC Parsing
# =============================================================================

@pytest.mark.property
class TestJSONRPCParsing:
    """Property tests for JSON-RPC message parsing."""

    @given(valid_method, valid_params, valid_id)
    @settings(max_examples=50)
    def test_parse_request_roundtrip(self, method, params, req_id):
        """Parsing a serialized request should return the original."""
        request = JSONRPCMessage.request(method, params=params, id=req_id)
        json_str = json.dumps(request)

        parsed = JSONRPCMessage.parse(json_str)

        assert parsed == request

    @given(valid_method, valid_params, valid_id)
    @settings(max_examples=30)
    def test_parse_bytes(self, method, params, req_id):
        """Parsing bytes should work the same as string."""
        request = JSONRPCMessage.request(method, params=params, id=req_id)
        json_bytes = json.dumps(request).encode('utf-8')

        parsed = JSONRPCMessage.parse(json_bytes)

        assert parsed == request

    def test_parse_invalid_json_raises(self):
        """Parsing invalid JSON should raise ValueError."""
        invalid_jsons = [
            "not json at all",
            "{missing: quotes}",
            "{'single': 'quotes'}",
            "{incomplete",
            "[1, 2, 3",
            "undefined",
        ]
        for invalid_json in invalid_jsons:
            with pytest.raises((ValueError, json.JSONDecodeError)):
                JSONRPCMessage.parse(invalid_json)


# =============================================================================
# Property Tests for Response Classification
# =============================================================================

@pytest.mark.property
class TestResponseClassification:
    """Property tests for response type classification."""

    @given(valid_id, json_values)
    @settings(max_examples=30)
    def test_success_response_is_response(self, req_id, result):
        """Success responses should be classified as responses."""
        response = JSONRPCMessage.success_response(req_id, result)

        assert JSONRPCMessage.is_response(response) is True
        assert JSONRPCMessage.is_error(response) is False

    @given(valid_id, st.integers(), st.text(max_size=50))
    @settings(max_examples=30)
    def test_error_response_is_error(self, req_id, code, message):
        """Error responses should be classified as errors."""
        response = JSONRPCMessage.error_response(req_id, code, message)

        assert JSONRPCMessage.is_response(response) is True
        assert JSONRPCMessage.is_error(response) is True

    @given(valid_method, valid_id)
    @settings(max_examples=30)
    def test_request_is_not_response(self, method, req_id):
        """Requests should not be classified as responses."""
        request = JSONRPCMessage.request(method, id=req_id)

        assert JSONRPCMessage.is_response(request) is False


# =============================================================================
# Property Tests for MCP Types
# =============================================================================

@pytest.mark.property
class TestMCPTypes:
    """Property tests for MCP data types."""

    @given(
        st.from_regex(r'^[a-z][a-z0-9_]{0,20}$', fullmatch=True),
        st.text(max_size=200),
        valid_params
    )
    @settings(max_examples=30)
    def test_tool_schema_creation(self, name, description, input_schema):
        """ToolSchema should be creatable with valid data."""
        schema = ToolSchema(
            name=name,
            description=description,
            input_schema=input_schema
        )

        assert schema.name == name
        assert schema.description == description
        assert schema.input_schema == input_schema

    @given(
        st.booleans(),
        json_values,
        st.integers(min_value=0, max_value=10000)
    )
    @settings(max_examples=30)
    def test_mcp_call_result_creation(self, success, content, duration_ms):
        """MCPCallResult should be creatable with valid data."""
        result = MCPCallResult(
            success=success,
            content=content,
            raw_response={"jsonrpc": "2.0", "id": 1, "result": content},
            duration_ms=duration_ms
        )

        assert result.success == success
        assert result.content == content
        assert result.duration_ms == duration_ms

    @given(
        st.from_regex(r'^[a-z][a-z0-9_-]{0,20}$', fullmatch=True),
        st.sampled_from([ConnectionStatus.CONNECTED, ConnectionStatus.DISCONNECTED, ConnectionStatus.CONNECTING])
    )
    @settings(max_examples=30)
    def test_server_status_creation(self, name, status):
        """ServerStatus should be creatable with valid data."""
        server_status = ServerStatus(
            name=name,
            status=status,
            tools=["tool1", "tool2"]
        )

        assert server_status.name == name
        assert server_status.status == status
        assert len(server_status.tools) == 2


# =============================================================================
# Property Tests for Tool Call Message Building
# =============================================================================

@pytest.mark.property
class TestToolCallMessages:
    """Property tests for tool call message building."""

    @given(
        st.from_regex(r'^[a-z][a-z0-9_]{0,20}$', fullmatch=True),
        valid_params,
        valid_id
    )
    @settings(max_examples=50)
    def test_tools_call_request_structure(self, tool_name, arguments, req_id):
        """tools/call requests should have correct structure."""
        request = JSONRPCMessage.request(
            "tools/call",
            params={"name": tool_name, "arguments": arguments},
            id=req_id
        )

        assert request["method"] == "tools/call"
        assert request["params"]["name"] == tool_name
        assert request["params"]["arguments"] == arguments

    @given(valid_id)
    @settings(max_examples=30)
    def test_tools_list_request_structure(self, req_id):
        """tools/list requests should have correct structure."""
        request = JSONRPCMessage.request("tools/list", id=req_id)

        assert request["method"] == "tools/list"
        assert "params" not in request

    @given(valid_id)
    @settings(max_examples=30)
    def test_initialize_request_structure(self, req_id):
        """initialize requests should have correct structure."""
        request = JSONRPCMessage.request(
            "initialize",
            params={
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"}
            },
            id=req_id
        )

        assert request["method"] == "initialize"
        assert "protocolVersion" in request["params"]
        assert "clientInfo" in request["params"]

"""Golden master / snapshot tests for workflow outputs.

Uses syrupy for snapshot testing to ensure workflow outputs remain consistent.
"""

import pytest
from syrupy.assertion import SnapshotAssertion


@pytest.mark.snapshot
class TestWorkflowOutputSnapshots:
    """Snapshot tests for workflow output formats."""

    def test_snap_001_simple_workflow_output(self, snapshot: SnapshotAssertion):
        """SNAP-001: Simple workflow output format."""
        output = {
            "workflow_name": "simple-test",
            "version": "1.0",
            "status": "completed",
            "result": 42,
            "steps_executed": 1
        }

        assert output == snapshot

    def test_snap_002_multi_step_workflow_output(self, snapshot: SnapshotAssertion):
        """SNAP-002: Multi-step workflow output format."""
        output = {
            "workflow_name": "multi-step",
            "version": "1.0",
            "status": "completed",
            "result": {"sum": 100, "count": 5},
            "steps_executed": 3,
            "step_results": {
                "step1": {"output": 10},
                "step2": {"output": 50},
                "step3": {"output": 100}
            }
        }

        assert output == snapshot

    def test_snap_003_workflow_with_inputs_output(self, snapshot: SnapshotAssertion):
        """SNAP-003: Workflow with inputs output format."""
        output = {
            "workflow_name": "with-inputs",
            "version": "1.0",
            "status": "completed",
            "inputs_received": {
                "name": "Alice",
                "count": 5
            },
            "result": "Hello, Alice! Count: 5"
        }

        assert output == snapshot

    def test_snap_004_workflow_error_output(self, snapshot: SnapshotAssertion):
        """SNAP-004: Workflow error output format."""
        output = {
            "workflow_name": "error-test",
            "version": "1.0",
            "status": "failed",
            "error": {
                "code": "EXECUTION_ERROR",
                "message": "Step 'step2' failed",
                "step_id": "step2",
                "details": "Division by zero"
            },
            "steps_executed": 1,
            "steps_failed": 1
        }

        assert output == snapshot

    def test_snap_005_workflow_with_tool_output(self, snapshot: SnapshotAssertion):
        """SNAP-005: Workflow with tool call output format."""
        output = {
            "workflow_name": "tool-test",
            "version": "1.0",
            "status": "completed",
            "result": "Tool result: success",
            "tool_calls": [
                {
                    "tool": "echo",
                    "input": {"message": "hello"},
                    "output": "hello"
                }
            ]
        }

        assert output == snapshot


@pytest.mark.snapshot
class TestValidationOutputSnapshots:
    """Snapshot tests for validation output formats."""

    def test_snap_010_validation_success_output(self, snapshot: SnapshotAssertion):
        """SNAP-010: Validation success output format."""
        output = {
            "valid": True,
            "workflow_name": "test-workflow",
            "version": "1.0",
            "warnings": [],
            "errors": []
        }

        assert output == snapshot

    def test_snap_011_validation_error_output(self, snapshot: SnapshotAssertion):
        """SNAP-011: Validation error output format."""
        output = {
            "valid": False,
            "workflow_name": None,
            "version": None,
            "warnings": [],
            "errors": [
                {
                    "code": "MISSING_FIELD",
                    "message": "Required field 'name' is missing",
                    "path": "$.name"
                },
                {
                    "code": "MISSING_FIELD",
                    "message": "Required field 'steps' is missing",
                    "path": "$.steps"
                }
            ]
        }

        assert output == snapshot

    def test_snap_012_validation_warning_output(self, snapshot: SnapshotAssertion):
        """SNAP-012: Validation with warnings output format."""
        output = {
            "valid": True,
            "workflow_name": "test-workflow",
            "version": "1.0",
            "warnings": [
                {
                    "code": "DEPRECATED_FIELD",
                    "message": "Field 'timeout' is deprecated, use 'step_timeout' instead",
                    "path": "$.timeout"
                }
            ],
            "errors": []
        }

        assert output == snapshot


@pytest.mark.snapshot
class TestMCPMessageSnapshots:
    """Snapshot tests for MCP message formats."""

    def test_snap_020_mcp_initialize_request(self, snapshot: SnapshotAssertion):
        """SNAP-020: MCP initialize request format."""
        message = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                    "prompts": {}
                },
                "clientInfo": {
                    "name": "ploston",
                    "version": "1.0.0"
                }
            }
        }

        assert message == snapshot

    def test_snap_021_mcp_initialize_response(self, snapshot: SnapshotAssertion):
        """SNAP-021: MCP initialize response format."""
        message = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": True}
                },
                "serverInfo": {
                    "name": "ploston-server",
                    "version": "1.0.0"
                }
            }
        }

        assert message == snapshot

    def test_snap_022_mcp_tools_list_response(self, snapshot: SnapshotAssertion):
        """SNAP-022: MCP tools/list response format."""
        message = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo the input message",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "message": {
                                    "type": "string",
                                    "description": "Message to echo"
                                }
                            },
                            "required": ["message"]
                        }
                    }
                ]
            }
        }

        assert message == snapshot

    def test_snap_023_mcp_tool_call_request(self, snapshot: SnapshotAssertion):
        """SNAP-023: MCP tools/call request format."""
        message = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "echo",
                "arguments": {
                    "message": "Hello, World!"
                }
            }
        }

        assert message == snapshot

    def test_snap_024_mcp_tool_call_response(self, snapshot: SnapshotAssertion):
        """SNAP-024: MCP tools/call response format."""
        message = {
            "jsonrpc": "2.0",
            "id": 3,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": "Hello, World!"
                    }
                ]
            }
        }

        assert message == snapshot

    def test_snap_025_mcp_error_response(self, snapshot: SnapshotAssertion):
        """SNAP-025: MCP error response format."""
        message = {
            "jsonrpc": "2.0",
            "id": 4,
            "error": {
                "code": -32600,
                "message": "Invalid Request",
                "data": {
                    "details": "Missing required field 'method'"
                }
            }
        }

        assert message == snapshot


@pytest.mark.snapshot
class TestConfigOutputSnapshots:
    """Snapshot tests for configuration output formats."""

    def test_snap_030_default_config_output(self, snapshot: SnapshotAssertion):
        """SNAP-030: Default configuration output format."""
        config = {
            "server": {
                "host": "localhost",
                "port": 8080
            },
            "execution": {
                "max_steps": 100,
                "step_timeout": 30,
                "max_retries": 3
            },
            "security": {
                "sandbox_enabled": True,
                "allowed_modules": ["math", "json", "datetime"]
            }
        }

        assert config == snapshot

    def test_snap_031_custom_config_output(self, snapshot: SnapshotAssertion):
        """SNAP-031: Custom configuration output format."""
        config = {
            "server": {
                "host": "0.0.0.0",
                "port": 9090,
                "ssl_enabled": True
            },
            "execution": {
                "max_steps": 500,
                "step_timeout": 60,
                "max_retries": 5,
                "parallel_steps": True
            },
            "security": {
                "sandbox_enabled": True,
                "allowed_modules": ["math", "json", "datetime", "re", "collections"]
            },
            "logging": {
                "level": "DEBUG",
                "format": "json"
            }
        }

        assert config == snapshot

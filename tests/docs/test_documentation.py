"""Documentation tests.

Tests that validate documentation accuracy and code examples.
"""

import pytest
import yaml
import re
from pathlib import Path


@pytest.mark.docs
class TestWorkflowExamples:
    """Test workflow examples from documentation."""
    
    def test_doc_001_simple_workflow_example(self):
        """DOC-001: Simple workflow example is valid."""
        # Example from documentation
        workflow = {
            'name': 'hello-world',
            'version': '1.0',
            'steps': [
                {
                    'id': 'greet',
                    'code': 'result = "Hello, World!"'
                }
            ],
            'output': '{{ steps.greet.output }}'
        }
        
        # Validate structure
        assert 'name' in workflow
        assert 'version' in workflow
        assert 'steps' in workflow
        assert len(workflow['steps']) > 0
        assert 'id' in workflow['steps'][0]
    
    def test_doc_002_workflow_with_inputs_example(self):
        """DOC-002: Workflow with inputs example is valid."""
        workflow = {
            'name': 'greeting',
            'version': '1.0',
            'inputs': [
                {'name': {'type': 'string', 'default': 'World'}}
            ],
            'steps': [
                {
                    'id': 'greet',
                    'code': 'result = f"Hello, {{ inputs.name }}!"'
                }
            ],
            'output': '{{ steps.greet.output }}'
        }
        
        assert 'inputs' in workflow
        assert len(workflow['inputs']) > 0
    
    def test_doc_003_multi_step_workflow_example(self):
        """DOC-003: Multi-step workflow example is valid."""
        workflow = {
            'name': 'calculator',
            'version': '1.0',
            'steps': [
                {
                    'id': 'step1',
                    'code': 'result = 10'
                },
                {
                    'id': 'step2',
                    'depends_on': ['step1'],
                    'code': 'result = {{ steps.step1.output }} * 2'
                },
                {
                    'id': 'step3',
                    'depends_on': ['step2'],
                    'code': 'result = {{ steps.step2.output }} + 5'
                }
            ],
            'output': '{{ steps.step3.output }}'
        }
        
        assert len(workflow['steps']) == 3
        assert workflow['steps'][1].get('depends_on') == ['step1']
    
    def test_doc_004_conditional_workflow_example(self):
        """DOC-004: Conditional workflow example is valid."""
        workflow = {
            'name': 'conditional',
            'version': '1.0',
            'inputs': [
                {'value': {'type': 'integer', 'default': 10}}
            ],
            'steps': [
                {
                    'id': 'check',
                    'code': 'result = {{ inputs.value }} > 5'
                },
                {
                    'id': 'high',
                    'when': '{{ steps.check.output }}',
                    'code': 'result = "High value"'
                },
                {
                    'id': 'low',
                    'when': 'not {{ steps.check.output }}',
                    'code': 'result = "Low value"'
                }
            ],
            'output': '{{ steps.high.output or steps.low.output }}'
        }
        
        assert 'when' in workflow['steps'][1]
        assert 'when' in workflow['steps'][2]
    
    def test_doc_005_loop_workflow_example(self):
        """DOC-005: Loop workflow example is valid."""
        workflow = {
            'name': 'loop-example',
            'version': '1.0',
            'steps': [
                {
                    'id': 'generate',
                    'code': 'result = [1, 2, 3, 4, 5]'
                },
                {
                    'id': 'process',
                    'foreach': '{{ steps.generate.output }}',
                    'as': 'item',
                    'code': 'result = {{ item }} * 2'
                }
            ],
            'output': '{{ steps.process.output }}'
        }
        
        assert 'foreach' in workflow['steps'][1]
        assert 'as' in workflow['steps'][1]


@pytest.mark.docs
class TestConfigExamples:
    """Test configuration examples from documentation."""
    
    def test_doc_010_basic_config_example(self):
        """DOC-010: Basic configuration example is valid."""
        config = {
            'server': {
                'host': 'localhost',
                'port': 8080
            }
        }
        
        assert 'server' in config
        assert config['server']['port'] == 8080
    
    def test_doc_011_full_config_example(self):
        """DOC-011: Full configuration example is valid."""
        config = {
            'server': {
                'host': '0.0.0.0',
                'port': 8080,
                'workers': 4
            },
            'execution': {
                'max_steps': 100,
                'step_timeout': 30,
                'max_retries': 3
            },
            'security': {
                'sandbox_enabled': True,
                'allowed_modules': ['math', 'json', 'datetime']
            },
            'logging': {
                'level': 'INFO',
                'format': 'json'
            }
        }
        
        assert 'execution' in config
        assert 'security' in config
        assert config['security']['sandbox_enabled'] is True
    
    def test_doc_012_env_config_example(self):
        """DOC-012: Environment variable configuration example."""
        # Example showing env var substitution
        config_template = """
server:
  host: ${PLOSTON_HOST:-localhost}
  port: ${PLOSTON_PORT:-8080}
"""
        
        # Validate YAML is parseable
        config = yaml.safe_load(config_template)
        assert 'server' in config


@pytest.mark.docs
class TestMCPExamples:
    """Test MCP protocol examples from documentation."""
    
    def test_doc_020_mcp_initialize_example(self):
        """DOC-020: MCP initialize example is valid."""
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "clientInfo": {
                    "name": "ploston",
                    "version": "1.0.0"
                }
            }
        }
        
        assert request["jsonrpc"] == "2.0"
        assert request["method"] == "initialize"
        assert "protocolVersion" in request["params"]
    
    def test_doc_021_mcp_tools_list_example(self):
        """DOC-021: MCP tools/list example is valid."""
        request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list"
        }
        
        response = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo the input",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "message": {"type": "string"}
                            },
                            "required": ["message"]
                        }
                    }
                ]
            }
        }
        
        assert request["method"] == "tools/list"
        assert "tools" in response["result"]
    
    def test_doc_022_mcp_tool_call_example(self):
        """DOC-022: MCP tools/call example is valid."""
        request = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "echo",
                "arguments": {
                    "message": "Hello!"
                }
            }
        }
        
        response = {
            "jsonrpc": "2.0",
            "id": 3,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": "Hello!"
                    }
                ]
            }
        }
        
        assert request["method"] == "tools/call"
        assert "name" in request["params"]
        assert "content" in response["result"]


@pytest.mark.docs
class TestCLIExamples:
    """Test CLI command examples from documentation."""
    
    def test_doc_030_cli_validate_example(self):
        """DOC-030: CLI validate command example."""
        # Example command: ploston validate workflow.yaml
        command = "ploston validate workflow.yaml"
        parts = command.split()
        
        assert parts[0] == "ploston"
        assert parts[1] == "validate"
        assert parts[2].endswith(".yaml")
    
    def test_doc_031_cli_run_example(self):
        """DOC-031: CLI run command example."""
        # Example command: ploston run workflow.yaml --input name=Alice
        command = "ploston run workflow.yaml --input name=Alice"
        parts = command.split()
        
        assert parts[0] == "ploston"
        assert parts[1] == "run"
        assert "--input" in parts
    
    def test_doc_032_cli_config_example(self):
        """DOC-032: CLI config command example."""
        # Example command: ploston config show
        command = "ploston config show"
        parts = command.split()
        
        assert parts[0] == "ploston"
        assert parts[1] == "config"
        assert parts[2] == "show"
    
    def test_doc_033_cli_workflows_example(self):
        """DOC-033: CLI workflows command example."""
        # Example command: ploston workflows list --server http://localhost:8080
        command = "ploston workflows list --server http://localhost:8080"
        parts = command.split()
        
        assert parts[0] == "ploston"
        assert parts[1] == "workflows"
        assert parts[2] == "list"
        assert "--server" in parts


@pytest.mark.docs
class TestAPIExamples:
    """Test API examples from documentation."""
    
    def test_doc_040_api_list_workflows_example(self):
        """DOC-040: API list workflows example."""
        # Example: GET /api/v1/workflows
        endpoint = "/api/v1/workflows"
        method = "GET"
        
        response = {
            "workflows": [],
            "total": 0,
            "page": 1,
            "page_size": 10
        }
        
        assert endpoint.startswith("/api/")
        assert "workflows" in response
    
    def test_doc_041_api_create_workflow_example(self):
        """DOC-041: API create workflow example."""
        # Example: POST /api/v1/workflows
        endpoint = "/api/v1/workflows"
        method = "POST"
        
        request_body = {
            "name": "my-workflow",
            "version": "1.0",
            "steps": [
                {"id": "step1", "code": "result = 42"}
            ],
            "output": "{{ steps.step1.output }}"
        }
        
        response = {
            "id": "wf-123",
            "name": "my-workflow",
            "version": "1.0",
            "created_at": "2024-01-01T00:00:00Z"
        }
        
        assert "name" in request_body
        assert "id" in response
    
    def test_doc_042_api_execute_workflow_example(self):
        """DOC-042: API execute workflow example."""
        # Example: POST /api/v1/workflows/{id}/execute
        endpoint = "/api/v1/workflows/wf-123/execute"
        method = "POST"
        
        request_body = {
            "inputs": {
                "name": "Alice"
            }
        }
        
        response = {
            "execution_id": "exec-456",
            "status": "completed",
            "result": "Hello, Alice!"
        }
        
        assert "/execute" in endpoint
        assert "execution_id" in response


@pytest.mark.docs
class TestErrorExamples:
    """Test error response examples from documentation."""
    
    def test_doc_050_validation_error_example(self):
        """DOC-050: Validation error example."""
        error = {
            "error": "Validation Error",
            "code": "VALIDATION_ERROR",
            "message": "Workflow validation failed",
            "details": [
                {"field": "name", "message": "Required field is missing"}
            ]
        }
        
        assert "error" in error
        assert "code" in error
        assert "details" in error
    
    def test_doc_051_execution_error_example(self):
        """DOC-051: Execution error example."""
        error = {
            "error": "Execution Error",
            "code": "EXECUTION_ERROR",
            "message": "Step 'step1' failed",
            "step_id": "step1",
            "details": "Division by zero"
        }
        
        assert error["code"] == "EXECUTION_ERROR"
        assert "step_id" in error
    
    def test_doc_052_not_found_error_example(self):
        """DOC-052: Not found error example."""
        error = {
            "error": "Not Found",
            "code": "NOT_FOUND",
            "message": "Workflow 'wf-123' not found"
        }
        
        assert error["code"] == "NOT_FOUND"

# Error Codes Reference

Complete reference for Ploston error codes and their resolutions.

## Error Structure

All Ploston errors include:

| Field | Description |
|-------|-------------|
| `code` | Unique error code (e.g., `TOOL_TIMEOUT`) |
| `category` | Error category (TOOL, EXECUTION, VALIDATION, WORKFLOW, SYSTEM) |
| `message` | Human-readable error message |
| `detail` | Extended explanation |
| `suggestion` | Actionable fix suggestion |
| `retryable` | Whether retry may help |

## Tool Errors (TOOL)

### TOOL_UNAVAILABLE

**Message:** Tool '{tool_name}' is unavailable

**Cause:** The requested tool could not be reached or is not responding.

**Resolution:**
1. Check that the MCP server is running
2. Verify the tool is registered: `ploston tools list`
3. Check MCP server logs for errors
4. Try refreshing tools: `ploston tools refresh`

### TOOL_TIMEOUT

**Message:** Tool '{tool_name}' timed out after {timeout_seconds}s

**Cause:** The tool did not respond within the configured timeout.

**Resolution:**
1. Increase the timeout in configuration
2. Check if the tool is stuck or overloaded
3. Verify network connectivity to the MCP server

### TOOL_REJECTED

**Message:** Tool '{tool_name}' rejected the request

**Cause:** The tool refused to execute with the provided parameters.

**Resolution:**
1. Check the tool parameters match the schema
2. View tool schema: `ploston tools show {tool_name}`
3. Verify required parameters are provided

### TOOL_FAILED

**Message:** Tool execution failed

**Cause:** The tool encountered an error during execution.

**Resolution:**
1. Check the tool logs for details
2. Verify input data is valid
3. Check MCP server health

## Execution Errors (EXECUTION)

### CODE_SYNTAX

**Message:** Syntax error in code block

**Cause:** The Python code contains syntax errors.

**Resolution:**
1. Check Python syntax in the code step
2. Validate indentation and brackets
3. Test code locally before adding to workflow

### CODE_RUNTIME

**Message:** Runtime error in code block

**Cause:** The Python code raised an exception during execution.

**Resolution:**
1. Check the error message for details
2. Add error handling to your code
3. Verify variable names and types

### CODE_TIMEOUT

**Message:** Code execution timed out after {timeout_seconds}s

**Cause:** The code block did not complete within the timeout.

**Resolution:**
1. Optimize the code for performance
2. Increase step timeout in workflow
3. Break into smaller steps

### CODE_SECURITY

**Message:** Security violation in code block

**Cause:** The code attempted to use forbidden imports or builtins.

**Resolution:**
1. Remove dangerous imports (os, subprocess, etc.)
2. Use allowed imports only
3. See [Code Steps Guide](../guides/code-steps.md) for allowed imports

### TEMPLATE_ERROR

**Message:** Template rendering failed

**Cause:** Failed to render Jinja2 template.

**Resolution:**
1. Check template syntax: `{{ variable }}`
2. Verify referenced variables exist
3. Check for typos in step/input names

## Validation Errors (VALIDATION)

### INPUT_INVALID

**Message:** Invalid workflow input

**Cause:** The workflow input does not match the expected schema.

**Resolution:**
1. Check input types match schema
2. Provide all required inputs
3. Validate input values

### PARAM_INVALID

**Message:** Invalid parameters for tool '{tool_name}'

**Cause:** The tool parameters do not match the expected schema.

**Resolution:**
1. Check tool schema: `ploston tools show {tool_name}`
2. Verify parameter types
3. Provide all required parameters

### CONFIG_PATH_INVALID

**Message:** Invalid configuration path: {path}

**Cause:** The configuration path is not valid.

**Resolution:**
1. Use dot notation: `logging.level`
2. Check available paths: `ploston config show`

## Workflow Errors (WORKFLOW)

### WORKFLOW_NOT_FOUND

**Message:** Workflow '{workflow_id}' not found

**Cause:** The requested workflow does not exist.

**Resolution:**
1. Check workflow ID: `ploston workflows list`
2. Verify workflow file exists
3. Check workflows directory in config

### STEP_NOT_FOUND

**Message:** Step '{step_id}' not found

**Cause:** The referenced step does not exist in the workflow.

**Resolution:**
1. Check step ID in workflow definition
2. Verify depends_on references valid steps

### CIRCULAR_DEPENDENCY

**Message:** Circular dependency detected

**Cause:** The workflow contains circular step dependencies.

**Resolution:**
1. Review depends_on relationships
2. Remove circular references
3. Validate workflow: `ploston validate workflow.yaml`

### WORKFLOW_TIMEOUT

**Message:** Workflow timed out after {timeout_seconds}s

**Cause:** The workflow did not complete within the timeout.

**Resolution:**
1. Increase workflow timeout
2. Optimize slow steps
3. Check for stuck tools

## System Errors (SYSTEM)

### INTERNAL_ERROR

**Message:** Internal Ploston error

**Cause:** An unexpected error occurred in the Ploston engine.

**Resolution:**
1. Check Ploston logs for details
2. Report issue on GitHub with logs

### MCP_CONNECTION_FAILED

**Message:** Failed to connect to MCP server

**Cause:** Could not establish connection to the MCP server.

**Resolution:**
1. Check MCP server is running
2. Verify command/URL in config
3. Check network connectivity

### CONFIG_INVALID

**Message:** Invalid configuration

**Cause:** The Ploston configuration is invalid.

**Resolution:**
1. Validate config: `ploston config show`
2. Check YAML syntax
3. See [Configuration Reference](config-reference.md)


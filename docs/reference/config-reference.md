# Configuration Reference

Complete reference for Ploston configuration options.

## Configuration File Location

Ploston searches for configuration in this order (first found wins):

1. **CLI flag:** `--config ./path/to/config.yaml`
2. **Environment variable:** `PLOSTON_CONFIG_PATH=/path/to/config.yaml`
3. **Current directory:** `./ploston-config.yaml`
4. **User home:** `~/.ploston/config.yaml`

## Configuration Modes

### Running Mode

When Ploston finds a valid configuration file, it starts in **running mode** with full functionality.

### Configuration Mode

When no configuration file exists, Ploston starts in **configuration mode** with limited tools for initial setup:

| Tool | Description |
|------|-------------|
| `config_get` | Read current staged configuration |
| `config_set` | Stage configuration changes |
| `config_validate` | Validate staged configuration |
| `config_done` | Write config to disk and switch to running mode |
| `config_location` | Get/set config file location |

Force a specific mode:

```bash
ploston serve --mode configuration  # Force config mode
ploston serve --mode running        # Force running mode (fails if no config)
```

## Complete Configuration Schema

```yaml
# ═══════════════════════════════════════════════════════════════
# PLOSTON CONFIGURATION FILE
# ═══════════════════════════════════════════════════════════════

# Server settings
server:
  name: "ploston"                    # Server name for MCP
  version: "0.1.0"               # Server version

# MCP server connections
mcp:
  servers:
    # Example: Filesystem server
    filesystem:
      command: "npx"
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
      env: {}
    
    # Example: Custom server
    # my-server:
    #   command: "python"
    #   args: ["-m", "my_mcp_server"]
    #   env:
    #     API_KEY: "${MY_API_KEY}"

# Tool configuration
tools:
  # Built-in tools to enable
  builtins:
    - python_exec
  
  # MCP servers to connect (references mcp.servers)
  mcp_servers:
    filesystem:
      command: "npx"
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]

# Workflow settings
workflows:
  # Directory containing workflow YAML files
  directory: "./workflows"
  
  # Auto-reload on file changes
  hot_reload: true

# Execution settings
execution:
  # Maximum concurrent workflow executions
  max_concurrent: 10
  
  # Default timeout in seconds
  default_timeout: 300
  
  # Retry configuration
  retry:
    max_attempts: 3
    backoff_multiplier: 2.0

# Python execution sandbox
python_exec:
  # Timeout for code execution (seconds)
  timeout: 30
  
  # Maximum memory (bytes)
  max_memory: 536870912  # 512MB
  
  # Allowed imports
  allowed_imports:
    - json
    - re
    - datetime
    - math
    - collections
    - itertools
    - functools

# Logging configuration
logging:
  # Log level: DEBUG, INFO, WARNING, ERROR
  level: INFO
  
  # Log format: text or json
  format: text
  
  # Component-specific logging
  components:
    workflow: true
    step: true
    tool: true
    sandbox: true
  
  # Output options
  options:
    show_params: false
    show_results: false
    truncate_at: 1000

# Security settings
security:
  # Allowed hosts for HTTP requests
  allowed_hosts: []
  
  # Blocked hosts
  blocked_hosts: []

# Telemetry (optional)
telemetry:
  enabled: false
  endpoint: ""
```

## Configuration Sections

### `server`

Server identification for MCP protocol.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | `"ploston"` | Server name |
| `version` | string | `"0.1.0"` | Server version |

### `mcp`

MCP server connections.

```yaml
mcp:
  servers:
    server-name:
      command: "executable"
      args: ["arg1", "arg2"]
      env:
        VAR: "value"
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `command` | string | Yes | Executable to run |
| `args` | list | No | Command arguments |
| `env` | object | No | Environment variables |

### `tools`

Tool configuration.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `builtins` | list | `["python_exec"]` | Built-in tools to enable |
| `mcp_servers` | object | `{}` | MCP server definitions |

### `workflows`

Workflow settings.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `directory` | string | `"./workflows"` | Workflow files directory |
| `hot_reload` | bool | `true` | Auto-reload on changes |

### `execution`

Execution settings.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_concurrent` | int | `10` | Max concurrent executions |
| `default_timeout` | int | `300` | Default timeout (seconds) |
| `retry.max_attempts` | int | `3` | Max retry attempts |
| `retry.backoff_multiplier` | float | `2.0` | Backoff multiplier |

### `python_exec`

Python sandbox settings.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `timeout` | int | `30` | Execution timeout (seconds) |
| `max_memory` | int | `536870912` | Max memory (bytes) |
| `allowed_imports` | list | See above | Allowed Python imports |

### `logging`

Logging configuration.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `level` | string | `"INFO"` | Log level |
| `format` | string | `"text"` | Output format |
| `components.*` | bool | `true` | Component logging |
| `options.show_params` | bool | `false` | Show parameters |
| `options.show_results` | bool | `false` | Show results |
| `options.truncate_at` | int | `1000` | Truncate long values |

## Environment Variable Substitution

Use `${VAR}` syntax for environment variables:

```yaml
mcp:
  servers:
    github:
      command: "npx"
      args: ["-y", "@modelcontextprotocol/server-github"]
      env:
        GITHUB_TOKEN: "${GITHUB_TOKEN}"
```

### Syntax Options

| Syntax | Description |
|--------|-------------|
| `${VAR}` | Required variable (error if unset) |
| `${VAR:-default}` | Use default if unset |
| `${VAR:?message}` | Custom error message if unset |

## Example Configurations

### Minimal Configuration

```yaml
workflows:
  directory: "./workflows"
```

### Development Configuration

```yaml
workflows:
  directory: "./workflows"
  hot_reload: true

logging:
  level: DEBUG
  options:
    show_params: true
    show_results: true
```

### Production Configuration

```yaml
workflows:
  directory: "/app/workflows"
  hot_reload: false

execution:
  max_concurrent: 50
  default_timeout: 600

logging:
  level: WARNING
  format: json

security:
  allowed_hosts:
    - "api.example.com"
```

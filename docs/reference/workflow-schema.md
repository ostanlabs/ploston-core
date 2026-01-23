# Workflow Schema Reference

Complete YAML schema reference for Ploston workflows.

---

## Canonical Example

This example shows every field. Copy it as a starting point for your workflows.

```yaml
# ─────────────────────────────────────────────────────────────────
# METADATA (required)
# ─────────────────────────────────────────────────────────────────
name: data-pipeline                    # Required: Workflow identifier
version: "1.0.0"                       # Required: Semantic version
description: "Fetch, transform, and validate data"  # Optional

# ─────────────────────────────────────────────────────────────────
# PACKAGES (optional)
# ─────────────────────────────────────────────────────────────────
packages:
  profile: standard                    # minimal | standard | data_science
  additional:                          # Extra packages to allow
    - requests

# ─────────────────────────────────────────────────────────────────
# DEFAULTS (optional)
# ─────────────────────────────────────────────────────────────────
defaults:
  timeout: 30                          # Default step timeout (seconds)
  on_error: fail                       # fail | continue | retry
  retry:                               # Retry config (when on_error: retry)
    max_attempts: 3
    initial_delay: 1.0
    max_delay: 30.0
    backoff_multiplier: 2.0

# ─────────────────────────────────────────────────────────────────
# INPUTS (optional, but usually needed)
# Format: Array of input definitions
# ─────────────────────────────────────────────────────────────────
inputs:
  # Simple syntax: just the name (required, type: string)
  - url

  # With default value (makes it optional)
  - format: "json"

  # Full definition with all options
  - count:
      type: integer                    # string | integer | number | boolean | array | object
      required: false                  # Default: true
      default: 10                      # Default value
      description: "Number of items"   # For documentation
      minimum: 1                       # Validation: minimum value
      maximum: 100                     # Validation: maximum value

  # Enum constraint
  - output_format:
      type: string
      enum: ["json", "csv", "xml"]     # Allowed values
      default: "json"

  # Pattern constraint
  - email:
      type: string
      pattern: "^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\\.[a-zA-Z0-9-.]+$"

# ─────────────────────────────────────────────────────────────────
# STEPS (required, at least one)
# ─────────────────────────────────────────────────────────────────
steps:
  # Tool step: calls an MCP tool
  - id: fetch                          # Required: unique step identifier
    tool: http_get                     # MCP tool name
    params:                            # Tool parameters (templates allowed)
      url: "{{ inputs.url }}"
      headers:
        Accept: "application/json"
    timeout: 60                        # Override default timeout
    on_error: retry                    # Override default error handling
    retry:
      max_attempts: 3
      initial_delay: 2.0

  # Code step: runs Python in sandbox
  - id: transform
    code: |
      import json

      # Access previous step output
      data = context.steps['fetch'].output

      # Access inputs
      limit = context.inputs.get('count', 10)

      # Process data
      items = data.get('items', [])[:limit]

      # Return result (available as steps.transform.output)
      return {"items": items, "count": len(items)}

  # Step with dependency
  - id: validate
    depends_on: [transform]            # Wait for these steps first
    code: |
      data = context.steps['transform'].output
      if data['count'] == 0:
          raise ValueError("No items found")
      return {"valid": True, "count": data['count']}

# ─────────────────────────────────────────────────────────────────
# OUTPUTS (optional)
# ─────────────────────────────────────────────────────────────────

# Option 1: Single output (simple)
output: "{{ steps.validate.output }}"

# Option 2: Multiple named outputs (use this OR output, not both)
# outputs:
#   - name: result
#     from_path: steps.validate.output
#     description: "Validation result"
#   - name: item_count
#     value: "{{ steps.transform.output.count }}"
#     description: "Number of items processed"
```

---

## Top-Level Structure

```yaml
# Required
name: string          # Workflow identifier (alphanumeric, hyphens)
version: string       # Semantic version (e.g., "1.0", "2.1.3")

# Optional
description: string   # Human-readable description
packages: object      # Python package configuration
defaults: object      # Default step settings

# Schema
inputs: array         # Input definitions (array format)
steps: array          # Step definitions (required, at least one)
outputs: array        # Output definitions (optional)
output: string        # Single output expression (alternative to outputs)
```

## Metadata

### `name` (required)

Unique workflow identifier.

- Type: `string`
- Pattern: `^[a-zA-Z][a-zA-Z0-9-]*$`
- Example: `data-transform`, `hello-world`

### `version` (required)

Semantic version string.

- Type: `string`
- Example: `"1.0"`, `"2.1.3"`

### `description` (optional)

Human-readable description.

- Type: `string`
- Example: `"Transform and validate JSON data"`

## Packages Configuration

```yaml
packages:
  profile: string     # Package profile: minimal | standard | data_science
  additional: array   # Additional packages to install
```

### Profiles

| Profile | Packages |
|---------|----------|
| `minimal` | json, re, datetime, math |
| `standard` | minimal + collections, itertools, functools, hashlib, uuid |
| `data_science` | standard + numpy, pandas (if available) |

## Defaults

```yaml
defaults:
  timeout: integer    # Default step timeout (seconds)
  on_error: string    # Error handling: fail | continue | retry
  retry: object       # Retry configuration
```

### Retry Configuration

```yaml
defaults:
  retry:
    max_attempts: 3           # Maximum retry attempts
    initial_delay: 1.0        # Initial delay (seconds)
    max_delay: 30.0           # Maximum delay (seconds)
    backoff_multiplier: 2.0   # Exponential backoff multiplier
```

## Inputs

**Format:** `inputs` is an **array** (list) of input definitions.

Ploston supports three syntaxes for input definitions:

### Syntax 1: Simple String (Required Input)

```yaml
inputs:
  - url                    # Required string input named "url"
  - topic                  # Required string input named "topic"
```

### Syntax 2: Name with Default (Optional Input)

```yaml
inputs:
  - format: "json"         # Optional, defaults to "json"
  - count: 10              # Optional, defaults to 10
```

### Syntax 3: Full Definition (All Options)

```yaml
inputs:
  - url:
      type: string         # Required: string | integer | number | boolean | array | object
      required: true       # Optional: default is true
      default: null        # Optional: default value (makes input optional)
      description: "URL"   # Optional: human-readable description
      enum: [...]          # Optional: allowed values
      pattern: "^https?"   # Optional: regex pattern (strings only)
      minimum: 1           # Optional: minimum value (numbers only)
      maximum: 100         # Optional: maximum value (numbers only)
```

### Input Types

| Type | JSON Type | Example | Notes |
|------|-----------|---------|-------|
| `string` | string | `"hello"` | Default type if not specified |
| `integer` | number | `42` | Whole numbers only |
| `number` | number | `3.14` | Any numeric value |
| `boolean` | boolean | `true` | true or false |
| `array` | array | `[1, 2, 3]` | JSON array |
| `object` | object | `{"key": "value"}` | JSON object |

### Complete Input Examples

```yaml
inputs:
  # Simple required inputs
  - url
  - topic

  # With default values
  - format: "json"
  - retries: 3

  # Full definitions
  - count:
      type: integer
      required: false
      default: 10
      description: "Number of items to fetch"
      minimum: 1
      maximum: 100

  - output_format:
      type: string
      enum: ["json", "csv", "xml"]
      default: "json"
      description: "Output format"

  - email:
      type: string
      required: true
      description: "Contact email"
      pattern: "^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\\.[a-zA-Z0-9-.]+$"
```

### Required vs Optional

| Condition | Required? |
|-----------|-----------|
| Simple string syntax (`- url`) | ✅ Required |
| Has `default` value | ❌ Optional |
| `required: true` (explicit) | ✅ Required |
| `required: false` (explicit) | ❌ Optional |

## Steps

```yaml
steps:
  - id: string          # Step identifier (required)
    
    # Type (exactly one required)
    tool: string        # MCP tool name
    code: string        # Python code block
    
    # Tool parameters (tool steps only)
    params: object      # Tool parameters
    
    # Dependencies
    depends_on: array   # List of step IDs to wait for
    
    # Error handling
    timeout: integer    # Step timeout (seconds)
    on_error: string    # Error handling: fail | continue | retry
    retry: object       # Retry configuration
```

### Tool Step

```yaml
steps:
  - id: fetch
    tool: http_get
    params:
      url: "{{ inputs.url }}"
      headers:
        Authorization: "Bearer {{ inputs.token }}"
```

### Code Step

```yaml
steps:
  - id: process
    code: |
      import json
      data = json.loads('{{ inputs.data }}')
      result = {"processed": data}
```

### Dependencies

```yaml
steps:
  - id: step1
    code: |
      result = "first"

  - id: step2
    depends_on: [step1]
    code: |
      result = "second"

  - id: step3
    depends_on: [step1, step2]
    code: |
      result = "third"
```

## Outputs

### Single Output

```yaml
output: "{{ steps.final.output }}"
```

### Multiple Outputs

```yaml
outputs:
  - name: string        # Output name
    from_path: string   # Path to value (e.g., "steps.process.output.data")
    value: string       # Template expression (alternative to from_path)
    description: string # Human-readable description
```

### Output Examples

```yaml
outputs:
  - name: result
    from_path: steps.transform.output
    description: Transformed data

  - name: count
    value: "{{ steps.count.output }}"
    description: Number of items processed
```

## Template Expressions

Use Jinja2 templates to reference values:

| Expression | Description |
|------------|-------------|
| `{{ inputs.name }}` | Input value |
| `{{ steps.id.output }}` | Step output |
| `{{ steps.id.output.field }}` | Nested field |
| `{{ value \| tojson }}` | JSON encode |
| `{{ value \| default('x') }}` | Default value |

## Complete Example

```yaml
name: data-pipeline
version: "1.0"
description: Fetch, transform, and validate data

packages:
  profile: standard

defaults:
  timeout: 30
  on_error: fail

inputs:
  url:
    type: string
    description: API endpoint URL
  format:
    type: string
    enum: ["json", "csv"]
    default: "json"

steps:
  - id: fetch
    tool: http_get
    params:
      url: "{{ inputs.url }}"
    timeout: 60

  - id: transform
    depends_on: [fetch]
    code: |
      data = {{ steps.fetch.output }}
      result = [item for item in data if item.get("active")]

  - id: format
    depends_on: [transform]
    code: |
      import json
      data = {{ steps.transform.output }}
      result = json.dumps(data, indent=2)

output: "{{ steps.format.output }}"
```


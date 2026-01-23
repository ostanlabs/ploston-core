# ploston-core

[![CI](https://github.com/ostanlabs/ploston-core/actions/workflows/ci.yml/badge.svg)](https://github.com/ostanlabs/ploston-core/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

Core engine for Ploston - Deterministic Agent Execution Layer.

## Overview

`ploston-core` contains the shared engine components that both OSS and Enterprise packages depend on. It provides:

| Module | Description |
|--------|-------------|
| **Engine** | Workflow execution engine with sequential, deterministic execution |
| **Registry** | Tool and workflow registries with auto-discovery |
| **MCP** | Model Context Protocol client connections |
| **MCP Frontend** | MCP server frontend (stdio/HTTP transport) |
| **API** | REST API framework (FastAPI-based) |
| **Sandbox** | Python sandbox with 7-layer security model |
| **Template** | Jinja2 template engine for parameter rendering |
| **Telemetry** | Structured logging and metrics |
| **Config** | Layered configuration with hot-reload |
| **Errors** | Structured, actionable error handling |
| **Plugins** | Extensible plugin framework |

## Installation

```bash
pip install ploston-core
```

Or with [uv](https://github.com/astral-sh/uv):

```bash
uv add ploston-core
```

## Quick Start

```python
from ploston_core.engine import Engine
from ploston_core.registry import ToolRegistry

# Create engine with tool registry
registry = ToolRegistry()
engine = Engine(registry=registry)

# Execute workflow
result = await engine.execute(workflow)
```

## Documentation

Detailed documentation is available in the [`docs/`](docs/) directory:

### Concepts

- **[How Ploston Works](docs/concepts/how-ploston-works.md)** - Architecture overview and design principles
- **[Execution Model](docs/concepts/execution-model.md)** - How workflows execute step-by-step
- **[Security Model](docs/concepts/security-model.md)** - 7-layer sandbox security for code execution

### Reference

- **[Workflow Schema](docs/reference/workflow-schema.md)** - Complete YAML schema for defining workflows
- **[Configuration Reference](docs/reference/config-reference.md)** - All configuration options
- **[Error Codes](docs/reference/error-codes.md)** - Error codes and troubleshooting guide

### Architecture

- **[Implemented Architecture](docs/architecture/IMPLEMENTED_ARCHITECTURE.md)** - Current architecture summary

## Development

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (recommended)

### Setup

```bash
# Clone the repository
git clone https://github.com/ostanlabs/ploston-core.git
cd ploston-core

# Install dependencies
uv sync

# Run tests
uv run pytest tests/unit/ -v

# Run linting
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

### Running Tests

```bash
# Unit tests
uv run pytest tests/unit/ -v

# With coverage
uv run pytest tests/unit/ -v --cov=ploston_core --cov-report=term-missing
```

## Related Packages

| Package | Description |
|---------|-------------|
| [`ploston`](https://github.com/ostanlabs/ploston) | OSS distribution (core + CLI) |
| [`ploston-cli`](https://github.com/ostanlabs/ploston-cli) | Command-line interface |
| [`ploston-enterprise`](https://github.com/ostanlabs/ploston-enterprise) | Enterprise features |

## License

Apache-2.0 - see [LICENSE](LICENSE) for details.

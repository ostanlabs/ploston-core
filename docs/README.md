# ploston-core Documentation

This directory contains documentation for the `ploston-core` package - the core engine for Ploston.

## Contents

### Concepts

Core concepts explaining how Ploston works:

- **[How Ploston Works](concepts/how-ploston-works.md)** - Overview of Ploston's architecture and design
- **[Execution Model](concepts/execution-model.md)** - How workflows execute step-by-step
- **[Security Model](concepts/security-model.md)** - 7-layer security sandbox for code execution

### Reference

Technical reference documentation:

- **[Workflow Schema](reference/workflow-schema.md)** - Complete YAML schema for workflows
- **[Configuration Reference](reference/config-reference.md)** - All configuration options
- **[Error Codes](reference/error-codes.md)** - Error codes and troubleshooting

### Architecture

Engineering architecture documentation:

- **[Implemented Architecture](architecture/IMPLEMENTED_ARCHITECTURE.md)** - Current architecture summary

## Package Overview

`ploston-core` provides:

| Module | Description |
|--------|-------------|
| `engine` | Workflow execution engine |
| `registry` | Tool and workflow registries |
| `mcp` | Model Context Protocol client connections |
| `mcp_frontend` | MCP server frontend (stdio/HTTP) |
| `api` | REST API framework (FastAPI-based) |
| `sandbox` | Python sandbox for safe code execution |
| `template` | Jinja2 template engine |
| `telemetry` | Logging and metrics |
| `config` | Configuration loading and management |
| `errors` | Structured error handling |
| `plugins` | Plugin framework |

## Quick Links

- [Main README](../README.md)
- [PyPI Package](https://pypi.org/project/ploston-core/)
- [GitHub Repository](https://github.com/ostanlabs/ploston-core)


# ploston-core

Core engine for Ploston - Deterministic Agent Execution Layer.

## Overview

`ploston-core` contains the shared engine components that both OSS and Enterprise packages depend on. It provides:

- **Engine**: Workflow execution engine
- **Registry**: Tool and workflow registries
- **MCP**: Model Context Protocol client connections
- **API**: REST API framework (FastAPI-based)
- **Sandbox**: Python sandbox for safe code execution
- **Template**: Jinja2 template engine
- **Telemetry**: Logging and metrics
- **Config**: Configuration loading and management

## Installation

```bash
pip install ploston-core
```

## Usage

```python
from ploston_core.engine import Engine
from ploston_core.registry import ToolRegistry

# Create engine with tool registry
registry = ToolRegistry()
engine = Engine(registry=registry)

# Execute workflow
result = await engine.execute(workflow)
```

## License

Apache-2.0

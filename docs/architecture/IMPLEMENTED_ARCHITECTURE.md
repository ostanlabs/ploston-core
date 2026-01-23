# AEL Architecture Summary

**Last Updated:** 2026-01-13  
**Status:** Implemented (MVP + Phase 0 + Phase 1)

This document consolidates key architectural decisions from implemented components. For detailed original documents, see [archive/engineering/architecture/](../../archive/engineering/architecture/).

---

## Core Architecture

### Execution Model
- **Separation of Concerns:** LLM plans, AEL executes deterministically
- **MCP Native:** AEL acts as MCP server exposing tools and workflows to agents
- **Workflow-as-Tool:** Workflows published as MCP tools reduce agent token usage

### Component Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         LLM AGENT                               │
└───────────────────────────┬─────────────────────────────────────┘
                            │ MCP Protocol (stdio)
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    AEL MCP Frontend                             │
│  • tools/list, tools/call                                       │
│  • Mode-aware (configuration vs running)                        │
│  • Self-config tools (config_get, config_set, etc.)            │
└───────────────────────────┬─────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│ Tool Registry │   │Workflow Engine│   │  Config Loader │
│ • Discovery   │   │ • YAML schema │   │ • Layered      │
│ • Schemas     │   │ • Templating  │   │ • Hot-reload   │
└───────┬───────┘   └───────┬───────┘   └───────────────┘
        │                   │
        ▼                   ▼
┌───────────────┐   ┌───────────────┐
│ Tool Invoker  │   │ Python Exec   │
│ • MCP calls   │   │ • Sandbox     │
│ • Retry       │   │ • Security    │
└───────────────┘   └───────────────┘
```

---

## Key Decisions

### Python Exec (Dual Role)
- **Implicit:** Inline `code:` blocks in workflow steps
- **Explicit:** `tool: python_exec` with custom config
- **Security:** 7-layer sandbox (RestrictedPython, AST, imports, builtins, resources, network, filesystem)

### YAML Workflow Schema
- **Simple cases stay simple:** Linear workflows need minimal boilerplate
- **Explicit over implicit:** Dependencies, outputs, context access are clear
- **Tool schemas from registry:** Users don't redeclare tool schemas

```yaml
name: example-workflow
version: "1.0.0"
inputs:
  url: { type: string, required: true }
steps:
  - id: fetch
    tool: http_request
    params: { url: "{{ inputs.url }}" }
  - id: transform
    code: |
      return context.steps['fetch'].output
outputs:
  result: "{{ steps.transform.output }}"
```

### Tool Registry
- **Sources:** MCP servers (primary), HTTP endpoints, system tools (python_exec)
- **Discovery:** Auto-discovery from configured MCP servers
- **Caching:** Schema caching with refresh capability

### Error Model
- **Errors are data:** Structured, consistent, queryable
- **Categories:** TOOL, EXECUTION, VALIDATION, WORKFLOW, SYSTEM
- **Actionable:** Every error includes suggestion and docs link

### Configuration Model
- **4 Layers:** Step → Workflow → System Config → Hardcoded Defaults
- **Precedence:** Higher layer wins, merge semantics apply
- **Hot-reload:** Config changes without restart

### Logging
- **Hierarchical:** Workflow → Step → Tool Call → Sandbox
- **Colored:** Visual distinction between components
- **Formats:** Human-readable (dev), JSON (production)

### MCP Integration
- **Exposes:** Individual tools + workflows as tools
- **Prefix:** `workflow:name` for workflow tools
- **Passthrough:** Direct tool access when needed

---

## Implementation Status

| Component | Status | Tests |
|-----------|--------|-------|
| Shared Types | ✅ | Part of 319 |
| Logger | ✅ | Part of 319 |
| Error Registry | ✅ | Part of 319 |
| Config Loader | ✅ | Part of 319 |
| MCP Client Manager | ✅ | Part of 319 |
| Tool Registry | ✅ | Part of 319 |
| Template Engine | ✅ | Part of 319 |
| Workflow Registry | ✅ | Part of 319 |
| Python Exec Sandbox | ✅ | Part of 319 |
| Tool Invoker | ✅ | Part of 319 |
| Workflow Engine | ✅ | Part of 319 |
| MCP Frontend | ✅ | Part of 319 |
| CLI | ✅ | Part of 319 |
| Self-Config Tools | ✅ | Part of 319 |

---

## Future Architecture (Not Yet Implemented)

| Feature | Phase | Document |
|---------|-------|----------|
| Library Tiers | Phase 2+ | [04_LIBRARY_MANAGEMENT.md](04_LIBRARY_MANAGEMENT.md) |
| Plugin Framework | Phase 2 | [05_PLUGIN_FRAMEWORK.md](05_PLUGIN_FRAMEWORK.md) |
| REST API | Phase 3 | [14_REST_API_SURFACE.md](14_REST_API_SURFACE.md) |
| Telemetry Store | Phase 3 | [15_TELEMETRY_MODEL.md](15_TELEMETRY_MODEL.md) |
| Premium Features | Phase 4+ | [17_PREMIUM_FEATURES_OVERVIEW.md](17_PREMIUM_FEATURES_OVERVIEW.md) |

---

## Design Decisions Reference

See [decisions/DECISION_LOG.md](../decisions/DECISION_LOG.md) for full decision log (DEC-001 to DEC-064).


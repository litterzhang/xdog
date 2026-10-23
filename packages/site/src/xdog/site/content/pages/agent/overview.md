---
title: Overview
---

*One reusable tool-calling loop, with host-owned composition and permissions.*

`xdog-agent` is the Linux-only Python 3.12+ runtime above [AI](/packages/ai).
It streams model responses, validates and executes tool calls, appends paired
results, applies steering/follow-ups and retains conversation state. Tool calls
run in parallel by default; preparation/finalization hooks are serial.

An `AgentTool` is an explicit name, description, parameter schema and async
executor. A bare `Agent(...)` has no bash/filesystem defaults and discovers no
capabilities automatically. Hosts supply tools, permission hooks and resources.

## Highlights

- One model/tool loop with events, identified inputs and owned cancellation
- Host-owned `ToolRegistry`; separate `McpRegistry` consumed directly by Agent
- Versioned snapshots with live runtime/permission reinjection, not automatic replay
- Built-in compaction with automatic mode **off by default**
- Capability SDK composition and skill discovery separate from body activation
- Inert child handles/contracts; Coding and Claw own child execution runtimes

The SDK has no global tool-registration runtime, child supervisor service or
implicit security sandbox. Extension workers contribute hooks, commands, UI and
providers, not model tools. Skill text and source/role labels grant no authority.

## Try it

From the workspace root after `uv sync`:

```bash
uv run xdog-agent --help
uv run xdog-agent login
uv run xdog-agent chat copilot/claude-sonnet-4.5 "Explain this runtime" --no-tools
```

`--no-tools` skips tool creation and application capability discovery/startup.
Without it, the CLI creates default bash/filesystem/current-time tools, injects
web search and opens installed application capabilities, which may start owned
resources. Tool registration is not approval; use trusted environments and supply
host permission policy when embedding. Model IDs depend on the active catalog.

Standalone installation: `pip install xdog-agent`; ACP is the optional
`xdog-agent[acp]` extra. See [design](/packages/agent/design),
[reference](/packages/agent/reference) and the
[full SDK README](https://github.com/litterzhang/xdog/blob/main/packages/agent/README.md).

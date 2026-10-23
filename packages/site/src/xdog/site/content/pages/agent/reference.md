---
title: Reference
---

Public Python imports use `xdog.agent`, not `agent`. Requires Linux/Python 3.12+.
Hosts retain permissions, resources and storage ownership.

## Core API

| Name | Import | Purpose |
|---|---|---|
| `Agent` | `xdog.agent` | Stateful conversation, events, tool loop and input queues. |
| `AgentConfig` | `xdog.agent` | Model/prompt/options, parallel tools by default, opt-in compaction and event settings. |
| `agent_loop / run_agent_loop` | `xdog.agent` | Lower-level loop and runner for explicit host integration. |
| `AgentTool / AgentToolResult` | `xdog.agent` | Executor definition and typed result/content/error. |
| `StreamFn / EmbedFn / WebSearchFn` | `xdog.agent` | Injected model/search/embedding contracts. |
| `stream_fn_from_provider` | `xdog.agent` | Adapter over the actual AI provider/runtime and cached metadata. |
| `ToolDef / action / Param` | `xdog.agent` | Declarative multi-action tool definitions. |
| `ToolRegistry / ToolContext` | `xdog.agent.tools` | Host-local factory catalog and ordinary tool bindings. |
| `McpRegistry` | `xdog.agent.mcp` | Separate MCP connections/proxies; inject into Agent, not ToolRegistry. |
| `CapabilitySet / CapabilityAssembly / ApplicationPort` | `xdog.agent.capabilities` | Explicit installed-capability owner startup and tool/skill composition. |
| `SkillManager / SkillPlacement / create_skill_tool` | `xdog.agent.skills` | Discovery, prepared body placement and optional read-only list/load tool. |
| `CompactionConfig` | `xdog.agent` | Built-in compaction policy; `auto=False` by default. |
| `ChildClient / ChildHandle / ChildBackend / ChildSpec / ParentHandle` | `xdog.agent` | Host-backed child contracts, not a child runtime/store/supervisor. |

A basic SDK call (login/network needed for generation):

```python
import asyncio
import xdog.ai as ai
from xdog.agent import Agent, AgentConfig, stream_fn_from_provider

async def main():
    async with Agent(
        stream_fn_from_provider(ai.provider("copilot")),
        config=AgentConfig(model="claude-sonnet-4.5"),
    ) as agent:
        stream = await agent.prompt("Explain host-owned tools")
        async for event in stream:
            print(event.type)
        await stream.result()  # Completion only, not the retained history.
        print(len(agent.messages))

asyncio.run(main())
```

Consume events while the producer runs: the default 256-event buffer applies
backpressure. Awaiting only `result()` can stall. Bare Agent creates no default
shell/filesystem tools or capabilities.

## Built-in tools and registry

| Tool | Availability | Actions / purpose |
|---|---|---|
| `filesystem` | Default ToolRegistry factory selection, not automatic Agent installation | read · write · delete · edit · ls · grep · find. |
| `bash` | Default factory selection | Shell commands. |
| `current_time` | Default factory selection | Current time. |
| `submit_result` | Opt-in factory or `create_submit_result_tool()` from `xdog.agent.tools` | Validate structured output against injected `result_schema`; retain in `result_sink["result"]`. |
| `web_search` | Explicit tool or injected `web_search_fn` | Provider-backed search. |
| `embed` | Explicit tool or injected `embed_fn` | Provider-backed embedding. |
| `skill` | Explicit `create_skill_tool(manager, ...)` | Read-only list/load; no activation, unload, execution or permission changes. |

A registry example inside an async host with an existing idle `agent`:

```python
from pathlib import Path
from xdog.agent.tools import ToolContext, ToolRegistry

registry = ToolRegistry()
created = registry.create_tools(["current_time"], context=ToolContext(cwd=Path.cwd()))
binding = registry.replace_source("host", created)
registry.publish(agent)
# Later: remove this revision, then publish the reduced inventory explicitly.
binding.close()
registry.publish(agent)
```

`create_tools(None)` selects defaults; `create_tools([])` selects none.
Creation, binding and publication are separate. Duplicate names fail; removing a
binding does not close resources. `agent.local_tools` is ordinary inventory,
`state.tools` is combined inventory and `active_tools` is effective selection.
MCP registration does not connect; hosts explicitly connect and close their own
registry after Agent cleanup. Extensions cannot register model tools.

For `submit_result`, inject a new shared sink per collection through
`tool_ctx={"result_schema": schema, "result_sink": sink}`. The outer context is
shallow-copied by ToolDef dispatch; the nested sink carries the accepted value.
JSON Schema objects with `type`/`properties` and legacy flat field/type mappings
are supported; no schema accepts any JSON value. Validation errors return
`Error: ...` for correction. Old `flow_output_schema`/`flow_result_sink` keys are
rejected. Acceptance neither stops the Agent nor proves durable task completion;
the caller must check the sink and overall outcome.

## Lifecycle and composition

| API | Contract |
|---|---|
| `queue_input / pending_inputs / change_input / drain_inputs` | Identified steer/follow-up inputs on the original owning loop; consumption is not mere queue acceptance. |
| `dump / restore` | Settled versioned JSON; current tools, permissions, providers and registries stay host-owned. |
| `dump_metadata / restore_parts` | Separately retained long history with per-part validation and complete tool pairs. |
| `dump(recovery=True)` | Diagnostic recovery export; no authority to replay uncertain effects. |
| `compact(instructions="")` | Idle manual compaction; does not enable auto mode or archive to disk. |
| `AgentConfig(compaction=CompactionConfig(auto=True))` | Explicit auto opt-in; real adapter metadata or trusted limits/reserves required. |
| `abort / cancel_and_wait / wait_for_idle / aclose` | Cooperative stop request, owned producer cancellation/join, passive wait and owned cleanup respectively. No effect rollback. |
| `CapabilitySet.start / aclose` | Explicit provider resource lifetime on a real owner; discovery alone is inert. |
| `CapabilityAssembly.create_agent` | Compose already-started Sets, base tools and selected/default skills into a native Agent. |
| `CapabilityAssembly.restore_agent / restore_agent_parts` | Validate saved bindings and merge current default session skills without rearming consumed turn bodies. |

Assembly `enabled_tools=None` includes all capability tools; `[]` includes none
and does not filter base tools. `declared_skills` is not activation; Flow/Eyes/Goal
bodies need explicit selection. Skill text never grants tools or approval.
Closing an Eyes binding detaches from its independent daemon, not daemon stop.
Close Agents before shared Sets; child Agents receive explicit grants, not the
whole application Set. Coding/Claw own child runtimes and persistence.

## CLI — `xdog-agent`

| Command | Does |
|---|---|
| `login` | Copilot login; use `xdog-ai login <provider>` for another provider. |
| `chat [model] [message]` | One-shot or TTY interactive conversation with system/sampling/thinking options. Use qualified model IDs for multi-provider routing. |
| `chat --no-tools` | Disable all tools and application capability discovery/startup. |
| `chat --tool-ctx JSON` | Shared context object or `@file`; does not enable submit_result by itself. |
| `acp [--model MODEL] [--no-tools]` | Optional standard stdio ACP adapter; requires the `acp` extra. |

Without `--no-tools`, the CLI prepares default registry tools, injects search and
opens application capabilities. It is not a host approval sandbox.

## Chat slash commands

| Command | Does |
|---|---|
| `/model [name]` | Show/switch model. |
| `/thinking [level]` | Show/set reasoning; interactive `off` clears the option. |
| `/image <path>` | Attach an image to the next input; model capability still matters. |
| `/tools` | List configured tools. |
| `/status` | Show session status, context estimate and token/cost information. |
| `/clear · /verbose · /help · /exit` | Conversation reset, diagnostics, help and exit. |

Full [SDK reference](https://github.com/litterzhang/xdog/blob/main/packages/agent/README.md),
[registry API](https://github.com/litterzhang/xdog/blob/main/docs/host-owned-tool-registry.md)
and [capability assembly example](https://github.com/litterzhang/xdog/blob/main/docs/sdk-capability-assembly.md).

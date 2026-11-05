> **历史归档，不是当前接口、待办或执行授权。**
> 原日期、结论和验证范围保留；当前入口见 [文档索引](../../README.md)。

# Coding Agent Tool Permission Design

**Date:** 2026-08-13
**Status:** Implemented
**Scope:** `xdog-coding`, using enforcement hooks already provided by `xdog-agent`

## Summary

The coding agent should pause before a tool executes when policy requires user approval. The user can allow the call once, allow a narrowly scoped rule for the current session, or deny it. Enforcement belongs immediately before tool execution—not in the prompt—so a model cannot bypass it.

`xdog-agent` already provides the required enforcement point. `_prepare_tool_call()` validates arguments and then awaits `before_tool_call`; a blocking `BeforeToolCallResult` prevents execution and returns the reason to the model as an error tool result. The main work is therefore a permission policy and an asynchronous approval UI in `xdog-coding`.

## Goals

- Ask before potentially mutating or dangerous tool calls.
- Support an optional mode that asks before every tool.
- Let the user allow once, allow for the session, or deny.
- Fail closed when approval is required but no approver is available.
- Show enough of the proposed operation for an informed decision.
- Keep policy independent of the model and system prompt.
- Support interactive TUI, print, and RPC execution modes deliberately.

## Non-goals

- Treating approval prompts as an operating-system sandbox.
- Proving arbitrary shell commands safe through command parsing.
- Persisting broad permanent allow rules in the first implementation.
- Asking the model whether its own tool call is safe.

## Existing enforcement point

`xdog.agent.agent_loop._prepare_tool_call()` currently performs this sequence:

1. Resolve the tool.
2. Validate tool arguments.
3. Await `AgentLoopConfig.before_tool_call`.
4. Return an immediate error result when the hook blocks.
5. Execute the tool only after the hook permits it.

`Agent` accepts the hook in its constructor and also exposes `set_before_tool_call()`. A denied call is represented by:

```python
BeforeToolCallResult(
    block=True,
    reason="User denied this tool call",
)
```

The model receives that reason as a tool error and can explain the denial or try a safer alternative.

## Permission modes

The coding CLI should support four modes:

| Mode | Behavior |
|---|---|
| `ask` | Automatically permit known read-only calls; ask for mutating, dangerous, or unknown calls. |
| `ask-all` | Ask before every tool call. |
| `allow-all` | Execute without asking. Intended for explicitly trusted automation. |
| `deny` | Deny every call that is not automatically permitted by policy. |

Recommended defaults:

- Interactive TUI: `ask`
- Non-interactive print mode without a TTY: `deny`
- RPC: request approval through the RPC protocol
- CI or unattended execution: require an explicit `allow-all`

## Default classification policy

The initial built-in policy should be small and auditable:

| Call | `ask` behavior |
|---|---|
| `current_time` | Allow |
| `filesystem` action `read`, `ls`, `grep`, or `find` | Allow |
| `filesystem` action `write`, `edit`, or `delete` | Ask |
| `bash` | Ask |
| Unknown or extension-provided tool | Ask |

The filesystem tool is multi-action, so classifying only by tool name is incorrect. Policy must inspect `args["action"]`.

Shell commands should be treated as dangerous rather than parsed in an attempt to prove safety. Shell syntax, aliases, scripts, subprocesses, and indirect writes make a reliable allow classifier impractical.

## User decisions

An approval prompt offers:

1. **Allow once** — permits only this call.
2. **Allow for this session** — installs a narrow in-memory rule.
3. **Deny** — blocks the call and tells the model it was denied.

A session rule should not mean “allow every future bash command.” Initial matching should be conservative, for example:

- exact tool name plus filesystem action and normalized path scope;
- exact shell command, or no session option for shell commands initially;
- exact tool and normalized argument subset for extension tools.

Permanent rules can be designed later after the matching semantics are stable.

## Core types

Add `xdog.coding.core.permissions` with types similar to:

```python
PermissionMode = Literal["ask", "ask-all", "allow-all", "deny"]
PermissionDecision = Literal["allow_once", "allow_session", "deny"]

@dataclass(frozen=True)
class PermissionRequest:
    id: str
    tool_name: str
    arguments: dict[str, Any]
    summary: str
```

A `PermissionManager` owns:

- the selected mode;
- the built-in classifier;
- session allow rules;
- pending approval requests;
- a callback or subscription API for approval front ends;
- `before_tool_call(ctx, cancel)` for attachment to `Agent`.

## Approval flow

```text
model emits tool call
        │
        ▼
agent validates arguments
        │
        ▼
PermissionManager classifies call
        │
        ├── automatically allowed ───────────────► execute
        │
        ├── denied by mode/no approver ─────────► blocked tool result
        │
        └── approval required
                 │
                 ▼
          publish PermissionRequest
                 │
                 ▼
          await user decision
                 │
          ┌──────┴────────┐
          ▼               ▼
       allow           deny/cancel
          │               │
          ▼               ▼
       execute       blocked tool result
```

## TUI concurrency

Interactive turns run in a background thread with their own asyncio event loop, while terminal rendering and input handling run in the main TUI thread. The permission hook must not call `input()` because that would compete with the raw-mode TUI.

The broker should use an asyncio future owned by the agent thread:

1. The hook creates a future and records its owning loop.
2. It publishes a permission request to the TUI event queue.
3. The TUI displays an approval selector.
4. The user's selection calls `PermissionManager.resolve()`.
5. `resolve()` uses `loop.call_soon_threadsafe(future.set_result, decision)`.
6. The hook resumes and allows or blocks the call.

The wait must race against the agent cancellation event. Escape, Ctrl+C, TUI shutdown, or a disconnected front end must deny/cancel every pending request so no agent thread remains suspended.

## TUI presentation

The existing `SelectList` component can present a focused approval panel:

```text
The agent wants to run:

  bash
  uv run pytest packages/coding/tests -q

→ Allow once
  Allow for this session
  Deny
```

While a request is pending:

- status reads `awaiting permission`;
- normal message submission is disabled;
- input is routed to the approval component;
- Escape denies or cancels;
- the proposed tool and relevant arguments remain visible;
- secrets in arguments are redacted before display.

`ToolExecutionStartEvent` is currently emitted before the permission hook. The UI should distinguish “requested/awaiting approval” from “executing”; alternatively, a future generic permission event can be added to `xdog-agent`, but the first implementation can keep approval events local to `xdog-coding`.

## CLI and configuration

Add `permission_mode` to global, project, and resolved runtime configuration:

```json
{
  "permission_mode": "ask"
}
```

Add a CLI override:

```text
--permission-mode ask|ask-all|allow-all|deny
```

The existing `allowed_tools` setting is not an approval mechanism. Tool availability and permission to execute are separate concerns and should remain separate.

## Print mode

A non-interactive process must never hang waiting for input that cannot arrive.

- If stdin/stderr is an interactive TTY, a simple terminal approver may prompt on stderr.
- Otherwise, calls requiring approval are denied.
- `--permission-mode allow-all` is the explicit escape hatch for trusted automation.
- JSON output must not be corrupted by human prompts on stdout.

## RPC mode

RPC should emit:

```json
{
  "type": "permission_request",
  "id": "req-123",
  "tool": "bash",
  "arguments": {"command": "pytest"},
  "summary": "Run: pytest"
}
```

The client responds with:

```json
{
  "type": "permission_response",
  "id": "req-123",
  "decision": "allow_once"
}
```

The current RPC loop awaits an entire prompt before reading the next command. It must be refactored so command reading continues while a turn runs; otherwise it cannot receive the permission response needed to unblock that turn.

Unknown request IDs and duplicate responses should produce protocol errors. Client disconnect denies all pending requests.

## Security properties

- **Fail closed:** malformed policy data, absent approvers, UI failure, and unknown tools deny calls requiring approval.
- **Argument-aware:** classification happens after schema validation and sees normalized call arguments.
- **Redaction:** UI and logs must avoid printing credentials, tokens, authorization headers, or secret environment values.
- **No prompt enforcement:** system-prompt instructions may describe policy but never replace the hook.
- **No TOCTOU mutation:** the exact validated arguments shown for approval are the arguments passed to execution.

Permission prompts are not a sandbox. An approved bash call still has the operating-system permissions of the coding CLI. Strong isolation requires workspace confinement, restricted environment exposure, process limits, container/OS sandboxing, and optional network isolation.

## Testing plan

### Policy tests

- Read-only filesystem calls are allowed in `ask` mode.
- Mutating filesystem calls request approval.
- Bash and unknown tools request approval.
- `ask-all`, `allow-all`, and `deny` behave as documented.
- Malformed filesystem arguments fail closed.
- Session rules match only their intended scope.

### Broker tests

- Allow once resumes exactly one pending call.
- Deny returns a blocking result and never invokes the tool.
- Cancellation resolves pending waits.
- Cross-thread resolution uses the owning event loop safely.
- Shutdown denies all unresolved requests.

### TUI tests

- A request displays tool name, summary, and choices.
- Normal submission is suspended while approval is focused.
- Allow, deny, and Escape resolve the correct request.
- Arguments are redacted before display.

### Mode tests

- Non-TTY print mode fails closed.
- Explicit `allow-all` works in automation.
- RPC can receive a permission response during a running turn.
- RPC disconnect cancels pending approvals.

## Implementation sequence

1. Add permission types, policy classifier, and unit tests.
2. Add the asynchronous broker and cancellation tests.
3. Wire the manager through `RuntimeConfig`, SDK construction, and `AgentSession`.
4. Add CLI/config modes.
5. Add the TUI approval selector and thread-safe resolution.
6. Add safe print-mode behavior.
7. Refactor RPC for concurrent input and add permission protocol messages.
8. Document sandbox limitations and add end-to-end tests.

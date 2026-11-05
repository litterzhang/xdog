> **历史归档，不是当前接口、待办或执行授权。**
> 原日期、结论和验证范围保留；当前入口见 [文档索引](../../README.md)。

# Claw Abstraction Review — Where to Simplify

**Date**: 2026-04-08

## Current layer stack

```
Gateway  →  Orchestrator  →  GroupRuntime  →  SessionManager  →  AgentSession  →  Agent
  (socket)    (routing)       (resources)      (persistence)     (turn lifecycle)   (LLM loop)
```

Six layers. The question: does each layer earn its existence?

## Layer-by-layer analysis

### Gateway: correct abstraction, wrong responsibilities

The Gateway is a Unix socket daemon. It should do: accept connections, parse JSON, write responses, run background loops. It does all that correctly.

But it also does things the Orchestrator should do:
- `_handle_chat_message()` constructs `UserInput`, defines streaming callbacks (`on_text_delta`, `on_todo_update`, `on_goal_update`), calls `route_message()`, and decides how to format the response (`"final"` vs `"response"`). This is presentation logic mixed into the transport layer.
- `_scheduler_loop()` checks `orchestrator.scheduler.get_due_tasks()` and calls `orchestrator.run_scheduled_task()`. The Gateway polls, but the Orchestrator knows when tasks are due. The scheduling policy belongs in the Orchestrator; the Gateway should just call `orchestrator.tick()`.
- `_goal_cooldown_ok()` tracks per-group cooldown timestamps. Goal execution policy is an Orchestrator concern, not a transport concern.
- `_handle_status()` and `_handle_reset()` reach into `orchestrator.get_runtime()` to access `session_manager`, `goal_tracker`, and model metadata. The Gateway knows about SessionManager, GoalTracker, and model internals.

**Simplification**: The Gateway should be a thin transport layer. It receives JSON, calls `orchestrator.route_message()`, and forwards the result. The Orchestrator should own scheduling policy, goal runner policy, and status queries. The Gateway just calls `orchestrator.tick()` on a timer.

### Orchestrator: correct role, but SessionManager leaks through

The Orchestrator routes messages and manages concurrency. This is correct.

But it reaches through GroupRuntime to access SessionManager for steering and follow-up:
```python
sm = runtime.session_manager
sm.steer(group_id, msg.content)
sm.follow_up(group_id, msg.content)
```

And it reaches through GroupRuntime to access GoalTracker:
```python
runtime.goal_tracker.has_running_tasks()
runtime.goal_tracker.build_active_summary()
```

The Orchestrator shouldn't need to know that `session_manager` and `goal_tracker` exist. These are internal resources of the group. The Orchestrator should call methods on GroupRuntime directly:
```python
runtime.steer(msg.content)
runtime.has_running_goals()
```

GroupRuntime already has `build_system_prompt()` and `goals_summary()` as facade methods. `steer()` and `has_running_goals()` would follow the same pattern.

### GroupRuntime: parameter bag, not an abstraction

GroupRuntime holds 15 fields. Its `create()` factory is where all the initialization happens. Its methods are either facades (`build_system_prompt()`, `goals_summary()`) or lazy properties (`tools`).

The problem: GroupRuntime is a god object because AgentSession needs all 15 fields. AgentSession's `__init__` unpacks GroupRuntime field by field:
```python
self._group_id = runtime.group.id
self._model = runtime.model or "test/dummy"
self._stream_fn = runtime.stream_fn
self._tools = list(runtime.tools)
self._session_manager = runtime.session_manager
self._workspace_dir = runtime.workspace_dir
self._reindex_fn = runtime.reindex_fn
self._context_window = runtime.context_window
self._agent_config = runtime.agent_config
```

Then run_turn() reaches back into runtime for compaction:
```python
self._runtime.flush_runner.run(...)
self._runtime.summarizer.summarize(...)
self._runtime.build_system_prompt()
self._runtime.goals_summary()
```

AgentSession copies some fields from runtime into itself, then still uses runtime directly for other fields. There's no clear line between "what AgentSession owns" and "what it borrows from runtime."

**Simplification**: AgentSession should take runtime and use it directly — not copy fields out. The `__init__` becomes:
```python
def __init__(self, runtime: GroupRuntime, session_meta: SessionMeta):
    self._runtime = runtime
    self._meta = session_meta
    self._agent = Agent(runtime.stream_fn, config=..., tools=runtime.tools, ...)
```

The dual-path `__init__` (runtime vs direct params for testing) goes away. Tests create a GroupRuntime with the `__init__` directly (no factory) and pass it in. This is why we made GroupRuntime's `__init__` accept all fields — so tests can construct it without the `create()` factory.

### SessionManager: two unrelated responsibilities

SessionManager does two things:
1. **JSONL persistence**: create/load/append/replace transcripts, manage session index
2. **AgentSession lifecycle**: `get_or_create_agent_session()`, caching, resets, `steer()`/`follow_up()`/`abort()`

These are unrelated. The persistence methods don't know about AgentSession. The lifecycle methods don't know about JSONL. They share a class because they both need `sessions_dir`, but that's it.

The lifecycle methods also create a Law of Demeter violation: the Orchestrator calls `runtime.session_manager.get_or_create_agent_session(runtime)`, passing `runtime` back to its own child. The session manager creates an AgentSession that takes the runtime. This is circular: runtime owns session_manager, session_manager creates sessions using runtime.

**Simplification**: Split into:
- `TranscriptStore` — pure JSONL persistence (create, load, append, replace, branch)
- Keep `get_or_create_agent_session()` on GroupRuntime itself — it already has all the context

GroupRuntime becomes:
```python
class GroupRuntime:
    def get_or_create_session(self) -> AgentSession:
        # reset checks, caching, all in one place
```

The Orchestrator calls `runtime.get_or_create_session()` instead of `runtime.session_manager.get_or_create_agent_session(runtime)`.

### AgentSession: correct abstraction, just too coupled

AgentSession owns the Agent and runs turns. This is correct. After the Phase 2 refactoring, the turn is clean: rebuild prompt → compact → prompt → drain → persist.

The coupling issue is the dual-path constructor. With the simplification above (always take runtime), this goes away.

### Agent: correct abstraction, no changes needed

The agent package is the clean layer. Agent owns the tool loop, message history, event stream, steering queues. It knows nothing about claw. No changes.

## Proposed simplification

### Change 1: AgentSession always takes GroupRuntime

Remove the dual-path `__init__`. Always take `runtime`. Tests construct a `GroupRuntime(...)` directly with test values (this is why we made `__init__` accept all fields).

**Impact**: AgentSession.__init__ drops from 40 lines to 15. The `if runtime is not None: ... else: ...` branch disappears.

### Change 2: Split SessionManager into TranscriptStore + session lifecycle on GroupRuntime

Move `get_or_create_agent_session`, `steer`, `follow_up`, `abort`, caching, and reset logic to GroupRuntime. Rename what remains of SessionManager to `TranscriptStore` (or keep the name, but remove the lifecycle methods).

**Impact**: 
- `TranscriptStore` becomes a pure persistence object (no imports of AgentSession or GroupRuntime)
- GroupRuntime gains `get_or_create_session()`, `steer()`, `follow_up()`, `abort()`
- Orchestrator calls `runtime.get_or_create_session()` instead of `runtime.session_manager.get_or_create_agent_session(runtime)`
- The circular dependency (runtime → session_manager → AgentSession → runtime) becomes linear (runtime → AgentSession → Agent)

### Change 3: Move scheduling and goal runner policy into Orchestrator

Move `_scheduler_loop`, `_goal_cooldown_ok`, and the polling logic from Gateway into Orchestrator as `tick()`:

```python
class Orchestrator:
    async def tick(self) -> None:
        """Run one scheduler + goal runner cycle. Called by Gateway on a timer."""
        for task in self._scheduler.get_due_tasks():
            await self.run_scheduled_task(task)
        for group_id in self.get_group_ids():
            if self._should_run_goals(group_id):
                await self.run_goal_step(group_id)
```

Gateway becomes:
```python
async def _background_loop(self):
    while not self._shutdown_event.is_set():
        await asyncio.sleep(30)
        await self._orchestrator.tick()
```

**Impact**: Gateway drops from 450 lines to ~300. Gateway no longer imports GoalTracker or knows about cooldowns.

### Change 4: Add facade methods on GroupRuntime

```python
class GroupRuntime:
    def steer(self, content: str) -> None:
        session = self._active_session
        if session:
            session.steer(content)

    def has_running_goals(self) -> bool:
        return self.goal_tracker.has_running_tasks()
```

Orchestrator no longer reaches through `runtime.session_manager` or `runtime.goal_tracker`.

## After simplification

```
Gateway  →  Orchestrator  →  GroupRuntime  →  AgentSession  →  Agent
  (socket)    (routing +       (resources +     (turn lifecycle)  (LLM loop)
               scheduling)      session cache)

                              TranscriptStore  (pure JSONL persistence)
```

Five layers instead of six. SessionManager splits — its lifecycle half merges into GroupRuntime (where it belongs), its persistence half becomes a clean standalone component.

The key wins:
- No circular dependency (runtime → session_manager → AgentSession → runtime → ...)
- Gateway is pure transport (no scheduling policy, no goal tracking, no model metadata)
- AgentSession has one constructor path (always takes runtime)
- Orchestrator doesn't reach through runtime's children (calls facade methods)

## What I would NOT change

- **The compaction pipeline** — three clean stages, each with one job. Don't touch it.
- **The workspace-as-brain pattern** — markdown files are the right abstraction for agent memory.
- **The tool system** — ToolDef, registry, ctx-based resolution. Documented and tested.
- **Agent as a separate package** — the decoupling is real and valuable.
- **Frozen dataclasses for config** — Group, SessionMeta, Goal, ScheduledTask. Correct.

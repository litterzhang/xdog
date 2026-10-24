# Core Directory Organization

> **Historical / superseded SDK interface — 2026-10-24:** Historical implementation plan: global factory registration and lazy create_tools below are retired, not current architecture.
> Current integration uses ToolRepo / SkillRepo, explicit registration and provider
> selection before open; see [SDK capability assembly](../sdk-capability-assembly.md).
> Old names and result counts are retained as evidence, not compatibility aliases.


**Status**: Implemented | **Tests**: 523 passing

## Final layout

```
core/
  types.py              ← shared frozen types
  
  runtime/              ← the spine (request path)
    gateway.py          (546)  Unix socket transport
    orchestrator.py     (274)  routing, concurrency, tick()
    group.py            (321)  resource ownership, session lifecycle
    session.py          (409)  turn execution, compaction
    workspace.py         (59)  workspace file management
    queue.py             (99)  concurrency control
    
  persistence/          ← serialization
    transcript_store.py (212)  JSONL persistence
    transcript_convert.py (199) message ↔ dict conversion
    chunker.py          (114)  paragraph-aware text splitting
    
  compaction/           ← context management
    transcript.py       (161)  compact + archive
    flush_runner.py      (64)  pre-compaction agent turn
    summarizer.py        (69)  structured summary via LLM
    
  memory/               ← memory subsystem (registers "memory" tool)
    manager.py, daily_log.py, long_term.py, flush.py,
    indexer.py, search.py, simple_search.py
    
  goal/                 ← goal domain (registers "goal" tool)
    tracker.py          (293)
    
  task/                 ← task domain (registers "task" tool)
    scheduler.py        (104)
    
  tools/                ← ALL tool definitions + registry
    registry.py          (68)  pure dict, lazy init
    tool_goal.py        (100)  GoalTool(ToolDef)
    tool_task.py         (97)  TaskTool(ToolDef)
    tool_memory.py       (66)  MemoryTool(ToolDef)
    todo_write.py        (52)  TodoWriteTool(ToolDef)
    send_message.py      (29)  raw AgentTool
```

## Registration pattern: domain owns its tool

Each domain package registers its own tool on import:

```text
# goal/__init__.py
from claw.core.tools.registry import register
from claw.core.tools.tool_goal import create_goal_tool
register("goal", create_goal_tool)

# task/__init__.py
from claw.core.tools.registry import register
from claw.core.tools.tool_task import create_task_tool
register("task", create_task_tool)

# memory/__init__.py
from claw.core.tools.registry import register
from claw.core.tools.tool_memory import create_memory_tool
register("memory", create_memory_tool)
```

The registry triggers domain imports lazily on first `create_tools()`:

```text
def _register_builtins() -> None:
    # Agent built-in tools (no domain)
    register("current_time", create_current_time_tool)
    register("filesystem", create_filesystem_tool)
    register("bash", create_bash_tool)
    register("todo_write", create_todo_write_tool)
    
    # Trigger domain self-registration
    import claw.core.goal    # registers "goal"
    import claw.core.task    # registers "task"
    import claw.core.memory  # registers "memory"
```

**Why this is better**: The tool definition lives in `tools/` (easy to find), but the registration decision belongs to the domain. If someone adds a new domain with a tool, they add `tool_xxx.py` to `tools/` and `register()` in their domain `__init__.py`. The registry never needs to be edited.

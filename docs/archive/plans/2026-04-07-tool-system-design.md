> **历史归档，不是当前接口、待办或执行授权。**
> 原日期、结论和验证范围保留；当前入口见 [文档索引](../../README.md)。

# Tool System Design

**Date**: 2026-04-08
**Tests**: 522 passing (37 dedicated to ToolDef)

---

## How it works

```python
class MemoryTool(ToolDef):
    name = "memory"
    required_ctx = ("workspace_dir",)

    @action("get", description="Read a file")
    async def get(self, ctx, filename: str):
        return Path(ctx["workspace_dir"]).joinpath(filename).read_text()

    @action("search", description="Search by keyword")
    async def search(self, ctx, query: str, top_k: int = 5): ...

tool = MemoryTool().build()  # -> AgentTool
```

`build()` generates JSON Schema, dispatch, description, and validation from the decorators and type hints. The Agent holds `tool_ctx: dict` and threads it to every `tool.execute(...)`. The agent package never inspects ctx.

## Tools

| Tool | Actions | required_ctx |
|------|---------|-------------|
| filesystem | read, write, delete, edit, ls, grep, find | — |
| current_time | (single) | — |
| bash | (single, raw) | — |
| memory | get, search, write | workspace_dir |
| goal | create, list, update_task, add_task, complete_goal, abandon_goal | data_dir, group_id |
| task | schedule, cancel, list | — |
| todo_write | (single) | — |
| send_message | (single, raw) | — |

## What the framework handles

- **Schema**: JSON Schema from `@action` decorators + type annotations. Universally-required params in `required` array. Per-action `[action]` annotations on non-universal param descriptions.
- **Dispatch**: Route by `action` param → handler method. Validate required params before calling.
- **Inference**: `param: str` → required string. `param: int = 5` → optional integer with default. Explicit `Param()` overrides type/description/enum/items; `required` inherits from handler signature when not explicitly set.
- **Validation**: `required_ctx` checked at dispatch time. Required params checked before handler call.
- **Description**: Auto-appended action summary with required/optional param lists.

---

## Assessment

### What's right

The framework solves a real problem and solves it correctly. Schema, validation, and description are generated from the same Param declarations. The decoupling between agent and claw is structurally enforced. Type inference handles the common case and escapes cleanly to explicit `Param()` for enum/items. Required-param enforcement returns clear errors. All paths are tested.

### What I still think could be better

**1. ToolDef serves two unrelated patterns through one class.**

Multi-action tools use `@action` decorators, `_build_multi_action_schema`, and `_build_multi_action_dispatch`. Single-action tools use `execute()`, `_build_single_action_schema`, and `_build_single_action_dispatch`. They share `name`, `description`, `required_ctx`, and `_validate_ctx`. That's it — four lines of shared logic in a 500-line file.

This works. It's not a bug. But it means `build()` has an `if/else` that selects between two entirely separate code paths. A reader has to understand both to understand either. And the `params` class attribute only applies to the single-action path, which is confusing when reading the class definition.

I wouldn't split them today — the codebase has exactly 2 single-action ToolDef tools (current_time, todo_write). But if more single-action tools appear and the paths diverge further, a `SimpleToolDef` base class would be cleaner.

**2. `_action_counter` is process-global.**

Same as before. Harmless, works, relative ordering is all that matters. Would be more correct as a per-class counter but not worth the added complexity.

**3. `except Exception` swallows bugs.**

Same as before. Logged via `logger.exception()`. Standard pattern. Right default for production, mildly annoying for development.

### What was fixed in this round

- **Path traversal vulnerability** in MemoryTool: `str.startswith()` replaced with `path.is_relative_to(ws)`. Two new tests including the prefix-attack scenario.
- **TaskTool config singleton**: now reads `tasks_file` / `data_dir` from ctx, consistent with every other tool. Tests use ctx instead of monkeypatching.
- **`Param.required` default footgun**: explicit `Param("string", description="...")` now inherits `required` from the handler signature via `_UNSET` sentinel. Writing `Param("string")` on a `name: str` param correctly becomes required. Writing `Param("string", required=False)` explicitly is honored. Four new tests.
- **GoalTool copy-paste actions**: `start_task`/`complete_task`/`skip_task` collapsed into `update_task` with a `status` enum. 8 actions → 6. Same expressiveness, no redundancy.

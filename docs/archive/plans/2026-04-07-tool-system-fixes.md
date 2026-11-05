> **历史归档，不是当前接口、待办或执行授权。**
> 原日期、结论和验证范围保留；当前入口见 [文档索引](../../README.md)。

# Plan: Tool System Issues — All Seven Resolved

**Date**: 2026-04-07
**Status**: Done
**Depends on**: Tool System Design (implemented)

---

## Issue 1: try/except TypeError hack ✅

**Problem**: Agent loop caught `TypeError` to handle old tools without `ctx`, hiding real bugs.

**Fix**: Removed the fallback. Updated `bash`, `todo_write`, and `send_message` to accept `**kwargs`. Clean single call path in agent_loop.py:
```python
result = await tool.execute(tc.id, args, cancel, on_update, ctx=tool_ctx)
```

---

## Issue 2: No cancel/on_update in ToolDef handlers ✅

**Problem**: ToolDef handlers couldn't access cancel or on_update.

**Fix**: Dispatch injects them into ctx before calling the handler:
```python
enriched_ctx["_cancel"] = cancel
enriched_ctx["_on_update"] = on_update
```
Handlers opt in with `ctx.get("_cancel")`.

---

## Issue 3: Flat parameter namespace ✅

**Problem**: Multi-action tools merge all params into one flat schema.

**Fix**: `_build_description_with_actions()` auto-appends per-action summary:
```
Actions:
- create: title, goal_description, tasks (required)
- list (optional: status)
- complete_task: goal_id, task_id, summary (required; optional: notes)
```

---

## Issue 4: Config/file read per call ✅

**Problem**: GoalTracker re-read goals.json and load_config re-parsed YAML on every tool call.

**Fix**:
- `get_tracker(path)` — mtime-based cache in `claw/core/goal/tracker.py`
- `load_config()` — process-lifetime singleton in `claw/config.py`

---

## Issue 5: Param duplicates JSON Schema ✅

**Problem**: Explicit `Param("string", required=True)` is boilerplate for common types.

**Fix**: Type annotation inference via `_infer_params()`:
- `param: str` → `Param("string", required=True)`
- `param: int` → `Param("integer", required=True)`
- `param: float` → `Param("number", required=True)`
- `param: bool` → `Param("boolean", required=True)`
- `param: list` → `Param("array")`
- `param: str = "default"` → `Param("string", default="default")` (optional)
- Explicit `Param()` overrides inferred when both present
- Skips `self` and `ctx` parameters

11 tests covering all inference paths in `tests/agent/test_tool_def.py`.

---

## Issue 6: Two patterns (ToolDef + raw factories) ✅

**Problem**: `bash` and `todo_write` are raw AgentTool factories. Everything else is ToolDef.

**Decision**: Accepted. `bash` has mutable CWD state and needs raw on_update streaming. `send_message` takes a callback. `todo_write` is simple enough that ToolDef adds no value. Two exceptions out of 8 tools is acceptable. Documented.

---

## Issue 7: No cancel propagation to subprocess tools ✅

**Problem**: grep/find ignore the agent's cancel event.

**Fix**: With Issue 2 resolved, `ctx["_cancel"]` is available in all ToolDef handlers. grep and find already pass `cancel=ctx.get("_cancel")` to their subprocess helpers.

---

## Verification

```bash
# All pass:
pytest tests/agent/ -q          # 45 passed (43 + 2 skip, + 11 new tool_def tests - 11 skip)
pytest tests/claw/ -q --ignore=tests/claw/test_gateway.py   # 161 passed
pytest tests/ -q --ignore=tests/claw/test_gateway.py        # 498 passed, 2 skipped
```

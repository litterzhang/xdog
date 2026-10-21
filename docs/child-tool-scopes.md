# Scoped child capability tools

Installed capabilities remain parent-only by default. A host can now delegate a
capability tool only when the original parent explicitly selects that actual tool
and supplies a provider-validated, finite data scope. The SDK/hosts do not know
Goal schemas or create feature-owned child runners.

Example child configuration fragment (the original host still owns spawn/access):

```json
{
  "tools": ["goal"],
  "tool_actions": {"goal": ["tasks", "task_update"]},
  "tool_scopes": {
    "goal": {"goal_id": "EXACT_CURRENT_GOAL_ID", "task_ids": ["assigned-task"]}
  }
}
```

Writing requires the child's existing `access="inherit"` and the original parent's
live permissions; a scope never overrides readonly/deny/ask-all or grants bash.
Ordinary children do not implicitly receive Goal. A Goal task worker needs only
its narrow tool description and explicit first-user task, not the parent's full
Goal-management skill or conversation.

## Generic boundary

- `ChildConfig`/`ChildSpec.tool_scopes` contains bounded finite JSON, not code,
  module names to import, callable objects, credentials or runtime handles.
- `CapabilitySet.can_delegate_child_tool(actual_tool)` recognizes the exact live
  binding, not a name, copied tool, discovery metadata or retired definition.
- An optional installed binding `delegate_child_tool(actual_tool, spec, scope)`
  returns the same tool name with a narrower schema/executor. Factory, validator
  and effects use the existing provider owner loop; there is no second loop.
- Actual source identity, original and live schema, child action restrictions,
  scoped schema and saved scope ceiling all apply. Advertisement is narrow too.
  Explicit follow-up validates scopes before persisting/admitting new child input.
- Scoped calls use the normal host `protect`/approval/read-only paths, never the
  older role-bound capability exemption. Effect classification comes from the
  actual original source; labels and `readOnlyHint` do not grant permissions.
- Current scoped intersections support inline schemas. Reference-bearing or
  legacy root object-enum scope schemas fail closed; ordinary unscoped child
  compatibility is unchanged. Scope data cannot request code loading.

## Goal provider

Goal owns its hook and task operation implementation in `xdog.skills.goal`, not in
Coding/Claw. It validates the current Goal and explicit existing task IDs at
admission. The child sees only `tasks(task_id=...)` and
`task_update(task={id, expected_version, status/summary/evidence})`.

The executor independently validates scope and CAS under the same existing task
write lock. It reads only the granted file, not an all-task scan. It cannot create
a task, change worker/dependencies/title, write Goal reports/plans, activate,
resume, complete or clear the Goal. Original accepted workers may report while a
Goal is paused/waiting without releasing that state. Retired parents, replaced or
terminal Goals and revoked grants reject subsequent operations.

The main Agent assigns one writer per task and verifies evidence. Main maintains
its own execution/acceptance tasks and Goal reports, not a duplicate worker status.
`task_cli` remains an explicitly permission-gated fallback for external workers or
hosts without scoped tools, never an automatic escape after delegation denial.

## Evidence for v2.4.5

- Domain task safety: `packages/skills/tests/goal/test_child_tools.py` (90 cases).
- Actual Goal capability admission: `test_child_capability.py` (11 cases).
- Generic Agent/host scope tests include unchanged parent capability defaults,
  copied identity rejection, approval/read-only fencing and continuation ceilings.
- Real Coding/Claw Goal child cases: 22 passed; no bash/tool inheritance shortcut.
- Real Eyes/Unix ACP loop now uses the scoped child Goal tool and parent
  status/report/activate/complete, retaining original receipt identities and all
  child/draft/producer deferral cases. This uses deterministic test models, not
  live provider model calls, and does not claim long-term uptime verification.

---
title: Reference
---

## Native commands inside Coding/Claw

| Command | Purpose |
|---|---|
| `/goal` or `/goal status` | Human-readable current Goal, next actor and distinct runtime facts. |
| `/goal create ...` | Create the owner's current Goal; no worker is started. |
| `/goal pause ...` | Stop proactive continuation/owned observation. |
| `/goal resume` | Explicitly resume a paused Goal under actual authority. |
| `/goal clear ...` | Clear the binding without undoing accepted effects or deleting task history. |
| `/goal history` | Read historical Goals. |
| `/goal status --json` | Explicit full machine-readable status. |

Commands use the same owner-bound service as model tools. No extra Goal enable
switch or automatic Goal creation is needed.

## Model workflow

| Operation | Owner of the decision |
|---|---|
| create / update / plan | Current main Agent, using its real conversation and user intent. |
| report | Main Agent assesses baseline/advanced/unchanged/regressed with evidence and next steps. |
| tasks / task_update | Authoritative task data; children need explicit assigned-task scope. |
| wait | Main Agent waits for tasks or a specific user question; not for itself. |
| activate | Return from an eligible wait to active; it cannot wash away user authority requirements. |
| pause / resume | Explicit pause/resume semantics; no timer-based permission release. |
| complete | Main Agent's evidence-based judgment, not a script/reviewer verdict. |
| status / history | Reads, not proof of model execution or channel delivery. |

`active`/`waiting`/`paused`/`completed` Goal state is separate from Agent execution
and reminder delivery. Task waits can react to relevant changes; a completed Goal
remains visible until explicit clear or a new Goal. Use the actual tool schema for action-specific fields;
omit unrelated parameters rather than filling null placeholders.

## Files and boundaries

Tasks live in `tasks/*.json`, plans in `plan.md`, progress in `progress.md` and the
optional generated observation in `eyes.json` under the Goal workspace. Model
workers should use their assigned scoped Goal tool. A low-level task_cli CAS
helper exists for explicit external integrations, not as a required child path.

`plan` is also a packaged instruction skill, not a second planning engine. Neither
skill activation nor Goal reminders grant tool permission. See the
[package guide](https://github.com/litterzhang/xdog/blob/main/packages/skills/README.md)
for model action schemas, persistence and restart limits.

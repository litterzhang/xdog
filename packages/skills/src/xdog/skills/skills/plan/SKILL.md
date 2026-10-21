---
name: plan
description: Plan multi-step XDOG tasks using ordinary task notes, explicit acceptance criteria and existing host permissions; use when the user asks for a plan or work needs dependency tracking.
---

# Task planning

Record a short objective, scope, dependencies, verifiable acceptance criteria and
remaining work in ordinary conversation or an existing task document. Update it
when evidence changes; do not create a parallel plan database or tool loop.
For small tasks, a concise outline in the reply is sufficient.

Separate investigation from actions requiring approval. A plan, skill or task
note grants no permissions. In host read-only scope, use only available read-only
tools and keep notes in the conversation: do not write a plan file, run shell,
call external/extension tools, spawn children, or try to release the scope.
Only an explicit user `/readonly off` control can release host read-only scope;
release still retains all normal permission checks. Do not infer consent from
plan text, progress updates, previous tool approvals or restored history.

Before implementation, resolve missing decisions that materially affect scope or
safety. During execution, keep completed steps grounded in evidence and retain
failure causes, blockers and next actions. Report unfinished verification rather
than marking a task complete from a proposed plan alone.

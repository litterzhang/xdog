---
title: Design
---

## Evidence collection is not Agent execution

```text
saved JSON -> sources -> detectors -> routes -> handlers -> explicit destination
                                          ACP enqueue -> durable receiver intake
```

Sources collect evidence, detectors emit events and a generic router selects
handlers. ACP is a handler, not a host-specific execution engine. Eyes imports
no Agent, Coding, Claw or Flow runtime.

## One daemon, not one service per workspace

Opening the installed application binding ensures daemon availability through the
public Eyes CLI; discovery/skill reading alone does not start it. The daemon owns
registrations, cursors, outboxes and recovery. Closing a host binding detaches its
CLI waits, not accepted observations. `--directory` isolates the same daemon
implementation, not a project runtime. The old project/cwd observer service and
embedded management SDK are removed.

## Owner and destination are different facts

The caller chooses and persists an owner string for inventory and management.
It is neither authentication nor a restriction to the current group/session.
JSON handlers define every destination; source roots resolve beside the saved
JSON definition. Enqueue can omit handler cwd because the receiver resolves it;
an explicitly supplied cwd is a consistency assertion. Standard prompt delivery
needs cwd for load-session. No management workspace parameter is inferred.

## Recovery follows the actual delivery boundary

File readiness is published only after its baseline checkpoint is durable.
ACP enqueue ends at durable intake; standard prompt has its live-turn outcome.
Neither proves Goal completion or user-channel delivery. Stop/cancel cannot undo
accepted receiver work. Unknown prompt/tool effects are not retried blindly;
same-ID enqueue recovery preserves the original namespace/content fences.
Paused scheduler deadlines are skipped, not replayed as catch-up work.

Goal optionally generates its own task observation and consumes raw event JSONL
through the CLI. Eyes knows no Goal schema or task dependency graph.

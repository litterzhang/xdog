> **历史归档，不是当前接口、待办或执行授权。**
> 原日期、结论和验证范围保留；当前入口见 [文档索引](../../README.md)。

# Claw Architecture — Current State

**Date**: 2026-04-08
**Lines**: ~8,500 (claw) + ~4,500 (agent) | **Tests**: 523

---

## Layer stack

```
Gateway    (546)  →  Orchestrator  (274)  →  GroupRuntime  (321)  →  AgentSession (409)  →  Agent
Unix socket          routing, tick()         resources, lifecycle    turn execution          LLM loop
JSON protocol        concurrency             session cache           compaction
background timer     scheduling              facades                 persistence

                                             TranscriptStore (212)   transcript_convert (199)
                                             pure JSONL              pure functions
```

Each layer calls the one below through a defined interface. No layer reaches through another's children. No circular dependencies.

## What each layer does

| Layer | Lines | Knows about | Doesn't know about |
|-------|-------|-------------|-------------------|
| Gateway | 546 | Orchestrator | GroupRuntime, AgentSession, TranscriptStore, GoalTracker |
| Orchestrator | 274 | GroupRuntime (type only) | TranscriptStore, AgentSession, GoalTracker, Agent |
| GroupRuntime | 321 | TranscriptStore, AgentSession, GoalTracker, MemoryManager, tools | Orchestrator, Gateway |
| AgentSession | 409 | Agent, runtime (via facade) | Orchestrator, Gateway, other sessions |
| Agent | (agent pkg) | Nothing in claw | Everything in claw |

## Assessment

I've reviewed this system across five rounds of design review and implementation. Here is where it stands and what I think is left.

### What's done well

**The layering is correct and enforced.** The Orchestrator imports `GroupRuntime` as a type and `TurnResult` as a return value. It doesn't import `TranscriptStore`, `AgentSession`, `GoalTracker`, or anything else from the internals. Gateway imports only `Orchestrator` and `ClawConfig`. These aren't conventions — they're verified by the import graph. If someone adds `from claw.core.session.agent_session import AgentSession` to `orchestrator.py`, the PR diff shows it immediately.

**The responsibilities are properly separated.** TranscriptStore does JSONL. GroupRuntime does resource ownership and session lifecycle. AgentSession does turn execution. Orchestrator does routing and concurrency. Gateway does transport. Each file has one job. You can understand what `agent_session.py` does without reading `orchestrator.py`.

**The compaction pipeline is the strongest subsystem.** Three stages — FlushRunner (agent with tools saves facts), Summarizer (direct LLM call produces structured text), compact_transcript (pure data manipulation) — each using the right execution strategy. The `<previous-summary>` tag for iterative accumulation is elegant. Nothing in this pipeline needs to change.

**The concurrency model handles all the edge cases.** Per-group lock (no interleaving within a group), global semaphore (rate limiting across groups), user priority (bypass global sem), three queue modes (collect, steer, steer-backlog). The model matches the real usage patterns.

### What I'd look at next — honest assessment

After five rounds of review I've addressed: bugs (persistence race, tool errors hidden), structural issues (circular dependency, dual-path constructor, leaky accessors, monolithic methods, scattered scheduling), and encapsulation tightening. The architecture is now clean.

What remains are not architectural problems. They are questions about whether this architecture will scale to the next set of requirements. These are the questions I'd ask if I were evaluating this for a production deployment:

**1. Is the single-group-per-agent model the right long-term choice?**

Each group has one AgentSession, which has one Agent, which has one conversation. If the system needs multi-user groups (e.g. a Slack workspace where multiple humans talk to the same agent), the current model doesn't support it — each group is one agent with one session. The fix would be per-user sessions within a group, but that's a different architecture. The current design is correct for the personal-assistant use case. I wouldn't change it preemptively.

**2. Is JSONL the right persistence layer for the next 10x?**

JSONL works for conversations under 10,000 turns. `load_transcript()` reads the entire file into memory. `replace_transcript()` rewrites the entire file. Compaction keeps the file manageable, but the IO pattern is read-all/write-all on every turn (for compaction checks) and every reset (for session info). If a group accumulates long conversations before compaction triggers, this becomes an issue.

The TranscriptStore abstraction is clean enough that a SQLite backend could replace JSONL without changing any caller. The interface is: `create_session`, `append_turn`, `load_transcript`, `replace_transcript`, `increment_turn`, `needs_daily_reset`, `needs_idle_reset`, plus branching. All of these map cleanly to SQL.

I wouldn't change this now. JSONL is simple, human-readable, and fast enough. But it's the first thing to revisit under load.

**3. Does `_maybe_compact` read the transcript twice?**

```python
async def _maybe_compact(self):
    transcript = self._store.load_transcript(self._meta.session_id)  # read 1
    token_est = estimate_tokens(transcript)
    if not should_flush(...):
        return
    # ... later in the same method:
    # transcript is already in memory, used for summarization and compaction
```

Actually, it reads once and uses the result throughout. Good. But `_persist_turn` then calls `self._store.append_turn()` which opens and writes the same file. And `run_turn` calls `_maybe_compact` then `_persist_turn` — so in the worst case (compaction + normal turn), the file is: read once (compact check), written once (replace), read zero more times, written N times (append per message). The IO is O(transcript_size) per turn because of the compaction check, even when compaction doesn't trigger.

A simple optimization: cache the transcript length in the session metadata instead of re-reading the file. `estimate_tokens` is `sum(len(content)) // 4` — this could be maintained incrementally in `_persist_turn` instead of re-reading.

This is a performance note, not a correctness issue. The current behavior is correct.

**4. GroupRuntime is the right abstraction but carries too many public fields.**

GroupRuntime has 12 public fields and 14 methods. It's the "hub" that everything connects through. This is correct — it's the resource owner. But many of those public fields (`flush_runner`, `summarizer`, `reindex_fn`, `memory`, `context_window`, `max_prompt_tokens`, `agent_config`) are only read by AgentSession. They don't need to be public.

Since AgentSession takes `runtime` and accesses `self._runtime.flush_runner`, `self._runtime.summarizer`, etc., these could be private with the understanding that AgentSession is a "friend" class. But Python doesn't have friend classes, so making them private would just mean AgentSession accesses `runtime._flush_runner` with an underscore — which is arguably worse stylistically.

This is fine as-is. The public fields are documentation: "here's what a group needs."

**5. No observability beyond logging.**

The system logs at INFO level: tool calls, turn usage, compaction triggers, scheduled tasks, goal runner firings. But there's no structured metrics, no event bus, no way for an external system to observe what's happening without parsing log lines.

For a personal-assistant deployment, logging is sufficient. For a multi-tenant service, you'd want: turn latency histograms, compaction frequency per group, tool call counts, token usage per group, error rates per tool. The right abstraction would be a metrics callback on the Orchestrator, similar to how the Agent has event listeners.

This is not an architecture issue — it's a product maturity question. The architecture supports adding observability without restructuring.

### Summary

The system is architecturally complete for its intended use case (personal AI assistants with long-term memory). The five layers are correctly separated, the encapsulation is tight, the concurrency model is correct, and the compaction pipeline is well-designed.

The remaining items are forward-looking concerns — not things to fix now, but things to watch:
- JSONL persistence under sustained load
- Single-group-per-agent model if multi-user scenarios emerge
- Incremental token estimation to avoid per-turn file reads
- Structured observability if deployed as a service

None of these require architectural changes. They're all additive (new backend behind existing interface, new metrics callback, new field on existing type). The architecture accommodates them.

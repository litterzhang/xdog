# Claw ↔ Eyes through ACP

## Design

Claw exposes the existing `xdog.agent.acp.AcpAdapter` over an explicitly enabled
private Unix socket owned by its gateway. Eyes is unchanged: ACP remains one
handler behind its generic EventRouter, not a Claw-specific routing abstraction.

```text
File → Eyes source/detector → EventRouter → generic ACP handler
                                               │
                            Claw gateway's private ACP endpoint
                                               │ durable receipt
                            group inbox (SQLite, independent of TUI)
                                               │ automatic consumption
                            application InputService + existing per-group exclusion
                                               │
                            group's current cached AgentSession → Agent
                                               │
                            normal tool selection and pre-tool policy
```

No second Agent/tool loop, callback CLI invocation, event-handler host, hosted
broker, automatic observer startup, or new TUI execution owner is introduced.
Loading the packaged Eyes skill still does not start anything.

### Group addresses, not TUI/transcript addresses

The ACP address is **`group:<configured group ID>`**, for example `group:main`.
Only already registered groups are valid; ACP cannot create a group or silently
fall back to the main group. `cwd` must match the group's actual workspace.
`session/new` is deliberately rejected; `session/load` binds the explicit group
without constructing its AgentSession or replaying history to the ACP peer.

A group address intentionally follows the group's active conversation across
normal daily/idle resets or explicit navigation. It is **not** a coding exact
session/branch address. At execution, the Orchestrator selects the active session
under its existing group lock. The execution session ID is retained with the
inbox result and completion notification.

`xdog/session_info` advertises a receiver generation for prompt clients. Durable
`xdog/inbox_status` exposes a persistent namespace scoped to **group + canonical
workspace**, with `branch_id: ""`: there is no promise to pin a transcript branch.
Changing a group's workspace changes its inbox scope; old-scope messages are not
silently delivered to the new workspace. Existing Eyes namespace checks apply.

### Receipt and execution

Both `session/prompt` and `xdog/enqueue` persist a canonical
`xdog.session_input.v1` input before execution. Source, evidence, provenance and
message identity survive the actual Agent history and JSONL transcript.

- Enqueue acknowledges the committed receipt, **not** model completion. A stable
  ID with identical canonical content is a duplicate, including after restart.
  The same ID with different content is rejected.
- A per-group consumer waits on the **same** Orchestrator lock as human/channel/
  scheduler work. It retains its own result rather than using the collect queue's
  fire-and-forget steer/fold/drop behavior. Each callback currently runs as one
  independent Claw turn; callbacks also honor `max_concurrent_agents` as
  background work, without starving human input. Coding's configurable batch
  policy is not transplanted.
- Standard prompt waits for its own actual terminal outcome and returns bounded
  ACP text chunks from its final response. It does not broadcast another
  message's output, reasoning or tool results to unrelated ACP peers.
- `session/cancel` detaches that peer's waiter and returns `cancelled` with
  `execution_continues: true`; it does **not** claim to retract an accepted input
  or abort another channel's turn. Disconnect has the same durable-work rule.
- Sending/consuming a message needs no message approval or wake credit. Tool
  selection, planning restrictions, MCP/extension allowlists and existing Claw
  tool hooks still apply. This does **not** add coding's approval policy to Claw
  or allow evidence/labels to grant tool permissions.

The sole SQLite input/attempt journal lives at `<data_dir>/inputs/inbox.sqlite3`,
independent of ACP being enabled. An exclusive application lease protects recovery
and consumption. Disk transactions run off-loop and settle before ownership is
released. States are `pending → claimed → running → succeeded | failed`;
explicit unstarted withdrawal is `cancelled`. `claimed` is not Agent admission.
A failed attempt retains whether it actually started, not an error-string guess.
Endpoint restart leaves actual execution alone. Application shutdown/crash changes
running to interrupted, never automatic side-effect replay; unstarted claims can
safely return to pending. A committed terminal result wins a shutdown race.

ACP owns only protocol bindings/peer waiters, not that journal, its consumer or
observer/Goal policy. Namespace/status inspect the same receipts as native inputs.
Managed observer state has its own leased `<data_dir>/observers/journal.sqlite3`:
cursor and whole outbox commit atomically there before original-ID ACP intake.
Report attempts bind the actual original execution attempt. Their send status is
separate from execution and later explicit retries cannot steal an old report.
No legacy database import or format-upgrade path is provided.

There is no automatic retry of interrupted/failed work. Inspect the transcript
and effects first, then deliberately submit a **new** message ID if retry is safe.
Re-enqueueing an existing ID only returns its original receipt/state.

### TUI display

The gateway works with no TUI connected. A new TUI negotiates descriptive
`acp_updates: 1` notifications for its selected group. Completed external input and
its final response appear once, scoped to the actual session. A first ACP-created
conversation can be described without turning the viewer into its session owner.
Reopening the conversation replays the canonical external input from the normal
transcript. External inputs are labeled as external and never inserted into the
human editor history or unsent draft. Notifications are completion-level, not
live ACP tool/reasoning streaming; display failure cannot undo a durable result.
An already bound viewer ignores completion from a different transcript session;
reconnect/open that session to view its history.

## Usage

1. Configure Claw's normal model, groups and tools (`xdog-claw onboard`). Then
   explicitly enable ACP, either in YAML or on gateway startup:

   ```yaml
   # Existing model/tool/group configuration is omitted here.
   acp_socket_path: /home/your-user/.local/state/xdog/claw/acp/agent.sock
   ```

   ```bash
   xdog-claw gateway start --foreground \
     --acp-socket "$HOME/.local/state/xdog/claw/acp/agent.sock"
   ```

   `--acp-socket` overrides the configuration for that gateway process only. The
   endpoint's immediate directory must be owned by the current user, without
   symlinks, and mode `0700`; a missing directory is created privately. The socket
   is `0600`. Use a short absolute path within the platform's Unix socket limit.
   No ACP socket is opened by default.

2. Copy Eyes' packaged `source-test-review.json` to the **Claw group workspace**
   as `eyes.json`, retaining its source/detector/routes. Set its handler to:

   ```json
   {
     "id": "review",
     "type": "acp",
     "connection": {
       "transport": "unix",
       "path": "/home/your-user/.local/state/xdog/claw/acp/agent.sock"
     },
     "session": "group:main",
     "delivery": "enqueue",
     "instruction": "Review changed source and related tests. Follow target tool permissions."
   }
   ```

   Run from that same group workspace:

   ```bash
   xdog-eyes validate eyes.json
   xdog-eyes start eyes.json
   xdog-eyes status REGISTRATION_ID
   xdog-eyes stop REGISTRATION_ID
   ```

   Narrow the source include/exclude globs to avoid watching generated state and
   agent self-edits unintentionally. For synchronous completion instead of an
   immediate durable receipt, select `"delivery": "prompt"`.

3. TUI is optional: `xdog-claw tui --group main`. Stopping Eyes closes its own
   observations/connections, not the Claw gateway or already accepted messages.

## Limits and recovery

- The **gateway must be running** to receive new messages. This is not a separate
  model-free offline receiver/installed service. No TUI or previously active
  AgentSession is required; the normal runtime is activated when consumption runs.
- Unix only, using the official ACP SDK `>=0.12.1,<0.13` on Python 3.12–3.14. No TCP,
  Windows, stdio gateway process adapter, or automatic daemon provisioning.
- The endpoint refuses to replace an existing path, including a stale socket.
  After a crash, first confirm its old owner is gone, then remove that stale
  socket explicitly before restarting. Do not remove another live receiver's
  socket. Normal shutdown unlinks only its own socket inode.
- Default active inbox capacity is 50 per group/workspace (pending + running);
  retained message count is bounded at 4096. Overflow is rejected, never dropped.
  Canonical inputs and stored final responses/errors are bounded at one MiB each.
  Status is paged (64 entries) and does not load message payloads. No automatic
  pruning/archiving CLI is shipped in this change; retain/back up receipts and
  deliberately rebaseline Eyes if replacing a full inbox namespace.
- Restart does not reconnect Eyes automatically. For enqueue delivery, explicitly
  resume only against the same persisted namespace. Lost prompt results remain
  outcome-unknown and must never be blindly resent.
- No exactly-once guarantee for tool side effects. Private filesystem permissions
  and file leases are local coordination, not a sandbox against the same OS user.

## Verification

New tests cover real Unix SDK initialize/load/prompt/enqueue/status/cancel,
Eyes file polling → router → ACP → actual Claw Agent/transcript, busy-group
serialization, peer disconnection, deduplication/conflicting IDs, pending recovery
and interrupted non-replay, cancellation after terminal commit, configured tool
disablement, bounded output frames, gateway lifecycle, private path rejection,
and live/replayed TUI display without changing a draft/editor history.

Paid providers and external IDEs are not required by these deterministic tests.
Independent review remains deferred at the user's request; this document does
not claim an independent review verdict.

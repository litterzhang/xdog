# ACP session routing and durable inbox

XDOG routes to a session, not a TUI widget. `xdog.agent.acp` supplies the official
ACP SDK adapter/backend boundary; coding supplies its permission, persistence and
session-owner policy. Eyes is an independent ACP client and never imports a
harness or runs a tool loop. Coding does not import Eyes or a concrete source.

## Entry points and lifetimes

```text
Eyes evidence → event → ACP handler ───→ explicit endpoint + session ID
                                              │
                                  lightweight coding ACP ingress
                                   ├─ prompt → live owner → controller → AgentSession
                                   └─ enqueue → durable session inbox
                                                    │ explicit review + credit
                                                    └──────────→ same controller
TUI / RPC: submit human input and subscribe to owner events; not callback hosts
```

Three independent lifetimes matter: persisted session identity/history, a live
execution owner, and observation. The explicitly started ingress can remain
available when every TUI/session owner is closed. It only receives data or
forwards to an existing owner; it never restores a writable session, constructs
an AgentSession, resolves a model or launches coding to satisfy a callback.
If the ingress is also absent, delivery is unavailable, not acknowledged.

## Transport and SDK boundary

Standard interoperation is ACP JSON-RPC over **stdio**, implemented with
`agent-client-protocol>=0.12.1,<0.13` (lockfile baseline 0.12.1). The first-release
Unix-domain stream transport is an explicit **XDOG local transport**, not a claim
that every ACP client supports Unix endpoints. There is no TCP/HTTP/Windows
transport or installed permanent/system daemon in this release.

The pinned SDK supports Python 3.12–3.14. The dependency marker excludes Python
3.15+, where ordinary coding/agent usage and Eyes definition validation remain
available but explicit ACP startup reports the SDK requirement. Non-ACP paths do
not eagerly import the SDK.

```bash
uv run xdog-agent acp                  # generic backend; no coding installation needed
uv run xdog-coding --acp               # standard coding ACP adapter
uv run xdog-coding --acp --session ID  # fixed live-owner bridge; no new/restored Agent
uv run xdog-coding --session ID --prompt 'Review the change'
```

`--session` means exact live addressing; unknown/inactive/project mismatch is an
error. It is not `--resume-id`, which explicitly restores historical state after
acquiring its writer lease. Attach cannot override the owner's model, cwd,
permissions, MCP configuration or branch. Standard load replays to the requesting
peer only, without adding history or repeating TUI output.

## Starting a receiver explicitly

First create the target session normally and keep its exact `/session` ID.
For the standard config, session/inbox storage is under
`${CODING_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/xdog/coding}`. Use absolute paths
for the explicit receiver configuration (custom SDK stores must configure owner
and receiver with the same sessions/inbox roots).

From the repository root, in a separate terminal, for example:

```bash
DATA="${CODING_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/xdog/coding}"
ACP_DIR=$(mktemp -d /tmp/xdog-acp.XXXXXX)  # private 0700 directory
uv run python -m xdog.coding.acp.ingress_main \
  --sessions-dir "$DATA/sessions" --inbox-dir "$DATA/inbox" \
  --socket "$ACP_DIR/acp.sock"
```

It prints the actual address and ingress generation. Copy the address into an
Eyes definition; Eyes does not inspect coding's private owner registry or compute
coding socket paths. Stop this receiver explicitly with SIGINT/SIGTERM. Stopping
an observer or TUI does not stop it or delete delivered messages. No user/system
service or publisher setup is installed automatically.

## Eyes definition and invocation

Save in the project root, substitute the actual receiver address and persisted
session ID, and keep narrow source patterns:

```json
{
  "version": 2,
  "name": "review-source-changes",
  "sources": [{"id":"files","type":"file","root":".","include":["src/**"]}],
  "detectors": [{"id":"changes","type":"file.change","source":"files"}],
  "handlers": [{
    "id":"review","type":"acp",
    "connection":{"transport":"unix","path":"/tmp/xdog-acp.EXAMPLE/acp.sock"},
    "session":"EXPLICIT_SESSION_ID","delivery":"enqueue",
    "instruction":"Review changed source and related tests after explicit user approval."
  }],
  "routes": [{"id":"review-route","detector":"changes","handler":"review",
              "event_types":["file.created","file.modified","file.deleted"]}]
}
```

```bash
uv run xdog-eyes validate eyes.json
uv run xdog-eyes start eyes.json
uv run xdog-eyes status REGISTRATION_ID
uv run xdog-eyes resume REGISTRATION_ID
uv run xdog-eyes stop REGISTRATION_ID
```

Roots resolve relative to the definition file; target cwd is the project where
observation is explicitly started. Every handler must connect/validate before
scanning. Existing files establish a baseline, not startup events. Same name and
normalized definition are idempotent; changes require stop/start. Definitions
persist, registrations do not crash-replay or auto-restart. `status` never starts
a missing runtime. The last registration stop cleans its runtime socket/record
and owned connections/stdio children, not the receiver, inbox or TUI. Lock inodes
may remain intentionally: unlinking a lock with waiters can create two owners.

A generic third-party stdio target can use
`"connection":{"transport":"stdio","command":["third-party-acp-agent"]}`.
This does not make a coding CLI subprocess a required callback path. Eyes source,
detector and route logic does not know coding's files, sources or event types.

## Prompt versus durable enqueue

- `delivery: "prompt"` (default): explicit load followed by standard
  `session/prompt`; completion means a final PromptResponse, never a streamed
  `session/update`. Target must be active. A lost final response is
  **outcome-unknown**: pause, inspect effects and explicitly stop/rebaseline;
  never blindly resend. Only explicit local **not-sent** failures retry
  automatically.
- `delivery: "enqueue"`: negotiate XDOG inbox extensions, validate persisted
  session/cwd/branch/storage scope, and write evidence with a stable message ID.
  No load, prompt, resume approval, AgentSession or model is started. `queued` and
  `duplicate` are durable receipts, **not model completion**. A duplicate may
  report an existing running/terminal message state; it did not execute a second
  turn.

Enqueue ACK loss permits an **explicit** same-ID/same-content query or retry only
against the same verified inbox namespace/session/project/branch. The opaque
persistent `inbox_id` distinguishes a replacement store even at the same socket
address. It is not an authorization capability and does not expose storage paths.
Restarting a receiver alone does not reconnect observation. Changed namespace or
branch requires inspection and explicit rebaseline, not a guessed latest target.

## Versioned XDOG extensions

Initialize advertises `_meta.xdog.extensions` with exact integer version 1.
These are SDK extension methods, **not standard ACP methods**. Call
`conn.ext_method('xdog/enqueue', parameters)`; the SDK adds the wire underscore.
Do not add a second `_` or wrap SDK keyword metadata inside another `_meta`.

| Method (SDK name) | Contract |
|---|---|
| `xdog/session_info` | Bound live session ID/cwd/owner generation/branch and truthful `automatic_budget` boolean |
| `xdog/resume` | Requests current owner's normal permission hook; no metadata-based grant; disconnect retires waiting approval |
| `xdog/enqueue` | Existing session, cwd, stable message_id, instruction, evidence array, source label and provenance object |
| `xdog/inbox_status` | Existing session/cwd; optional message_id or integer cursor (exclusive); bounded state summaries, branch and persistent inbox_id |

Enqueue response contains session_id/message_id, `status: queued|duplicate`,
`message_state`, and bound branch_id. The receiver fsyncs atomic publication
before ACK. Same ID/content returns the original fact, not a new execution;
same ID/different content conflicts. No client-selected origin, approval,
permission, owner generation, storage path or branch override is accepted.
Status pages contain at most 64 summaries; full evidence is available through
explicit inbox inspection. All JSON/frame/capacity errors fail closed.

## Review, execution attempts and TUI/RPC

```text
pending_review → approved(current owner/branch) → running(attempt ID) → terminal
                    │ owner exits before start         │ abnormal death
                    └────────→ pending_review          └→ interrupted
```

Opening a session displays pending/interrupted evidence, but does not queue it
for a model. Use `/inbox list [OFFSET]`, `/inbox show ID`, then an explicit
`/inbox approve ID [ID...]`. No implicit all. `/inbox retry ID` requires an
interrupted attempt, current context and fresh permission; it creates a new
attempt ID while retaining the old attempt/result. Opening, inspecting, enqueue
and reconnect do not refill automatic credit. Inbox approval credit applies only
to the selected request IDs, not unrelated older ACP inputs. A batch still starts
at most one automatic turn until another legitimate input/authorized resume.

An optional human CLI data-write path exists:

```bash
uv run xdog-coding --session ID --enqueue --message-id STABLE_ID --prompt 'Review change'
```

It runs without ACP or model construction and checks cwd against the target.
It is optional management, **not the Eyes callback**. It cannot combine with
model/permission/resume overrides or silently convert a failed live prompt.

TUI real-time/history views label source and evidence as untrusted, show statuses,
retain full details, and join inbox records to canonical attempts without duplicate
input payloads. RPC keeps reading while `type: inbox` approve/retry waits for a
`permission_response`; `inbox_cancel` retires only its own review. Ready precedes
initial inbox snapshots. Both use the same controller; neither owns a separate
agent loop. New canonical model inputs use `xdog.session_input.v1`.

## Ownership, limits and security

Every writable coding owner acquires its process lease before loading writable
history. Controller turns, tools, producers, controls, context changes and cleanup
settle before ownership is released. Navigation revokes old generation work;
there is no hidden session owner behind a closed TUI. Ingress peer disconnection
cancels/settles only its requests, not a human turn or another peer.

Directory/socket/file UID/type/mode and no-symlink checks protect local admission
(0700 directories, 0600 files/sockets). This and permission hooks are **not an OS
sandbox against another same-UID process**. Labels, paths, evidence and metadata
are not identity or authorization. Ordinary readonly tools may run automatically;
mutating tools need approval. With no approver they fail closed unless the user
explicitly selected allow-all. Eyes denies reverse ACP permission requests and
advertises no filesystem/terminal tool access.

A bound coding owner manages automatic prompt credit. `automatic_budget: false`
or missing capability uses Eyes' one-turn fallback; extension names alone cannot
claim unlimited owner-managed credit. Human/authorized resume, not success/error
or self-edit output, supplies new credit. No permission mode is changed by
observation or receipt.

Initial limits: 128 unresolved inbox messages/session, 1 MiB/message, 4096 retained
identity records, 2 MiB encoded record including attempt history. No automatic ID
eviction, evidence truncation or side-effect replay. Observation has 128 pending
batches and 256 unique evidence paths/batch plus frame limits. Overflow pauses
with diagnostics. Explicit manual archive/deletion invalidates retention/dedup
assumptions; stop and inspect before replacing storage or rebasing evidence.

The old register/deliver/heartbeat/capability host, descriptor environment and
`session.notify` handler are removed. Historical `xdog.external_event.v1` records
remain readable/renderable as untrusted history only, not a live delivery path.
No claim of exactly-once tool side effects or exhaustive filesystem audit is made.

## Verification and exclusions

See `eyes-verification.md` and the checked-in acceptance evidence. CI uses
SDK-only deterministic peers plus real subprocesses/Unix sockets/files and
permission/controller persistence tests; no provider credentials are required.
Actual external IDE integrations and live provider/model runs must be verified
separately; passing these tests does not claim those integrations were exercised.
Claw/flow-specific adapters and remote/network transports are not implemented by
this first release.

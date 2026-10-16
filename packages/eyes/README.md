# xdog-eyes

A local-first, declarative observation layer, shaped like flow: versioned JSON,
frozen models, whole-definition validation, async execution, CLI and a packaged
**eyes-observers** skill. Sources collect evidence, detectors emit events, explicit
EventRouter dispatches explicit routes to extensible EventHandler implementations.
ACP is one built-in handler, not the routing abstraction. Eyes never imports a harness or runs an agent
loop; coding never imports a concrete Eyes source.

## Use an explicit ACP destination

An independently managed receiver and an existing session ID are prerequisites.
Copy the packaged `source-test-review.json` into your project root and replace
its Unix endpoint and `EXPLICIT_SESSION_ID`. Roots resolve relative to that file.
The v2 example uses `delivery: "enqueue"` to retain evidence while TUI/session
owners are offline. It negotiates durable inbox support, never loads an owner or
starts a model. If the receiver is also offline, report unavailable—not receipt.
Eyes never launches a coding CLI to provide a callback.

```bash
xdog-eyes validate eyes.json
xdog-eyes start eyes.json
xdog-eyes status REGISTRATION_ID
xdog-eyes resume REGISTRATION_ID
xdog-eyes stop REGISTRATION_ID
```

`validate` needs no harness/model/active SDK. `start` explicitly launches Eyes'
private per-project control process after definition validation, then connects
all handlers before scanning. Same name + normalized definition is idempotent;
changes need stop/start. Source/route/connection status is separate. Queued or
duplicate enqueue receipts are not model completion. The receiver's TUI shows
pending evidence; an activated owner consumes it automatically. `/inbox list` and
`/inbox show ID` are read-only, `/inbox cancel ID` targets pending intake, and
`/inbox retry ID` explicitly retries interrupted work. There is no message approval
or human wake-up budget. Actual tool permissions remain unchanged.

`delivery: "prompt"` instead loads a live explicit session and waits for its final
ACP result. A coding target persists live prompts through the same Inbox and
waits for their actual batch result. Inactive prompt targets still fail rather
than silently enqueueing offline. Generic ACP servers keep their own policy.
A lost prompt response is outcome-unknown and is never blindly resent. Enqueue
can explicitly retry the original ID/content after checking the same persistent
inbox namespace/project/branch; reconnect never approves model execution.

## Router, handlers and destination batches

`HandlerRegistry` explicitly registers each type's definition parser and factory.
The shared `EventHandler` lifecycle is start, validate, deliver, snapshot, check,
resume and close; ACP payload/connection details stay in its handler. Unknown
types fail validation rather than importing arbitrary code from JSON. Embedders
can register additional handlers; the shipped CLI still includes only ACP.

Coding selects fixed batches with `max_messages_per_turn` (default10, strict
integer1..128). Five queued messages remain five independent history inputs with
source/evidence/IDs/attachments, not concatenated text or five separate Agent
turns. They share a response/tool stream; new arrivals wait for a later turn.
A canceled peer cannot withdraw its message from an already running shared batch
or cancel other members. Tools still require the target's normal permissions;
unattended restricted tools fail closed without explicit `allow-all`.

## Collection and lifetime

First release: regular-file content changes with bounded polling, fingerprints
and net-change/debounce detection. No symlink following, mtime-only/unchanged-save
events or historical file contents. Rename is delete+create; polling can miss
transient changes. Keep roots/globs narrow and exclude generated/cache trees.
Payloads, source labels and filenames are untrusted evidence, never permissions.

Definitions persist; active registrations do not crash-replay or auto-restart.
Enqueue observers can outlive the TUI/owner while the receiver stays available.
Receiver failure pauses collection; restart alone does not reconnect it. Prompt
owner/branch changes require inspection/rebaseline. Stop closes owned observation
connections/stdio peers, not the shared receiver/TUI or delivered inbox. Last stop
cleans the control socket/record; safe lock inodes may remain.

The old event-handler host/capability/descriptor protocol is removed. Core local
permissions are not an OS sandbox. No TCP/HTTP/Windows transport, installed system
daemon, hosted broker, automatic coding startup, API/web source, script handler,
or claw/flow-specific adapter is added. Stdio is an optional generic ACP transport;
Unix is the documented XDOG-local transport for the coding callback.

## Installation and development

The only runtime dependency is the official `agent-client-protocol` SDK, pinned
`>=0.12.1,<0.13` for Python 3.12–3.14. It does not depend on ai/agent/coding/claw/flow.
The marker excludes the currently unsupported ACP SDK on Python 3.15+; definition
validation remains usable. Workspace development commands run from repo root.

See [ACP/session/inbox protocol](../../docs/acp-session-routing.md) for explicit
receiver startup, all commands, permission/unknown-result semantics and limits,
and [current verification](../../docs/unified-inbox-batch-verification.md) for
release evidence. The older `eyes-verification.md` is a historical checkpoint.
`uv build --package xdog-eyes` ships skill, example, py.typed and license. This
feature does not push/publish or provision a PyPI publisher/system service.

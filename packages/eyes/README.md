# xdog-eyes

A local-first, declarative observation layer, shaped like flow: versioned JSON,
frozen models, whole-definition validation, async execution, CLI and a packaged
**eyes-observers** skill. Sources collect evidence, detectors emit events, explicit
routes invoke generic ACP handlers. Eyes never imports a harness or runs an agent
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
pending evidence; `/inbox approve ID [ID...]` is an explicit, permission-gated
execution action. Opening/querying a session does not replay backlog.

`delivery: "prompt"` instead loads a live explicit session and waits for its final
ACP result. It respects target permissions and a one-turn automatic wake budget.
A lost prompt response is outcome-unknown and is never blindly resent. Enqueue
can explicitly retry the original ID/content after checking the same persistent
inbox namespace/project/branch; reconnect never approves model execution.

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
and [verification](../../docs/eyes-verification.md) for release evidence.
`uv build --package xdog-eyes` ships skill, example, py.typed and license. This
feature does not push/publish or provision a PyPI publisher/system service.

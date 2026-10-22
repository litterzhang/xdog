# xdog-eyes

> Platform scope: **Linux only**. Non-Linux adapters and native-platform support
> are outside the current product scope; historical audit evidence is not a support promise.

A local-first, declarative observation layer, shaped like flow: versioned JSON,
frozen models, whole-definition validation, async execution, CLI and a packaged
**eyes** skill. Sources collect evidence, detectors emit events, explicit
EventRouter dispatches explicit routes to extensible EventHandler implementations.
ACP is one built-in handler, not the routing abstraction. Eyes never imports a harness or runs an agent
loop; coding never imports a concrete Eyes source.

The supported management surfaces are the **Eyes CLI** and the **installed,
registered Eyes tool**. Python engine classes such as `ObserverService`, handler
registries and hosting/control modules are internal implementation details, not a
public embedding SDK or an alternative supported observation-management entry point.

## Use an explicit ACP destination

An independently managed receiver and an existing session ID are prerequisites.
Copy the packaged `source-test-review.json` into your project root and replace
its Unix endpoint and `EXPLICIT_SESSION_ID`. Roots resolve relative to that file.
The v2 example uses `delivery: "enqueue"` to retain evidence while TUI/session
owners are offline. It negotiates durable inbox support, never loads an owner or
starts a model. If the receiver is also offline, report unavailable—not receipt.
Eyes never launches a coding CLI to provide a callback.

```bash
xdog-eyes daemon start
xdog-eyes daemon status
xdog-eyes validate eyes.json
xdog-eyes start eyes.json
xdog-eyes list  # alias for status without an ID, scoped to owner
xdog-eyes status REGISTRATION_ID
xdog-eyes resume REGISTRATION_ID
xdog-eyes pause REGISTRATION_ID
xdog-eyes stop REGISTRATION_ID  # cancel source, not shared daemon
xdog-eyes daemon stop         # explicit whole-instance shutdown
```

`validate` needs no harness/model/active SDK. `start` explicitly opens the reusable
same-user daemon; handlers mode connects its receivers before scanning. The installed Eyes tool invokes the public CLI using the same Python environment.
Only the CLI uses the private, versioned, generation-fenced Unix control protocol; there is no public Python daemon SDK. The default
private directory is `$XDG_DATA_HOME/xdog/eyes` (otherwise
`~/.local/share/xdog/eyes`). Use `--directory /private/path` or
`XDOG_EYES_RUNTIME_DIR` for isolation; an explicit directory takes precedence.
Directories/sockets/journals are UID-owned and private. No service is installed;
importing/listing skills does not start a daemon. There is no daemon idle timeout.

Owner/key plus normalized definition is idempotent across CLI processes/tools,
host retirement and daemon restart. Within one daemon instance, `owner` is the
only management namespace; `--owner` defaults to `cli`. There is no management
`workspace` parameter or `--workspace` option. Changing the CLI current directory
does not select another inventory. Keys default to definition names; different
content requires a new key. Cancelled keys are tombstones, not silently reusable.
Source/route/connection status is separate. Queued or
duplicate enqueue receipts are not model completion. The receiver's TUI shows
pending evidence; an activated owner consumes it automatically. `/inbox list` and
`/inbox show ID` are read-only, `/inbox cancel ID` targets pending intake, and
`/inbox retry ID` explicitly retries interrupted work. There is no message approval
or human wake-up budget. Actual tool permissions remain unchanged.

`delivery: "prompt"` instead loads a live explicit session and waits for its final
ACP result. It requires an explicit handler `cwd` for standard ACP `load_session`.
A coding target persists live prompts through the same Inbox and
waits for their actual batch result. Inactive prompt targets still fail rather
than silently enqueueing offline. Generic ACP servers keep their own policy.
A lost prompt response is outcome-unknown and is never blindly resent. Enqueue
can explicitly retry the original ID/content after checking the same persistent
inbox namespace/project/branch; reconnect never approves model execution.

## Definition paths and ACP target directories

Eyes does not manage a workspace. Keep these distinct:

| Concern | Field or base |
| --- | --- |
| Registration inventory, controls and ACK cursor | Caller-chosen `owner` within the selected daemon instance |
| Tool definition file | Required absolute `definition_path` |
| CLI file definition paths | Relative source roots / handler `cwd` use the JSON file's directory |
| CLI stdin definition paths | Relative source roots / handler `cwd` use the CLI current directory |
| ACP target session directory | Handler `cwd`, under the delivery rules below |
| Stdio receiver process startup directory | Inherited from the Eyes process that spawns it, not handler `cwd` |

For `delivery: "enqueue"`, `session` identifies an **existing** receiver session.
Handler `cwd` may be omitted: the receiver resolves the session's directory. If
supplied, `cwd` is an assertion checked against that session, not a request to
move it. The client pins the returned directory, inbox namespace and branch for
later delivery/recovery; it never substitutes a definition base or daemon cwd.
For `delivery: "prompt"`, an explicit handler `cwd` is required for the standard
ACP `load_session` protocol. Neither mode creates a session from a directory.

A stdio connection has only `transport: "stdio"` and `command` (an argv array).
There is no `connection.cwd` field. Handler `cwd` is the target session directory,
**not** the subprocess startup directory; it is never passed as the stdio spawn
cwd. Use an absolute executable/script path when startup-directory independence
is needed. Unix `connection.path` remains an explicit absolute socket path.

## Router, handlers and destination batches

Internally, `HandlerRegistry` binds each type's definition parser and factory.
The internal `EventHandler` lifecycle is start, validate, deliver, snapshot, check,
resume and close; ACP payload/connection details stay in its handler. These engine
contracts are not public embedding or handler-registration SDK entry points.
Unknown types fail validation rather than importing arbitrary code from JSON.
The supported daemon handlers are built-in ACP and data-only `events`; the daemon
never imports code named by a definition.

Coding selects fixed batches with `max_messages_per_turn` (default10, strict
integer1..128). Five queued messages remain five independent history inputs with
source/evidence/IDs/attachments, not concatenated text or five separate Agent
turns. They share a response/tool stream; new arrivals wait for a later turn.
A canceled peer cannot withdraw its message from an already running shared batch
or cancel other members. Tools still require the target's normal permissions;
unattended restricted tools fail closed without explicit `allow-all`.

## Scheduler sources

Sources can also generate timed observations: one-off `at`, fixed
`every_seconds`, or five-field `cron` with an explicit IANA `timezone`.
Exactly one mode is allowed per source. `scheduler.due` detectors emit ordinary
`scheduler.due` events through the same EventRouter/handlers; no agent or system
cron is called directly. File and scheduler sources can coexist in one v2 file.

```json
{"id": "daily", "type": "scheduler", "schedule": {
  "cron": "0 9 * * *", "timezone": "Asia/Shanghai"
}}
```

The packaged `scheduled-review.json` is a complete executable definition. Replace
its endpoint/session and use the same validate/start/status/stop commands.
Repeating schedules start after handler readiness; one-off start refuses past
zoned timestamps. Intervals use monotonic time (finite seconds, minimum0.001),
cron uses timezone-aware calendar instants, and suspended periods do not generate
a catch-up burst. A late active poll coalesces to its latest occurrence. Scheduled
evidence keeps occurrence identity and planned/observed UTC times, not file paths.
See [scheduler definition, timing and recovery](../../docs/eyes-scheduler-source.md)
for all modes, DST/clock semantics, mixed-source examples and bounds.

## Collection and lifetime

Regular-file content changes use bounded polling, fingerprints
and net-change/debounce detection. No symlink following, mtime-only/unchanged-save
events or historical file contents. Rename is delete+create; polling can miss
transient changes. Keep roots/globs narrow and exclude generated/cache trees.
Payloads, source labels and filenames are untrusted evidence, never permissions.

The daemon journals registrations, file fingerprints/debounce state, scheduler
cursors, immutable pending deliveries, event rows and consumer ACK cursors in
`daemon.sqlite3`. Cursor and pending evidence commit together before handler send.
Accepted registrations with `recover=True` resume from their original IDs and
baselines, not a fresh scan that discards downtime changes. No historical file
contents are retained. Registration pause skips scheduled deadlines rather than
replaying missed ticks; accepted pending evidence remains intact. Cancel is
idempotent and does not delete already committed events or destination inputs.

External in-flight/unknown delivery after interruption is quarantined for review,
never automatically replayed. The original ACP definition and pinned inbox/branch
or owner-generation binding must still match on recovery. Proven-not-sent delivery
may retry; a changed receiver cannot acquire an old outbox. Events-mode publication
is idempotent by original delivery ID and may be recovered safely. Whole-instance
`daemon stop` settles work and releases only its own socket/record, retaining data.
`daemon status` is read-only and never starts an absent instance.

## CLI event consumers (including Goal)

Goal uses a **real Eyes CLI subprocess** for every registration, event read,
ACK and pause/resume/cancel operation. It imports no Eyes module and does not
construct a Python control object. A Python host invokes the installed command
with an argv array, e.g. `[sys.executable, "-m", "xdog.eyes", ...]`, never a shell
command. `xdog.eyes._daemon_control.DaemonControl` is private implementation for
Eyes' CLI internals only, not a supported SDK or host integration
entry point. The former `xdog.eyes.client`, `Client` and `EyesClient` are removed;
there is deliberately no compatibility export.

```bash
# Each command uses the same explicit scope; global options also work after commands.
xdog-eyes --directory /private/eyes --owner goal:stable \
  start - --delivery events --key tasks --no-wait < tasks.json
xdog-eyes --directory /private/eyes --owner goal:stable \
  status tasks --wait-ready --timeout 10
xdog-eyes --directory /private/eyes --owner goal:stable \
  events --follow --after 0 --limit 64
# After Goal runtime has durably handled the original event:
xdog-eyes --directory /private/eyes --owner goal:stable ack SEQUENCE
xdog-eyes --directory /private/eyes --owner goal:stable pause tasks
```

Use a definition handler `{"id": "changed", "type": "events"}` with its explicit
route and `--delivery events`. Events mode **never creates an ACP handler or
wakes an Agent**. Goal receives raw event JSON through CLI stdout, handles it
inside Goal runtime and decides any later reminder under host policy. Claw does
**not** invoke this CLI itself: it loads the installed Eyes registered tool;
the registered tool itself invokes the public CLI, keeping daemon-control details inside Eyes.

### Machine-readable command contract

| Command | stdout on success |
| --- | --- |
| `start FILE` or `start -` | One registration JSON object, including `registration_id`, `key`, `state`, `duplicate` |
| `status ID`, including `--wait-ready --timeout SECONDS` | One status object for the original registration |
| `status`, `list` | One scoped `{"registrations": [...]}` inventory object |
| `events --after N --limit 64` | One `{"events": [...]}` object, including `{"events": []}` for an empty batch |
| `events --follow --after N --limit 64` | JSONL, one event object per line, no wrapper, heartbeat or empty batch |
| `ack SEQUENCE` | One `{"acknowledged": SEQUENCE}` object |
| `pause ID`, `resume ID`, `cancel ID` / `stop ID` | One registration status object |
| `daemon start/status/stop` | One daemon lifecycle/status object |

Each event object has `sequence`, `key`, `registration_id` and the full original
Delivery JSON in `delivery`. `--limit` is a per-batch integer 1..128, not a total
stream limit. An omitted `--after` starts after this owner's persistent
ACK cursor; explicit `--after 0` re-reads retained history. Follow advances only
its local read cursor and never automatically ACKs. ACK is cumulative within the
scope and idempotent; persist consumer effects before ACK and deduplicate original
delivery IDs across the effect/ACK crash window. Unacknowledged events provide
backpressure. No Python callable crosses this boundary.

`start -` reads one UTF-8 JSON object from stdin until EOF, bounded to **1,044,480
bytes**; duplicate keys, non-finite values, multiple documents and oversized input
are rejected. For `start -`, relative source roots and any explicit relative
handler `cwd` resolve against the **CLI process current directory**, not the shell
redirection file's directory or daemon startup directory. For `start FILE` (and
`validate FILE`), they resolve against the **definition file's parent directory**.
Use absolute roots when a stdin producer cannot guarantee the CLI current directory.
`--no-wait` returns the registration receipt without waiting for a baseline; it does not resume an explicitly paused
registration. Resume explicitly, then use `status ID --wait-ready --timeout 10`.
The wait is read-only and never registers or resumes anything. Optional
`--source-checkpoints-json JSON_OBJECT` supplies definition-bound scheduler cursor
mapping for already accepted source recovery, not executable code or a clock.

Commands exit 0 on success; errors exit 2 with a bounded diagnostic only on stderr.
A timed-out wait emits no success JSON. JSON output frames are bounded to one MiB;
follow writes with pipe backpressure, not an unbounded output queue. Closing stdin
of a follow process is harmless; closing stdout, terminating it or reaching a
broken pipe only detaches the consumer. It does not ACK, pause/cancel observation,
stop the shared daemon or roll back an accepted registration. A lost mutation
response remains uncertain: inspect/reconcile its original scope/key/ID rather
than automatically retrying a different mutation.

Follow reconnects **read-only** after socket EOF, disconnection or generation
change. It waits for an absent daemon to be explicitly restarted; it never starts
one, re-registers, resumes or ACKs during reconnection. Nonempty malformed protocol
frames remain errors, not a reason to retry indefinitely. Host retirement closes
its stream only; Goal pause explicitly runs `pause`, and Goal completion/clear can
explicitly run `cancel`. Cancelled/unknown work is not blindly replayed. Scope
strings isolate accidental cross-owner access within a UID, not malicious code
with that UID's authority. Event history has no automatic retention/compaction.

The old event-handler host/capability/descriptor protocol is removed. Core local
permissions are not an OS sandbox. No TCP/HTTP/Windows transport, installed system
daemon, hosted broker, automatic coding startup, API/web source, script handler,
or harness-specific handler is added to Eyes. Claw can expose its own ACP backend
through its gateway, without an Eyes dependency or a special router path; see
[Claw/Eyes ACP](../../docs/claw-eyes-acp.md) for `group:<id>` addressing and setup.
Stdio is an optional generic ACP transport; Unix is the XDOG-local transport for
coding and Claw callbacks.

## Installation and development

The runtime dependencies are the official `agent-client-protocol` SDK, pinned
`>=0.12.1,<0.13` for Python 3.12–3.14, and `croniter>=2.0,<7` for bounded cron
validation/calendar calculations. Eyes does not depend on ai/agent/coding/claw/flow.
The marker excludes the currently unsupported ACP SDK on Python 3.15+; definition
validation remains usable. Workspace development commands run from repo root.

See [ACP/session/inbox protocol](../../docs/acp-session-routing.md) for explicit
receiver startup, all commands, permission/unknown-result semantics and limits,
and [current verification](../../docs/unified-inbox-batch-verification.md) for
release evidence. The older `eyes-verification.md` is a historical checkpoint.
`uv build --package xdog-eyes` ships skill, example, py.typed and license. This
feature does not push/publish or provision a PyPI publisher/system service.


## Installed model tool

Installed metadata registers `xdog.eyes.capability:provider`. Discovery/import is
inert. Opening the tool binding only ensures the shared daemon is running; it
does not open a host journal, subscribe to application events, capture replies,
recover host observers or start a model. There is no ManagedObservers runtime.

Choose a stable owner, save a full Eyes v2 JSON, then call
`eyes(action="create", owner="my-watches", definition_path="/project/eyes.json")`.
`definition_path` must be an **absolute path** to an already saved regular,
non-symlink JSON file (maximum one MiB); relative paths are not accepted. Source
roots and any explicit relative handler `cwd` resolve beside that JSON file.
The caller owns the naming scheme; Eyes never derives owner from a host/session/group.
JSON handlers/routes select all destinations, including other endpoints/sessions.
The current host address is convenience metadata, not an allowlist. Normal
receiver authentication and protocol checks remain; the tool does not restrict
targets to the current host/group.

The registered tool returns a CLI envelope `{exit_code, stdout, stderr}`. The
CLI's JSON result remains a string in stdout; check exit_code before decoding it.
Nonzero exits preserve their diagnostics; uncertain or cancelled mutations are
not automatically retried.

The daemon executes handlers directly, like CLI `start`. Tool status is source
and handler-delivery state only. There is no model execution/report watcher or
chat notification policy. Supported actions: create/list/status/pause/
resume/cancel. `cancel_notifications` is removed. Tool/CLI controls use the same
registration IDs and owner namespace; closing a binding only detaches.
Old managed host journals are left untouched, not imported or migrated.

The provider remains application-scoped. Coding and Claw both load that scope
through normal installed-capability discovery; no Eyes-specific host adapter or
enable switch is required. The package must already be installed. All model tool
actions require caller-supplied owner; there is no context action. The
shared skill contains no application-specific naming or routing policy.


### Capability contributions and readiness

`skill_names=("eyes",)` advertises a packaged skill, not a default body or tool
permission. SkillManager discovers it and explicit activation loads its body;
Flow follows the same policy. Capabilities can contribute skills, tools, runtime
resources or extension hooks without supplying all four. Startup and close belong
to their real scope owner; no capability creates a second Agent execution loop.

The daemon control endpoint becomes available before slow handler recovery. Its
live protocol/features are negotiated separately from the on-disk journal version.
An incompatible process/state is reported rather than automatically replaced,
migrated or replayed. Closing/cancelling a model tool invocation settles its owned
CLI process and IO; it does not stop the shared daemon, retract an accepted
registration or blindly retry a request whose outcome is uncertain.

# Shared Eyes daemon and host-owned Goal runtime

> Managed observer/tool-hosting portions below are superseded by
> `eyes-thin-tool-design.md`. ManagedObservers and its host report paths are removed.
> The separate Goal CLI/raw-event boundary remains applicable.

Decision: 2026-10-22. This supersedes the earlier no-shared-daemon and embedded
Goal task-observation design. It does not authorize changing running user services.

## Boundaries

- Eyes owns the independent Linux user-local observation process, CLI, registered
  model tool, registration state and event delivery. Goal invokes the real CLI
  through fixed subprocess argv and bounded JSON/JSONL; it imports no Eyes SDK,
  protocol, loader or runtime. Claw only loads the Eyes-registered tool through
  generic capabilities, never shells out to Eyes itself. CLI and Eyes tool internals
  share private daemon control code, not a public Python client API. Loading
  metadata or skills does not start observation.
- Goal remains an installed capability in the original Coding session/branch or
  Claw group process. It owns Goal records, lifecycle and reminder coalescing. A
  file event is input to Goal runtime, **not** a direct request to run the model.
- Clients publish only generic execution/owner ports. No Goal business policy is
  added to ACP, Coding or Claw. ACP is still the canonical automatic model-input
  route, after Goal decides whether a reminder is appropriate.
- ACP-origin Claw work executes on the bound group but has no implicit chat reply
  destination. Protocol acknowledgements, persisted results and result queries
  remain available. Meaningful user notifications are explicit single-destination
  `message.send` calls to an actually bound group channel, not automatic broadcast.
- Host exceptions are execution/delivery facts, not Goal lifecycle decisions.
  `wait(host)` is removed. Uncertain or interrupted execution is not automatically
  replayed merely because the Goal remains active.

## Owner restoration

An activated original owner opens installed capabilities **before** admitting its
first queued/new input. It must not require a human turn to restore subscriptions.
Same-owner restoration reads durable Goal state, reconciles original registration
and receipt identities, and restores only the automation allowed by that state:

| Goal | Restore behavior |
| --- | --- |
| active | Restore observation and reminder scheduling under existing permissions |
| waiting(tasks) | Restore task observation; inspect changes before further work |
| waiting(user) | Restore management/query only; no proactive continuation |
| paused | Restore management/query only; explicit resume still required |
| completed | Retain completed current Goal; no observer or continuation |
| no current Goal | Load capability only; do not create work |

Restoration is not a synthetic user resume and must not increment Goal versions.
New session/branch/group owners cannot take over another owner's registrations.
Recovering accepted events is distinct from replaying model/tool effects. A
reconnection cannot authorize retrying unknown delivery or interrupted execution.

## Acceptance checklist

- [x] CLI-only Goal integration and registered-tool-only Claw integration; no public EyesClient SDK.
- [x] Claw managed observers and Goal task observation do not embed the engine.
- [x] Task events cross process into Goal coalescing before ACP model activation.
- [x] Registration scope, targets and original delivery IDs survive reconnect.
- [x] Pause/cancel cannot affect unrelated owner registrations.
- [x] Daemon/host recovery does not duplicate accepted effects or invent downtime events.
- [x] ACP has no implicit chat destination; native user reply behavior stays intact.
- [x] No new wait(host); historical records retain their evidence without effect replay.
- [x] Original owner startup restores active/tasks observation without a human turn.
- [x] User wait/pause/completion survive owner startup without state changes.

Check marks require test evidence, not implementation intent. Final verification
must distinguish real subprocess/Unix socket tests from mocked model or channel IO.

## Durable event acceptance and shutdown

Goal's event consumer returns an explicit acceptance boolean. A normal callback
return, a stale owner, a paused/non-current Goal, or failed storage is **not** an
ACK. Only a committed (or already committed duplicate) original delivery ID may
advance the daemon cursor. A declined delivery remains available to the original
owner after restoration; it never falls through to direct ACP model input.

Registration startup is owned asynchronous work. Cancellation after server commit
must join the original registration result before suspension, rather than abandon
an unseen registration. If a response is lost, the same original owner/key can
suspend that registration without inventing another ID or touching another owner.
A host detach intentionally keeps the registration; a business pause suspends it.

Coding capability preparation runs on the existing serialized owner control route,
not concurrently with navigation. Startup pins its inventory and checks original
identity again after IO; shutdown cannot publish a stale parent. No Goal-specific
branch or second model/execution loop exists in that host code.

## Recovery is not a hidden host wait

Known preparation failure does not impose a Goal resume requirement. A subsequent
real successful execution on the same owner can supersede observed known failures
without changing the Goal lifecycle; original fault and receipt history remain.
Such known host failures do not create a user question or claim user intervention
is required. Uncertain delivery/interrupted effects remain fenced for review,
never silently replayed or cleared by an unrelated availability notification.


## Earlier daemon verification snapshot, before CLI-only amendment (2026-10-22)

- All nine configured test paths: **9868 passed, 34 skipped**, 232.80 seconds
  (`/tmp/xdog-daemon-final-regression3.log`). Final status-presentation assertion
  adjustment was additionally checked by all Skills tests: **711 passed**.
- Ruff across packages/test_support; strict mypy across nine packages, **628 source
  files**; offline lock check; Flow examples **17 passed**.
- Seven package wheel/sdist builds (**14 artifacts**) and Twine checks. Fresh
  wheel-only environment outside the repository verified Coding owner startup,
  shared daemon use, original Goal registration restoration, offline events before
  model input, business-state preservation, host-wait rejection and scoped CLI list.
- Tests include real independent daemon subprocesses and Unix sockets. Deterministic
  scheduler tests separately use an in-process Unix daemon with injected clocks.
  Model transports and actual Weixin network delivery are mocked; no live Weixin
  send, production Goal mutation, or gateway restart was performed.
- Two test-only default-daemon instances found during migration were verified to
  contain exclusively pytest workspaces, backed up, normally stopped and archived
  under `/tmp/xdog-eyes-daemon-refactor/`. Host tests now share a private-daemon
  fixture. Final process inspection found no remaining test daemon/pytest process.

Limits: same-UID owner namespaces are binding isolation, not protection from a
malicious process with the same OS authority. Event history has no automatic
retention cleanup. Unknown/interrupted effects and old host pending deliveries
without a safe daemon binding remain fail-closed; no automatic replay, silent
migration, or new effect-review override API was added. Historical host waits
remain readable but cannot be newly created. No commit, push, tag or release is
part of this implementation run.


## CLI-only integration amendment (2026-10-22)

The previous asynchronous Python client is removed, not retained as an alias.
Goal uses `python -m xdog.eyes` as the installed CLI entry point with explicit
`--owner` (the sole management namespace), no shell, bounded stdin/stdout/stderr,
and owned process cleanup. `start - --no-wait` accepts the definition on stdin; baseline readiness
uses `status ID --wait-ready`; `events --follow` emits JSONL without ACK. Goal
commits each original event then invokes the separate `ack` command. Detaching
terminates only its own CLI stream process. Follow reconnects read-only to a
restarted daemon; it does not undo an explicit `daemon stop` by auto-starting it.
Registration/owner restoration explicitly ensures daemon readiness through CLI.

Claw remains unaware of daemon transport and CLI mechanics. Its application
continues to discover/load the Eyes capability and expose its registered `eyes`
tool under actual host permissions. The registered tool invokes the public Eyes
CLI too; only CLI internals speak private daemon control. There is no Claw-owned
Eyes SDK or CLI adapter.

The full-regression numbers above describe the preceding implementation snapshot;
the CLI-only amendment requires its own verification below before acceptance.


### CLI-only amendment verification

The final package matrix totals **9903 passed, 34 skipped** across nine configured
paths. The first full run found one old documentation assertion expecting a Python
client; the assertion was updated to the supported CLI event-stream wording and
all Claw tests reran: **1982 passed**. The other eight package paths passed in the
full run. Logs: `/tmp/xdog-eyes-cli-refactor/regression.log` and `claw-final.log`.

Ruff and nine-package strict mypy (**630 source files**), offline lock check and
**17 Flow examples** passed. Seven packages / fourteen wheel+sdist artifacts passed
Twine checks. The fresh wheel-only probe blocked every Eyes import in Goal's host
interpreter while exercising real CLI registration, event reception, ACK, offline
restoration and pause. Public client module/exports were verified absent in the
installed wheel. Claw has static and real registered-tool boundary coverage.

No production gateway restart, real channel send, commit, push, tag or version
change was performed. Test CLI processes/daemons are isolated and cleaned up.

## JSON-owned delivery routes (2026-10-22 refinement)

The current thin tool supersedes the earlier host-managed observer, context and
route-authorization design. Eyes does not interpret group IDs or construct
application-specific session addresses. The caller chooses an explicit stable
`owner`; Eyes does not derive it from a host/session/group. Within the selected
daemon instance, owner is the **only management namespace**, including inventory,
registration controls and event ACK cursors. There is no management `workspace`
parameter, CLI `--workspace`, `tool_scope` owner inference or `eyes context` action.
Owner labels isolate accidental cross-owner access, not malicious same-UID code.

The tool creates from `definition_path`, an **absolute path** to an already saved,
bounded regular non-symlink JSON file (maximum one MiB). Relative tool definition
paths are not accepted. The same v2 `handlers` and `routes` used by CLI define
all destinations; they are not restricted to the current host or session. The
daemon executes handlers directly. There is no host event relay, captured reply,
model-execution watcher or user-notification policy in Eyes. Receiver admission,
authentication, permissions and original receipt ownership remain intact.

Path resolution is independent of management identity:

- Tool `definition_path`: relative source roots and any explicit relative handler
  `cwd` resolve beside the JSON file.
- CLI `start FILE` / `validate FILE`: the same paths resolve beside that file.
- CLI `start -`: the same paths resolve against the **CLI current directory**,
  not the file redirected into stdin or the daemon startup directory. CLI consumers
  should use absolute roots when they cannot guarantee that current directory.
- Changing directory does not select a different owner inventory. Accepted
  normalized definitions are immutable snapshots; file edits never redirect an
  original registration or pending delivery. Recovery retains the original key,
  registration/delivery IDs and pinned destination bindings.

### ACP session directory and stdio startup directory

The ACP target-directory fields are:

- Handler `session` names an existing receiver session. For `delivery: "enqueue"`,
  handler `cwd` is optional; without it the receiver resolves the session's actual
  directory. An explicit `cwd` is a checked assertion, not a session relocation.
  The client pins the returned directory, inbox namespace and branch for later
  delivery/recovery. No observation root, definition base or daemon directory is
  substituted for a missing target directory.
- Standard ACP `delivery: "prompt"` requires an explicit handler `cwd` for
  `load_session`. Removing management workspace does not remove protocol cwd.
- A stdio connection contains only `transport: "stdio"` and `command` (argv).
  There is no `connection.cwd` field. The subprocess inherits the spawning Eyes
  process's startup directory, independently of the target session directory;
  handler `cwd` is never passed as subprocess cwd. Use absolute executable/script
  paths when startup-directory independence is needed.
- Unix `connection.path` remains an explicit absolute socket path.

## Generic tool/skill contract refinement

The Eyes skill is application-neutral: no harness-specific group/session rules,
queue settings, permission modes, legacy scheduler migrations or gateway setup.
Tool schema describes exact per-action fields and the owner → write JSON → create
→ status sequence. All actions require `owner`; `create` additionally requires
absolute `definition_path`; status/pause/resume/cancel additionally require the
original `observer_id`. `list` takes only action/owner. No inline definition,
management workspace or null/empty unrelated fields are supplied.

The installed capability is application-scoped and available to both Coding and
Claw through generic discovery. Host product setup stays in that product's docs.
The retired `authorize_target` callback is not renamed or replaced by a host
allowlist. Actual runtime information may supply available ACP address facts;
Eyes does not require a private host context API. CLI and tool manage the same
owner-bound registrations, and closing a tool binding only detaches.

The verification snapshots above remain historical evidence for their stated
implementations, not verification of this refined owner/path/ACP contract.

# Eyes: daemon, JSON and thin tool only

Decision: 2026-10-22. This supersedes all ManagedObservers / observer-report
hosting designs, including the managed portions of eyes-daemon-goal-hosting.md.

## Execution boundary

```text
installed tool binding → ensure Eyes daemon is available
model → eyes tool → public Eyes CLI → daemon controls
JSON → sources → detectors → routes → handlers → destination
                                      ACP → normal receiver input
```

Opening the binding starts/reuses the same-user Eyes daemon. The daemon owns
registrations, source cursors, pending deliveries, handler connections and
recovery. Binding closure does not stop/cancel it. Discovery/import alone is
inert. No host DB, source consumer, receipt mirror, output-source callback,
creator reply capture, report tracker or host scheduler exists.

The tool does not restrict delivery to the current owner/session/endpoint. The
JSON fully specifies handlers, connections, opaque session IDs and optional
handler `cwd`. A relative handler cwd resolves beside the JSON file. Enqueue
can omit cwd: the receiver resolves the existing target session directory. Explicit
cwd is a consistency assertion; standard ACP prompt requires cwd for load_session. Source roots independently resolve
beside the JSON file. Multiple sources/handlers/routes and Unix/stdio ACP are
supported. Existing receiver authentication, protocol, identity and namespace
checks remain intact; the tool does not bypass a receiver or supply credentials.

Management registrations are caller-selected owner-only scoped for inventory
and cancel/pause/resume. This namespace does not restrict destinations. Tool and
CLI use the same registry, IDs and controls; each tool action requires owner,
and create requires an absolute definition_path. There is no management workspace
parameter, context action, or host identity lookup. Ownership is registration metadata alongside the
normalized JSON, not a field inserted into its handlers.
There is no `cancel_notifications`: downstream messages belong to the receiver.

ACP enqueue delivery ends at the receiver's durable intake acknowledgement, not
Agent completion or chat delivery. Standard ACP prompt retains its explicit
protocol completion semantics, without an additional watcher. Raw events mode
retains explicit business ACK and is separate from direct handler delivery.
Goal's CLI/raw-event consumer remains unchanged and owns only Goal decisions.

## Claw

The gateway starts normal private ACP by default, independent of installed
capabilities. Its actual live address and enqueue payload shape are included in
the main Agent's dynamic prompt. ACP routes into the ordinary Core Inbox/current
group Agent. No observer-specific processing occurs. Normal user replies retain
their existing routes; automatic ACP work is silent unless the Agent uses the
normal message tool. No Eyes-created output route can override that behavior.

The installed tool receives only minimal generic tool context, not host database,
execution/status, output subscription, reply capture or notification controls.
Old observer journals remain untouched and are never imported. Retired implicit
output records are not automatically replayed. Existing accepted Core work remains
Core-owned; no cleanup reverses effects or silently removes user data.

## Verification requirements

- Tool registration starts/reuses daemon only; no observation/host DB/model.
- Tool closure and recreation preserve daemon state; daemon restart restores
  its own accepted definitions, without host observers.
- CLI and tool see identical management IDs in the same namespace.
- Different JSON endpoint/session/cwd, external roots and multiple sources work.
- Closing the host tool binding does not stop direct daemon delivery.
- A blocked receiver Agent does not prevent the next event's enqueue.
- Ordinary ACP silence, explicit message.send and user reply behavior stay intact.
- Actual main prompt exposes its live ACP address without Eyes-specific rules.

## Application scope and caller ownership refinement

Eyes declares application scope only. Both Coding and Claw discover this scope.
They pass the same generic inert Agent ApplicationPort marker, not a feature
adapter or live session/group manager. Eyes ignores that marker: it has no ToolHost
protocol, application data_dir dependency, tool_scope callback or owner hash.

Coding keeps its application CapabilitySet/marker on the top-level controller
across conversation and branch navigation, while its separate session providers
retire normally. Both close through existing owned cleanup when the controller
exits. It adds no process-global execution owner or child auto-discovery. Normal
permission hooks, readonly state, tool selection and parent child grants remain.

The caller chooses and persists owner naming. The same owner accesses
the same accepted JSON from Coding, Claw or CLI; other owner strings select another
management inventory, not another delivery permission. No owner format is imposed
and no application/session/group ID is inferred. The default daemon root is
`~/.local/share/xdog/eyes`, honoring XDG_DATA_HOME; explicit runtime-directory
overrides remain supported. Old state directories are not migrated or replayed.

## Capability loading and lifecycle

A trusted installed capability is a composition unit: it may provide skill names,
tools, owned runtime resources and extension hooks. The SDK need not understand
the domain and a provider need not supply every contribution. Its real scope and
host own startup/close and authority. SkillManager discovers packaged Flow/Eyes
skills; neither body is injected by default. Explicit selected skills must resolve
successfully rather than being reported active without instructions.

The Eyes control socket is made available independently of target recovery. A
slow target cannot consume the host daemon-bootstrap deadline. Compatibility of a
live daemon is negotiated separately from its persistent journal format; mismatches
are explicit and do not trigger automatic process replacement or migration.
Tool cancellation/closure joins its owned CLI wait, never silently retries a
mutating request or cancels an observation already durably accepted by the daemon.

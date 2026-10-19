# Claw owned ingress, source and constructor IO — Task3 source verification

Date: 2026-10-19. This completes the known owned-transcript-io source findings
when its final task gate is recorded in the ledger. It is **not** final47/47,
whole-branch review, installed-artifact parity or overall goal completion.
Previous optional-memory/application checkpoint: b252262.

## Actual ownership boundaries

| Production path | Data/resource IO owner | Owning-loop boundary / proof |
| --- | --- | --- |
| Unix native + private ACP bind/close | Existing BoundEndpoint worker acquires descriptor, mode and inode; worker cleanup removes only that inode | Actual server adopts descriptor after0600, not before. Private ACP still requires owned0700 non-symlink directory and refuses **all** existing endpoints, including stale sockets; native stale policy is separate. |
| Python3.13 server closure | Shared listen helper disables asyncio's automatic unlink | Otherwise asyncio silently deletes the socket on the event loop before the explicit inode owner can act. Native and ACP actual unlink traces were RED. Python3.12 uses the existing API without the newer cleanup argument. |
| ACP load/prompt/enqueue/status cwd checks | Immutable Path resolution on owned workers | Registry/workspace/peer/binding rechecked on original loop; canonical aliases still accepted. Prompt waiter registered before yielding so cancel can stop unadmitted validation. Cancel checks its original peer/ref without waiting for another path read; accepted input is never withdrawn by disconnect. |
| Generic Agent ACP adapter cwd | Same existing validation engine, now an owned worker call | Peer identity rechecked before backend dispatch and after return; no late closed-peer session publication. No Goal/Eyes policy or new execution owner. |
| Source full-history spool | Actual prepare/read/close tasks settle before its scope is cleared | One close owner despite repeated caller cancellation; late prepared files are closed on workers. Latest-request coalescing, revision fences, redaction/checksum/chunks remain. Late close error is logged once without replay or changing business outcomes. |
| MCP readonly preview spool | Same existing MCP builder/data plane, owned settled cleanup | Cancellation only cancels its original operation once, then joins actual preparation/read/close. Typed-content negotiation/session fences retained; preview failure is not model/tool execution. |
| Existing child catalog restoration | Frozen branch + JSON record data/diagnostic read during session preparation | Existing ChildStore loader/validation retained. Original loop constructs ChildSupervisor from detached data, no second Agent/automatic child replay. Corruption bytes survive; delegation remains explicitly unavailable, not an empty success. |
| Child model metadata | Original narrow readonly model port on an owned worker | Original group operation remains fenced through repeated cancellation; no child/model is started after canceled preparation. |
| Native model metadata, extension-root aliases, inert built-in tool resources | Metadata and ExtensionRoot read on workers; built-in tool factories run in unpublished GroupRuntime factory | Actual Agent/ExtensionRuntime/ChildSupervisor constructors stay on their original loop. Bash cwd resolution no longer occurs lazily during first prompt. Factories create no model, observer, receiver or tool execution. |
| Model list/reasoning/selection | Readonly catalog/resolve calls on owned workers | Recheck actual session/model/stream after yielding before applying/persisting. Atomic setting cancellation semantics remain. Inspection uses the actor's frozen metadata, not another filesystem lookup. |
| Default provider adapter | Separate unpublished resource preparation, explicitly handed to constructor | Not hidden as a callback in frozen session data and not assigned over configured stream_fn=None. New contexts retain original fresh-discovery behavior, with no login/network/model request in preparation. |
| TUI connect preflight | Owned readonly exists check | Same GatewayConnectionError, no receiver/model startup. |

`PreparedSession` contains detached history/settings/skills/children/model/root
facts, **not** an Agent, Session, callback or another business store. The provider
adapter is a distinct resource argument to the original constructor. ExtensionRoot
reuses the original resolver and checks the configured location on adoption;
normal synchronous SDK composition retains its prior default behavior.

The direct async-path audit currently finds only gateway `uploads.resolve`
methods, which operate on the existing in-memory attachment map, not files.
Manual callgraph inspection additionally covered synchronous constructors and
indirect metadata/ChildStore loaders: AST/grep absence alone was insufficient and
would have missed the real child/native/extension/tool cases fixed here.

Synchronous get_or_create_session/get_session_info/navigation/reset helpers are
fixture/composition boundaries; production async ingress/control paths use the
owned preparation/status/application methods. Readonly cached index/branch/name
lookups are in-memory (name uses nonblocking lock acquisition); real reads and
writes remain behind the owned IO helpers and original scope fences.

The separate TUI client assembles source/MCP views on workers under its receiver
lock and closes them before its connection loop finishes; asyncio.run also joins
its default executor. Its existing terminal-thread bounded join is UI exit
latency, **not** release of a Core execution/store lease or cancellation of
accepted business work. No new fixed child execution/model/token/record budget
was introduced or justified by UI resource capacities.

## Generic source neutrality (F-05)

Removed only the old goal_runner/scheduler channel and Goal-prefix filtering from
source_records. Five real ordinary inputs through Orchestrator/Inbox/Agent/JSONL
retain their exact user text in the generic source spool, including `[Goal:`,
`Goal completed:` and the old planner prefix. Labels do not imply business kind
or permission. Tool redaction, canonical input decoding, leases and readonly
history remain unchanged. This is not a Goal migration, observer/Goal control
rule or model trigger. Main source/UI projection no longer decides Goal policy.

## Observed behavioral RED and focused evidence

- Real ACP startup/close + load/adapter/prompt/enqueue/status + late-load cases:
  **RED8/1.74s**, actual loop IO and premature publication. After moving binding,
  the trace exposed Python3.13 automatic unlink; native cleanup independently
  **RED1/1.07s**, then explicit inode ownership for both endpoints.
- A non-socket replacement immediately after actual bind was otherwise chmodded
  and published: **RED1/1.04s**. It is now refused without altering file bytes or
 0640 mode. These are accidental-race guards, not a hostile same-UID filesystem
  sandbox or a claim to defeat arbitrary concurrent user tampering.
- Source preparation/read/close repeated cancel and actual ordinary-input
  projection: **RED8/1.82s**. Additional late error/TUI preflight **RED2/0.34s**.
- MCP actual preview spool phases: **RED3/0.28s**, then existing preview/context
  cases plus new actual ownership cases12 passed0.66s.
- Existing real terminal child catalog normal/corrupt restoration: **RED2/1.08s**;
  original metadata preparation cancel **RED1**. Real saved bytes, original IDs,
  original group fence, no replay and original diagnostics are retained.
- Real constructor metadata native-catalog read, extension alias and bash cwd:
  **RED3/1.15s**. Real reasoning/catalog/selection **RED3/1.22s**; inspection cache
  **RED1/0.73s**. All preserve original model execution thread.
- A preliminary default-port implementation accidentally cached the adapter as
  configuration: **RED1/0.52s**. Explicit per-context resource handoff fixes it;
  frozen prepared state contains no callbacks and configured None is unchanged.
- Agent ACP/Claw ingress/source/real socket suites75 passed10.40s before final
  constructor fixes. Constructor/child/Goal/tool state65 passed14.75s; subsequent
  root/cache scope refinements and all new cases are included in final full9.

Only model/external data boundaries and physical post-operation barriers are
controlled. Files, native SQLite stores, real private descriptors, actual stdio
extension workers, actual child execution/history, and existing controllers are
used. No paid model, user credential operation, real user service or existing
user data is invoked/modified. Original terminal/socket/deadline coverage remains;
no xfail/filter/timeout relaxation was added.

## Final source gates

Results are appended only from terminal commands. Known earlier default4 timing,
full-root and explicitly failed monitor-hang runs stay in their milestone docs;
these source fixes do not retrospectively label those runs green.

## Required return path

Complete the existing Task3 ledger gate, then return to parent final-acceptance
Task2: inspect all47 actual sources/tests/limits, then **one** fresh-context
whole-branch reviewer and required fixes. ParentTask3 rebuilds current affected
wheels/sdists, Twine/parity and Goal-only/Coding/Claw/ACP/native/output consumers
**after** the last required runtime fix. Existing cold snapshots predate this
code; they cannot prove current release behavior. No push/merge/publish occurs.

Current unfiltered all9 `uv run python scripts/test.py --jobs 2`: **7022 passed,
34 skipped**,199.49s; Claw1193/136.18s, Agent1935/15 skipped. Strict all9 mypy601
source files, full Ruff packages/scripts, Flow17, lock/diff checks passed.
Logs under `/data/t.Vy5SaQ/`: `claw-owned-ingress-source-final-root9.log`,
`claw-owned-final-io-strict.log`, `claw-owned-ingress-source-flow-lock.log`.
No runtime edit occurred during this full gate. Formal post-commit task-done
re-runs its all9 command; its result is recorded in the original plan ledger.

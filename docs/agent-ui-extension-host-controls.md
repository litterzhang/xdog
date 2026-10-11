# UI-043 extension host controls — explicit implementation scope

Updated 2026-10-11. UI-043 remains **待实现**. The original named requirement includes
active-tool selection, session lifecycle/compaction, messages and queue controls;
existing inspection and native controls are not blanket extension interoperability.
No OS sandboxing is implied, and no additional requirement ID is counted here.

## Active-tool foundation implemented

Agent exposes `active_tools`, `tool_selection_revision`, and host-only
`set_active_tools(names, expected_revision=...)`. `state.tools` remains the configured
inventory; the selection is independent, in configured order. None explicitly restores
defaults, empty list disables all; explicit subsets cannot include missing/duplicate/
invalid names. CAS rejects stale/noninteger revisions; selection changes require an
idle agent, including the owned producer's cleanup lifetime. No prompt/model starts
from selection. Message reset retains the choice, never silently restoring tool access.

`set_tools` still refreshes configured registrations. Explicit selections survive the
refresh and do not grow when new tools appear. Removed names retire from the selection;
later re-registration does not restore them without explicit host selection. Ambiguous
configured names refuse while a selection is active. Defaults retain existing behavior.
The existing shared agent loop uses active_tools both for model definitions and tool
lookup, in sequential and parallel modes. An unselected model-requested tool yields an
ordinary unavailable-tool result, not execution; selected tools still pass the caller's
pre-tool permission hooks. Model supports_tool_calls=False remains authoritative.
This feature is an API selection/confinement boundary, not OS-enforced sandboxing.

Current tests: configured-vs-active identity, stale CAS/invalid names/revisions,
refresh/removal/re-add, empty/default behavior, idle/in-progress selection refusal,
parallel/sequential model/executor matching with host denial, no-tool model handling,
and real ExtensionRuntime worker load/unload/reload/sync retaining selection. Initial
3 test-harness failures treated ToolExecutionMode Literal as a constructor and omitted
model config; corrected to existing runtime types, not a production red claim.

Final10-file102 passed/no skips43.38s `/tmp/ui043-active-tools-hosts.log`, handle4944
exit0, covers Agent/loop, actual extension runtime/tools and both-host inventory/lifecycle
regressions. Earlier core59 pass8.25s `/tmp/ui043-active-tools-integrated.log`.
Root Ruff/seven-namespace strict mypy508 clean `/tmp/ui043-active-tools-final-static.log`.
Agent wheel/sdist `/tmp/ui043-active-tools-artifacts`, handle60559 exit0; source bytes
exact SHA256 `/tmp/ui043-active-tools-source.json`. No live provider/auth/browser,
downloads, publishing or user-service restart; unrelated worktree changes preserved.

## Current remaining scope (supersedes historical OPEN wording below)

This document's implementation sections are chronological evidence, not additional
requirements. UI-043 is one stable ID. Active-tool review now exists in Coding native
TUI/RPC and Claw native TUI/socket; both child factories enforce captured parent
ceilings. Both hosts now persist reviewed selections and inventory retirement, and
report configured versus selected definitions. The latest sections give the exact
contracts and evidence. Do not redo these because earlier checkpoints called them absent.

Remaining within this same ID:

1. Applicable child direct-selection/headless contract reconciliation, distinct from
   the implemented inherited parent ceiling and read-only invocation inventory.
   Preserve original host review and deny authority borrowing; do not infer broader
   permission from another API's name.
2. Remaining message/queue public host controls and lifecycle/compaction invocation-scope
   reconciliation. Reviewed parent-command compaction requests now exist in both native
   hosts and Coding RPC; do not reimplement that path or treat it as broader-scope parity.
   Reuse canonical runtime controls, not a second loop.
3. Scope-matched real worker/host/terminal validation and the unchanged final frozen
   complete-root/static/distribution/example gates. Scoped green tests are not final
   requirement or goal completion.

64 stable requirements:43 complete/6 implement/6 verify/5 missing external/4 deferred;
17 non-deferred unfinished. UI-041's remaining broader editor/host contract scope stays
separate. Only non-current-Linux native validation is deferred (UI-060–063).

## Extension API and reviewed Coding RPC selection (2026-10-11)

ExtensionAPI now exposes command-host get_active_tools and set_active_tools(names,
revision), with bounded session request/answer vocabulary. Outside an original
interactive command context (including tools/headless), reads return unavailable and
selection returns False. Names may be an explicit subset, [] or None/default reset;
unknown/duplicate/out-of-ceiling names cannot gain configured tools. Snapshot exposes
configured/active names and original Agent selection revision, not permission grants.

Coding RPC independently negotiates extension_active_tools (default0). Host returns
its original Agent snapshot, substitutes trusted current_tools into the client review,
and uses the existing broker for explicit boolean approval. Only unchanged original
command/session/navigation-revision/capability generation and Agent inventory revision
may queue selection. Selection is mutually exclusive with deferred navigation/name/
browser actions in the same callback. It applies only after successful callback,
cleanup and command-result delivery; separate extension_active_tools_result reports
applied/not-applied. Agent idle CAS and pending parent approval/active-child checks
still gate final application. Permission mode is untouched; selecting tools is not
a permission bypass or a second agent/tool loop. No persistence or native TUI grant
is implied by this RPC-only integration.

Initial apply test failed because unchanged ExtensionRuntime.sync_tools rebuilt equal
registered tool wrappers with new callback identities, invalidating every selection
revision at callback completion (`/tmp/ui043-tool-review-current.log`,1fail5pass).
Runtime now reuses a wrapper only for identical original McpTool spec object and
connection client, while checking current registration before returning it. Changed
spec/connection produces new guarded wrapper; removal prunes cache; close clears it.
Actual replacement/reload tests retain stale executor/validator refusal. Actual sync
now keeps tool-selection revision stable for the same registered inventory; changes
still invalidate queued selection instead of silently admitting it.

Worker/RPC tests cover apply/deny/callback failure/withdrawal/inventory change/old client,
foreign responses and original history/deny permissions/no model calls. Actual CLI
selects empty tools through independent review, gets command result before application
receipt, then reads empty active selection through another command without model use.
Real tool callback and headless invocation cannot borrow command selection authority.
Initial type-import/line diagnostics corrected. No test grants implicit tool execution.

Final15-file158 passed/no skips105.50s `/tmp/ui043-tool-review-integrated.log`,
handle25663 exit0; root Ruff/seven-namespace strict mypy509 clean
`/tmp/ui043-tool-review-current-static.log`. Agent/Coding wheel+sdist
`/tmp/ui043-tool-review-artifacts`, handle33196 exit0; all five modified/new production
modules byte-exact SHA256 `/tmp/ui043-tool-review-source.json`. Earlier31 focused
pass14.08s `/tmp/ui043-tool-review-expanded.log`. No downloads/live provider/auth/
browser/publishing or user-service restart, unrelated worktree changes preserved.

UI-043 remains待实现: native Coding/Claw selection UI and Claw socket, applicable
child/headless scope/persistence/description/inventory integration, original host
lifecycle/compaction/messages/queue APIs and full scope-matched verification still
required. Current native-tool tests are regressions of existing behavior, not evidence
that the new selector is installed there.64 IDs43 complete/6 implement/6 verify/
5 external/4 deferred;17 non-deferred unfinished. UI-041 still retained separately;
final frozen complete-root/current-Linux gates unchanged.

## Coding native active-tool review (2026-10-11)

Coding's actual extension command UI now handles active_tools inspection and
select_tools review. The displayed current selection is supplied by the owning Agent,
not the worker. Full target/selection/revision metadata uses the existing full-painted
boolean form; an input acknowledgment alone cannot queue selection. DialogBroker now
publishes select_tools accepted only when its actual future resolves True, reusing the
name/navigation chain. Original command/extension/session/view/inventory and permission/
child-idle guards are rechecked before queued selection and after callback completion.
Only successful callback completion applies Agent idle CAS. Changed inventory, false/
unpainted/repeated/canceled/expired replies or callback failure leave active tools intact.
Draft, configured tools, history and permissions remain unchanged. Selection persists
through unchanged extension sync but is not yet durable session state.

Coding system-prompt tool definitions now derive from active_tools too, avoiding
unselected tools remaining advertised through text when API tools are absent. Configured
inventory is still separate. Native Claw has not installed selection controls yet:
unknown session operations no longer fall through to browser scope; it safely returns
unavailable for these new APIs. A real Claw worker command test proves no browser/model
or composer mutation, not native selector parity. Child host selection is not enabled.

Actual Coding UI tests cover apply/deny/failure/cancel/inventory change and a fabricated
transport True losing to actual broker expiration. Physical private Linux Coding paths
cover approved empty selection and cancellation, full form paint/resize/cursor/native
main-buffer/termios recovery, then a separate command confirms active count0 without a
model call. Existing name-navigation renderer behavior unchanged. Initial static line/
import issues corrected; no approval or safety assertions weakened.

Final9-file90 passed/no skips81.15s `/tmp/ui043-native-tools-integrated.log`, handle31920
exit0 includes actual native UI, physical Coding, native both-host names, RPC selection,
existing tool inventory/lifecycle and core/dialog regressions. Root Ruff/seven-namespace
strict mypy509 clean `/tmp/ui043-native-tools-current-static.log`. All-four Agent/TUI/
Coding/Claw wheel+sdist `/tmp/ui043-native-tools-artifacts`, handle62776 exit0; five
changed production modules byte-exact SHA256 `/tmp/ui043-native-tools-source.json`.
No downloads/live provider/browser/auth calls/publishing/user-service restart.

UI-043 remains待实现: Claw socket/native selection, applicable child/headless behavior,
persistence/reporting and remaining lifecycle/compaction/message/queue host APIs are
still required. This checkpoint is native Coding integration, not full host parity.
64 IDs43 complete/6 implement/6 verify/5 external/4 deferred;17 non-deferred unfinished.
UI-041 and final frozen complete-root/current-Linux verification gates remain intact.

## Claw socket/native active-tool selection (2026-10-11)

Claw now independently negotiates extension_active_tools:1. Its original reserved
command owner handles active_tools inspection on the host loop, supplies current
configured/active names and revision, and forwards select_tools for full-painted
boolean consent. Client footer/cache is not the runtime snapshot. Capability generation,
connection liveness, original group/session/source revision/Agent identity, child-idle
and compaction state are rechecked around review and at final application. Missing
capability/headless calls remain unavailable. Withdrawal cancels only matching
connection-owned tool selection dialogs, not another connection's dialogs.

After the extension callback succeeds and registrations resynchronize, the reserved
host applies Agent CAS if the original capability and inventory still match. Commands
cannot mix accepted selection with another deferred name/navigation/browser transition.
Result reports applied/not-applied separately from API queued:true. Unlike Coding RPC's
post-result delivery ordering, Claw applies inside its held command reservation before
returning the final command result: loss of that receipt leaves effects uncertain and
is not rollback or automatic replay. No tool permission mode is changed or tool executed.
The native application displays applied status after long callback output so it stays
visible. Runtime prompt tool descriptions filter by the active selection too.

Actual Claw tests cover apply/deny/failure/cancel/inventory change/capability withdrawal,
original composer/history/no model and command-reservation settlement. Held successful
callback followed by withdrawal proves previously accepted selection is not applied.
Existing Coding unavailable-on-unnegotiated Claw case explicitly disables capability
instead of falsely claiming native support is absent. Two private Linux Claw physical
paths verify full review/cancel, resize, empty selection and later command count0,
parent draft/cursor/main-buffer/native-scrollback/termios. Initial physical1 failure/
14passes `/tmp/ui043-claw-tools-physical.log` exposed completion notice scrolling out
behind long output; production notice moved after output, assertions retained. Fixed
15pass20.11s `/tmp/ui043-claw-tools-physical-fixed.log`.

Final12-file114 passed/no skips92.61s `/tmp/ui043-claw-tools-integrated.log`, handle95335
exit0 includes both native selectors and their physical tests, both-host naming,
RPC selection, inventory/lifecycle/core/dialog/gateway regression. Root Ruff/seven-
namespace strict mypy509 clean `/tmp/ui043-claw-tools-current-final-static.log`.
Claw wheel/sdist `/tmp/ui043-claw-tools-artifacts`, handle96403 exit0; all four modified
Claw modules byte-exact SHA256 `/tmp/ui043-claw-tools-source.json`. Initial type/import/
line diagnostics corrected. No live provider/auth/browser/download/publication/user-
service restart. Private test gateways only.

UI-043 remains待实现: applicable child/headless selection semantics, durable session
restore/persistence and active-vs-configured inventory reporting; original lifecycle/
compaction/message/queue API matrix and full verification remain required. Current
selection does not claim persistence or inherited child ceilings.64 IDs43 complete/
6 implement/6 verify/5 external/4 deferred;17 non-deferred unfinished. UI-041 and final
frozen complete-root/current-Linux gates remain intact.

## Captured parent active-tool ceiling for children (2026-10-11)

Coding and Claw child factories now capture the parent's active definitions at child
admission, before asynchronous child startup/persistence. Agent.active_tool_selection
exposes explicit selection versus configured defaults without mutable state access.
For an explicit parent selection, child profile tools must be a subset (reserved
supervision/planning tools excluded); an expanded profile refuses before child extension
reservation/start. Empty parent selection stays empty. Default parent selection keeps
previous dynamic child registration behavior rather than silently imposing a new cap.

The captured ceiling is independent of later parent expansion. Child extension context
receives the effective narrowed spec, initial/after-start configured inventory is
filtered, and every context/input/pre/post hook sync retains the same ceiling. A child
extension cannot re-enable unselected tools via registration refresh. Finish retires
captured state and limits. Existing child access/planning/permission checks remain;
this is a tool availability ceiling, not a permission grant or new descendant authority.
No persisted parent selection policy is implemented by this patch.

New tests on both actual factories/supervisors exercise empty/current_time parent
selections while the child model tries filesystem write: no filesystem definition or
execution, no target file and parent history unchanged. Expanded explicit child profile
refuses before worker startup. Actual child toolmock worker enables a tool after child
creation; subsequent context-hook sync still exposes only captured current_time, even
when parent has explicitly restored defaults after admission. Default-child permission
and runtime tests retain prior behavior. Agent snapshot test distinguishes explicit-all
from None/defaults. Current80 subset checks28.37s `/tmp/ui043-child-active-expanded.log`.

Final9-file173 passed/no skips91.92s `/tmp/ui043-child-active-integrated.log`,
handle86890 exit0 includes both host child suites, RPC children/selection, native
selection/inventory/tool lifecycle and core tests. Root Ruff/seven-namespace strict
mypy509 clean `/tmp/ui043-child-active-current-static.log`. Agent/Coding/Claw wheel-sdist
`/tmp/ui043-child-active-artifacts`, handle20214 exit0; three changed production modules
byte-exact SHA256 `/tmp/ui043-child-active-source.json`. No new physical-terminal claim
for this child-ceiling patch; no downloads/live provider/auth/browser/publishing or
user-service restart, unrelated worktree preserved.

UI-043 remains待实现: durable selection/session restore semantics, active/configured
reporting, applicable child direct-selection UI/headless contract reconciliation and
original session lifecycle/compaction/message/queue host controls still required.
Parent selection inheritance is now implemented, not full UI-043 completion.64 IDs43
complete/6 implement/6 verify/5 external/4 deferred;17 non-deferred unfinished.
UI-041 and frozen complete-root/current-Linux final gates remain unchanged.

## Coding durable active-tool selection and owned disk commit (2026-10-11)

Coding SessionData persists a validated active_tools selection. Missing/null means
configured defaults; [] means explicitly no tools. Saved extension selections are
masked to empty until extension initialization completes, then intersected with the
actual configured inventory. Startup persistence retains pending saved names rather
than saving the temporary safety mask. Missing tools are not synthesized or enabled
by a later registration. New sessions use defaults; restart/switch, branches,
checkpoint navigation and compaction recovery/return heads restore their own choice.
Permission settings are not restored or broadened by tool selection.

Agent.validate_active_tools provides the same dry validation as set_active_tools,
including the producer cleanup lifetime, before any disk write. It is not a lock or
permission grant: the host retains its idle mutation owner through application.
AgentSession.select_active_tools stages session data, saves it atomically, then
publishes the selection. SessionManager's optional before_replace check runs after
serialization/fsync and immediately before replacement, so cancellation, a stale
inventory or withdrawn original host cannot commit an unadmitted selection. Failure
leaves the old runtime and persisted bytes; temporary files are removed. A commit
already admitted is settled and never automatically rolled back or replayed.

Native Coding queues this save through its existing owned command thread, not the
terminal polling thread. RPC keeps its original busy/control owner while awaiting
an offloop disk worker; competitors cannot take over. Callback cleanup and whole-line
command-result delivery still precede application. Cancel/close waits for the actual
worker to settle; capability generation, session/navigation identity, pending approval
and selection revision are rechecked at commit. A separate applied receipt follows
settlement. Cancel after durable commit reports the real applied outcome rather than
inventing rollback; disconnect suppresses delivery, not completed effects.

Tests exercise null/empty/subset restarts, a real extension worker's inventory restore,
startup saves, unavailable names, malformed data, live-producer preflight, disk failure,
branch/checkpoint/new/switch and compaction return-head ownership. RPC disk barriers
cover responsiveness, competitor refusal, cancel, withdrawal, changed inventory,
failure, close and post-commit cancellation. Native UI barriers cover slow saves,
cancellation and failure without blocking terminal polling. Both physical Linux
Coding selection paths remain covered. All fixtures use offline models/private peers.
An initial restore test incorrectly assumed a command-style tool alias; it was corrected
to select the actual registered alias, without weakening the restore assertions
(`/tmp/ui043-durable-restoration.log`:1 failure/13 passes; fixed14 passes3.50s).

UI-043 remains待实现. This implements Coding persistence, not Claw persistence or all
original lifecycle/compaction/message/queue extension APIs. The consolidated remaining
scope above replaces the earlier checkpoint statements that these particular Coding
behaviors had not yet been implemented. No additional requirement ID is counted.

Current integrated evidence:21 test files, **208 passed/no skips143.27s**
`/tmp/ui043-durable-integrated.log`, including physical Linux Coding selectors,
child ceilings, RPC deferred delivery/generation/capability ownership, native naming,
SDK/session staging/navigation and compaction regressions. Root Ruff and all-seven
namespace strict mypy509 clean `/tmp/ui043-durable-current-static.log`. Offline
Agent/Coding wheel and sdist builds `/tmp/ui043-durable-artifacts`; all five changed
production modules byte-exact `/tmp/ui043-durable-source.json`. No dependency downloads,
live provider/auth/browser, publishing or user-service restart. This is scoped current
source evidence, not final frozen root regression or physical power-loss validation
(UI-058).64 IDs43 complete/6 implement/6 verify/5 external/4 deferred;17 non-deferred
unfinished, with UI-043 still one unfinished requirement.

## Claw durable selection, journal recovery and compaction ceiling (2026-10-11)

Claw TranscriptStore stores active-tool selection in a private atomic `.tools.json`
sidecar per session/branch. Missing/null is configured defaults, [] is explicit none;
invalid/oversized/non-file/symlink state refuses rather than silently granting defaults.
AgentSession starts a saved explicit selection under an empty safety mask, initializes
the actual extension inventory, then restores the available selected names. Native
new-session reset, restart/resume, fork/goto/branch and compaction recovery/return
checkpoints preserve their own selection scope. Selected tools still pass existing
Claw configured tool/extension/MCP/planning checks; no permission policy is persisted
or broadened by this setting.

Native reviewed extension selection now awaits an owned offloop save inside the held
group reservation. Owner/session/Agent/revision/capability and producer-idle checks
run before work and immediately before atomic replacement. The session excludes other
SDK input/context changes during the write, and holds child-idle ownership. Failure
retains old disk/runtime selection. Cancel/withdraw/disconnect before commit refuses;
a canceled task waits for the actual disk worker, and an admitted commit publishes its
real runtime selection even if its acknowledgment is canceled or disconnected. Claw
still commits before its final extension command result; it does not adopt Coding
RPC's different callback-result-before-application ordering.

Prepared navigation reads the target selection into its buffered store before creating
its worker. Target/return-branch `.tools.json` files are part of the existing navigation
undo journal and managed-destination validation. An uncommitted process death restores
exact old bytes; a committed marker retains the chosen selection and the return head's
prior choice. Existing crash-boundary tests now cover all13 managed writes rather than
the previous11, and include nondefault selection recovery. The legacy SDK branch
restore also uses the journal for transcript/plan/entries/selection before publishing
its rebuilt runtime; storage failure does not publish the target state.

A separate real regression was found during this audit: `_compaction_tools()` supplied
all configured tools to the silent FlushRunner despite an explicit parent subset.
`/tmp/ui043-claw-flush-selection-red.log` shows2 failures/1 pass: both [] and
[current_time] unexpectedly wrote the owned test target. Filtering the guarded flush
inventory by the parent's active tools fixes both model definitions and execution
lookup while preserving existing permission hooks. Actual flush-loop tests retain a
default-selection write control, and assert no write/definition for excluded tools.
Focused28 passed3.59s `/tmp/ui043-claw-flush-selection-fixed.log`.

Other test coverage: real extension worker restart/lazy restore, null/empty/subset,
malformed/hostile sidecars, SDK navigation rollback, compaction and return scopes,
reader responsiveness and competitor refusal while storage is held, native socket
cancel/withdraw/disconnect, and post-commit cancellation. Initial test failures used
the wrong branch metadata key, omitted the worker cwd, used a nonexistent UserInput
field, assumed cancel was delivered after a fixed sleep, or supplied a transcript
whose only large turn could not be compacted. Tests were corrected to real metadata,
owned workspace, content field, actual cancellation admission and a multi-turn history;
these are not claimed as production bug discoveries. Evidence includes
`/tmp/ui043-claw-durable-initial.log`, `/tmp/ui043-claw-durable-staging.log`,
`/tmp/ui043-claw-durable-owned-ui-fixed.log` and `/tmp/ui043-claw-durable-recovery-final.log`.

UI-043 remains待实现: active/configured reporting and inventory lifecycle persistence,
applicable child direct-selection/headless contracts and remaining original extension
lifecycle/compaction/message/queue APIs require reconciliation/implementation and proof.
This section retires the historical claim that Claw has no durable selection or that
compaction may ignore the parent selection; it does not close the whole requirement.

Final current-source23-file **164 passed/no skips**
`/tmp/ui043-claw-durable-final-integrated.log`, including all four physical Coding/Claw
selector paths, journal/process recovery, SDK/staged navigation, native gateway/tool
selection, compaction flush and captured child ceilings. Earlier pre-flush-fix161 pass
`/tmp/ui043-claw-durable-integrated.log` is not substituted for the final source gate.
Root Ruff/all-seven strict mypy509 clean `/tmp/ui043-claw-durable-current-final-static.log`.
Offline Claw wheel/sdist `/tmp/ui043-claw-durable-final-artifacts`; all six production
modules byte-exact `/tmp/ui043-claw-durable-source.json`. No live provider/auth/browser,
downloads, publication or user-service restart. Scoped current-source validation is not
final frozen root regression or physical power-loss proof (UI-058).64 unique IDs43
complete/6 implement/6 verify/5 external/4 deferred;17 non-deferred unfinished.

## Configured/active inventory and durable retirement (2026-10-11)

Read-only inventory now carries the configured definitions plus active names, explicit
versus default selection mode, and the Agent selection revision. The model tool-API gate
remains separate: a selected tool on a no-tool model is not claimed exposed or permitted.
Capture rejects selection changes during serialization. Selection/mode changes affect
the inventory fingerprint, pagination stale checks and worker cache identity even when
configured schemas are unchanged. Pages retain configured definitions, report selected
names on that page and a matching-query selected total. Full schemas and configured
order are preserved; this is inspection, not execution approval.

Legacy envelopes remain valid without selection metadata, and display “unknown (legacy
snapshot)” rather than inventing an all-active/default state. Claw advertises
`tool_inventory_selection:1`; new native clients request `include_selection:true` on
an already opted-in tool inventory read. Requests without that boolean still receive
the exact legacy envelope; malformed values refuse. The request's choice is captured
per read, never borrowed from another request. Native Coding/Claw `/tools` renders
configured/selected totals and selected/excluded/unknown row labels, while preserving
schema search, selection/export, explicit refresh and parent drafts. Headless Coding
query output filters selection names consistently with the requested definitions.
Real extension command/tool/event/context/pre/post inventory keeps its original owner,
including child/headless profiles, and never becomes host mutation authority.

A second real retirement bug was reproduced on both hosts:
`/tmp/ui043-retirement-red.log`2 failed. After a successful real extension unload, the
live mask was empty but the stored selection still contained the retired alias; a
fresh worker could therefore restore the older choice. Both AgentSessions now await
strict-subset-only persistence after extension inventory sync at command, initialization,
input/context/pre/post and turn-completion boundaries. Missing saved names are also
retired durably after startup inventory initialization. Coding prepared navigation
clips target choices to the staged configured inventory before its atomic commit;
Claw already commits its staged Agent selection through the navigation journal.
MCP disconnect/refresh and credential-forget paths persist resulting narrowing before
acknowledging successful host control. Defaults/new registrations remain unchanged;
this path cannot persist an expansion or bypass explicit reviewed selection.

Retirement storage runs in an owned disk worker. Parent input/context mutation is
excluded while it settles; the pre-replacement check binds the same owner and selection
revision. Restriction already applied in memory is not rolled back on a failed write.
Failure surfaces instead of a success receipt; initialization retries the narrowing
before a later turn. Cancellation settles admitted restriction rather than restoring
retired tools. This is acknowledged lifecycle consistency, not a guarantee about an
unacknowledged crash before persistence or arbitrary direct Agent setter use. The latter
remains host-owned in the shared Agent contract; physical storage-fault evidence stays
UI-058, and no OS sandbox is claimed.

Actual workers verify unload/reload/restart cannot re-enable a retired alias; a fixture
tool disposes itself during actual model execution, and the next model step sees neither
its definition nor a broader stored mask. Both hosts' held-disk tests cover reader
responsiveness, ownership, cancellation, failure and subsequent successful narrowing.
Real MCP stdio peers reconnect the same alias without reselecting it or calling a remote
tool. Prepared target navigation verifies only available names are stored. Independent
inventory tests cover explicit-all/default distinction, no-tool models, malformed and
partial metadata, captured-selection races, stale pages, exact legacy Claw replies,
callback cache behavior, and native/schema/export/no-model invariants. Four physical
Coding/Claw selector paths include browsing empty selection and excluded filesystem
metadata after real review, with parent/cursor/main-buffer restoration.

Focused evidence:72 inventory/browser/worker checks
`/tmp/ui043-inventory-selection-expanded.log`;14 physical/browser checks
`/tmp/ui043-inventory-physical.log`;48 retirement/staged navigation checks
`/tmp/ui043-retirement-staging.log`;32 retirement/MCP/native checks
`/tmp/ui043-retirement-mcp-native.log`. Initial syntax/import/line diagnostics were fixed,
not treated as production discoveries. UI-043 still requires the remaining public host
API and applicable invocation-scope reconciliation listed at the top; these inventory
and retirement gaps are no longer counted as absent implementations or as extra IDs.

Final current-source24-file **262 passed/no skips179.12s**
`/tmp/ui043-inventory-retirement-integrated.log`, including four physical selectors/
configured-active browser paths, actual worker callbacks/cache/child/headless inventory,
retirement disk barriers, MCP peers, both-host prepared navigation and journal recovery.
Root Ruff/all-seven strict mypy509 clean
`/tmp/ui043-inventory-retirement-current-static.log`. Offline Agent/TUI/Coding/Claw
wheel-sdist `/tmp/ui043-inventory-retirement-artifacts`; all ten changed production
modules byte-exact `/tmp/ui043-inventory-retirement-source.json`. No live provider/auth/
browser, dependency downloads, publishing or user-service restart. This scoped gate is
not the final frozen root regression.64 stable IDs43 complete/6 implement/6 verify/
5 external/4 deferred;17 non-deferred unfinished. UI-043 remains待实现 for its other
original host API contracts, not for the inventory/retirement pieces closed here.

## Public export reconciliation and owned RPC compaction foundation (2026-10-11)

Current local public methods were inspected, not inferred from documentation headings
(`/tmp/ui043-compaction-request-exports.json`, refreshed source hash and AST-derived method list;
previous prerequisite capture remains `/tmp/ui043-host-exports.json`):

| Original behavior family within UI-043 | Existing actual public surface | Remaining distinction |
| --- | --- | --- |
| Session metadata/name/catalog/browser/lifecycle navigation | `get_session`, `get_session_name`, `set_session_name`, `list_sessions`, `get_session_tree`, `open_sessions`, `navigate_session` with new/resume/fork/branch/goto | Do not duplicate these under guessed vendor-specific names. Applicable invocation scope still requires reconciliation; a child cannot borrow the parent's mutation owner. |
| Active/configured tools | `get_tool_inventory`, `get_active_tools`, `set_active_tools`; reviewed native/RPC/socket selection, persisted retirement and child ceiling | Read-only invocation inventory is already available in child/headless callbacks. It is not authority for direct parent selection from a child callback. |
| Compaction integration | `on` supports session_before_compact/session_compact; `request_compaction` now queues reviewed parent-command compaction on both native hosts and Coding RPC | True means queued, not completed; host-native receipts establish the outcome. Tool/hook/headless calls cannot borrow that command owner. Remaining applicable invocation-scope reconciliation is not waived. |
| Message and queue control | `append_entry` stores extension metadata; `notify`/status/widget are UI contributions; `on_input` transforms admitted input | None is a host-originated message/steer/follow-up request. Map the remaining message/queue contracts to canonical Agent input ownership, review, delivery and no-replay behavior, not a second tool loop. |

The attempted primary Pi document retrieval in this checkpoint did not return usable
content (web requests empty; bounded curl failed with exit35/connection reset,
`/tmp/ui043-pi-reference.headers`). It does not establish new upstream API signatures,
rename the reference project, or update the historical reference baseline. All original
applicable scope remains required; the concrete local missing public compaction request
and message/queue controls are not waived because a reference fetch failed.

### Real reader blockage found and fixed

A canonical Coding compaction was suspended inside its actual summary stream. The RPC
loop could not process a following status command until summarization was released:
`/tmp/ui043-rpc-compaction-reader-red.log`1 failure. The legacy `compact` branch directly
awaited `session.compact()` in the JSON-lines reader. This also prevented that reader
from admitting abort/quit during the wait.

`RpcNavigation` now owns canonical compaction as an async `compact` action. It uses the
same busy owner as session navigation, so existing prompt/extension/child/terminal/
parent-draft/source interlocks apply. The SDK still owns summarization, recovery
checkpoint persistence and cancellation cleanup; no second model loop is introduced.
Both the legacy `compact` command and new `session_control` action use this task.
Readiness advertises `session_compaction:1`; status reports `compaction_running`.
Instructions use the existing bounded16KiB summary validator. Invalid current owner,
foreign navigation targets, malformed instructions and reused request IDs refuse.

Owned session-control requests use expected_session/session_revision/request_id and
receive session_result. Legacy commands retain ok/error action=compact receipts and
can optionally supply request_id/instructions. A legacy caller without a request ID
can abort its active compaction; an owned caller can cancel its specific session-control
ID. Foreign cancellation cannot affect it. Cancellation before admission reports no
effects; during a summary it closes the real iterator. Abort/quit/EOF leave no running
summary producer. Successful receipts refresh the conversation revision. For admitted
storage, the SDK's shielded commit must settle before this RPC owner is released: a
canceled receipt reports state_changed/receipt_canceled if history actually committed,
never an invented rollback. Failed storage retains original history. This is a native
user/RPC control capability, not a new extension or child mutation grant.

New tests exercise actual session summarization and JSONL status/mutation interlocks,
abort/quit/EOF/scoped cancellation, recovery persistence, unadmitted cancellation,
stale/invalid owners and instructions, and held before-write/after-write/failing disk
commits. Four real CLI subprocess paths cover success, abort, quit and EOF using offline
provider fixtures; held summaries prove status remains readable and actual iterator
cleanup occurs. Initial successful-save equality assertion was corrected to compare
canonical message serialization (user strings versus restored TextContent tuples),
not to weaken persisted-content checks. Focused21 passed5.39s
`/tmp/ui043-rpc-compaction-cli.log`.

UI-043 stays待实现. The explicit extension compaction request and message/queue APIs in
the matrix remain absent and required. The RPC reader defect is closed evidence and
foundation work, not a replacement success criterion for the original broader goal.

Final12-file **191 passed/no skips123.27s** `/tmp/ui043-rpc-compaction-integrated.log`,
including four current-Linux physical native compaction paths and four actual RPC CLI
subprocess paths, canonical cancellation/storage recovery and related RPC ownership/
draft/editor/navigation regressions. Root Ruff/all-seven strict mypy509 clean
`/tmp/ui043-rpc-compaction-current-static.log`; offline Coding wheel/sdist
`/tmp/ui043-rpc-compaction-artifacts`, both changed RPC modules byte-exact
`/tmp/ui043-rpc-compaction-source.json`. No live provider/auth/browser execution,
dependency downloads, publishing or user-service restart. Primary document fetch
failure is not upgraded to reference proof.64 IDs43 complete/6 implement/6 verify/
5 external/4 deferred;17 non-deferred unfinished. Final frozen root/Linux gates remain.

## Reviewed extension compaction requests on Coding and Claw (2026-10-11)

`ExtensionAPI.request_compaction(instructions='', timeout=300)` now provides the
previously absent parent-command request. It validates canonical bounded16KiB summary
instructions and asks the original host to review them. True means **queued**, not
completed; the extension callback must finish successfully before native compaction
may start. Existing session_compact lifecycle hooks remain success notifications,
not a second model loop or a blanket completion receipt for every request.

The shared whole-target boolean form defaults to false, requires painted review and
warns about the selected model, replacement of older history and permitted memory-flush
tools. Instructions cannot smuggle another session/branch or a navigation target.
Tool/hook/headless calls cannot borrow an interactive command owner; real worker/tool
and headless denial tests verify this even when a UI handler exists. Broader applicable
invocation-scope reconciliation remains required, not silently waived by this parent
integration. This is a local host contract, not a claim of latest Pi wire/API compatibility.

Coding native review uses the existing accepted-broker event and original command/view
owner, then queues the canonical compaction on its owned command worker. The request
uses a typed internal target, not a slash-command string: an instruction literally
`cancel` stays summary text. Failure/cancel/expired owner does not start summarization.
The unsent composer stays owned and untouched. Compaction cannot combine with another
accepted navigation/selection/browser action in the same command.

Coding RPC independently negotiates `extension_compaction` (default0), preserving other
capability grants. Accepted replies are bound to the original command/session/revision
and capability generation. Callback cleanup and actual complete command-result delivery
precede native dispatch through RpcNavigation's existing compact action. The queued
receipt names the native request ID; its session_result establishes the actual outcome.
Failed delivery, callback failure, withdrawal/re-enable and foreign replies do not run
compaction. Withdrawal also cancels this extension's already-dispatched native request,
without canceling an unrelated later/manual compaction.

Claw negotiates the extension capability separately from native session_compaction.
Gateway publication includes its original capability generation, and the held command
reservation revalidates actual broker acceptance before returning compaction_queued.
Only that successful result allows the frontend to request native compaction. The native
request carries the reviewed generation, so withdrawal/re-enable between callback result
and native admission still refuses it. Source/session/Agent/child-idle checks remain;
connection loss cannot replay an old request. Withdrawal cancels only extension-origin
compactions on that connection. Normal manual compaction is not coupled to that grant.
Existing canonical storage/cancellation/permission/tool-selection behavior remains the
execution path for both clients.

### Additional cancellation boundary discovered during verification

A real same-buffer native compact + extension-withdrawal request reproduced a lost
receipt before coroutine admission (`/tmp/ui043-request-compaction-admission-red.log`,
1 failure). Canceling a task before its body ran also skipped its result publication.
Claw now marks such work canceled but lets the owned job publish one canceled result
and release its reservation without calling the model. The marker survives immediate
re-enable and repeated pre-admission cancellation. Native capability withdrawal and
explicit cancel use the same path. Started operations retain the original repeated
explicit-cancel behavior, including repeated cancellation while an admitted disk commit
settles; the existing strict cancellation tests were retained, not weakened.

Initial broad278-pass evidence preceded this additional bug and is not final-source
proof. An intermediate refinement produced3 failures/280 passes: two existing tests
required repeated explicit cancellation and one required the precise before-summary
receipt. The final correction preserves both contracts. Current cancellation/native
regressions63 passed34.70s `/tmp/ui043-request-compaction-final-cancel.log`. Other initial
harness corrections waited for actual terminal readiness, restored the seeded Claw
history before comparing timestamps, and observed the actual extension result before
compaction rebuilt the UI history. None is claimed as a separate production defect.

Tests cover both real native clients, worker and actual CLI RPC review/delivery, callback
failure, denial, unknown/withdrawn grants, accepted-reply off/on races, stale native
dispatch, summary cancellation, unrelated native work, compound-request refusal,
literal-cancel instructions, protocol validation and no parent authority in tool or
headless callbacks. Four physical Linux approve/deny paths check full review/resize,
no model before approval, actual compaction/checkpoint persistence and parent cursor,
main-buffer and termios restoration. No live provider/auth/browser, dependency download,
publication or user-service restart was used.

The current export matrix above supersedes the older “no extension compaction request”
statement. UI-043 remains待实现 for remaining message/queue controls and applicable
invocation-scope reconciliation; no new requirement ID is counted. Final frozen root,
current-Linux safety/transport/terminal/build/example gates remain required.

Final current-source21-file **283 passed/no skips219.29s**
`/tmp/ui043-request-compaction-current-final.log`, including four physical native
approve/deny paths, actual RPC CLI approval/delivery/results, worker scope denial,
generation/late-admission cancellation, native compaction and existing name/navigation/
selection regressions. Root Ruff/all-seven strict mypy509 clean
`/tmp/ui043-request-compaction-final-static2.log`. Offline all-four-package wheel/sdist
`/tmp/ui043-request-compaction-final-artifacts`; nine final production modules byte-exact
`/tmp/ui043-request-compaction-current-source.json`. Earlier intermediate gates are not
substituted for this source.64 IDs43 complete/6 implement/6 verify/5 external/4 deferred;
17 non-deferred unfinished. This is not the final frozen complete-root goal regression.

## Authorized parallel completion checkpoint — 2026-10-11 15:22 +08

User requested a three-hour completion target and explicitly authorized child agents.
Target window: 15:20:43–18:20:43 Asia/Shanghai; aim for implementation handoff by
17:10 and reserve remaining time for integration and a frozen full regression.
This is a scheduling target, not permission to waive requirements or claim missing
external environments passed. Persistent goal completion remains governed by the
64-ID authoritative ledger:43 complete/6 implement/6 verify/5 external/4 deferred,
17 non-deferred unfinished at this checkpoint. Only UI-060–063 native non-Linux
verification is deferred. All other completion conditions remain unchanged.

Disjoint work ownership: UI-036 raw input, UI-037 renderer/container, UI-044 provider
registration/auth, UI-048 MCP negotiation, UI-041/053 verification/reference review,
and UI-043 Claw message delivery are delegated. Main owns shared message API, Coding
native/RPC delivery, integration and central ledger. Delegates do not edit the central
ledger; scoped test evidence is integrated before status changes. Final full-root
regression must wait for a coordinated source freeze. No service restarts, publication,
new dependencies, unrelated memory/scheduler changes or interruption of progressing
tests. Prior compaction283-pass evidence predates current message WIP and is not
claimed as a current-source gate.

## Reviewed idle command messages — implementation checkpoint 2026-10-11

Shared `request_message(text)` now validates literal nonempty bounded text and asks
for a false-default whole-message review. Host-generated extension provenance is
part of the reviewed input. A positive receipt means queued, never completed or tool
permission. Parent command success/cleanup gates delivery, with actual complete command
result delivery additionally required in Coding RPC and Claw socket. Coding uses its
canonical native turn worker / RPC `_handle_prompt`; Claw uses its existing gateway
message admission with a one-use connection/session/source grant. Literal `/quit` is
model input, not a slash command. Neither draft text nor composer/uploaded images are
implicitly consumed. Tool policy stays unchanged. Stale/foreign/withdrawn/canceled or
failed callback/result delivery cannot dispatch; EOF/abort settles the owned Coding
RPC producer, including pre-turn work. Unsupported callers remain fail-closed.

Coding focused evidence: `/tmp/ui043-message-expanded.log`21 pass; later
`/tmp/ui043-message-final-focused.log`18 pass includes actual CLI permission denial,
abort/EOF/withdrawal/queued image retention, before-admission cancellation receipt,
and both physical Coding review answers. Shared/native images/drafts tests are in
`test_extension_message_request.py`, `test_extension_message_ui.py`; actual RPC tests
in `test_rpc_extension_message.py`, physical Coding in `test_extension_message_terminal.py`.
The initial physical persistence assertion used an invalid filename/session-ID mapping;
it was corrected to the existing SessionManager listing API, not a production fix.

Claw scoped current evidence: `/tmp/ui043-claw-message-scoped-final.log`123 pass,
no skips117.70s, including private physical Claw approve/deny and real held-result
transport/one-use grant/lifecycle/draft/permission paths. Claw's independent message
capability is not a grant to send arbitrary client-supplied text/images with that token.

An intermediate main integration run `/tmp/ui043-message-integrated.log` recorded
84 pass/11 fail while the parallel editor-write worker had installed a native binder
before its matching ChildExtensions method existed. The method is now present; that
run is NOT green and must be rerun after source integration. None of these scoped
results substitute for final frozen UI-049/055/056/064 gates.

**UI-043 remains待实现.** Verified Pi0.73.1 source establishes broader active-turn
steer/follow-up, custom next-turn records/typed image input and tool/hook invocation
contracts; idle parent-command text is only one slice. Work on reviewed original-run
queue proposals uses the shared original-invocation completion lease, not a second
model loop or another client's draft. Child/headless/compaction applicability and
own-queue controls must still be reconciled. At this checkpoint UI-037's finite
renderer contract is complete;64 IDs44 complete/5 implement/6 verify/5 external/
4 deferred,16 non-deferred unfinished. UI-044 private env/resolver auth evidence is
retained but its confirmed Pi OAuth lifecycle contract is not silently closed.

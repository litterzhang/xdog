# UI-048: finite MCP client-capability reconciliation

Current checkpoint 2026-10-11. **UI-048 remains 待实现**. Explicit refusal and
transport safety evidence below do not implement reverse capabilities or waive
original applicable feature gaps. This is not complete MCP conformance.

## Primary local baseline actually inspected

Already installed independent `@modelcontextprotocol/core`2.3.1 in
`/tmp/xdog-mcp-pass68.lw1gcs/node_modules/@modelcontextprotocol/core`:
`dist/auth-BNDyLTqp.mjs` ClientCapabilitiesSchema lines301–310 names roots,
sampling(context/tools), elicitation, tasks, experimental, extensions. Task capability
comments mark that wire vocabulary deprecated2025-11-25 with no SDK runtime.
This is a pinned locally inspected SDK source, not newly fetched latest documentation.
Web retrieval returned no usable content; no internet currency claim.

## Named current implementation vs remaining scope

| Family | Current contract | Required next action under UI-048 |
| --- | --- | --- |
| tools/resources/prompts/completion | Client-initiated catalogs, calls, inert preview/export and explicit editor insertion implemented; ordinary permission/ownership gate remains | Existing UI-025–029 evidence, not new reverse capability counts |
| subscriptions/progress/log telemetry | Owned current subscriptions and legacy notices; telemetry never model input | Existing watcher tests retained, not root/sampling/elicitation support |
| roots | Explicit server-configured roots metadata, legacy stdio/HTTP reverse handler; no cwd inference, no file access, modern wire omitted | Static configured disclosure/connection withdrawal verified below; dynamic reviewed host root selection/listChanged and modern applicable counterpart remain to reconcile/implement |
| sampling context/tools | No reverse model handler; model/tool loop never borrowed from unsolicited request | Implement applicable explicit reviewed model admission with bounds, cancellation, owner/permission ceilings; no hidden history/tool grants |
| elicitation form/URL | Existing extension dialogs/OAuth are not reverse MCP elicitation handlers | Implement applicable reviewed form/URL request contract, independent opt-in, lease/cancel/host focus safety; no unsolicited browser/login |
| tasks | No client task registry; pinned SDK says deprecated wire vocabulary/no runtime | Reconcile named applicable legacy task support with original goal; do not infer support from child jobs or SDK schemas |
| experimental/extensions | No registered protocol extensions | Unknown names remain unsupported; add only named applicable registered contracts, not unlimited arbitrary plugin promises |

No extra requirement IDs. UI-048 is one aggregate requirement with this finite
subordinate matrix. Unsupported families still require original applicability and
implementation reconciliation, not automatic closure because advertised() returns{}.

## Implemented this checkpoint: shared honest inventory and safe refusal

`client_capabilities.py` centralizes a fresh empty wire snapshot and a named public
support inventory. Modern request metadata, legacy initialize and subscription
frames consistently use it; manager status surfaces families as unsupported rather
than borrowing host extensions/children/OAuth as capabilities.

Legacy reverse requests use one strict responder: integer-safe/bounded nonempty string
identity, shape/method/params validation, ping only, fixed -32601 unsupported replies.
Invalid bool/null/oversized identities are not reflected. Modern unrelated server
requests remain refused by transport. None of these paths submits a model, exposes
roots, opens a UI/browser, changes policy or replays a request.

Current actual legacy stdio peer proves all four named reverse refusals and ping
while original tools/list correlation and subsequent ping remain responsive; cleanup
reaps the owned process. HTTP owned MockTransport proves exact response identity,
unsupported family/error and modern no-unsolicited boundary. HTTP contract is not
represented as a real server reverse stream implementation.

## Evidence

Current integrated gate with explicit installed SDK root:
`/tmp/ui048-current-integrated.log` **78 passed,no skips,34.78s** (handle94183 exit0).
Includes client contract, stdio/HTTP real peers, independent SDK2.3.1 stdio/HTTP,
actual Coding session/browser, actual Claw gateway, both resource-watch UIs.
No downloads/model/live-provider/auth calls or user-service restart.
Root Ruff and seven-namespace strict mypy489 clean,
`/tmp/ui048-current-static.log`. Current agent wheel/sdist built
`/tmp/ui048-artifacts`; four production modules byte compared,
`/tmp/ui048-source-receipt.json`. Whitespace check clean.

UI-048 remains unfinished for roots/sampling/elicitation and applicability of task
contracts. Current count unchanged:64 requirements,42 complete,7 implement,6 verify,
5 external,4 deferred;18 non-deferred unfinished. Final frozen root and Linux safety
requirements remain independent. Do not rerun this inventory-only work as a substitute
for the concrete remaining implementations.


## UI-048 roots implementation checkpoint (2026-10-11)

This is concrete implementation beyond the previous refusal-only audit, not closing
UI-048 or treating static metadata as all reverse capability support.

Server configuration may explicitly specify a bounded list:

```json
{"command":"trusted-peer","roots":[{"uri":"file:///explicit/project","name":"Explicit project"}]}
```

Configuration plus explicit connect is consent to disclose those exact URI/name
pairs to that configured legacy peer. Declaring config alone launches nothing.
No automatic cwd/home exposure, resolving paths, filesystem read, resource read,
tool invocation, model turn or permission grant. This is metadata, not confinement.
Validation refuses remote file authorities, non-file schemes, traversal, duplicate
decoded paths, malformed percent escapes/invalid UTF-8 and control characters.

RootCapability holds connection-local immutable roots. Only legacy initialize
advertises roots/listChanged:false; legacy roots/list returns a fresh exact list.
Current stateless discovery/requests do not advertise reverse roots without an
applicable reverse channel; no unrelated current unsolicited request admission.
Disconnect, failed negotiation/reader loss withdraws root disclosure. Reusing a
transport resets modern/version/root activation before discovery, preventing an old
legacy connection from accidentally granting roots to a modern successor.

Current tests: actual owned legacy stdio and loopback HTTP SSE reverse request,
response POST bound to original endpoint, opt-in/absent refusal, duplicate/invalid
metadata, strict reverse identity, disconnect withdrawal/reused transport. Actual
Coding session and Claw gateway configured connect/status/disconnect exercise both
consent states without model/history/tool calls or inherited cwd disclosure.

Final integrated **103 passed,no skips,36.20s**,
`/tmp/ui048-roots-current-integrated.log`, handle71131 exit0. Includes roots and
client contract, real transport peers, independently installed SDK2.3.1, both-host
session/gateway/browser/resource watch groups. Ruff/seven-namespace strict mypy489
clean `/tmp/ui048-roots-final-static.log`; five production modules current agent
wheel/sdist byte checked `/tmp/ui048-roots-artifacts`,
`/tmp/ui048-roots-source.json`. No downloads/provider calls/services restarted.

Earlier host checkpoint8 failed/13 passed (`/tmp/ui048-roots-hosts.log`) was an
owned peer script fixture import accidentally removed together with the test's
unused import. Restore only the embedded Path import; original assertions retained.
Corrected real-host/roots21 passed (`/tmp/ui048-roots-hosts-current.log`); final
integrated evidence above is passing, red not erased/represented as passing.

Remaining within UI-048: applicable dynamic reviewed root selection/withdrawal
notifications and modern counterpart, sampling, elicitation form/URL and task
applicability/implementation. Static configured roots do not replace reviewed
interactive scope. No new IDs, scope shrink or broad conformance assertion.
64 unique requirements:42 complete,7 implement,6 verify,5 external,4 deferred;
18 non-deferred unfinished. Final Linux/frozen complete-root gates intact.


## Dynamic roots manager protocol checkpoint (2026-10-11)

`McpManager.manage_roots` now provides prepare/commit/cancel with connection identity,
view lease, exact revision, five-minute receipt expiry and one-use commit. Prepared
selection must be a subset of immutable configured URI/name disclosure ceiling;
empty selection revokes all roots, re-enabling configured entries requires another
review. Preparation alone changes nothing. SDK protocol returns the full proposed
list and disclosure notice for host painting; **native interactive painting/wiring
is still pending**, not claimed by these manager tests.

Legacy capability now advertises listChanged:true and commits notify
`notifications/roots/list_changed`. Empty active selection returns roots:[], not a
false unsupported response. Local publication precedes notification: cancellation/
failure has uncertain remote notice effects, no rollback/replay. A publishing lease
blocks concurrent preparation/commit until actual transport settlement. Disconnect
clears prepared receipts; successor connections cannot use old receipts. No roots
outside configured ceiling, model/history/file/tool operations or changed permissions.

Real stdio peer verifies review/no implicit change, empty-list response and exactly
one notification, restored selection, foreign lease/canceled/expired/revoked/reconnect
refusal. Real loopback HTTP SSE peer verifies commit notification and subsequent
empty-list reverse response on the same endpoint. Held-notification cancellation
settles the actual bridge coroutine, rejects concurrent reviews and old receipt reuse.
Existing two-host static integration remains in the gate, but is not dynamic UI proof.

Current integrated **110 passed,no skips,36.79s**,
`/tmp/ui048-dynamic-roots-integrated.log`, handle17929 exit0; Ruff/seven-namespace
strict mypy489 clean `/tmp/ui048-dynamic-roots-final-static.log`, handle78253 exit0.
Current agent wheel/sdist `/tmp/ui048-dynamic-artifacts`, handle89732 exit0, two
changed modules byte-compared `/tmp/ui048-dynamic-source.json`. No new requirement
IDs or scope waiver. UI-048 remains待实现; next roots action is actual reviewed native
host/Claw transport UI wiring, then remaining sampling/elicitation/task contracts.
Current64 IDs:42 complete,7 implement,6 verify,5 external,4 deferred;18 unfinished.


## Actual Coding dynamic root review UI checkpoint (2026-10-11)

Shared McpPanel now offers `o roots`: editable JSON within configured ceiling;
Enter prepares a full typed disclosure review, never direct admission. The dedicated
McpRootsReview owns an immutable snapshot, pages every notice/URI, permits only
focused distinct press confirmation after all rows painted at current width/budget.
Focus loss/resize invalidates the paint proof; paste cannot mutate the underlying
form while reviewing; repeat/release cannot confirm. Empty selection explicitly
says withdrawal. The receipt is sent only once. Escape/back/close cancels preparation;
Coding auth-view retirement also detaches root receipts on the actual manager loop.

Actual Coding session/UI sends prepare/commit through an idle-owned session boundary,
with parent/child idle guard, original manager/session/view lease and cancellation.
Real legacy worker peer observes one listChanged after fully reviewed empty selection;
parent editable draft/history/model and permission remain unchanged. Close retires
prepared receipts, and old receipt cannot commit even if tested with a fresh live guard.
These tests use actual UI class/render paths, not a physical terminal capture.

Claw's shared UI explicitly refuses roots operations as unavailable and sends no
request: it must not fall through to MCP prompt/context retrieval. This is a safe
intermediate state, **not Claw dynamic capability completion**. Next critical work is
independently negotiated Claw root-control transport/owner job wiring and both-host
physical terminal verification, then applicable modern/sampling/elicitation/tasks.

Current integrated11-file group **115 passed,no skips,32.14s**,
`/tmp/ui048-roots-ui-integrated.log`, handle77507 exit0. Root Ruff/strict seven-namespace
mypy490 clean `/tmp/ui048-roots-ui-complete-static.log`, handle25826 exit0.
Current agent/tui/coding/claw wheels/sdists built `/tmp/ui048-roots-ui-artifacts`,
handle42563 exit0; six changed modules exact bytes `/tmp/ui048-roots-ui-source.json`.
Initial actual Coding test exposed missing roots form in input/render dispatch,
1failed/43passed `/tmp/ui048-roots-ui-current.log`; dispatch fixed, not assertions
removed. Initial mypy Optional-manager issues fixed with explicit unavailable guard.
No dependency download/model/provider/auth network/user-service restart or publish.

UI-048 remains待实现, no extra ID/count and no completion scope waiver.
64 IDs:42 complete,7 implement,6 verify,5 external,4 deferred;18 unfinished.


## Claw dynamic roots independent transport/UI and physical hosts (2026-10-11)

Claw now explicitly negotiates `mcp_roots:1` in its owned hello/pong; bool/noninteger
values do not grant capability. `mcp_roots_control` prepare/commit/cancel/detach
has bounded request identities, non-reusable IDs, connection-local view tokens,
group/session/source-revision and actual manager identity, separate host/internal
review leases, bounded views/jobs and idle group reservation. Remote MCP roots
metadata never borrows OAuth authorization or tool permission. Old/unnegotiated
viewers cannot request this operation and UI never downgrades to prompt/context.

Shared painted roots review now sends through actual Claw socket control jobs.
Close/cancel revokes immediately; jobs await actual manager coroutine cleanup.
Capabilities withdrawn via ping revoke all prepared views; disconnect/new connection
clears root request tracking and paint evidence. Successor/foreign connection cannot
commit original receipts. Notification-inflight cancellation preserves the already
published local root selection, does not replay/rollback, keeps reader ping responsive
and releases group reservation only after actual job completion.

Actual private gateway/socket tests cover absent negotiation, foreign connection,
withdrawal, disconnect, reused ID, fully painted TUI commit/parent draft preservation
and held notification cancel/reader responsiveness. Physical private Linux tmux runs
both shipped hosts: configured peer, root form, full empty-disclosure review after
resize, one listChanged notification, no tools/resource reads, normal parent editing
and main-buffer/termios restoration. Tests wait for panel close before typing the
successor parent draft; earlier physical2 failures were missing that asynchronous
focus checkpoint, not weakened production assertions (red retained at
`/tmp/ui048-claw-roots-physical.log`). Corrected physical/host13 passed4.93s;
final broader integrated gate is the authoritative current evidence below.

**122 passed,no skips,36.90s**, `/tmp/ui048-claw-roots-integrated.log`, handle20044
exit0; root Ruff/seven-namespace strict mypy490 clean
`/tmp/ui048-claw-roots-complete-static.log`, handle11621 exit0. Current Claw wheel/
sdist `/tmp/ui048-claw-roots-artifacts`, handle44841 exit0, both changed production
modules byte-compared `/tmp/ui048-claw-roots-source.json`. No user service restart,
publishing, dependency download or live provider/model call.

Named legacy roots static/dynamic manager and actual two-host review/physical
acceptance now has evidence. UI-048 remains待实现 for applicable modern reverse
counterpart, sampling/elicitation form/URL and task applicability/implementation.
Do not count these subordinate checkpoints as completed requirement IDs.
64 total42 complete/7 implement/6 verify/5 external/4 deferred;18 non-deferred
unfinished. Final frozen root and Linux all-host safety remain independent gates.


## Roots review hardening checkpoint (2026-10-11)

Reviewed the new actual roots surfaces for ownership/data/cleanup edge cases before
continuing the remaining reverse APIs. Root review's public inspection now returns
an isolated copy of its private bounded snapshot, so external mutation cannot change
receipt or disclosure covered by prior paint proof. Notice/server/root text bounds
and unsafe controls are refused before rendering. Long Unicode/emoji URI tests prove
all wrapped rows and suffix are available, cannot confirm before every row is seen,
and remain display-cell bounded. Existing focus/resize/press and full-review tests
remain intact.

Claw connection's256 non-reusable admission-ID budget previously also blocked cancel/
detach once full. Deallocation now bypasses ONLY that admission-ID capacity/reuse
check while still validating request/view/group and original connection binding.
It does not permit a new prepare/commit or replay. Actual socket test exhausts255
extra failed admissions, verifies the next prepare is denied, and proves cancel with
an already-used admission ID can still retire the original attached view. No unrelated
processes/services changed; cancellation is not a new privilege.

Current integrated **134 passed,no skips,37.13s**,
`/tmp/ui048-roots-hardened-integrated.log`, handle73279 exit0; root Ruff and strict
seven-namespace mypy490 clean `/tmp/ui048-roots-hardened-static.log`, handle43074
exit0. Current tui/claw wheel/sdist `/tmp/ui048-roots-hardened-artifacts`, handle78948
exit0; two changed modules byte verified `/tmp/ui048-roots-hardened-source.json`.
These are named roots hardening checks, not completion of sampling/elicitation/tasks
or final whole-goal safety. UI-048 remains待实现.64 stable IDs42 complete/7 implement/
6 verify/5 external/4 deferred;18 non-deferred unfinished. Next implementation work
must address the remaining named capability matrix, not repeat roots-only checks.


## Sampling reverse-request transport foundation (2026-10-11)

Started remaining sampling implementation instead of another roots-only checkpoint.
New ReverseRequests dispatcher integrates actual legacy stdio reader and HTTP stream
router: host handler must be explicitly installed; default clients still advertise no
sampling, and current stateless transports do not advertise reverse capabilities.
An installed legacy handler receives a bounded copied JSON request in a separate
owned async job, up to4 simultaneous jobs/256 non-reusable IDs. Pending human/model
work does not block request correlation or control reading. No result cache/replay.

Remote legacy notifications/cancelled addresses exact original reverse ID; bool IDs
are not integer aliases. Withdrawal/close revokes delivery before cancel and awaits
callback finally. Replacing handler prevents stale result delivery. Fixed refusal/
failure/timeout messages do not echo private callback diagnostics. Callback result
size/JSON validated; all actual host validation/model/review remains required.
The transport foundation **does not validate the full sampling schema or supply
human approval/model execution** and is not presented as completed sampling support.
No ordinary manager/host registers this handler yet.

Actual owned stdio peer requests sampling while its installed test handler is held;
tools/list and ping remain responsive, disconnect settles callback and reaps peer.
Owned HTTP MockTransport proves original correlated result continues while reverse
job waits and later sends exact result ID. Deterministic capacity/dedup/exact cancel/
failure/timeout/handler replacement/cleanup tests cover the dispatcher. Installed
SDK/current transports and both-host roots UI/physical regression remain in gate.

Final current integrated **114 passed,no skips,33.61s**,
`/tmp/ui048-reverse-current-integrated-final.log`, handle38807 exit0. Root Ruff/
strict seven-namespace mypy491 clean `/tmp/ui048-reverse-complete-static.log`;
current agent wheel/sdist `/tmp/ui048-reverse-artifacts`, three production modules
byte verified `/tmp/ui048-reverse-source.json`. Earlier type narrowing was corrected,
then actual-source group rerun; no observation timeout counted as test termination.

UI-048 remains待实现: next bounded work is sampling schema/request/result review,
actual parent-owned model admission/permission/no-history-leak routing, independent
native host/Claw controls and physical cancellation, then elicitation/tasks/modern
applicability. No extra IDs or scope reductions.64 IDs42 complete/7 implement/
6 verify/5 external/4 deferred;18 non-deferred unfinished. Final Linux/root intact.


## Sampling request/result validation and immutable authority (2026-10-11)

Current `sampling.py` adds bounded copied request/review/result contracts. Requests
preserve exact messages (text/image/audio/tool-use/tool-result), explicit system
prompt, context preference, model hints/priorities, maxTokens/temperature/stops,
opaque metadata/task retention and proposed tool schemas/annotations. The codec does
not grant any context/tools/model authority; `allServers` is review data, never hidden
parent-history sharing. Unknown top-level fields/content are refused, not silently
coerced; binary is validated inert data, no URI/file/playback. Request/review digest
binds the complete finite JSON. Full native sampling human input/result review and
actual selected-adapter admission remain required, not implemented by this codec.

Reverse dispatcher runs request/result validation in awaited owned worker threads;
cancellation retains capacity until real worker exit. Host callback receives its own
copy, not the canonical request used for result checks, so callback mutation cannot
add a tool or turn a non-tool request into array/tool output. Results validate model/
role/content, exact named proposed-tool membership and tool input JSON schema; malformed
handler output never sends arbitrary private result data. Error diagnostics remain
fixed, secret-free. No implicit model calls from these tests.

Named tests cover context/tool preference preservation, digest changes/copy isolation,
bounds/booleans/nonfinite/invalid roles/images/unknown content/external-schema refusal,
typed result metadata/audio, array/tool permission refusal and exact tool argument
schema. Actual dispatcher negatives prove bad requests never enter handler, attempted
request mutation cannot enlarge result authority, and held validation cancellation
waits until the real thread exits. Existing reader/transport/roots/both-host physical
and pinned SDK groups retained in final integrated evidence.

**143 passed,no skips,33.82s**, `/tmp/ui048-sampling-current-integrated.log`,
handle99238 exit0. Root Ruff/strict seven-namespace mypy492 clean
`/tmp/ui048-sampling-final-static.log`, handle8585 exit0. Current agent wheel/sdist
`/tmp/ui048-sampling-artifacts`, handle83227 exit0; two changed production modules
byte-verified `/tmp/ui048-sampling-source.json`. An intermediate test collection
failed because an attempted import assumed module-level validate rather than existing
CompiledSchema.validate; fixed to inspected API, red retained
`/tmp/ui048-sampling-codec-recheck.log`, no tests weakened.

This is the bounded sampling codec/admission-validation checkpoint, not complete
sampling protocol conformance. Proposed tool icon metadata is retained JSON, not yet
fully validated. Tool-use/result pairing, full declared optional content/schema corpus
and native reviewed input/output/model/permission routing need further evidence.
Current ordinary hosts still do not install a sampling reverse handler. Remaining
UI-048 sampling/elicitation/tasks/modern applicability stays待实现, no extra IDs.
64 total42 complete/7 implement/6 verify/5 external/4 deferred;18 non-deferred
unfinished. Full current-Linux/final-root goal preserved.

## Sampling two-phase consent owner and actual peer checkpoint (2026-10-11)

New SamplingOwner provides an original-loop host lease with four bounded review
jobs. Exact input body/digest and fresh one-use phase receipt precede executor entry;
selection requires explicit model/max tokens/context/tools within request ceilings.
Executor receives isolated copies. Actual chosen model and selected tool subset are
checked on result, then a separate output body/digest/receipt must be approved before
returning data to the reverse transport. Output denial never publishes private output.
This owner is infrastructure: publisher must implement actual fully painted native
review, executor must independently enforce host context/provider/tool permissions;
ordinary Coding/Claw hosts do not install it yet. No model adapter was called by tests.

Host revocation is watched during executor work, not merely after a model finishes.
Revocation/timeout/close signals cancellation, awaits executor finally even during
repeated caller cancellation, and withholds output. Review deadlines fail without
executor entry; old/foreign phase/digest/lease receipts cannot execute or release.
Closed events contain only identities, not request or private output bodies.

Actual owned legacy stdio peer now drives both consent phases through the registered
test SamplingOwner handler: input pending has zero executor calls; output pending has
zero peer result; ping remains responsive in both phases. Explicit release produces
one exact result; denial produces one fixed error with no private text. Deterministic
owner tests cover request ceilings, unselected tools, wrong model, review expiry and
executor/host-revocation cleanup. Test executor is an owned callback, NOT production
model admission or actual UI proof.

Final current-source13-file gate **157 passed,no skips,34.09s**,
`/tmp/ui048-sampling-consent-current-final.log`, handle9497 exit0. Root Ruff/strict
seven-namespace mypy493 clean `/tmp/ui048-sampling-consent-final-static.log`.
Current agent wheel/sdist `/tmp/ui048-sampling-consent-artifacts`, handle17157 exit0,
new owner bytes verified `/tmp/ui048-sampling-consent-source.json`. Earlier strict
mypy exposed Awaitable vs create_task Coroutine type; use ensure_future to support
the documented executor Awaitable contract and rerun final current gate. No service
restart, dependency download, provider/auth/model calls or publication.

UI-048 stays待实现: next work is actual configured-model executor and approved
context/tool scope, manager registration/withdrawal, native input/output review and
Claw independent transport/physical proof, then remaining elicitation/task/modern
contracts. No narrower completion condition or extra requirement counts.
64 unique IDs42 complete/7 implement/6 verify/5 external/4 deferred;
18 non-deferred unfinished. Final whole-root and Linux safety remain required.


## Configured-adapter sampling executor checkpoint (2026-10-11)

SamplingExecutor now makes an independent single request on the explicitly supplied
host adapter and allowed-model catalog. No provider lookup/fallback, parent Agent
mutation, tool execution loop or automatic MCP handler registration. Only approved
context resolver is called for non-none scope; no resolver means refusal, never
parent-history fallback. Actual resolved context and proposed schema data are bounded.
User image input is validated through the same adapter's capabilities. Unsupported
neutral audio/assistant-image content explicitly refuses instead of flattening it.
These limitations remain named interoperability work, not capability completeness.

Selected model/max tokens and model output limits checked before provider call;
metadata changes at admission refuse. Explicit temperature/stops, selected proposed
tool definitions and tool-choice reach the adapter. Tool-call proposals are output
review data only; they are never executed. Original tool-call/result messages must
pair before context can be submitted. Unselected/unknown tool output is rejected by
existing sampling result validator. Provider reasoning/signatures are not disclosed;
failed/aborted responses withhold partial text. Ordinary text segments become one
sampling text response when the basic protocol requires a single content block.

Stalled providers are actively watched for cancel/host revocation, not just checked
when a delta arrives. Executor owns consumption and closes wrapper/source iterators
on timeout/cancellation. SamplingOwner still independently requires input and output
review; direct executor tests do not imply that a UI granted consent.

Current offline same-adapter tests verify two-phase execution, exact request/options,
no parent context/default tools, explicit resolver scopes, required tools/model/audio/
unsettled-call refusal before provider entry, proposals-only output, withheld reasoning,
model output budget and actual-context bounds, failed provider refusal and stalled
iterator timeout/task/event cancellation cleanup. No live provider/model/auth call.

Final integrated14-file gate **171 passed,no skips,34.15s**,
`/tmp/ui048-executor-integrated.log`, handle29955 exit0. Root Ruff/strict seven-namespace
mypy494 clean `/tmp/ui048-executor-final-static.log`. Current agent wheel/sdist
`/tmp/ui048-executor-artifacts`, handle27451 exit0; new executor bytes verified
`/tmp/ui048-executor-source.json`. Initial strict mypy annotations corrected before
final regression; no existing tests removed or shortened.

UI-048 remains待实现: native fully painted input/output sampling UI, manager registration/
withdrawal and Claw independently negotiated controls, actual both-host/physical lifecycle
verification, content/schema interoperability and remaining elicitation/tasks/modern
applicability are still required. Ordinary hosts do not install sampling yet.
64 stable IDs42 complete/7 implement/6 verify/5 external/4 deferred;
18 non-deferred unfinished. Final frozen root and Linux safety requirements unchanged.


## Explicit sampling manager registration/withdrawal checkpoint (2026-10-11)

Manager now registers a bounded host sampling owner before connection negotiation,
with explicit lease/publisher/executor/alive callback. Registration alone launches
nothing and executes no model. Legacy initialize receives sampling capability only
with this explicit handler; ordinary clients and modern stateless requests remain
unadvertised. Status differentiates configured host registration from unsupported.
Original-loop answer routing checks registration/lease/current connection/handler,
then delegates phase/digest/single-use input/output consent to SamplingOwner.

Detaching a foreign lease cannot revoke an owner. Original detach revokes transport
handler delivery immediately, settles reverse jobs/executor finally and owner before
removing registration. Disconnect and failed connection retire registration; reconnect
never silently reinstalls consent or replays requests. Manager shutdown revokes all
handlers before cleanup. No parent host automatically opts in yet: actual native
sampling enable/input-output review and Claw control transport remain required.

Real owned stdio peer tests manager registration plus configured offline adapter:
no model before input approval, no peer output before output approval, one exact
reviewed result, foreign/stale answers denied, reader ping while waiting, foreign
vs original detach, executor-held cleanup, disconnect/reconnect no capability replay
and missing executable startup refusal. This is SDK/manager integration evidence,
not a claimed production Coding/Claw sampling UI feature or live provider call.

Final current15-file gate **177 passed,no skips,34.94s**,
`/tmp/ui048-registration-integrated.log`, handle42849 exit0; root Ruff/seven-namespace
strict mypy494 clean `/tmp/ui048-registration-final-static.log`, handle51187 exit0.
Current agent wheel/sdist `/tmp/ui048-registration-artifacts`, handle53691 exit0,
manager exact bytes verified `/tmp/ui048-registration-source.json`. No downloads,
publication or user-service restart. UI-048 remains待实现 for native host registration/
review/Claw transport/physical and remaining content/elicitation/tasks/modern contracts.
64 unique IDs42 complete/7 implement/6 verify/5 external/4 deferred;
18 non-deferred unfinished. Final frozen full-root and Linux safety gates intact.


## Sampling full-paint input/output component checkpoint (2026-10-11)

New dependency-light McpSamplingPanel supplies input explicit JSON model/token/context/
tool selection followed by exact full request+selection review, and a separate exact
output release review. It validates bounded finite JSON/shape/identities/body digest,
model catalog and request ceilings. Private event snapshots isolate inspection copies;
focused press-only one-use confirmation requires every current-width/budget row painted.
Focus loss/resize/selection edits clear proof; repeat/release/paste cannot confirm or
mutate a pending review. Permanent host disable prevents late repaint re-admission,
while Escape still denies exactly once. No agent imports/provider/model/IPC operations
are performed by the component itself.

Exact approval JSON uses ASCII-escaped, lossless fixed-width rows rather than word
wrapping: initial test exposed stripped boundary spaces in ordinary word wrapping.
Controls including ESC/C1/bidi are literal JSON escapes, not terminal operations;
concatenating payload rows reconstructs original JSON values. Request notice remains
sanitized presentation. Binary metadata is inspectable encoded data, not rendered/
played or silently dropped. Native image/binary preview integration remains separate.

Named component tests cover both phases, selected catalog/token/context/tool ceilings,
all-page paint, current viewport/focus/edit invalidation, digest/identity failures,
private inspection, long Unicode/control escaped suffix, no premature approval and
host withdrawal/one-use cancel. Initial1failed/31passed at
`/tmp/ui048-sampling-panel-initial.log` and recheck retained; lossless rendering fixed
instead of weakening exact-content assertions.

Current17-file integrated gate **204 passed,no skips,34.86s**,
`/tmp/ui048-sampling-panel-integrated.log`, handle19540 exit0. Root Ruff/seven-namespace
strict mypy495 clean `/tmp/ui048-sampling-panel-final-static.log`, handle71152 exit0.
Current tui wheel/sdist `/tmp/ui048-sampling-panel-artifacts`, handle15209 exit0,
new component bytes verified `/tmp/ui048-sampling-panel-source.json`.

This component is **not yet installed in either native host**. Next work is actual
Coding/Claw explicit sampling enable/manager registration/event queue/input-output
answer routing, protected focus/approval ownership and physical terminal cancellation.
Then remaining optional content/task/elicitation/modern contracts. UI-048 stays待实现;
64 IDs42 complete/7 implement/6 verify/5 external/4 deferred;18 non-deferred unfinished.
Final current-Linux/frozen root gates remain required, no service restart/publication.

## Actual Coding sampling UI and physical terminal checkpoint (2026-10-11)

Shared MCP browser now exposes explicit b sampling ON/Alt+b OFF. Coding installs
sampling only for its original idle view and disconnected configured server;
arming starts no peer/model. Subsequent explicit connection uses the existing
manager. Original session/model/adapter/context/view guards, protected approvals/
dialogs and exclusive executor ownership fence model admission. Parent submission/
session mutations are blocked during execution through the existing auth-changing
boundary. Close/deny sets a synchronous revoked flag before async detach settles.

Input and output events use the owned UI queue with server/lease/session/context
identity; the shared full-paint component routes original phase/digest receipts to
manager answer calls. Another request cannot replace an active review or clear its
paint guards with a closed event. Unavailable connected-server enable returns a
visible refusal without throwing on non-dict error data. Claw explicitly refuses
sampling operations instead of treating them as prompt/context commands; actual
Claw sampling enable/control transport remains pending, not completed by this path.

Actual Coding session/real legacy peer/offline configured adapter tests prove default
OFF, arming without startup, zero model calls before full input review, one independent
model call, zero peer result before full output approval, deny/close detach, unchanged
parent draft/history/default-deny permissions and registration refusal on connected
server. Physical private Linux tmux proves the same input/output phases with actual
shipped client, resize/full painting, release or denial, no extra model call, parent
cursor and exact main-buffer/termios recovery. Initial physical reds retained: first
fixture only moved cursor to start without deleting selection default; corrected to
C-a/C-k. Second waited for transient Read-all hint although entire review fit current
viewport; waits actual request review then resizes/pages. Full-paint checks and no-
model/no-peer assertions retained, no production delay or weakened tests.

Current21-file integrated **228 passed,no skips,48.55s**,
`/tmp/ui048-coding-sampling-integrated.log`, handle42170 exit0; root Ruff/strict
seven-namespace mypy495 clean `/tmp/ui048-coding-sampling-complete-static.log`,
handle14827 exit0. Tui/coding/claw wheel/sdist built
`/tmp/ui048-coding-sampling-artifacts`, handle54647 exit0; three changed production
modules byte-compared `/tmp/ui048-coding-sampling-source.json`. Physical narrower
2 passed4.13s `/tmp/ui048-coding-sampling-terminal-final.log`; all part of current
integrated gate. No live provider/auth calls, installs, publication or service restart.

UI-048 stays待实现: actual Claw independent sampling enable/answer/revoke gateway,
owner model/context/tool policy and physical proof; broader configured model catalog,
explicit additional context resolver and content/optional schema interoperability;
elicitation/tasks/modern applicability remain. Current Coding exposes only captured
current model and no automatic context resolver, not unlimited model/context support.
64 unique requirements42 complete/7 implement/6 verify/5 external/4 deferred;
18 non-deferred unfinished. Final whole-root/Linux safety gates intact.

## Claw independent sampling connection/UI/physical checkpoint (2026-10-11)

Claw now independently negotiates mcp_sampling:1; bare/old viewers get0. Explicit
connection-local enable uses original group/session/source revision/actual manager/
model/adapter binding and a separate random internal manager lease. Arming registers
only on a disconnected peer, without model/peer startup. The connection owns bounded
views/jobs/request IDs and asynchronous deliveries; answer checks original registration
and dual-phase digest receipt, and detach immediately revokes before actual cleanup.
Foreign connections cannot borrow the view or manager lease. Withdrawal schedules
bounded cleanup outside the socket reader; reconnect never restores consent.

Sampling executor is bridged to the original host loop and requires exclusive idle
group reservation plus child idle scope. Parent history is not borrowed or mutated;
current captured model only, no implicit context resolver. Reservation is retained
through provider cleanup. Actual group identity/revision is checked rather than the
transient source_lease navigation sentinel during initial MCP connection: a peer can
request sampling during tool discovery without mistaken revocation, while actual
session/revision/model changes still revoke and model entry still needs idle ownership.

Actual Claw TUI hello/control/event routing reuses the shared full-paint input/output
component. Selected original-view events only; no unavailable-feature prompt fallback.
Close, capability loss or transport loss disables pending reviews and retires requests;
answer ACK cannot overwrite a later review or completed status. The physical test
exposed that late ACK race; production fix ignores successful answer ACK for progress,
which remains owned by review/closed events, not altered test expectations.

Real private gateway/socket tests cover old viewer, explicit arming, exact two-phase
model execution/release, foreign connection refusal, detach/withdrawal/disconnect and
original model/history. Actual shipped Claw native TUI and Linux private tmux prove
full input/output painting after resize, one offline-adapter request, no unreviewed
peer result, release/deny, parent editing/cursor and main-buffer/termios recovery.
A deliberately stalled provider finally test proves capability withdrawal returns a
pong immediately, reader remains responsive while cleanup owns the group, then real
settlement releases reservation with no private output or history replay.

Red evidence preserved: initial actual UI2 failures/10 passes
`/tmp/ui048-claw-sampling-ui-current.log` revealed transient navigation-sentinel
revocation, fixed by actual owner/revision checks. Initial physical1failed/8passed
`/tmp/ui048-claw-sampling-physical.log` revealed late answer ACK overwriting terminal
state, fixed in production event handling. Targeted corrected group14 passed5.91s,
`/tmp/ui048-claw-sampling-fixed.log`; lifecycle group12 passed9.31s,
`/tmp/ui048-claw-sampling-lifecycle.log`. No sleeps/timeouts weakened to hide failures.

Final current23-file gate **238 passed,no skips,53.57s**,
`/tmp/ui048-claw-sampling-integrated.log`, handle94309 exit0; root Ruff/seven-namespace
strict mypy496 clean `/tmp/ui048-claw-sampling-final-current-static.log`, handle38137
exit0. Current Claw wheel/sdist `/tmp/ui048-claw-sampling-artifacts`, handle90487
exit0; three changed production modules byte verified
`/tmp/ui048-claw-sampling-source.json`. All handles reached actual terminal exit;
no live provider/auth/model call, dependency install, user-service restart or publication.

UI-048 remains待实现. Both native hosts now have named current-model/basic-context
sampling input/output/physical paths; broader model catalogs/scoped extra context,
optional content/schema interop, elicitation/tasks/modern applicability remain required.
No sub-contract counted as a new/completed requirement ID.64 unique IDs42 complete/
7 implement/6 verify/5 external/4 deferred;18 non-deferred unfinished.
Final frozen whole-root/Linux safety gates remain independent and unfinished.


## Sampling same-adapter catalog selection checkpoint (2026-10-11)

Both actual hosts now publish the captured same-adapter streaming-chat model IDs to
sampling review instead of only the parent current model. Catalog/resolver identity
must agree; duplicate/missing/unsafe IDs or actual catalog withdrawal refuse. Private
metadata snapshots pin capabilities and changes/errors revoke the original registration
via a safe boolean guard. No catalog exception diagnostics leak through that guard.
Only public IDs cross UI/socket; provider headers/credentials stay private. Sampling
chooses an alternate model on the original adapter without switching/persisting the
parent model or touching its history. Catalog size aligns with existing512 native+
64 extension bounds. Opaque SDK/base adapters retain only explicitly configured current
ID when no actual catalog exists, not arbitrary model inference or provider fallback.

Actual two-host UI/peer tests select alternate-model, perform both fully painted
reviews, inspect returned model identity and unchanged parent model/history/draft.
Catalog withdrawal before input confirmation yields zero provider calls. New negatives
prove an extension wrapper around an opaque adapter cannot fabricate a catalog while
an actual base catalog missing current model still refuses. Existing physical both-host
sampling paths remain in integrated gate.

Red evidence retained: initial catalog group1 failed/18 passed
`/tmp/ui048-sampling-catalog-current.log` exposed Claw response-handler local uuid import
shadowing the module import on refusal cleanup; remove the redundant local imports,
not the refusal assertion. Initial broad gate13 failed/230 passed
`/tmp/ui048-catalog-integrated.log` exposed wrappers declaring models() while their
opaque base supplied no catalog. Production model_catalog now explicitly distinguishes
that supported current-ID contract from true advertised catalog withdrawal. Existing
sampling UI/physical tests were not skipped/relaxed; repaired host group21 passed.

Final current24-file integrated **244 passed,no skips,53.87s**,
`/tmp/ui048-catalog-current-final.log`, handle57359 exit0. Root Ruff/seven-namespace
strict mypy496 clean `/tmp/ui048-catalog-complete-static.log`, handle34551 exit0.
Four package wheel/sdist builds `/tmp/ui048-catalog-artifacts`, handle28576 exit0,
five changed production modules byte verified `/tmp/ui048-catalog-source.json`.
No live provider/auth/model calls, dependency installs, service restarts or publication.

UI-048 stays待实现: additional explicitly scoped context/data disclosure review,
content/optional schema interoperability and elicitation/tasks/modern applicability
remain. Catalog selection does not waive those behaviors.64 stable IDs42 complete/
7 implement/6 verify/5 external/4 deferred;18 non-deferred unfinished. Final whole-root
and current Linux safety/terminal gates remain independent requirements.


## Sampling paired content/optional metadata checkpoint (2026-10-11)

Sampling request codec now validates original tool-use/result identities and role
pairing before host review: assistant calls have unique IDs, user results must settle
those exact pending calls, duplicate/foreign/missing results and premature assistant
continuation refuse. Original parallel results preserve their request order and names
through actual configured-adapter context conversion, without executing any host tool.
Sampling output tool proposals also require unique assistant IDs; output cannot
impersonate a server tool-result event. Existing declared-tool argument schema checking
is retained, and no optional malformed field is silently discarded.

Tool icons now validate bounded arrays/src/MIME/sizes/theme as inert metadata only:
no remote fetch, file read or image display. Task ttl follows pinned SDK2.3.1 numeric
vocabulary under a finite client ceiling, rejecting null/bool. Progress tokens reject
bool/invalid data. Source schema was actually inspected in installed core2.3.1; no
latest upstream/API conformance assertion. Optional metadata preserved exactly after
validation and included in existing full request review/digest.

Named codec negatives and actual adapter parallel result conversion added. Final
current24-file integrated **259 passed,no skips,53.43s**,
`/tmp/ui048-content-integrated.log`, handle45636 exit0. Root Ruff/seven-namespace
strict mypy496 clean `/tmp/ui048-content-final-static.log`, handle55743 exit0.
Current agent wheel/sdist `/tmp/ui048-content-artifacts`, handle35798 exit0,
codec bytes verified `/tmp/ui048-content-source.json`. Original two-host sampling
UI/physical, roots, permission/owner and installed SDK cases remain in the gate.
No user-service restarts, downloads, live provider/auth/model calls or publication.

UI-048 remains待实现: explicit additional context disclosure review, remaining modality/
schema corpus and elicitation/tasks/modern applicability. Typed data validation is
not general current SDK conformance or external tool exactly-once.64 unique IDs:
42 complete/7 implement/6 verify/5 external/4 deferred;18 non-deferred unfinished.
Final complete-root/current Linux all-host safety and terminal gates intact.


## Additional-context disclosure review protocol (2026-10-11)

SamplingOwner/manager registration now accept an explicit optional host context
provider. Choosing non-none context first requires original input consent, then
captures and validates a bounded exact context snapshot, then publishes a separate
context phase with selection/messages/system prompt and its own digest/one-use
receipt. Zero executor entry until that disclosure is approved. Execution receives
only that frozen combined request with context:none selection, preventing a second
resolver read after consent. Later changes to the provider's mutable data cannot
change the approved bytes. No provider means explicit refusal, not parent-history
fallback. Original request and output/tool ceilings remain independently enforced.

Context capture watches original-host revocation, cancels resolver and waits actual
finally before freeing capacity. Context denial stops before model entry; request/
context/output phases share original lease/phase/digest authority, never one reused
receipt. Shared policy-free sampling component supports fully painted context phase
without requiring another model selection or accepting modified selection at approval.

Actual manager/owned stdio peer/configured offline adapter test proves capture only
after input approval, separate context review with no adapter calls, frozen execution
despite later source mutation, no peer output before output approval, exact one result
and one context read. Deterministic owner/component tests cover context denial,
unavailable provider and host-loss resolver-finally settlement. **Native Coding/Claw
hosts still do not install a context provider**: actual scoped host history/context
projection, host review transport/physical tests and content/privacy boundaries remain
required next work; this protocol is not presented as completed extra-context UI.

Current24-file integrated **266 passed,no skips,53.81s**,
`/tmp/ui048-context-integrated.log`, handle45182 exit0; root Ruff/strict seven-namespace
mypy496 clean `/tmp/ui048-context-final-static.log`. Current agent/tui wheel/sdist
`/tmp/ui048-context-artifacts`, handle7672 exit0; three changed production modules
byte verified `/tmp/ui048-context-source.json`. Existing two-host native sampling/
physical/catalog/roots and pinned SDK gates retained. No downloads/provider/auth calls,
user-service restart or publication.

UI-048 remains待实现 for actual scoped host extra-context provider/review integration,
remaining modalities/schema corpus and elicitation/tasks/modern contracts.64 stable
IDs42 complete/7 implement/6 verify/5 external/4 deferred;18 non-deferred unfinished.
Final complete root/current Linux all-host safety requirements preserved.

## Actual two-host scoped sampling context and physical review (2026-10-11)

Both hosts now install an explicit context provider. Scope thisServer projects only
completed turns involving that manager's exact tool alias/server provenance;
allServers projects completed MCP-related turns across that manager's registered
servers. No general parent history, system/memory prompt, custom entries, local tools
or sibling tool result is automatically copied. Credentials in text/structured tool
arguments are redacted before disclosure; thinking/signatures never projected. Typed
images remain explicit payloads in the complete review. Context is a disclosure
projection, not resumable history or filesystem/tool authorization.

Duplicate call IDs/results or result names inconsistent with the original alias
refuse instead of accepting a foreign tool result. Incomplete turns are excluded;
capacity errors refuse rather than truncate. Captures use original immutable history
and copied manager alias provenance, run off the owner loop, and verify original
history identity after serialization. Claw captures on its actual host loop under
idle group reservation; no resource/URI read or automatic tool execution.

Actual Coding/Claw session/UI/peer/adapter tests seed related and unrelated completed
turns, verify full input then separate context painting, zero adapter entry before
context approval, context denial, redacted exact executed snapshot and parent history/
draft identity preservation. Physical private Linux tmux runs both shipped hosts
through input/context/output review after resize, no model before context approval,
no peer result before output approval, parent cursor/main-buffer/termios recovery.
These physical cases use empty additional MCP history; nonempty scoped/privacy cases
are proved by the actual-host tests, not claimed solely from physical rendering.

Initial2failed/4passed `/tmp/ui048-context-hosts-initial.log` came from fixture reading
catalog before Claw initial discovery finished (sampling legitimately arrives first).
Test now waits the actual tool inventory publication; assertions unchanged. Corrected
narrow tests6 passed; physical/scoped/provenance group11 passed7.01s at
`/tmp/ui048-host-context-physical.log`. Red evidence retained.

Final current27-file gate **277 passed,no skips,59.56s**,
`/tmp/ui048-host-context-integrated.log`, handle54699 exit0. Root Ruff/strict
seven-namespace mypy497 clean `/tmp/ui048-host-context-complete-static.log`.
Current agent/coding/claw wheel/sdist `/tmp/ui048-host-context-artifacts`, handle86425
exit0; four production modules byte verified `/tmp/ui048-host-context-source.json`.
No installs/live model/auth calls, publication or user-service restart.

UI-048 remains待实现: scope-to-protocol interoperability reconciliation, remaining
modalities/schema corpus, elicitation/tasks/modern counterpart. allServers is the
registered MCP-related conversation projection, not an unlimited parent-history grant;
explicit broader disclosure/API parity must not be inferred from these checks.
64 unique IDs42 complete/7 implement/6 verify/5 external/4 deferred;
18 non-deferred unfinished. Final frozen whole-root/Linux safety gates remain required.

## Elicitation form/URL codec and reverse transport foundation (2026-10-11)

Started elicitation implementation using actually inspected pinned core2.3.1 schemas.
New bounded request/result codec supports named primitive form fields, single/multiple
select enums and titled/legacy enum titles, required fields, size/numeric constraints,
string formats and validated defaults. Unknown/nested/arbitrary schema/ref contracts
refuse rather than becoming implicit arbitrary prompts. Accepted data must contain
only requested fields and satisfy exact types/enums/required/constraints; canceled or
declined responses cannot carry private form data. URL requests are inert HTTPS
metadata with explicit elicitation identity, no automatic browser/login/network calls;
URL accept carries no form data. This is not an implemented external interaction.

ReverseRequests now independently installs elicitation_handler; sampling is unchanged,
with separate per-family handler identity checks and honest capability declaration.
Both families share existing bounded jobs/non-reusable request IDs, exact cancel,
owned validation threads, secret-free errors and no cached response replay. Default
clients still install neither elicitation handler nor native form/browser approval.
An installed test handler can advertise form/url; actual host registration and human
input/whole-result consent must still be supplied before ordinary clients enable it.

Real owned legacy stdio peer verifies advertised test handler, pending elicitation
without blocking tools/list/ping, no result before handler review completion and exact
correlated typed form response. HTTP MockTransport proves original result correlation
continues while elicitation waits, then exact decline response. Deterministic codecs
cover types/required/unrequested fields/constraints/defaults/titled enums/unsafe URL/
external-schema refusal; handler-copy mutation cannot expand canonical accepted form
fields; original request cancellation waits callback finally. HTTP mock evidence is
not claimed as real HTTP reverse-peer interoperability.

Final20-file current integrated **233 passed,no skips,46.70s**,
`/tmp/ui048-elicitation-integrated.log`, handle28551 exit0; root Ruff/seven-namespace
strict mypy498 clean `/tmp/ui048-elicitation-final-static.log`. Current agent wheel/
sdist `/tmp/ui048-elicitation-artifacts`, handle17327 exit0; two changed production
modules byte verified `/tmp/ui048-elicitation-source.json`. Primary webpage retrieval
returned no content; no newly verified latest protocol claim. No live model/auth/
provider call, dependency install, user-service restart or publication.

UI-048 remains待实现: explicit manager elicitation registration/withdrawal, native
form/URL full-review and input/result routing, independent Claw transport, both-host
physical evidence and completion/optional schema/capability interop. Task and modern
applicability plus remaining modality/scope reconciliation remain required. Codec and
transport foundation are not human consent or entire elicitation feature completion.
64 unique IDs42 complete/7 implement/6 verify/5 external/4 deferred;
18 non-deferred unfinished. Final complete root/current Linux safety gates unchanged.

## Elicitation review owner/manager registration checkpoint (2026-10-11)

ElicitationOwner now requires original-loop/view/phase/digest and fresh single-use
receipts. Requested form/URL data is first reviewed; an accepted form reply or URL
acknowledgment then gets independent exact response review before release. Invalid
form edits can be corrected without changing the canonical request; concurrent submit
is refused. Decline/cancel releases no private content. URL mode makes no external
browser/login call: explicit external-navigation UI remains unfinished.

Manager explicitly installs elicitation before connection negotiation, independently
of sampling. Registration starts no peer/model; original connected handler/lease
routes replies. Original detach/disconnect/failed startup/shutdown retires ownership.
ReverseRequests adds family-specific withdrawal: detaching sampling does not cancel
elicitation and vice versa, and never clears connection no-replay IDs. Current default
hosts still do not install elicitation automatically or provide native input UI.

Close now tracks both admission and reply-validation tasks, revokes delivery before
cancel and waits actual worker thread exit. Review timeout and host close remove
capacity without output; terminal events contain identities only. Actual manager/
owned legacy peer tests prove request and response gates, no peer form data before
response approval, original vs foreign lease, detach/disconnect/reconnect and coexistence
with a pending sampling request. Held validation threads prove real settlement on
close. These are manager/owner evidence, NOT fully painted native-host consent proof.

Final22-file current integrated **246 passed,no skips,47.72s**,
`/tmp/ui048-elicitation-registration-current-final.log`, handle35030 exit0; root Ruff/
seven-namespace strict mypy499 clean `/tmp/ui048-elicitation-registration-final-static.log`.
Current agent wheel/sdist `/tmp/ui048-elicitation-registration-artifacts`, handle84543
exit0; three changed production modules byte verified
`/tmp/ui048-elicitation-registration-source.json`. Strict mypy variable reuse and
formatting errors corrected before final gates. No existing tests removed/weakened,
live model/auth calls, downloads, publication or user-service restart.

UI-048 remains待实现: native form/URL request/input/reply review components and actual
Coding/Claw registration/control/physical verification, URL external-interaction and
completion semantics, tasks/modern and remaining modality/scope interoperability.
64 unique IDs42 complete/7 implement/6 verify/5 external/4 deferred;
18 non-deferred unfinished. Final frozen root/current Linux safety gates preserved.

## Elicitation full-paint form/URL component (2026-10-11)

New dependency-light McpElicitationPanel reuses the exact ASCII-escaped review
renderer without agent imports or sampling callbacks. Request/schema/URL must be
fully painted before form editing or URL acknowledgment. Defaults are not adopted;
form begins empty. Typed requested JSON fields are edited then locally reviewed in
full before sending a proposal; canonical host validation and independent response
phase approval remain required before peer release. URL acknowledgment never opens
a browser, logs in or submits private form data. Ctrl+d declines with no editable
content; Escape invokes original cancel once.

Private copied event/identity/digest, current focus/viewport, selected reply edits,
repeat/release and permanent host withdrawal are tested. Required/type mistakes remain
editable without consuming host receipt; full field constraints/formats still belong
to the agent owner, not the policy-free TUI. Unicode/binary/terminal controls remain
inspectable literal JSON, not rendered/executed. Tiny viewports cannot approve.
Local reply edits invalidate earlier paint proof; no external event can mutate the
review snapshot. Native host integration is explicitly unfinished, not implied by
component tests.

Initial1failed/37passed `/tmp/ui048-elicitation-panel-initial.log` was a test fixture
constructing a control chord as a literal key string; corrected to KeyEvent('d',
ctrl=True), retaining the decline/no-private-data assertion. Corrected component
and owner48 passed1.04s `/tmp/ui048-elicitation-panel-hardened.log`.

Current25-file integrated **295 passed,no skips,48.16s**,
`/tmp/ui048-elicitation-panel-integrated.log`, handle24592 exit0. Root Ruff/seven-
namespace strict mypy500 clean `/tmp/ui048-elicitation-panel-final-static.log`.
Current tui wheel/sdist `/tmp/ui048-elicitation-panel-artifacts`, handle16918 exit0,
component exact bytes verified `/tmp/ui048-elicitation-panel-source.json`. All work
is local/offline, no dependency downloads/auth/model calls or user-service restart.

UI-048 remains待实现: actual Coding/Claw elicitation enable/event/answer/withdrawal
routing and protected-focus/physical evidence, URL interaction/completion, task/modern
and remaining interoperability. Next work must integrate this component, not substitute
more helper checks for native completion.64 stable IDs42 complete/7 implement/6 verify/
5 external/4 deferred;18 non-deferred unfinished. Final full-root/Linux gates intact.

## Actual Coding elicitation native UI/terminal checkpoint (2026-10-11)

Coding MCP browser now explicitly arms elicitation with `f` and revokes it with
Alt+f. Arming requires the original idle view/disconnected configured server and
registers its owner without peer startup/model execution. Session/context/view and
protected permission/dialog/overlay guards fence request and reply delivery. Close
sets a synchronous revoked flag before asynchronous manager detach; stale queued
reviews cannot adopt a successor view. Elicitation and sampling share protected
review focus but not their registration/cancellation authority.

Actual native request review precedes typed form editing or inert URL acknowledgment;
local exact proposal review precedes manager validation; canonical response phase
requires another complete review before peer release. Every phase remains subject
to viewport/focus/press and single-use receipt checks. Default OFF, no provider calls,
no parent history/permission change, no browser/login/navigation for URL mode.
Claw's shared button currently refuses elicitation explicitly, never routes it as
prompt/context; its independent implementation remains required.

Real Coding session/UI/owned stdio peer tests exercise form/URL release, decline and
close, no peer data before canonical response approval and unchanged parent draft/
default-deny/history. Physical private Linux tmux tests both form and URL modes:
arming without startup, request/reply/response review after resize, exact typed peer
result, zero model counter, parent cursor/main-buffer and termios restoration. URL
physical evidence proves inert acknowledgment only, not external completion semantics.

Final current29-file gate **320 passed,no skips,61.58s**,
`/tmp/ui048-coding-elicitation-integrated.log`, handle67967 exit0. Root Ruff/strict
seven-namespace mypy500 clean `/tmp/ui048-coding-elicitation-final-static.log`,
handle29240 exit0. Current tui/coding/claw wheel/sdist builds
`/tmp/ui048-coding-elicitation-artifacts`, handle40598 exit0; three changed production
modules byte verified `/tmp/ui048-coding-elicitation-source.json`. Targeted physical/
actual Coding group8 passed5.02s `/tmp/ui048-coding-elicitation-physical.log`.
No downloads/live model/auth calls, publication or user-service restart.

UI-048 remains待实现: independently negotiated Claw elicitation registration/answer/
withdrawal/event UI and physical verification, recoverable invalid constrained form
edits, URL interaction/completion, tasks/modern and remaining interoperability.
64 stable IDs42 complete/7 implement/6 verify/5 external/4 deferred;
18 non-deferred unfinished. Final frozen whole-root and current Linux safety intact.

## Claw independent elicitation/native terminal integration (2026-10-11 local)

Claw independently negotiates mcp_elicitation:1; absent/old viewer gets0 and cannot
register a handler. Connection-local enable/answer/detach owns bounded request IDs,
views/jobs, original group/session/source revision/manager and separate random internal
lease. Sampling and elicitation negotiate/register/withdraw separately. Arming starts
no peer/model; request and canonical response review remain required. Foreign socket
cannot adopt the view or original manager receipt. Withdrawal immediately revokes
then schedules awaited cleanup outside the reader; detach remains available during
admission capacity pressure. Disconnection/new connection permanently disables local
review/clears request tracking, not merely waiting for a later repaint.

Actual Claw TUI reuses request/schema/URL full-paint review, form editing/local exact
proposal review and independent canonical response confirmation. Answer acknowledgments
do not overwrite newer reviews/closed state. No provider/history/tool grant or automatic
browser/login. Physical private Linux tmux runs both form and URL acknowledgment on
the shipped client: full painting after resize, no peer response before canonical
approval, exact reply, parent cursor/native buffer/termios recovery. URL evidence is
inert acknowledgment, not external navigation/completion proof.

Initial actual socket group4 failed/17 passed
`/tmp/ui048-claw-elicitation-current.log`: fixture read helper discarded a legitimate
response review arriving before the answer ACK. Keep asynchronous review events in a
deferred queue rather than treating the next closed event as that review; production
protocol and original assertions preserved. Corrected actual socket/two-host21 passed
3.59s `/tmp/ui048-claw-elicitation-fixed.log`. Physical/Claw9 passed5.39s
`/tmp/ui048-claw-elicitation-physical.log`. Dedicated connection-loss test proves
old full-paint review cannot send into disconnected/successor transport.

Final31-file current integrated **330 passed,no skips,66.31s**,
`/tmp/ui048-claw-elicitation-integrated.log`, handle48990 exit0. Root Ruff/seven-
namespace strict mypy501 clean `/tmp/ui048-claw-elicitation-current-static.log`,
handle31328 exit0. Current Claw wheel/sdist `/tmp/ui048-claw-elicitation-artifacts`,
handle15740 exit0; three production modules byte verified
`/tmp/ui048-claw-elicitation-source.json`. No dependency installs, live model/auth
calls, publication or user-service restart. Tests were polled to actual exit.

UI-048 remains待实现: recoverable invalid constrained form replies, URL explicit
external-interaction/completion semantics, task/current protocol applicability and
remaining modality/scope/independent schema interoperability. Do not turn native
form/URL acknowledgment evidence into general protocol conformance or full goal
completion.64 unique IDs42 complete/7 implement/6 verify/5 external/4 deferred;
18 non-deferred unfinished. Final complete-root/current Linux safety gates intact.

## Constrained elicitation reply recovery (2026-10-11)

Invalid canonical form replies now rotate the original request phase's receipt and
publish a fresh review with the explicitly edited draft, rather than revoking the
entire elicitation registration. Original waiting request/future remains owned;
old receipt cannot commit, while unchanged request digest identifies the same schema.
Coding catches the specific recoverable validation error; Claw returns a recoverable
control acknowledgment, not a peer success/result or data release. Both hosts adopt
the fresh event only under their existing original connection/view guards.

The recovered component preserves explicit user fields, not schema defaults. Fresh
request painting is required before correcting the draft, then full proposal review
and separate canonical response review before returning valid data. Invalid fields
are never returned to the MCP peer. Other owner/connection failures retain fail-closed
revocation. Existing timeout and cancellation ownership remain unchanged.

Actual two-host/real peer tests reject a minLength violation, inspect retained draft
and changed receipt, reject stale authority, correct and fully review the valid reply,
and observe one exact result with unchanged parent draft/history and zero model calls.
Both shipped clients' private Linux tmux tests now include the invalid-to-corrected
workflow, retained editor text, fresh painting, final reply/cursor/native-buffer/
termios restoration. Narrow physical/recovery/owner group16 passed12.65s,
`/tmp/ui048-recovery-physical.log`.

Final32-file integrated **334 passed,no skips,70.21s**,
`/tmp/ui048-recovery-integrated.log`, handle81343 exit0. Root Ruff/seven-namespace
strict mypy501 clean `/tmp/ui048-recovery-final-static.log`. Current four-package
wheel/sdist `/tmp/ui048-recovery-artifacts`, handle19516 exit0; four changed production
modules byte-verified `/tmp/ui048-recovery-source.json`. No user-service restart,
publication, dependency installation or live model/auth calls.

UI-048 remains待实现 for URL interaction/completion, tasks/modern applicability and
remaining interoperability. This closes the named constrained-edit recovery subtask,
not the aggregate requirement or final safety/root gates.64 unique IDs42 complete/
7 implement/6 verify/5 external/4 deferred;18 non-deferred unfinished.

## Legacy URL completion notice ownership (2026-10-11 local)

URL acceptance and external completion are now separate states. Only after original
request and exact response consent does ElicitationOwner record a bounded URL identity
and deadline and publish a pending notice. Legacy completion notifications must match
that owner-approved ID; foreign/early/expired/duplicate/closed-owner notices cannot
complete it. Non-reusable URL IDs prevent successor request confusion. Expired pending
entries are pruned before capacity admission, without reusing historical IDs. Completion
is a peer assertion/notice only: no login-success claim, credential update, resource/tool
call or original request replay. Owner/connection withdrawal clears pending authority.

Legacy stdio and HTTP routes deliver the notice to the registered elicitation owner.
Current stateless transport rejects removed legacy completion notifications before
handler dispatch; no inferred current-protocol support. Native Coding/Claw event routes
show pending and peer-reported completion as notices instead of trying to render a
new form. Existing dialog/sampling focus and lease guards remain. External browser
opening is still not implemented or claimed by these notices.

Actual owned stdio peer emits foreign/exact/duplicate notices after the reviewed URL
response; exactly one notice appears, one reply was delivered and discovery is not
replayed. Both actual native UI tests verify pending/complete notices and zero model/
credential admission; both shipped Linux tmux URL paths observe peer completion plus
parent/cursor/native-buffer/termios recovery. Owner tests cover early/foreign/expiry/
dedup/close and current HTTP negative routing. Narrow physical/owner group21 passed
12.03s `/tmp/ui048-url-physical-current.log`.

Final15-file current gate **129 passed,no skips,40.55s**,
`/tmp/ui048-url-integrated-final.log`, handle49703 exit0. Root Ruff/seven-namespace
strict mypy501 clean `/tmp/ui048-url-current-final-static.log`. Current agent/coding/
claw wheel/sdist `/tmp/ui048-url-artifacts`, handle12917 exit0; seven changed production
modules byte-verified `/tmp/ui048-url-source.json`. No installs/live model/auth calls,
publication or user-service restart. An intermediate editing command had a syntax
error before changing files; corrected command and final lint/tests are authoritative.

UI-048 remains待实现: explicitly reviewed external URL opening/interaction workflow,
task/current protocol applicability and remaining interoperability. Peer-reported
completion is not external deployment verification (UI-057) or automatic credential
restoration.64 stable IDs42 complete/7 implement/6 verify/5 external/4 deferred;
18 non-deferred unfinished. Final frozen root/current Linux safety gates remain required.

## Explicit reviewed URL opening: both native hosts (2026-10-11 local)

Both hosts now supply a local ReviewedUrlOpener to the elicitation review component.
Only a distinct `o` press AFTER every URL request row was painted requests opening;
painting or Enter acknowledgment alone never launches a helper. Each review admits
one opening attempt, not automatic retries after uncertain effects. Input/context/
protocol response/completion remain separate from this gesture. The visible URL hint
states opening is optional and Enter acknowledges; no implicit browser/login action.

Original request/view/focus and exact URL are checked before opening. Coding also
requires the same registered manager/lease/current session, no parent/child turn,
approval/dialog/overlay or authorization mutation. Claw requires negotiated elicitation,
ready original connection generation/view/server, no active run/child/dialog/overlay.
Focus must actually belong to the original MCP panel, not merely its stale focused
flag. Closing the view cancels owned helper work; changes/lost focus revoke delivery.

Linux opening uses explicit `/usr/bin/xdg-open` without shell or ambient BROWSER
selection override. Only validated HTTPS URLs are admitted; control/userinfo/unsafe
schemes refuse. One bounded off-UI worker retains its capacity until actual completion;
helper failure/cancel/timeout is not rollback or login success. Cleanup targets ONLY
the subprocess created here, never a separately running user browser/service or its
process group. No plaintext credential persistence or model/tool call is added.

Owned helper tests exercise exact argv/environment, success/failure/stall revocation,
unsafe URLs, worker capacity and late receipt suppression. Actual Coding/Claw UI tests
use offline helper callbacks to prove distinct reviewed opening, exact URL, one attempt,
no implicit protocol accept/result and refusal when another surface owns focus.
Physical private Linux tmux tests now press `o` in both shipped hosts, using an owned
fixture helper subprocess instead of a real browser. Before the gesture no launch log
exists; after it exactly one URL is recorded, protocol accept/completion still pending.
Request/reply consent, completion notice, parent cursor/main-buffer and exact termios
recovery remain verified. This is **not a real desktop browser interaction pass**;
no external page was opened. Native Windows/macOS validation remains deferred.

Evidence: narrow component/helper/host group44 passed2.91s
`/tmp/ui048-opener-focus-final.log`; physical six cases passed11.90s
`/tmp/ui048-open-physical-final.log`, handle45437 exit0. Final current34-file
integrated **352 passed,no skips,67.79s**, `/tmp/ui048-open-physical-integrated.log`,
handle87386 exit0. Root Ruff and seven-namespace strict mypy502 clean
`/tmp/ui048-open-physical-static.log`, handle88108 exit0. Current tui/coding/claw
wheel/sdist builds `/tmp/ui048-open-artifacts`, handle84955 exit0; five production
modules byte-verified `/tmp/ui048-open-source.json`, revalidated unchanged after
final physical fixture additions. The production-source checkpoint's earlier gate
352 passed70.47s is retained at `/tmp/ui048-open-current-integrated.log`; final
physical gate above supplements it, not a count of additional requirements.

Early lint/type errors and test-edit command syntax errors corrected before final
gates; no existing functionality reset/overwritten or user-service restart/publication.
All test handles reached actual exit; no live provider/auth/model calls/dependency
installation. URL opening/notice contracts now have named local implementation and
owned Linux evidence. UI-048 still requires task/current-protocol applicability,
remaining modality/scope and independent schema/interoperability reconciliation;
real external deployment proof remains UI-057, not claimed by offline helpers.
64 unique IDs42 complete/7 implement/6 verify/5 external/4 deferred;
18 non-deferred unfinished. Final complete-root/Linux all-host safety gates intact.

## Unadvertised task-augmentation execution boundary (2026-10-11 local)

The current client does not advertise a task registry/lifecycle. Previously a valid
`task` field could reach the ordinary synchronous sampling/elicitation handler,
returning the wrong response contract and potentially performing work without task
consent. The shared reverse responder now explicitly refuses task or related-task
augmentation with -32602 before handler admission. Direct SamplingOwner,
SamplingExecutor and ElicitationOwner also refuse: bypassing the transport cannot
silently downgrade a task request. Codecs may retain valid task metadata for auditing;
that does not authorize execution. Ordinary synchronous calls are unchanged.

Actual owned legacy stdio peer proves exact task error responses while tools/list
and subsequent ping remain responsive, with zero handler calls. HTTP owned mock
contract proves the same identity/error and original response correlation. Direct
owner/executor negatives cover bypass refusal. The related-task key was verified in
locally installed core2.3.1 source. No task capability is advertised, no replay or
new model/form approval occurs. Initial direct test missing McpError import was
corrected; `/tmp/ui048-task-direct.log` red1/100 retained, not passing evidence.

Final18-file gate **219 passed,no skips,41.14s**,
`/tmp/ui048-task-integrated.log`, handle7934 exit0; root Ruff/strict seven-namespace
mypy502 clean `/tmp/ui048-task-final-static.log`. Current agent wheel/sdist
`/tmp/ui048-task-artifacts`, handle50431 exit0; four changed modules byte verified
`/tmp/ui048-task-source.json`. No downloads/live provider calls/service restart.

**Task functionality remains unimplemented**: refusal is a protocol/safety fix,
not parity completion or a scope waiver. UI-048 stays待实现 for original applicable
task lifecycle and modern/modal/schema/scope interoperability reconciliation.
64 stable IDs42 complete/7 implement/6 verify/5 external/4 deferred;
18 non-deferred unfinished. Final full-root/Linux safety gates remain required.

## Task registry lifecycle foundation (2026-10-11 local)

New TaskRegistry implements original-loop connection-scoped creation, public task
state/list, completed result snapshots, host-only input-required transitions, cancel,
expiry sweep and close. It is not installed in the transport/manager/native hosts
and does not yet advertise tasks. Existing task-augmentation refusal remains in force
until explicit negotiated routing is implemented. No rejection test is removed to
pretend registry helpers alone implement protocol parity.

Registry admission is bounded to16 tasks,256KiB per result and1MiB aggregate retained
result bytes, finite TTL up to600000ms. Public catalogs contain state/identities/
timestamps only, not private result or exception data. Completed results return copies;
pending/failed/canceled/expired/foreign/closed task reads refuse. Expired tasks retain
capacity until actual callback cleanup finishes. No persistence or automatic resume/
replay after connection loss. Operations must still use separately approved sampling/
elicitation owners; the registry supplies no provider, tool or browser permissions.

Cancellation waits callback finally, prevents a callback suppressing cancellation from
publishing a late result, and handles cancellation before the task's first step. Close
revokes all siblings before waiting on any one cleanup and continues settling all
owned tasks even if the control caller is canceled, then propagates cancellation.
Initial2failed/14passed `/tmp/ui048-task-registry-current.log` exposed confusing
worker cancellation with control-caller cancellation. Inspect caller.cancelling()
instead; before-first-step and control-cancel assertions retained. New deterministic
sibling-finally test covers close ordering and zero result disclosure during teardown.

Final19-file current gate **236 passed,no skips,41.56s**,
`/tmp/ui048-task-registry-integrated.log`, handle3465 exit0. Root Ruff/seven-namespace
strict mypy503 clean `/tmp/ui048-task-registry-final-static.log`. Current agent wheel/
sdist `/tmp/ui048-task-registry-artifacts`, handle90374 exit0; new registry exact bytes
verified `/tmp/ui048-task-registry-source.json`. No downloads/live provider/model calls,
publication or user-service restarts. This evidence covers lifecycle implementation,
not actual task wire negotiation or two-host interactive operation.

UI-048 remains待实现. Next task work: explicit capability negotiation, task-augmented
request routing through existing consent owners, tasks/get/list/result/cancel wire
handlers, family/connection withdrawal and actual both-host/real stdio+HTTP/terminal
proof. Modern applicability and remaining interoperability remain required.64 unique
IDs42 complete/7 implement/6 verify/5 external/4 deferred;18 non-deferred unfinished.
Final frozen complete-root/current Linux all-host safety gates intact.

## Explicit task wire controls and reader/close isolation (2026-10-11 local)

ReverseRequests now accepts tasks/list, tasks/get, tasks/result and tasks/cancel only
when an original-loop TaskRegistry was explicitly installed on that connection.
Active legacy capability declaration is tasks.list/cancel only; it does not claim
sampling/elicitation task creation support. Ordinary connections remain default-off;
modern transport still rejects unsolicited reverse requests. Task-augmented creation
continues to fail closed, including direct owner/executor bypasses. No manager or
native host currently installs this registry, so this is not native task parity.

Controls validate bounded original task identity, exact parameters and single-page
list semantics. Result waits execute outside the reader; cancellation of the read
alone does not cancel the operation. Completed result retrieval uses retained copies;
failed/cancelled/foreign/expired reads disclose no private payload. Cancellation only
returns cancelled after actual callback finally has settled. Reverse IDs share the
existing 256 nonreusable connection-lifetime budget. Review jobs, result waits and
short task controls each have independently bounded four-slot lanes, so four result
long polls cannot starve tasks/cancel/get/list or human review. Excess requests refuse,
not cache/replay. This is a maximum of twelve jobs, not an unbounded bypass.

Inspection found disconnect could wait on a held cancel-control cleanup before
signaling other task workers. Close now revokes delivery/registry identity, starts
registry-wide sibling cancellation concurrently with reverse-job settlement, and
shields aggregate cleanup from repeated caller cancellation; after actual cleanup it
propagates cancellation. Tests retain held-finally, sibling-signal, canceled-result-read,
no late private result, bounded lane flood and no delivery after teardown assertions.

Actual owned Linux stdio peer covers list/get/cancel and original tools/list/ping
correlation. New real loopback ThreadingHTTPServer (not MockTransport) sends
result/cancel requests in a legacy tools/list SSE response. Original tools/list and
ping finish while cancellation finally is held; after release cancel/result responses
POST exactly to the original endpoint, no tools/list replay and no private payload.
Server/thread/client/registry cleanups are awaited. No external browser, provider,
auth deployment or user service is used or restarted.

Evidence:
- Continued previous handle79926 to exit0:38 passed0.80s,
  `/tmp/ui048-task-controls-current.log`.
- Lane/teardown gate handle26515 exit0:12 passed0.69s,
  `/tmp/ui048-task-control-lanes.log`; expanded41 passed1.47s handle6661 exit0.
- Final focused43 passed/no skips1.53s handle46524 exit0,
  `/tmp/ui048-task-controls-final-focused.log`.
- Final17-file integrated255 passed/no skips30.06s handle93725 exit0,
  `/tmp/ui048-task-controls-integrated.log`. Initial file-selection typo exit4/no
  tests ran retained `/tmp/ui048-task-controls-selection-error.log`, not success.
- Root Ruff/seven-namespace strict mypy503 clean handle56891 exit0,
  `/tmp/ui048-task-controls-final-static.log`.
- Agent wheel/sdist build handle81354 exit0 `/tmp/ui048-task-controls-artifacts`;
  registry/reverse controls exact source bytes in both artifacts verified with hashes
  `/tmp/ui048-task-controls-source.json`.

UI-048 stays待实现. Next: negotiated task creation routing through original consent
owners; metadata/status/family withdrawal; explicit manager and both native hosts;
physical both-host lifecycle; modern applicability and finite remaining modality/
schema/scope interoperability reconciliation.64 unique requirements42 complete,
7 implement,6 verify,5 external,4 deferred;18 non-deferred unfinished. Final frozen
full-root/current-Linux terminal/safety/transport/distribution/example gates remain
required; these scoped gates do not substitute for them.

## Negotiated task creation through original consent owners (2026-10-11 local)

Explicit manager register_sampling/register_elicitation now accepts tasks=True;
boolean type is checked and default remains False. Before connect, only opted-in
families install an original-loop registry and their separate task-aware handlers.
Legacy declaration includes tasks.requests.sampling.createMessage and/or
elicitation.create only for paired installed ordinary/task handlers. Negotiation of
older legacy versions withdraws/closes tasks and retains ordinary review families;
modern unsolicited reverse requests remain refused. Native Coding/Claw registration
currently does not opt in; this checkpoint is manager/transport, not native parity.

Task-augmented creation gets a fresh local CreateTaskResult identity. Canonical
request retains exact task TTL/polling hint through input/request review (hint is not
a promise: registry supplies pollInterval250ms). Finite retention1..600000ms is
required; unsupported/null/zero/boolean/over-ceiling retention fails without owner or
operation admission. Peer-supplied related-task metadata remains refused and cannot
attach to another task. Existing default/direct augmented-refusal tests are retained.
Original registry.authorize requires its actual operation worker, identity and method;
calling a task-aware consent owner from another coroutine with a valid ID refuses.

SamplingOwner.handle_task and ElicitationOwner.handle_task use the existing exact
receipt/digest/lease-bound reviews, transition public task status input_required while
waiting and working afterward. Sampling input→optional context→output consent is
unchanged; only after original task request consent does its executor receive the
model operation without the task transport envelope. Original review is not stripped
or replaced, and direct SamplingExecutor augmented requests still refuse. Elicitation
request→exact reply review remains mandatory before any accepted private form result
is retained. Reviewed decline/cancel remains a protocol result, not private disclosure.
Result validation occurs against canonical original request; retained response adds
original registry-assigned related-task.taskId metadata, never a callback's identity.
No automatic provider selection, tool execution, browser/auth, parent history mutation,
persistence or replay is added.

Family withdrawal cancels/signals all that family's registered workers before waiting,
awaits actual cleanup and retires both completed/private and pending records. Other
family reviews remain live. Repeated control-caller cancellation shields settlement
then propagates cancellation, without premature slot release or late payload. Shared
nonreusable reverse IDs remain intact. Disconnect closes all records/registrations.

Actual manager-owned stdio peer verifies both creation-family negotiation, fresh
CreateTaskResult/no private premature result, input_required state, independent family
detach, approved decline/denied sampling state and original ping responsiveness.
Actual Linux loopback HTTP SSE/POST peer verifies form task creation, tasks/result
long-poll while ping completes, original request/reply reviews, no private reply before
second approval, exact related-task response and no tools/list replay. These are real
local transports; no external MCP/OAuth deployment was exercised. Task-aware owner
unit tests cover private sampled/form result withholding, exact envelope, forged
related IDs, foreign worker and repeated withdrawal cancellation with held finally.

Evidence: initial58 passed2.16s `/tmp/ui048-task-creation-current.log`; manager59
passed2.51s `/tmp/ui048-task-creation-manager.log`; actual HTTP/creation13
passed1.35s `/tmp/ui048-task-creation-http.log`. Final18-file gate269 passed/no
skips31.00s `/tmp/ui048-task-creation-final-integrated.log`, handle19435 exit0.
Root Ruff/seven-namespace strict mypy503 clean
`/tmp/ui048-task-creation-final-static.log`, handle96145 exit0; changed-path
git diff --check clean. Agent wheel/sdist `/tmp/ui048-task-creation-artifacts`,
handle16670 exit0; all ten modified production modules byte-verified both artifacts,
`/tmp/ui048-task-creation-source.json`. Initial static two line lengths then import/
unused-test imports corrected, diagnostic logs retained, not passing evidence.

Local pinned core2.3.1 source was inspected for named ClientTasksCapabilitySchema,
TaskCreationParamsSchema and TaskSchema/Create/Get/List/Cancel response vocabulary.
Web lookup returned no usable content: no freshly verified latest/blanket conformance
claim. The pinned source labels these as deprecated legacy interoperability vocabulary.
Modern applicability still needs explicit reconciliation, not extrapolation.

UI-048 stays待实现. Remaining: explicit native controls/Claw transport opt-in and
both-host full-painted review/lifecycle/physical terminal verification; TTL cleanup/
status-notification/result metadata interoperability reconciliation; finite remaining
modern/modality/schema/scope matrix. Counts unchanged64 unique IDs42 complete,
7 implement,6 verify,5 external,4 deferred;18 non-deferred unfinished. Final frozen
full-root/current Linux terminal/safety/transport/static/artifact/example gates retained.

## Native task opt-in and physical Linux reviews (2026-10-11 local)

Both Coding and Claw now expose separate explicit task arming on the selected MCP
server: j sampling tasks, k elicitation/form tasks. Existing b sampling/f elicitation
remain ordinary/default-off task registration; Alt+b/Alt+f withdraw the respective
whole review family and its task records. Arming is for the original view only and
still requires an explicit later connection. Re-arming a connected or already registered
family refuses; no silent capability upgrade or borrowed consent. Busy, release and
repeat events cannot duplicate task arming. Server-list help names the new gestures.

Coding task flags go to the original manager registration under existing session/
view/model/catalog/permissions/dialog/overlay/idle guards. Claw sends tasks:true only
for the dedicated task gesture through its independently negotiated sampling or
elicitation connection control. Gateway requires an actual boolean flag on enable;
nonboolean flags or flags on answer/detach refuse before registration. Ordinary
clients send no task flag and retain their old capabilities. Original connection/
group/session/revision/view ownership, bounded jobs/IDs, withdrawal and no replay
remain intact. No user service was started/restarted; private test gateways only.

Original full request includes TTL/polling hint in human review. Task creation response
contains only the fresh registry state; peer tasks/result waits independently while
input/request and output/exact-reply reviews proceed. Both hosts preserve unsent parent
drafts, permissions and empty parent model history; form tasks never execute a model.
Closing the view before release cancels/settles owned tasks and retires all result
records; no private tasks/result response is delivered. Successful release returns the
original reviewed result plus exact registry-assigned related-task metadata. Tests use
an owned offline stdio task peer that requests get/result only after CreateTaskResult.

Evidence:
- Existing native ordinary behavior36 passed4.49s
  `/tmp/ui048-native-tasks-existing.log`, handle13709 exit0.
- Both native hosts sampling/form release/close8 passed2.63s
  `/tmp/ui048-native-tasks-hosts.log`, handle74322 exit0.
- Native/boolean/gesture24 passed2.77s
  `/tmp/ui048-native-tasks-controls-fixed.log`, handle2789 exit0. Initial23 pass/
  1 failure `/tmp/ui048-native-tasks-controls.log` found repeat-k could re-arm;
  production now refuses task gesture repeats, assertion retained.
- Physical Coding/Claw sampling/form paths4 passed/no skips8.00s
  `/tmp/ui048-native-tasks-physical-fixed.log`, handle77535 exit0. Exact task
  capabilities/CreateTaskResult/result metadata, no premature model/private result,
  full-painted request/output/reply with resize, editable parent/cursor/main-buffer/
  native scrollback and termios recovery verified through private Linux tmux.
  Initial4 failures `/tmp/ui048-native-tasks-physical.log`, handle20926 exit1:
  sampling fixture resized before selection had been processed; wait for actual
  review mode before resizing. Elicitation matcher expected nonexistent wording;
  use actual 'Elicitation ended'. No production approval or testing assertions waived.
- Current18-file integrated172 passed/no skips12.35s
  `/tmp/ui048-native-tasks-integrated.log`, handle52362 exit0.
- Root Ruff/seven-namespace strict mypy503 clean
  `/tmp/ui048-native-tasks-final-static.log`. All-four agent/TUI/Coding/Claw wheel/
  sdist builds exit0 handle29420 `/tmp/ui048-native-tasks-artifacts`; five modified
  native production modules exact bytes in both distributions with SHA256 receipt
  `/tmp/ui048-native-tasks-source.json`. Initial line lengths corrected, logs retained.

UI-048 is still待实现. Next required task work includes active TTL expiry/cleanup,
status notification/result metadata interoperability and cancellation matrix; modern
applicability and finite remaining modality/schema/scope interoperability still need
reconciliation. Physical task evidence here covers release and resize/recovery;
unit/actual-host tests cover close before output release, not a blanket terminal matrix.
64 unique requirements42 complete/7 implement/6 verify/5 external/4 deferred;
18 non-deferred unfinished. Final frozen full-root/current-Linux terminal/safety/
transport/static/distribution/example gates remain required; native non-Linux validation
alone remains deferred and not claimed passed.

## Automatic task TTL cleanup without operator polling (2026-10-11 local)

TaskRegistry now schedules one original-loop deadline timer per admitted task. No
peer get/list/result, new admission or manual sweep is required to stop expired work.
At deadline each callback synchronously signals its own worker before starting owned
cleanup; a sibling's held finally does not defer other cancellation signals. An early
callback rechecks the injected monotonic clock rather than expiring prematurely.
Cleanup retains the task slot until actual worker settlement, prevents cancellation-
suppressing callbacks from publishing late private data, then deletes the record and
retained result bytes. Completed private results also expire automatically. There is
no automatic resume/retry/new model call. Disconnect cancels future timers, signals
all workers, awaits both workers and deadline cleanup jobs, and propagates caller
cancellation only after original owned work has settled. Manual sweep likewise now
signals every expired sibling before waiting, rather than serially delaying siblings.

Tests cover no-poll deadline cancellation, held finally/late-result suppression,
completed result deletion, early clock recheck, long-timer cancellation on close,
expiry-vs-disconnect with canceled caller, and16 simultaneous expired tasks retaining
full capacity until cleanup; subsequent explicit admission succeeds after retirement.
Both actual native hosts sampling/form task review expires without approval/model
execution or registry polling, clears pending review, rejects tasks/result with original
identity, preserves parent draft/history/permissions and restores an idle MCP view.
Actual Linux loopback HTTP SSE/result-POST test also verifies expired input review
withheld response and no tool execution. Owned peer takes an optional fixture TTL;
no unrelated live peer/test process or user service is edited/restarted.

Physical private Linux tmux matrix now has eight task paths: Coding/Claw × sampling/
form × release/expire. Release retains full-painted request/output/exact-reply/resize
checks; expire has no operator approval or model invocation, original error response,
parent editor/cursor/main-buffer/native-scrollback and termios recovery.31 combined
registry/physical checks passed/no skips22.35s `/tmp/ui048-task-ttl-physical.log`,
handle55681 exit0. Final focused57 passed/no skips8.05s
`/tmp/ui048-task-ttl-final-focused.log`, handle46017 exit0.

Final19-file gate192 passed/no skips37.28s
`/tmp/ui048-task-ttl-final-integrated.log`, handle74774 exit0; root Ruff/seven-
namespace strict mypy503 clean `/tmp/ui048-task-ttl-final-static.log`, handle95105
exit0; agent wheel/sdist `/tmp/ui048-task-ttl-artifacts`, registry exact source bytes
verified with SHA256 `/tmp/ui048-task-ttl-source.json`. No live provider/browser/
auth deployment, dependency downloads, publication or user-service restart.

UI-048 remains待实现. Active TTL gap is implemented/verified but this is not a new
requirement ID and does not close task parity wholesale. Remaining named task work:
status notification/result metadata interoperability and cancel/timeout/withdrawal
matrix reconciliation; modern applicability and finite remaining modality/schema/
scope contracts.64 unique IDs42 complete/7 implement/6 verify/5 external/4 deferred;
18 non-deferred unfinished. Final frozen full-root/Linux terminal/safety/transport/
static/distribution/example gates intact. Native non-Linux validation alone deferred.

## Undelivered task creation and timeout ownership (2026-10-11 local)

Inspection/red tests found a real task lifecycle gap: after registry.create, a failed,
stuck or canceled CreateTaskResult writer left the now-unknown task running until
TTL. ReverseRequests now bounds response writing and owns a whole-work finally:
if creation delivery does not successfully return a result, registry.abandon signals
that original worker, awaits actual finally despite repeated control cancellation,
then removes record/timer/private retained bytes. This also erases an already-completed
private result and works on expired records. It does not cancel sibling tasks, replay
responses, restart peers or weaken existing host consent. A revoked handler cannot
skip retirement by returning before delivery. During cleanup the original reverse
slot remains occupied. Delivery success is transport completion, not a claim the
remote application has durably consumed the task identity; no automatic replay.

New timeout matrix distinguishes ownership: tasks/result timeout/cancellation only
stops that read, leaves the original operation running, and a fresh request identity
can retrieve the subsequently reviewed result (old request ID still refuses). A timed-
out tasks/cancel signals the original operation but waits actual cleanup before
returning a fixed error/retiring its control slot. Failed creation delivery instead
retires the original task entirely because its identity delivery is uncertain. Private
provider/transport exceptions do not enter public errors. Existing task cancel-before-
start/held-finally/late suppression/foreign identity/family revoke/TTL/close cases remain.

Initial test3 failures `/tmp/ui048-task-delivery-red.log`, handle44186 exit1, exposed
all three writer error/timeout/cancel leak cases; assertions retained. Fixed four-file
57 pass3.55s `/tmp/ui048-task-delivery-current.log`; expanded59 pass3.75s
`/tmp/ui048-task-delivery-final-focused.log`; final creation/control29 pass3.77s
`/tmp/ui048-task-delivery-final-focused2.log`, handle39003 exit0, also covers completed
result deletion. Real Linux loopback HTTP peer rejects the creation-response POST
with500; original task/review are cleaned, no result read/replay/private result. No
MockTransport substitute for this endpoint-response evidence.

Final19-file199 passed/no skips38.01s
`/tmp/ui048-task-delivery-final-integrated.log`, handle14043 exit0, includes actual
both-host native and all eight physical Linux task release/expiry paths. Root Ruff/
strict seven-namespace mypy503 clean `/tmp/ui048-task-delivery-final-static.log`;
agent wheel/sdist `/tmp/ui048-task-delivery-artifacts`, handle39003 exit0; both
changed production modules exact bytes/SHA256 receipt
`/tmp/ui048-task-delivery-source.json`. Changed-path git diff --check clean. No
live provider/browser/auth deployment, downloads, publishing or user-service restart.

UI-048 stays待实现. This closes the discovered creation-delivery ownership leak,
not all task/protocol parity. Remaining: status notifications/result metadata protocol
reconciliation and remaining cancellation matrix; modern applicability and finite
remaining modality/schema/scope interoperability.64 unique IDs42 complete/7 implement/
6 verify/5 external/4 deferred;18 non-deferred unfinished. Final frozen full-root/
current-Linux terminal/safety/transport/static/distribution/example gates retained.

## Bounded post-delivery task status notifications (2026-10-11 local)

Task status notifications now start only after successful CreateTaskResult write.
Registry observers publish public TaskSchema snapshots; no request, form/context,
selection, generated output, private exception or result payload enters telemetry.
Original connection/family/registry identity guards remain active. A dedicated
TaskNotifications owner has at most16 monitors and one coalesced pending snapshot
per monitor. Slow writers have a finite write timeout; intermediate states may be
coalesced because notifications are optional telemetry, polling remains canonical.
Notifications never authorize execution, block human reviews/registry transitions,
or cancel the original operation on delivery failure. Observer exceptions detach
the optional observer without changing a successfully completed task into failure.
No status is sent before creation delivery or for expired/withdrawn identities.

Input-required/working/terminal updates are observed immediately, including cancel
before first worker step. Terminal delivery retires the monitor. Family withdrawal
cancels only that family's monitors; close awaits actual writer finally even when its
control caller is canceled, then propagates cancellation. Expiry also retires idle
monitors, no retry/replay/successor notification. Status jobs are separate from the
bounded review/result/control admission lanes and cannot consume their slots.

New tests cover post-delivery ordering, exact task/public fields, independent private
result retrieval,200 rapid state transitions coalesced behind a stalled writer, failed
observer isolation, family/expiry monitor retirement, pre-first-step cancel and held
writer cleanup under control cancellation. Actual both native hosts' owned stdio
peers receive original-task notifications without private contents and settle monitors
on close. Real Linux HTTP SSE peer receives status response POSTs with exact identity
and no private form data. Existing creation-delivery failure cases remain notification-
free. Pinned local core2.3.1 TaskStatusNotificationSchema/TaskSchema inspected;
no freshly fetched latest source or blanket protocol conformance claim.

Initial2failed/42passed `/tmp/ui048-task-status-initial.log`, handle31896 exit1:
tests counted every wire frame as a creation response; now assert exact single response
ID separately from status frames, preserving no model/replay/private output assertions.
Current57passed4.39s `/tmp/ui048-task-status-current.log`, handle76076 exit0;
new status6passed0.55s `/tmp/ui048-task-status-final-focused.log`, handle86956 exit0.
Final20-file205 passed/no skips `/tmp/ui048-task-status-final-integrated.log`,
handle64557 exit0; includes actual native hosts and all8 physical Linux task paths.
Root Ruff/seven-namespace strict mypy504 clean `/tmp/ui048-task-status-static.log`;
agent wheel/sdist `/tmp/ui048-task-status-artifacts`, handle85004 exit0; all three
modified/new production modules byte exact SHA256
`/tmp/ui048-task-status-source.json`. Changed-path git diff --check clean.
No provider/auth/browser calls, downloads, publishing or user-service restart.

UI-048 stays待实现. Status implementation is progress under the same requirement,
not a new ID or wholesale task parity closure. Remaining finite task result metadata/
applicability and cancel matrix reconciliation; modern/modal/schema/context-scope
interoperability still need explicit acceptance.64 IDs42 complete/7 implement/6 verify/
5 external/4 deferred;18 non-deferred unfinished. Final frozen full-root/Linux terminal/
safety/transport/static/artifact/example gates retained.

## Task contract reconciliation and independent legacy schemas (2026-10-11 local)

Named task acceptance matrix is now `agent-ui-mcp-task-contract.md`, refining UI-048
without additional IDs. It closes vague repeated task lifecycle OPEN descriptions
into concrete supported mechanics/evidence and three finite remaining applicability/
related-task conversation/modality-schema-context reconciliations. TTL/cancel/result-
read/delivery failure/status mechanics are not re-counted as new unfinished requirements.

New opt-in independent schema fixture imports pinned official core2.3.1 through package
exports and validates actual xdog task capabilities, creation request/response, get/list/
cancel/result requests/responses, original sampling/form response, related-task metadata
and status notices. Both families pass. This is legacy schema validation, **not** a
serving SDK task runtime or blanket conformance. Existing18 serving-SDK modern cases
also rerun:20 passed/no skips15.09s `/tmp/ui048-task-official-expanded.log`,
handle32636 exit0; standalone2 passed1.17s `/tmp/ui048-task-official-current.log`.
Web lookup returned no usable content; no fresh latest comparison claim.

New requestId-after-successful-creation test proves detached task remains live until
explicit tasks/cancel; no request replay or ordinary execution downgrade. Physical
matrix expanded to12 current-Linux native paths (2 hosts × 2 families × release/expire/
peer_cancel), no operator approval/model call for peer_cancel. Initial physical4
failures `/tmp/ui048-task-contract-physical.log` were startup fixture reading absent
first-frame log; wait safely for first actual creation frame. Subsequent1 failure
`/tmp/ui048-task-contract-physical-fixed.log` and integrated2 failures exposed real
UI race: late connect catalog erases an already-ended review notice. MCP panel now
preserves review-ended notice across list/connect/refresh, resets it on new review or
explicit operator action. Deterministic unit and physical race regressions retained;
no test was weakened to approve more or skip a Linux path.

Final physical+panel23 passed/no skips27.95s
`/tmp/ui048-task-contract-physical-final.log`, handle83418 exit0. Final21-file213
passed/no skips44.88s `/tmp/ui048-task-contract-final-integrated.log`, handle96666
exit0; pinned SDK environment explicitly set (no skipped schema check). Root Ruff/
seven-namespace strict mypy504 clean `/tmp/ui048-task-contract-final-static.log`.
Agent/TUI wheel-sdist builds handle96337 exit0 `/tmp/ui048-task-contract-artifacts`;
modified MCP panel exact bytes/SHA256 `/tmp/ui048-task-contract-source.json`.
No downloads/provider/auth/browser calls, publishing or user-service restart.

UI-048 still待实现 for its finite remaining protocol/interop reconciliations, not
because the named task TTL/status/cancel mechanics are unimplemented.64 stable IDs
42 complete/7 implement/6 verify/5 external/4 deferred;18 non-deferred unfinished.
Final frozen full-root/current-Linux safety/terminal/transport/static/artifact/example
gates remain required. Non-Linux native validation remains deferred, not passed.

## Sampling subcapabilities and exact tool-choice contract (2026-10-11 local)

Pinned core2.3.1 ClientCapabilitiesSchema explicitly names sampling.tools/context;
ToolChoiceSchema allows absent mode. Inspection exposed two real mismatches, red2/
41pass `/tmp/ui048-sampling-choice-red.log`: xdog wrongly required toolChoice.mode,
and accepted a provider tool_use despite reviewed choice none. Codec now preserves
empty toolChoice unchanged and executor uses existing default auto. Result validator
refuses tool_use for explicit none, including a provider ignoring none after one
approved model call; no tool proposal is released/executed. Existing required-tool/
selection/argument-schema/role/pairing boundaries retained. This does not add a tool
execution loop or relax model/context ceilings.

Explicit manager SamplingOwner installations now advertise sampling.tools and, only
when a context provider was installed, sampling.context. Arbitrary reverse callbacks
remain ordinary {} unless explicitly flagged, flags alone without a handler advertise
nothing, family withdrawal clears flags. This declaration is not approval: selected
model metadata, exact input/context/output reviews and tool/context selection remain
required; unsupported audio/model inputs still fail explicitly, not silently converted.
Actual both-host initialization checks verify tools/context declarations. Independent
pinned ClientCapabilitiesSchema and empty ToolChoice/CreateMessageRequest schemas
validate these wire shapes; no fresh latest protocol or blanket conformance claim.

Focused97 pass/no skips7.19s `/tmp/ui048-sampling-contract-focused.log`, handle44416
exit0. Final24-file293 pass/no skips45.29s `/tmp/ui048-sampling-contract-integrated.log`,
handle30713 exit0, includes explicit pinned independent schema checks, actual both
native hosts and12 physical Linux release/expire/peer-cancel paths. Root Ruff/strict
seven-namespace mypy504 clean `/tmp/ui048-sampling-contract-static.log`; agent wheel/
sdist `/tmp/ui048-sampling-contract-artifacts`, handle87810 exit0; all three changed
production modules byte-exact SHA256 `/tmp/ui048-sampling-contract-source.json`.
No live provider/auth/browser, downloads, publication or user-service restart.

UI-048 remains待实现: related-task conversation, modern applicability and finite
modal/schema/context interoperability matrix still require explicit reconciliation.
These two fixed choice gaps and declarations are evidence under UI-048, not additional
IDs.64 stable IDs42 complete/7 implement/6 verify/5 external/4 deferred;
18 non-deferred unfinished. Frozen complete-root/current-Linux final gates retained.

## Required-tool sampling execution/result boundary (2026-10-11 local)

Three red cases `/tmp/ui048-required-tools-red.log`, handle84639 exit1, proved
required tool selection could reach a custom executor with no selected tools, and
text-only replies from custom/provider execution could advance toward output release.
SamplingOwner now checks required selection before optional context capture/executor
entry; Sampling result validation requires at least one reviewed tool_use for required.
This applies to native SamplingExecutor and arbitrary explicitly registered callbacks,
including task routing. Existing none/auto, named subset/schema/role/unique identity,
reasoning withholding and exact output consent remain intact. No tools are executed;
a valid proposal still requires output approval before delivery to the original peer.

New current unit tests cover codec required-text refusal, owner no-selected-tools/no
executor and custom text-only/no output review; native provider ignoring any-tool
choice fails after one approved model call without private output. Actual Coding/Claw
task reviews selecting no tools preserve required envelope, parent unsent draft,
permissions/history, fail the task result with original identity and make zero adapter
calls.103 focused pass6.71s `/tmp/ui048-required-tools-hosts.log`, handle9847 exit0.
Initial static line-length diagnostic corrected; red tests not skipped/weakened.

Final24-file299 passed/no skips45.81s `/tmp/ui048-required-tools-integrated.log`,
handle94679 exit0; includes explicit pinned SDK schema checks and all12 physical
Linux release/expire/peer-cancel paths (new required-selection actual-host tests are
not falsely counted as extra physical terminal cases). Root Ruff/strict seven-namespace
mypy504 clean `/tmp/ui048-required-tools-final-static.log`; agent wheel/sdist
`/tmp/ui048-required-tools-artifacts`, handle33131 exit0; both changed modules exact
bytes/SHA256 `/tmp/ui048-required-tools-source.json`. Primary web attempts returned
no usable content; no freshly verified latest claim. No live provider/auth/browser,
downloads, publication or user-service restart.

UI-048 remains待实现 for related-task conversation and finite modern/modal/schema/
context interoperability. This required/none/auto response enforcement is a completed
subcontract, not a new ID or protocol-wide completion.64 stable requirements42 complete/
7 implement/6 verify/5 external/4 deferred;18 non-deferred unfinished. Frozen final
full-root/current-Linux safety/transport/terminal/static/artifact/example gates retained.

## Scoped structured MCP result disclosure (2026-10-11 local)

Inspection/red8 tests found context projection silently omitted structured MCP results.
Projection now includes only the original manager wrapper's explicitly present
structured_content with matching mcp_server provenance; host details and metadata
wrappers are not disclosed. Nested credentials redact through the same scoped redactor.
Present null/array/boolean preserves JSON type; absent structured content and arbitrary
host details stay excluded. Foreign server/malformed presence/missing payload refuses;
oversized data fails the whole bounded context snapshot rather than dropping it.
Original text/image/tool pairing/selected-server/completed-turn/reasoning privacy rules
remain. No additional tool/resource/provider request or source read is introduced.

Actual Coding/Claw tests insert matching structured result into the original MCP turn,
verify independent full context review includes public structure but no private key/
host wrapper, then mutate original structured data after capture. Approved adapter
gets the frozen reviewed structure, not changed data; declined disclosure makes no
model call. Parent history identity and draft/permission guards retained. Independent
pinned ToolResultContentSchema/SamplingMessageSchema/CreateMessageRequestSchema
validate the projected shape; not a blanket context/provider interoperability claim.

Red8 failures `/tmp/ui048-structured-context-red.log` retained. Current19 pass3.30s
`/tmp/ui048-structured-context-hosts.log`; independent/current21 pass/no skips3.81s
`/tmp/ui048-structured-context-official.log`, handle63165 test portion exit0 (later
static import-order failed and was corrected, not treated as a green whole command).
26-file316 passed/no skips49.76s `/tmp/ui048-structured-context-integrated.log`,
handle56203 exit0 includes both hosts' physical three-phase context reviews and12
physical task paths. Added schema-only assertions subsequently pass the21-case gate.
Root Ruff/seven-namespace strict mypy504 clean
`/tmp/ui048-structured-context-final-static.log`, handle75071 exit0; agent wheel/sdist
`/tmp/ui048-structured-context-artifacts`, source exact SHA256
`/tmp/ui048-structured-context-source.json`. No live model/browser/auth/deployment,
downloads, publication or user-service restart.

UI-048 still待实现 for finite related-task/modern/modal-schema-context applicability.
Structured projection omission is fixed under the same ID, not an additional
requirement.64 IDs42 complete/7 implement/6 verify/5 external/4 deferred;
18 non-deferred unfinished. Frozen full-root/current-Linux final gates unchanged.

## Sampling assistant tool-batch settlement (2026-10-11 local)

Red regression `/tmp/ui048-tool-batch-red.log` found request validation could accept
another assistant tool-call batch while previous calls remained pending, provided later
user results eventually covered both batches. The assistant-with-tool_use exception
hid the unsettled boundary. Codec now checks existing pending calls before processing
any new assistant message. Parallel calls within the same assistant batch and fully
settled sequential batches stay lossless; missing/duplicate/foreign/role-invalid
results still refuse. Actual SamplingOwner rejects the malformed history before
publishing any review or invoking any executor, no private output/provider call.
This is a protocol ordering check, not a new permission grant/tool execution loop.

Focused101 passed/no skips1.31s `/tmp/ui048-tool-batch-current.log`; root Ruff/
seven-namespace strict mypy504 clean `/tmp/ui048-tool-batch-static.log`.
Final26-file319 passed/no skips49.72s `/tmp/ui048-tool-batch-integrated.log`,
handle92449 exit0 includes explicit independent schema checks, both native hosts'
context reviews/physical three-phase flows and12 physical Linux task paths. Agent
wheel/sdist `/tmp/ui048-tool-batch-artifacts`, handle27706 exit0; codec exact bytes/
SHA256 `/tmp/ui048-tool-batch-source.json`. No downloads/model/auth/browser calls,
publication or user-service restart.

UI-048 remains待实现 for finite related-task/modern/modal-schema-context applicability,
not extra counting of this repaired ordering defect.64 stable IDs42 complete/7 implement/
6 verify/5 external/4 deferred;18 non-deferred unfinished. Final frozen full-root/
current-Linux safety/terminal/transport/static/artifact/example gates intact.

## Scoped context tool identity collisions (2026-10-11 local)

A red projection test `/tmp/ui048-context-id-red.log` proved independently completed
turns reusing original tool-call IDs failed the combined sampling codec as duplicates.
Projection now assigns deterministic scoped context IDs per original turn/call and
rewrites only the matching projected result identity. Duplicate/ambiguous IDs within
an original turn still refuse, original persisted history remains unchanged. Before
combining captured context with the original peer request, bind_context independently
validates context and rekeys only context IDs colliding with peer IDs, avoiding existing
IDs deterministically. Server IDs and request are unchanged. This happens BEFORE
full context disclosure review/digest; executor receives that exact frozen combined
snapshot. It does not authorize tasks/tools or repair invalid duplicate/unsettled input.

Tests cover independently completed reused IDs, deterministic copy and unchanged
history, malformed duplicate context refusal and exact context/server collision pair
mapping reviewed before execution; post-capture mutation does not reread/alter data.
Both actual native hosts now include two completed MCP turns sharing original-call,
review both independently paired projected histories, execute only after context
approval or deny without model calls. Existing structure/credential redaction/parent
identity/permission guards retained.

Focused43 passed3.75s `/tmp/ui048-context-id-current.log`; final26-file322 passed/
no skips49.68s `/tmp/ui048-context-id-integrated.log`, handle48442 exit0 includes
pinned schema checks, native context reviews and physical context/12 task paths.
Root Ruff/strict seven-namespace mypy504 clean `/tmp/ui048-context-id-final-static.log`;
agent wheel/sdist `/tmp/ui048-context-id-artifacts`, handle65352 exit0; two changed
modules byte-exact SHA256 `/tmp/ui048-context-id-source.json`. Initial static line
length corrected; no downloads/provider/auth/browser calls/service restart/publication.

UI-048 remains待实现 for finite related-task/modern/modal-schema-context reconciliation.
This repaired context identity gap is not another requirement ID.64 IDs42 complete/
7 implement/6 verify/5 external/4 deferred;18 non-deferred unfinished. Final frozen
full-root/current-Linux verification requirements remain intact.

## Remaining-contract reconciliation and modern implementation (2026-10-11)

**This section supersedes the initial inventory-only matrix, not its historical
receipts.** No central ledger or requirement IDs were changed in this work.
The named remaining protocol work is not a reason to reimplement the already
verified legacy roots/review/task lifecycle. Conversely, safe refusal alone is
not completion of an applicable modern contract.

### Corrected pinned-source applicability

The installed `@modelcontextprotocol/core` and `@modelcontextprotocol/server`2.3.1
at `/tmp/xdog-mcp-pass68.lw1gcs` were inspected directly. In addition to the
previously inspected legacy compatibility schemas, the serving package contains
its 2026-07-28 codec and input-required engine in `dist/src-Cqbh3MYc.mjs`, and the
legacy shim/serving guards in `dist/mcp-DIH4cS6P.mjs`. Important source locations:

- `ClientCapabilities2026Schema` and per-request metadata, around lines2850–2885:
  roots, sampling, elicitation still apply; task vocabulary does not.
- Embedded input request/response/result and continuation parameter schemas,
  around lines2997–3160: bare method/params input requests, bare results keyed by
  the same input identifiers, and opaque string `requestState`.
- `EXTENDED_RESULT_TYPE_METHODS`, around lines3744–3758: only `tools/call`,
  `prompts/get`, `resources/read` have this continuation contract.
- Driver commentary around lines5724–5810: bounded rounds, fresh request IDs,
  exact state echo, and pacing for state-only continuation.
- Modern URL elicitation around lines3057–3078 removes `elicitationId`; it does
  not add a new unsolicited completion channel.

Thus the previous “modern reverse counterpart still to reconcile” was a real
**input-required implementation gap**, not a justification to advertise nothing
indefinitely. Modern unsolicited server RPC remains refused. The applicable
client work instead belongs to an existing client-initiated request. Web attempts
returned no usable material; this is pinned local source/serving evidence, **not a
newly verified latest-protocol or complete third-party deployment claim**.

### Implemented contracts and safety boundaries

| Contract | Current implementation / evidence | Remaining separation |
| --- | --- | --- |
| Modern request ownership | New `mcp/input_requests.py`, integrated into both transports. Only the three named methods resume. Original arguments/extension metadata are frozen; each continuation uses a fresh JSON-RPC identity, exact opaque state and only that round's bare responses. Uncertain network outcomes never retry. | Native browser integration/physical acceptance is a separate main/sidecar gate below. |
| Modern capability declarations | Actual registered and live input handlers are declared per request, not from an old initialize frame. Bootstrap/subscriptions stay empty because they cannot service this input contract. Modern task registries are withdrawn, not advertised. Status distinguishes modern/legacy support. | Unknown experimental/extension names remain absent; no arbitrary protocol plugin promise. |
| Bounds and cancellation | Four owned flows, four embedded inputs per round, ten continuations,600s total,256KiB input-result validation,64KiB opaque state, existing8MiB wire ceiling. State-only rounds pace250ms. Validate the whole round before any callback; serial review avoids competing native panels. Cancellation/family withdrawal/close waits actual handler finally; late suppressed-cancellation output cannot resume. Stdio loss remains an ordinary uncertain-outcome `McpError`, not a spurious parent cancellation. | These are explicit supported resource limits, not an OS sandbox or rollback guarantee. |
| Modern roots | Same immutable configured disclosure ceiling and existing prepare/commit/cancel review. Modern declares roots without legacy listChanged and sends no obsolete notification. Empty selection returns roots:[] on the next owned input request; changed revision invalidates untransmitted old disclosure. No cwd inference, file read, tool grant or new roots. | Main's native root/terminal requirements are not replaced by manager tests. |
| Modern sampling | Existing original-loop owner, exact input/context/output review and selected same-adapter executor. Separate `handle_input` handles modern result arrays even without tool definitions. Declared tools/context subcapabilities remain independent of permission. Callback/host withdrawal is checked again before the continuation, including after another sibling review. | Actual configured model limitations remain enforced; no second model/tool loop or parent-history fallback. |
| Modern form / URL | Existing elicitation two-phase owner gains `handle_input`; events carry `protocol:2026-07-28`. Form request schema dialect/annotation metadata is preserved and compiled. Modern URL accepts no legacy completion ID, creates no pending notice registry, and response acknowledgment is not evidence of external success. Explicit modern null content refuses instead of borrowing legacy null-normalization compatibility. | Existing explicit URL opener remains separately guarded. No browser/auth call was made by these MCP tests. |
| Modern TUI elicitation | `McpElicitationPanel` recognizes the protocol marker and URL without an ID; full-paint notice explains that the original continuation checks the outcome. Distinct open and acknowledgment gestures, one-use open, focus/resize/press/digest gates remain. No synthetic completion ID/event. | Native host event relay and the outer browser job must retain this marker and final document; delegated tests are pending at this checkpoint. |
| Legacy task correlation | Existing create/get/list/result/cancel/expiry/withdrawal machinery retained. Task-status notifications now include registry-assigned related-task metadata, matching the original result correlation. Both checked by pinned independent schemas. Even a current local task ID cannot authorize a new sampling/elicitation operation or inherit its reviewed output/model selection. | No remote task ID is a host lease. No child job or SDK compatibility schema is misrepresented as an outgoing/server task runtime. |
| Related-task conversation scope | `ClientTasksCapability.requests` describes receiver-side sampling/elicitation task creation; `ServerTasksCapability.requests.tools.call` is the opposite direction. The installed client path does not originate a task-augmented tools/call or an automatic nested sampling tool loop. All admitted current receiver task work remains under its original registry worker; controls identify that task, status/results carry its assigned correlation. A new peer sampling/form request must obtain independent review, not attach via `_meta`. | This resolves the named client-capability contract without inventing an unsupported server-task conversation. It does not claim generic task-runtime conformance or waive any separately requested server-task feature. |
| Sampling tool/schema shapes | Empty catalogs and optional auto/none choice now preserve their valid wide response shape; required still needs a nonempty approved tool subset and schema-valid proposal. Result validation cannot gain a wide legacy shape merely because the executor inserted an artificial empty catalog. Official request/result schemas validate these cases. | Schema size/dialect/reference restrictions remain explicit; no remote schema retrieval or silent constraint discard. |
| Scoped structured context | Reviewed `structuredContent` now reaches model wire as labeled exact JSON text as well as typed neutral details. Previously the neutral details-only field was omitted by wire encoders. Present null/false/array/object values survive; absent does not invent null. Only protocol payload is serialized, never arbitrary host details. Existing server provenance, redaction, completed-turn, pairing and frozen-context guards remain. | This changes model-input representation of already reviewed data, not disclosure scope. |
| Modality / model representations | Native text and user/tool-result images are checked against the selected model before execution; unsupported MIME/count/text-only model refuses without adapter calls. Explicit custom executors can retain typed audio or assistant images through full input/output review; the neutral native adapter explicitly refuses content it cannot represent, without prose conversion/drop/fetch. | No claim that a text/image model produces audio or that an offline adapter is a live provider. Real-provider verification remains UI-052. |

The modality row includes a **separately main-owned AI fix**, not an edit by this
MCP owner: `ai/protocols/openai_responses.py` now preserves ordered typed image
output in the original tool call for an image-capable model;
`ai/proxy_responses.py` preserves the corresponding inbound typed output without
remote URL fetch. Main reported182passed/no skips1.16s at
`/tmp/ui048-responses-images-current.log` (new5 plus existing protocol/proxy tests),
using locally installed official OpenAI SDK type source. This worker additionally
exercises actual MCP sampling→neutral context→Responses typed wire, alongside
Anthropic/Responses structured JSON serialization. No live provider proof is claimed.

### Test ownership, failures retained, and external separation

New dedicated tests: `test_mcp_input_requests.py`, `test_mcp_input_official.py`,
`test_mcp_sampling_modalities.py`, `test_mcp_related_task_scope.py`, and
`tui/tests/test_mcp_elicitation_modern.py`; serving fixture
`fixtures/mcp_official_sdk/input_peer.mjs`. The fixture uses the installed SDK's
actual modern server, not a reimplemented schema/serving stack. It registers
roots/sampling/form/URL as each of tools, resources and prompts. Private loopback
HTTP and owned stdio cover all24 method/family/transport combinations, plus real
modern root withdrawal and stalled-provider cancellation/family detach/disconnect.

Focused modern/modal integration: **213passed/no skips25.04s**,
`/tmp/ui048-modern-modal-integrated.log`. Earlier focused gate88passed/no skips29.98s,
`/tmp/ui048-negotiation-focused.log`, included task correlation/control and old stdio
failure regressions. New shape/modal cases were added after those snapshots;
the final current aggregate receipt will be recorded below.

Preserved failures, not relabeled as passing:

- `/tmp/ui048-negotiation-initial.log`: legacy held-validator fixture did not accept
  the new optional keyword; the unchanged legacy code path now calls its original
  signature, retaining the cleanup assertion.
- `/tmp/ui048-input-official.log` earlier red showed SDK form `$schema` rejection;
  the real request is now accepted with the declared dialect retained/validated.
  The initial HTTP resource test used the wrong mirror name; corrected to existing
  production `Mcp-Name` before further interoperability checks.
- `/tmp/ui048-negotiation-integrated.log`:8failures/684pass on an earlier code
  snapshot (six actual normalized-form failures and two stdio loss/cancellation
  semantics regressions). Both production issues were repaired, not skipped.
- `/tmp/ui048-structured-wire-red.log`:4failures/1pass prove omission of reviewed
  null/boolean/array/object structured data from actual wire encoding.
- `/tmp/ui048-tool-empty-red.log`:four real empty-catalog/choice failures; a fifth
  test had the wrong AI helper import, corrected to the actual
  `context_to_responses_input` name, not counted as a production gap.

All current commands use `uv run --no-sync` and explicitly
`pytest -c pyproject.toml` from the root. Tests use owned local peers only. No
installs, live auth/model/provider/MCP deployment operations, browser launch,
publication or user-service restart. Documentation lookups returned no usable
content; source/interop evidence is the existing pinned installation.
`hatchling` is absent from the existing workspace interpreter, so no artifact
build was attempted with an install; final artifact verification remains main's
separate frozen-source gate. This is a tooling limitation, not an implementation
scope deferral.

Exact still-pending integration at this checkpoint: main/sidecar actual Coding and
Claw browser `resource`/`prompt` operations must keep the outer owned job alive
through review/answer, preserve the protocol marker, retain the final document
across review-close events, and verify current Linux physical acceptance. No
application or `extensions/api.py` edits were made by this worker. Final frozen
whole-root/static/artifact/examples and Linux safety gates remain main-owned.
Real provider behavior(UI-052), live MCP/OAuth deployments(UI-057) and externally
managed desktop/browser/native credential systems are separate external evidence,
not local passes. Non-Linux native validation is the only carried native-platform
deferral; this reconciliation introduces no additional scope waiver.

### Final optional metadata correction

`/tmp/ui048-sampling-metadata-red.log` records five real failures: string provider
metadata never reached the selected adapter, while unsupported non-string metadata
was silently ignored. `SamplingExecutor` now forwards an explicitly reviewed string
map through neutral `StreamOptions.metadata`, never as a model name, routing header,
permission or prompt text. Non-string values remain lossless in the generic codec/
custom-executor contract but the native string-map adapter refuses them **before**
context capture or any model call. This is not a claim that every upstream provider
implements vendor-specific metadata semantics. The model/preferences/tool/context
ceilings remain unchanged. Direct modern sampling/elicitation codecs additionally
refuse removed task augmentation, not merely the transport dispatcher.

Latest shape/cancellation/model-wire focused gate: **56passed/no skips1.08s**,
`/tmp/ui048-final-shape-focused.log`. This is a subordinate checkpoint, not a
replacement for the current integrated receipt below.

Native sidecar has now reported **32actual Coding/Claw browser cases passed36.22s**,
`/tmp/ui048-browser/initial3.log`, across both transports, resource/read+prompt/get,
and roots/sampling/form/URL. No production host gap was observed in that first gate;
the previous native-browser pending statement is superseded for those cases only.
Late review-close ordering and current Linux physical modern-browser acceptance are
still being exercised by that owner and are not silently counted as passed here.

### Frozen MCP handoff receipt — 2026-10-11 15:53+08

**794passed, no skips,108.64s**, root-config current integrated gate:

```bash
XDOG_MCP_OFFICIAL_SDK_ROOT=/tmp/xdog-mcp-pass68.lw1gcs \
  uv run --no-sync pytest -c pyproject.toml -q \
  packages/agent/tests/test_mcp*.py packages/tui/tests/test_mcp*.py \
  packages/ai/tests/test_responses_tool_images.py
```

Receipt: `/tmp/ui048-negotiation-final-integrated.log`, exit0. Focused root Ruff
and strict mypy(**35source modules**) clean in
`/tmp/ui048-negotiation-final-static.log`. All13 MCP/panel production files remained
byte-identical across the final gate, checked against
`/tmp/ui048-negotiation-production-snapshot.json`. Pinned independent source hashes
are in `/tmp/ui048-negotiation-pinned-source.json`; `git diff --check` clean.

Production paths owned by this worker:

- `packages/agent/src/xdog/agent/mcp/`: `input_requests.py`(new),
  `client_capabilities.py`, `stdio.py`, `http.py`, `manager.py`, `sampling.py`,
  `sampling_owner.py`, `sampling_executor.py`, `elicitation.py`,
  `elicitation_owner.py`, `reverse_requests.py`, `task_notifications.py`.
- `packages/tui/src/xdog/tui/components/mcp_elicitation_panel.py`.

Test paths: new agent `test_mcp_input_requests.py`, `test_mcp_input_official.py`,
`test_mcp_sampling_modalities.py`, `test_mcp_related_task_scope.py`; updated
`test_mcp_roots.py`, `test_mcp_task_notifications.py`,
`test_mcp_task_official_contract.py`; new
`fixtures/mcp_official_sdk/input_peer.mjs` and TUI
`test_mcp_elicitation_modern.py`. The old roots-only modern-empty assertions were
updated for the implemented per-request roots contract; bootstrap-empty,
unsolicited-refusal, selection ceilings and disconnect withdrawal remain tested.

The bounded MCP implementation/codec/interop work described in the matrix is now
handed off; **UI-048 aggregate completion is not asserted by this worker**. Exact
remaining local acceptance is the sidecar's late-close/native Linux physical
modern-browser gate plus main's final frozen whole-root/static/artifact/example
and Linux safety integration. The32-case native class/socket/browser gate already
passed as reported above. No Coding/Claw application files, `extensions/api.py`,
central ledger or other worker's AI files were edited here. No new requirement ID,
platform deferral or scope waiver was introduced. External provider/deployment/
desktop evidence remains explicitly separate, not represented by the794local passes.

### Native-browser closure received — 2026-10-11 16:08+08

The browser sidecar now reports its **127passed/no skips132.63s** integrated gate
and an overlapping36-case cold-owner/negative/browser/roots gate passing10.63s.
`docs/agent-ui-modern-browser-audit.md` records the exact files, logs and hashes.
Coverage includes56 natural/late-close class/socket browser cases,12 cancellation
cases,16 selected-owner Linux physical cases, and both actual cold Claw arm orders.
The cold Claw issue was not deferred: an explicitly owned idle-session bootstrap
and correlated replies were implemented by the authorized Claw owner. Direct
empty-owner reverse-capability admission remains denied.

Accordingly, this audit's native/browser/current-Linux pending clause is now
satisfied by that sidecar evidence. No further Coding/Claw patch is requested by
this MCP owner. Aggregate ledger disposition and the final frozen complete-root/
static/artifact/examples/Linux lifecycle gates remain main-owned; external provider,
deployment and non-Linux native evidence remain separated as previously stated.

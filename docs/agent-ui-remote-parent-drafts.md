# UI-040 remote parent drafts — implementation checkpoint

Updated: 2026-10-11. **UI-040 remains 待实现.** This document refines one existing
requirement; no new requirement IDs or completion waiver.

## Coding RPC currently implemented

Ready advertises independent capabilities: `parent_saved_drafts`,
`parent_saved_images`, `parent_saved_save`, `parent_saved_discard` (version1).
Each must be explicitly negotiated via `rpc_hello` using exact integer0/1.
`parent_draft_capabilities` returns original session/revision, connection-only
`parent_draft_owner` and `parent_draft_generation`. A change of capabilities revokes
pending jobs, prepared receipts and catalogs. Old/default clients acquire no access.

`parent_draft_control` requires this exact envelope, nonreusable `request_id`, action,
and (except catalog) client composer `{revision,sha256,empty}`. The composer digest is
SHA256 of JSON `draft_receipt(text,attachments)` with sorted keys and compact separators.
The **client** owns that composer; the server cannot attest it actually stayed empty
or a human painted the review. The required shipped client recovery gate is not yet
implemented and is not substituted by a client self-report.

| Action | Current behavior |
| --- | --- |
| catalog | Private parent-snapshot metadata only. Validated disk rows/id/hash; original catalog envelope retained. |
| save_prepare | Explicit client text/actual image bytes,64KiB text/max4 images/shared validated image limits. No paths/model/tools/policy. Exact supplied composer digest must match content. Full review body plus image digest/size metadata. Four prepared receipts/56MiB total,30-second deadlines. No disk save yet. |
| save_confirm | Exact JSON-typed review/composer and boolean True; original session/revision/generation. Single-use receipt consumed before disk admission. Shared private create-only/fsync store, no execution/history/composer change. |
| read | Exact original catalog id/hash and empty-client composer receipt. Checks parent source action. Separate begin review/chunks/end transport,64KiB chunks/exact image hashes; image capability independently required. Returns editing data only. No client adoption yet. |
| discard_prepare | Full content/image metadata/disk identity review bound to client composer; does not delete yet. |
| discard_confirm | Original receipt/exact JSON review/composer, boolean True; shared store exact on-disk hash check before unlink, single-use. Disk snapshot only, no composer/queue/history change. |
| review_cancel | Retire prepared receipt, no disk mutation. |
| parent_draft_cancel | Only original current request/envelope. Cancellation waits actual codec/filesystem worker settlement; cannot free capacity while worker still owns work. |

Root path is the host's existing session-store `saved-parent-drafts`, same as native
ParentDrafts; never a remote-supplied path. Existing snapshots' source sessions are
provenance, not active history or permission. Read/save/discard are original idle
session/child-safe operations and interlock with RPC prompt/navigation/extensions/
terminal controls. Request responses bounded10s; binary sequence bounded10s.
Prepared reviews/catalogs revoked on disconnect; close waits actual owned job even
if its caller is canceled. No automatic retry/replay after uncertain mutation/delivery.

## Current evidence

- `packages/coding/tests/test_rpc_parent_drafts.py`: ordinary old client refusal,
  full save/catalog/read/discard, changed/nonempty composer, single-use receipts,
  foreign/session-revision/withdrawal refusal, exact bool-vs-int JSON review, independent
  binary manifests/chunks/hash and no fallback, held-worker withdrawal cleanup.
- Actual CLI uses existing private offline peer, explicitly negotiates parent flags,
  saves only after exact receipt, catalogs metadata, zero model marker, graceful quit.
- Final6-file102 passed/no skips58.02s `/tmp/ui040-parent-final-integrated.log`,
  handle81270 exit0; actual existing RPC extension/child/terminal/image and shared
  private-store regression included. Not a new physical-terminal claim.
- Root Ruff/seven-namespace strict mypy505 clean `/tmp/ui040-parent-final-static.log`.
  Coding wheel/sdist `/tmp/ui040-parent-artifacts`, both RPC modules byte exact/hash
  `/tmp/ui040-parent-source.json`. Initial mypy/line/import diagnostics corrected.
  First selection typo no tests ran exit4 retained `/tmp/ui040-parent-selection-error.log`.

## Remaining original UI-040 acceptance — not waived

1. Client-owned receiver verifies owner/generation/revision/end/hash/sequence/budget,
   complete full-painted digest review and single-use empty/unchanged composer
   adoption including images; no automatic resend/model/policy/history changes.
2. Applicable Claw private-socket counterpart with independent negotiation, original
   group/session/view ownership, binary delivery/backpressure, revocation and cleanup.
3. Actual remote host/client integration, held-disk and transport failure matrix,
   Linux physical terminal verification of recovery/save/discard, no approval bypass.
4. Final frozen complete-root/static/distribution/example and Linux safety gates.

No publishing/user service restart, dependencies downloaded, live provider/auth or
Claw memory/search/scheduler work. Windows/macOS native validation alone deferred;
current Linux requirements and all implementation remain intact.

## Client receiver/full-review checkpoint (2026-10-11)

New shared `parent_draft_receiver.py` expects original request/session/revision/owner/
generation, empty composer revision/digest and catalog snapshot identity/hash. It
accepts only exact begin/chunk/end fields with sequential64KiB chunks, at most4 images/
40MiB aggregate, fixed manifest size/MIME/hash,4 pending/256 nonreusable requests,
30s transfer deadline. Private temporary spools close on error/cancel/expiry/close;
unsolicited or late frames never open a receiver. Finish runs off UI/event loop,
checks all bytes/hashes/decoded image MIME and requires matching successful final RPC
read receipt (end alone is not success). It returns an immutable draft with conservative
read-only settings, not an adopted composer or resumed agent.

ParentRecovery creates an exact digest/full-painted shared review panel. Enter before
paint, repeat/release, stale resize/focus and owner/composer changes cannot adopt.
Recovery is one-use across even recreated panels; cancel permanently revokes it,
review expires30s. Confirmation checks current original scope and empty unchanged
composer; atomic host callback must recheck expected receipt before changing local
editor/images. No submit/model/history/permission action is involved. This shared
component exists but ordinary ChatApp remote-parent controls are not integrated yet.

Evidence: initial15 receiver tests pass0.21s; expanded receiver+actual RPC images/
review/client-CAS33 pass1.90s. Actual CLI receipt→begin/end→successful completion→
full review→editing data verified, no model marker. Final focused26 pass1.81s
`/tmp/ui040-parent-receiver-focused-fixed.log`; final8-file
`/tmp/ui040-parent-receiver-current-final.log` exit0 includes old RPC/child/terminal/
image/private-store regressions. Root Ruff/seven-namespace strict mypy506 clean
`/tmp/ui040-parent-receiver-final-static.log`; TUI/Coding wheel-sdist
`/tmp/ui040-parent-receiver-artifacts`, receiver exact bytes/SHA256
`/tmp/ui040-parent-receiver-source.json`. Initial expiration test retried an already-
used panel (correctly inert); use newly painted expired panel, assertion retained.

Remaining UI-040: applicable Claw socket and actual shipped remote UI wiring,
complete save/discard review integration, physical Linux recovery/failure matrix;
final frozen full-root gates. Shared receiver unit/actual Coding CLI evidence does not
substitute for that integration. UI-040 remains待实现, no scope reduction.

## Claw socket catalog/read checkpoint (2026-10-11)

New ParentDraftConnection is independently negotiated by ping parent_saved_drafts/
parent_saved_images (exact0/1), connection-local owner/generation; ordinary clients
have no grant. Save/discard are explicitly0/unimplemented here, not falsely advertised.
Original group/session/view/source revision binds metadata catalog and exact snapshot
hash reads. Host config.data_dir/saved-parent-drafts only, no caller path. At most8
catalog views/4 jobs/256 nonreusable requests; read reserves original idle group,
checks child/compaction/dialog ownership and original source revision. Binary begin/
64KiB chunk/end and final success receipt match shared receiver. Exact empty client
composer receipt required, still not a claim of client adoption or physical consent.

Capability withdrawal clears catalogs/cancels jobs; reader remains responsive during
actual filesystem-worker settlement. Group reservation only releases after worker
completion, including cancel-before-first-step through done callback. Disconnect waits
owned jobs despite caller cancellation. Original snapshot remains unchanged. Actual
private socket two image/text paths feed shared receiver/full-painted recovery review,
foreign socket/withdraw generation refuse, held catalog worker withdrawal proves idle
reservation stays held. No model/history/policy changes or user service restart.

Final7-file80 passed/no skips6.24s `/tmp/ui040-claw-read-integrated.log`, handle23741
exit0; includes actual sampling/elicitation gateway regressions, shared receiver/private
store and Coding parent RPC. Root Ruff/strict seven-namespace mypy507 clean
`/tmp/ui040-claw-read-final-static.log`; Claw wheel/sdist handle49264 exit0
`/tmp/ui040-claw-read-artifacts`; both changed production modules exact bytes/SHA256
`/tmp/ui040-claw-read-source.json`. Initial type/line diagnostics corrected.

UI-040 remains待实现: Claw save/discard reviewed mutation, actual ChatApp remote
parent UI controls/adoption/save-discard integration, physical Linux terminal and
full failure matrix still required. Read-only socket work does not waive those gaps.

## Claw reviewed save/discard checkpoint (2026-10-11)

ParentDraftConnection now independently negotiates parent_saved_save/discard flags
(default0), revokes prepared reviews on any capability change, and implements
save_prepare/save_confirm/discard_prepare/discard_confirm/review_cancel. Original
connection/group/session/view/source revision binds reviews; request ID may change
between phases but ownership may not. Four prepared reviews/56MiB aggregate/30s
retention; exact JSON-typed composer/review and boolean True required. Save prepares
validated actual text/image bytes and checks client composer digest; no disk write
before confirmation. Confirmation consumes review before mutation and uses existing
private create-only/fsync store with owner/deadline recheck. Discard loads the original
catalog hash, reviews complete text/image metadata, confirms exact unchanged receipt,
then deletes only that disk snapshot under on-disk hash fence. No host composer,
model history, tool or permission state changes. Unknown paths/config fields refused.

Real private-socket tests cover text/image save/discard, no disk before approval,
foreign view/changed composer/reused receipt refusal, boolean-vs-integer JSON mismatch,
withdraw/re-enable rejecting old receipt. Existing reader-responsive held catalog
cleanup tests retained. These are server/receiver integration tests, not proof that the
ordinary ChatApp paints save/discard reviews; that integration is still required.

Final7-file83 passed/no skips6.32s `/tmp/ui040-claw-mutation-integrated.log`,
handle36121 exit0. Root Ruff/seven-namespace strict mypy507 clean
`/tmp/ui040-claw-mutation-final-static.log`. Claw wheel/sdist
`/tmp/ui040-claw-mutation-artifacts`, handle33940 exit0; both changed modules exact
bytes/SHA256 `/tmp/ui040-claw-mutation-source.json`. Initial type/line diagnostics
corrected, not passing evidence. No publication/download/live provider/auth/browser
or user-service restart. UI-040 stays待实现: ordinary ChatApp control/review/adoption,
physical Linux recovery/save/discard and expanded failure/cleanup matrix remain;
final frozen full-root gates remain required.

## Actual ChatApp remote recovery and Linux terminal (2026-10-11)

ChatApp now exposes `/draft remote`: independently negotiated parent catalog/image
read, original connection/session/output-context view, explicit metadata selection,
strict shared receiver and background image finish, complete full-painted exact review,
then local empty/unchanged composer CAS. Editor on-change and attachment callbacks
advance a revision (including empty→changed→empty ABA). Adoption preserves actual
image bytes and text, checks retained-image capacity and lossless editor sanitization,
changes no model/history/permission and never submits. Closing/reconnecting/changed
owner/focus/protected modal/timeout revokes delivery. Codec worker retains its spools
until completion; terminal close does not close files owned by an active decode.
New RemoteParentDraftPanel only provides metadata selection and exact recovery review;
it grants no disk write/model/tool/approval authority.

Initial6 actual-UI failures `/tmp/ui040-chat-read-current.log` exposed locally added
_connection_generation in wire frames: host validates generation then excludes that
local transport stamp before strict shared decoding. Corrected6 pass1.45s
`/tmp/ui040-chat-read-fixed.log`. Actual UI text/image recovery/change-ABA/close tests
retain empty composer/parent history/no model/source snapshot assertions.
Physical private Linux tmux text/image2 pass/no skips2.96s
`/tmp/ui040-chat-read-physical.log`, handle99463 exit0: real `/draft remote`, catalog,
full review/resize, image/text restoration without submission, visible cursor/main
buffer/native scrollback/termios recovery. Save/discard physical flows remain pending.

Final8-file91 pass/no skips8.96s `/tmp/ui040-chat-read-integrated.log`, handle93134
exit0. Root Ruff/seven-namespace strict mypy508 clean
`/tmp/ui040-chat-read-final-static.log`. Claw/TUI wheel-sdist
`/tmp/ui040-chat-read-artifacts`, handle80657 exit0; new panel and changed ChatApp exact
bytes/SHA256 `/tmp/ui040-chat-read-source.json`. No live provider/auth/browser,
downloads/publishing or user-service restart. UI-040 remains待实现: actual remote
save/discard full review integration, broader transport/disk/worker failure matrix and
current-Linux physical save/discard; final frozen complete-root gates not replaced.

## ChatApp remote save/discard full review checkpoint (2026-10-11)

Actual ChatApp now negotiates independent save/discard, prepares save directly from
original composer (no preliminary catalog reservation race), and supports x on the
remote catalog to prepare disk discard. Exact original scope/composer receipt, full
text/image digest/size metadata and selected catalog identity/hash/label/images are
validated before a whole-data full-painted review. Only explicit fresh Enter confirms;
changed composer/view, unpainted/repeat/release and close cannot mutate. Confirmation
clears local review authority before admission; close cannot imply disk rollback.
Closing an unconfirmed review sends review_cancel. Local editor/images remain intact
and no model/history/policy changes. Protected remote panel input cannot invoke unrelated
extension shortcuts or parent actions. Local generation stamps are not wire authority.

A dedicated configurable `remote_save_draft` host shortcut (Ctrl+Alt+Shift+S; test
remapped F8) preserves the parent composer, unlike submitting a slash command from
that same editor. Coding native consumes this gateway-only action with an explicit
unsupported notice, never as editor data/model submission. Shared keymap/remote
editable-key policy excludes it from editable events. Existing local save unchanged.

Current save transport limitation: ChatApp explicitly refuses image/text uploads over
its400KiB conservative single-frame ceiling rather than truncating. Large-image remote
save upload contract remains unfinished under UI-040; this is NOT a scope waiver or
permission to close the requirement. Gateway's existing1MiB line limit is not expanded.

Initial actual-UI3 failures `/tmp/ui040-chat-mutation-current.log` exposed concurrent
catalog and save_prepare reservation admission. Save now opens remote view without
issuing catalog; original composer survives. Fixed6 pass1.39s
`/tmp/ui040-chat-mutation-fixed.log`. Expanded actual UI/global keys/physical45 pass/
no skips6.55s `/tmp/ui040-chat-mutation-expanded.log`. Physical private Linux matrix
now4 paths: text/image recovery, actual remote save (configured host gesture preserves
unsent text), and selected disk discard. Full review/resize/no early disk effect,
no model/history change, cursor/main-buffer/native-scrollback/termios verified;
`/tmp/ui040-chat-mutation-physical.log`4 pass5.17s. No false large-image physical claim.

Final10-file134 passed/no skips20.85s
`/tmp/ui040-chat-mutation-final-integrated.log`, handle9644 exit0, includes independent
RPC editable-key regression and shared keymap/private snapshot/receiver plus actual
socket/native UI. Root Ruff/strict seven-namespace mypy508 clean
`/tmp/ui040-chat-mutation-final-static.log`; Claw/TUI/Coding wheel-sdist
`/tmp/ui040-chat-mutation-artifacts`, handle35931 exit0; four changed production
modules byte-exact SHA256 `/tmp/ui040-chat-mutation-source.json`. No download,
provider/browser/auth/publishing/user-service restart.

UI-040 remains待实现 for full-size image uploads and expanded transport/disk/worker
failure matrix, current-source broader integration/final gates. Existing full scope
retained; repeated audit paragraphs are not additional requirement IDs.

## Chunked full-size parent image save (2026-10-11)

Historical400KiB save limit above is superseded: ChatApp uses independently negotiated
parent_saved_uploads for actual images,64KiB chunks with one outstanding request and
exact partial/ready acknowledgments. Original connection/group/session/view/revision/
capability generation binds every upload and later reference; supplied paths are not
accepted. Final image hash and PNG/JPEG/WebP validation precede readiness. Server
retains existing10MiB/image,4 images/40MiB total capacity;256 nonreusable upload IDs,
4096 nonreusable chunk request IDs and serialized buffer validation. Chunk validation
runs through settled worker ownership, never blocks socket reader, and group reservation
stays held through actual thread completion even when capability is withdrawn.

Uploaded refs consume exactly once before save preparation; name/MIME/content hash
must match original composer. Preparation still requires full exact save review before
disk confirmation. Images stay in the editable client composer; no model/history/
permission change. Closing an uploading view sends owned discard controls, incomplete
uploads have120s timers, capability changes detach/revoke old buffers and timer ownership,
disconnect waits workers then clears remaining uploads/timers. No automatic upload/
response replay after failure. Slow validation owns its buffers before timer cleanup;
new controls cannot mutate buffers during a validation worker. Existing gateway1MiB
line limit is unchanged, no truncation or single oversized wire frame.

Actual ChatApp1MiB and10MiB-boundary image save verifies exact disk bytes/full review/
unchanged composer/zero-model. Fixture uses a shared-validator-accepted PNG header plus
inert payload; this is transport/storage evidence, not live decoder/provider evidence
(UI-052 remains missing external). Actual private socket negatives cover foreign view,
bad chunk index/hash, capability withdrawal and held-validator cleanup/reservation.
Physical Linux matrix now5 paths: text/image recovery, text save,1MiB-image save,
disk discard. Exact image hash/body preserved after physical full-review/resize and
termios/main-buffer/cursor recovery.30 socket/physical checks9.72s
`/tmp/ui040-upload-physical.log`; final10-file142 pass/no skips33.59s
`/tmp/ui040-upload-integrated.log`, handle70911 exit0. Root Ruff/seven-namespace strict
mypy508 clean `/tmp/ui040-upload-final-static.log`; Claw wheel/sdist handle86658
exit0 `/tmp/ui040-upload-artifacts`; all three changed production modules byte exact
SHA256 `/tmp/ui040-upload-source.json`. Initial type/import/line diagnostics corrected.
No downloads/live provider/browser/auth, publishing or user-service restart.

UI-040 stays待实现 for expanded disk/transport/decode/receipt-loss failure matrix,
remaining acceptance reconciliation and current-source final full-root gates. Chunked
upload closes the named single-frame image implementation limitation without adding
another stable requirement ID or waiving remaining work.

## Failed upload ACK, queued deadline and lost commit receipt (2026-10-11)

Red2 tests `/tmp/ui040-failure-red.log`, handle54806 exit1, found real ownership
windows: successful image assembly survived failed upload ACK; Coding RPC review
could expire while save waited in disk work and still publish. Upload acknowledgment
now has bounded write/whole-delivery finally: unknown ready/partial reference, owner
record and deadline timer retire on failed/canceled/timed-out ACK, upload ID remains
nonreusable. Original settled validation/group reservation behavior unchanged. Coding
RPC save/discard now rechecks review deadline in the store's immediate publication/
unlink alive guard, not merely before entering a blocking worker. Expired queued save
leaves no published snapshot or private temporary file; no model/permission change.

Additional both-server tests intentionally lose a committed save completion receipt.
Disk effects remain (not falsely rolled back), review was consumed before admission,
fresh request cannot replay it or create a duplicate. Errors do not expose private
transport details. This is uncertainty handling, not reliable remote delivery/rollback.

Focused38 passed/no skips14.89s `/tmp/ui040-failure-expanded.log`. Final10-file146
passed/no skips34.82s `/tmp/ui040-failure-integrated.log`, handle27491 exit0 includes
actual UI,1MiB/10MiB upload boundaries and5 physical Linux recovery/save/discard paths.
Root Ruff/strict seven-namespace mypy508 clean `/tmp/ui040-failure-final-static.log`;
Claw/Coding wheel-sdist `/tmp/ui040-failure-artifacts`, handle15664 exit0; both modified
server modules byte exact SHA256 `/tmp/ui040-failure-source.json`. Initial static line
length corrections recorded, red tests retained. No downloads/live provider/auth/
browser, publishing or user-service restart.

UI-040 still待实现 pending finite acceptance reconciliation: complete disk/hash-
changed/delivery/cancel race matrix, retained-byte lifecycle and broader current-source
integration. No original requirement waived or repeated OPEN counted twice; final frozen
full-root/Linux safety/terminal/transport/static/artifact/example gates remain required.

## Prepared review automatic retirement (2026-10-11)

Both remote parent controllers now schedule original-loop30s timers for prepared
save/discard receipts. Expiry actually removes private prepared bytes/metadata without
another command. Cancel/confirm/capability withdrawal/disconnect cancel timers and
retire records. Timer callbacks recheck monotonic deadlines; already-confirmed disk
work remains owned by its consumed receipt and immediate publication deadline guard.
Delivery of preparation is separately bounded: failed/canceled/timed-out review write
retires the unknown prepared receipt in finally, no replay or private bytes kept until
connection teardown. Committed disk effects still are not rolled back on receipt loss.

Initial red selection covered only Claw due to a name filter; one failure retained
`/tmp/ui040-review-lifecycle-red.log`, handle56675 exit1. Corrected two-controller
selection2 passed1.01s `/tmp/ui040-review-lifecycle-current.log`; no claim the first
selection exercised both. Automatic expiry tests schedule the actual production timer
callback against a shortened test deadline, assert records/timers removed without a
next command; withdrawal asserts timer cancellation. Original stored snapshots and
model/history/permissions remain untouched.

Final8-file134 passed/no skips32.89s `/tmp/ui040-review-lifecycle-integrated.log`,
handle39622 exit0 includes actual native UI, full-size upload boundaries and5 physical
Linux paths plus shared store/receiver/keymap/RPC regressions. Root Ruff/seven-namespace
strict mypy508 clean `/tmp/ui040-review-lifecycle-final-static.log`; Claw/Coding build
handle25184 exit0 `/tmp/ui040-review-lifecycle-artifacts`; both controller source bytes
exact SHA256 `/tmp/ui040-review-lifecycle-source.json`. No downloads/live provider/
auth/browser, publishing or user-service restart.

UI-040 stays待实现: finite hash-change/cancel/delivery acceptance reconciliation and
broader source-final integration still required. Prepared-byte retirement closes this
named lifecycle gap; it is not an additional requirement ID or full-root substitute.

## Hash replacement / transport / cancellation acceptance (2026-10-11)

New `test_remote_parent_hash_changes.py` exercises **both actual controller classes**,
not only the shared store. It validates these sixteen paths:

| Window (each Coding RPC + Claw controller) | Verified result |
| --- | --- |
| Catalog then on-disk replacement, read old hash | No binary begin/recovery, old receipt refuses, replacement remains intact. |
| Discard review then on-disk replacement | Delete refuses; original review consumed once, replay refuses, replacement remains intact. |
| Read delivery fails at begin/chunk/end | No successful completion/adoptable receiver; temporary spools released, original saved file unchanged. |
| Disk save raises error | No snapshot, confirmation consumed, no automatic retry or model/history mutation. |
| Close before job first step | No disk effects, original controller cleanup/Claw reservation released. |
| Close during held disk save; close caller canceled | Wait actual thread; cannot publish after revocation; no snapshot/private temp files, permissions preserved, reservation released only after settlement. |

Existing CLI/socket tests separately prove actual wire routes; these new injected
failure cases are controller-level evidence, not real filesystem hardware faults or
physical power loss. Shared store replacement is explicit test-owned update, never an
unrelated workspace/user snapshot. Diagnostics returned by controllers do not disclose
private replacement/file/transport contents. No deletion/weakening of Linux tests.

Focused sixteen checks passed1.29s `/tmp/ui040-hash-cancel-current.log`; final eleven-
file166 passed/no skips34.31s `/tmp/ui040-hash-cancel-integrated.log`, handle53600 exit0.
Root Ruff/seven-namespace strict mypy508 clean `/tmp/ui040-hash-cancel-static.log`.
Production source unchanged this checkpoint; previous source-exact artifacts remain
relevant to those unchanged modules, not falsely reported as newly built artifacts.
Current integration includes actual remote UI, large-image boundaries and five Linux
physical paths. No downloads/provider/browser/auth/publishing/user-service restart.

### Finite remaining UI-040 verification / implementation

These refine UI-040, not additional stable IDs, and replace the vague 'broader matrix'
remainder in earlier paragraphs:

1. Explicit per-view cancellation of **in-flight** Claw read/prepare/upload when the
   remote UI closes (connection survives), with original job scope/ownership check;
   existing review_cancel only cancels an already-delivered review. Current connection
   close and timer cleanup are not a substitute for this implementation.
2. Held client decode plus UI close/reconnect: wait actual decode/filesystem ownership,
   block late adoption, release retained binary data; verify actual native user flow.
3. Physical Linux Coding RPC client receiver recovery using shipped receiver/full-
   review component (existing actual CLI/offline UI tests are not physical-terminal
   proof), and physical cancellation/error recovery of remote operations.
4. Reconcile final receipt/status/retained-image capacity against original complete
   UI-040 acceptance; run named all-host integration and final goal-level frozen gates.

All other implementation and named acceptance evidence above remains recorded, not
recounted as repeated OPEN. UI-040 remains待实现 because item1 is a real implementation
gap and remaining evidence is not yet complete. Cross-platform native validation alone
remains deferred; current Linux work is not waived.

## In-flight per-view revocation (2026-10-11)

Remaining item1 above is now implemented: ChatApp closing a remote parent view sends
view_cancel bound to original connection owner/generation/group/session/view. Socket
reader synchronously marks that view revoked and signals matching read/prepare/upload
jobs, schedules cleanup outside reader, and permanently refuses subsequent admission
on that old view. Cleanup waits actual workers, retires matching reviews/timers/catalogs/
uploads and never cancels sibling views. Shared upload-buffer cleanup waits other
validation ownership without canceling it. Bounded256 revoked views, idempotent repeat
cancel, whole-connection close also awaits these cleanup jobs. A confirmed operation
still may have committed effects; view cancellation is not rollback/replay authority.

Actual ChatApp holds remote disk load, closes view with socket intact, confirms group
reservation stays held until thread returns, no late editor/image adoption, new view
reads normally. Other-controller tests prove foreign owner cannot revoke and only
selected view's prepared review is retired. Held upload cancel/repeat waits actual
validation then deletes bytes/owners/timer with no ready ACK/model call. Initial mypy
local-variable scope correction recorded; no test removed/weakened.

Current9-file153 passed/no skips33.01s `/tmp/ui040-view-cancel-integrated.log`,
handle49876 exit0 includes native UI, parent hash/storage/transport matrix/full-size
upload boundaries and5 physical Linux paths. Root Ruff/seven-namespace strict mypy508
clean `/tmp/ui040-view-cancel-final-static.log`; Claw wheel/sdist
`/tmp/ui040-view-cancel-artifacts`, handle28960 exit0; both modified production modules
byte exact SHA256 `/tmp/ui040-view-cancel-source.json`. No downloads/provider/browser/
auth/publishing/user-service restart.

UI-040 still待实现. Finite remaining items2–4: held client decode close/reconnect and
retained bytes cleanup, physical Coding RPC receiver/error paths, final receipt/capacity
all-host integration reconciliation. Per-view revoke evidence closes item1 only; final
frozen complete-root/Linux gates stay required.

## Client decode close/reconnect/quit lifecycle (2026-10-11)

Remaining named item2 is now implemented/verified. Each original ChatApp decode worker
owns a cancellation event. Closing view or new connection generation revokes delivery
immediately, but does not close active spool files. After actual finish/decode returns,
worker closes original receiver and does not enqueue private ParentRecovery if revoked.
Request exit defers terminal shutdown until active decode cleanup completes, using
existing deferred-quit machinery; no new operation can borrow that worker's capacity.
Late payload cannot become a new view/composer or a replay. On successful current
finish, original scope/digest/full-paint/empty composer guards remain unchanged.

Actual private-socket ChatApp tests hold finish BEFORE reading/decoding spools, then
close view, simulate the production new-connection-generation handler, or request quit.
Files remain open while worker is held, close after release; old receiver empty/closed,
no editor/image adoption/model/history changes, original disk snapshot remains. Quit
remains pending until worker completion then finishes. Reconnect evidence exercises
actual host handler on a live test socket, not a claim of network outage integration.
First2 fixture failures `/tmp/ui040-client-decode-current.log` were waiting without
polling host events (decode had not started); fixed active UI polling, not relaxed
lifecycle assertions. Focused3 pass1.35s `/tmp/ui040-client-decode-quit.log`.

Final9-file156 passed/no skips32.87s `/tmp/ui040-client-decode-integrated.log`,
handle5619 exit0 includes prior native UI/full-size upload/failure matrix and5 physical
Linux paths. Root Ruff/seven-namespace strict mypy508 clean
`/tmp/ui040-client-decode-final-static.log`; Claw wheel/sdist handle82193 exit0
`/tmp/ui040-client-decode-artifacts`; changed ChatApp exact bytes/SHA256
`/tmp/ui040-client-decode-source.json`. No downloads/live provider/browser/auth,
publishing or user-service restart.

UI-040 stays待实现: remaining named item3 Coding RPC physical receiver/error paths
and item4 final receipt/capacity/all-host reconciliation; original final frozen full-root
requirements intact. This closes item2 only, not an extra stable requirement ID.

## Coding RPC physical receiver/error paths (2026-10-11)

Remaining named item3 now has current-Linux physical evidence: a private offline
Coding RPC process is driven by a terminal test frontend using shipped InputPanel,
ParentDraftReceiver and digest/full-painted review, not a duplicate acceptance parser.
Six paths = text/image × recovery/cancel/incomplete. Frontend explicitly negotiates
parent flags, requests original catalog hash, feeds real begin/chunk/end/result frames,
finishes decode on owned worker, and applies only reviewed recovery under empty
composer CAS. Incomplete path drops end as a transport-truncation fixture; production
receiver refuses despite a later success packet. Cancel leaves empty composer and
allows safe typing. No test sends prompt or executes a model. Restored actual image
bytes remain client editor data, not new model/permission/history configuration.

Physical resize/full-review/cursor/main-buffer/native scrollback/termios and graceful
owned subprocess/thread cleanup verified. Initial text-only3 pass6.11s
`/tmp/ui040-rpc-physical-current.log`. Expanded10-file162 pass/no skips42.36s
`/tmp/ui040-rpc-physical-integrated.log`, handle89460 exit0, includes all6 physical
Coding RPC cases plus5 actual ChatApp physical paths and source-final parent controller/
receiver/hash/disk/cancel/upload/keymap regressions. Root Ruff/seven-namespace strict
mypy508 clean `/tmp/ui040-rpc-physical-final-static.log`; changed-path diff check clean.
No production edits/build this checkpoint; no claim of new source artifacts. Tests
still use named shared-validator PNG fixture, not live provider/desktop decoder proof.
No downloads/auth/provider/browser, publishing or user-service restart.

UI-040 stays待实现 pending only named item4 acceptance reconciliation: original
receipt/image-retention capacity, complete source-final all-host integration and any
concrete mismatches found. Items1–3 now have named implementation/evidence rather
than being repeated as historical OPEN. Final goal-level frozen full-root/static/
artifact/example and current-Linux safety/terminal requirements stay intact.

## Receiver aggregate and host retention capacity (2026-10-11)

Red regression `/tmp/ui040-capacity-red.log` proved multiple pending manifests could
individually satisfy40MiB yet exceed one receiver's aggregate budget. Receiver now
sums all admitted manifests against both its40MiB ceiling and current host remaining
capacity. It refuses the new transfer without closing an unrelated original transfer.
Finish rechecks remaining capacity before decoding and releases spools on refusal.
ChatApp checks catalog image bytes before requesting read, supplies current remaining
capacity to receiver, rechecks before presenting decoded review and before adoption.
Existing current composer and unrelated child/queued/saved images remain counted;
no partial image dropping or model/history/permission changes.

Receiver aggregate and capacity-change tests plus actual ChatApp read/adopt overflow
cases verify no adoption, source kept and temporary files released.56 current checks
13.80s `/tmp/ui040-capacity-current.log`; actual host2 pass1.09s
`/tmp/ui040-capacity-hosts.log`. Final10-file166 passed/no skips42.96s
`/tmp/ui040-capacity-integrated.log`, handle90869 exit0, includes six Coding RPC and
five ChatApp physical Linux paths, full-size uploads and hash/cancel/receipt matrix.
Root Ruff/seven-namespace strict mypy508 clean `/tmp/ui040-capacity-final-static.log`;
Claw/TUI wheel-sdist `/tmp/ui040-capacity-artifacts`, handle69665 exit0; both changed
production modules exact SHA256 `/tmp/ui040-capacity-source.json`.

UI-040 remains待实现 until item4's full requirement-by-requirement current-source
acceptance reconciliation is inspected; this checkpoint proves capacity fixes, not
blanket completion. Final frozen complete-root/terminal/safety/transport/artifact/example
gates are separately retained. No downloads/live provider/auth/browser/publishing or
user-service restart. Historical repeated OPEN statements are not additional IDs.

## Current-source implementation acceptance reconciliation (2026-10-11)

**UI-040 now 待验证, not 已完成.** This supersedes earlier incremental status
paragraphs; original scope unchanged. Named implementation matrix:

| Original acceptance | Current source and inspected evidence |
| --- | --- |
| Independent parent catalog/read vs child snapshots | Coding RpcParentDrafts / Claw ParentDraftConnection, separate flags and parent source action/root; actual CLI/socket tests. |
| Actual image transport | Coding inline/receiver chunks, Claw original-view uploads/ACK/ref consumption; 1MiB and10MiB boundary, independent flag refusal. |
| Save/discard two-phase full review | Exact typed JSON composer/review, original owner/deadline, one-use confirmation; actual ChatApp, CLI controls and physical save/discard. |
| Client owns composer recovery | ParentRecovery digest/full-painted review and original-scope/local empty composer CAS; text/image actual clients, ABA/changed/nonempty refusal. |
| Connection/session/view lifetime | Independent leases/generations/revisions; foreign/stale/reconnect refusal, in-flight view_cancel and sibling isolation. |
| Retained bytes and deadlines | Bounded manifests/uploads/reviews, active timers, worker-held capacity and private spool retirement, current-host budget checks. |
| Cleanup and uncertain effects | Before-first-step/held worker cancellation, decode close/reconnect/quit, failed review/upload ACK, lost committed receipt no replay/fictitious rollback. |
| Hash replacement / errors | Both controllers' catalog-read/review-delete replacements preserve current disk data; begin/chunk/end errors cannot adopt; disk save failure consumes confirmation. |
| Linux terminal | Six actual Coding RPC receiver text/image recovery/cancel/incomplete and five ChatApp recovery/save/discard paths; resize/full paint/main buffer/cursor/termios. |
| No model/history/policy side effects | Named actual-host/CLI/socket tests assert model marker/call counts, original snapshots and composer/history/deny policy preservation. |

Reconciliation exposed a final concrete implementation defect: moving a transfer out
of pending at finish entry released its aggregate capacity while decode still ran.
Receiver now tracks decoding owners under short lock; their manifests count toward
capacity/admission until actual finally, and cancel/close signals decode without
prematurely closing its files. A revoked decode refuses returning recovery. Regression
`/tmp/ui040-decode-capacity-red.log` retained; production fixed. ChatApp also processes
the terminal read receipt only once: duplicates cannot create a second decoder or
replace its cancellation event/review. Actual held decode tests feed duplicate receipt
and assert original worker ownership remains. No stream replay/adoption authority added.

Current ten-file167 passed/no skips42.83s `/tmp/ui040-final-audit-integrated.log`,
handle91419 exit0; focused59 pass14.07s `/tmp/ui040-final-audit-focused.log`.
Root Ruff/seven-namespace strict mypy508 clean `/tmp/ui040-final-audit-final-static.log`.
Claw/TUI/Coding wheel+sdist `/tmp/ui040-final-audit-artifacts`, handle59871 exit0;
eight named API/host modules current-source byte-exact with SHA256 receipt
`/tmp/ui040-final-audit-source.json`. No downloads/provider/auth/browser/publishing/
user-service restart; unrelated accumulated worktree preserved.

### Remaining verification (same UI-040, no new IDs)

- Broader current-source all-host regression must cover RPC prompt/navigation/
  extension/approval and Claw gateway/queue/modal integrations in addition to the
  named parent matrix; current ten-file gate is not broad host coverage.
- Inspect those results and any concrete failure before closing UI-040. Final frozen
  complete-root, statics/distributions/examples, Linux terminal/safety/transport gates
  still independently reside in UI-049/053–056/064; they are not satisfied by this
  matrix or local artifact receipt.

There is no presently identified unimplemented parent-API behavior within this named
matrix. New concrete defects remain in scope; status 待验证 records implementation
progress, not a full requirement-completion claim. Native non-Linux verification stays
deferred UI-060–063 and is not reported passed.

## UI-040 named acceptance completed (2026-10-11)

**Current status: 已完成.** This supersedes incremental 待实现/待验证 paragraphs,
without changing their historical evidence or waiving goal-level final gates.
The current-source implementation matrix above plus expanded host regression now
proves the named original parent snapshot APIs and client-owned recovery acceptance.

Expanded59-file first gate `/tmp/ui040-broad-host-current.log`, handle68736 exit1:
829 passed/1 failed431.80s. Failure in child transient flood measured a normal startup
message arriving after events.clear(); isolated reproduction passed. Test now waits
for actual adapter entry and delivered message_start containing RPC-LIVE-PARTIAL,
then zero scheduled deliveries before synthetic flood. Retains exact32-event capacity,
limited marker, empty oversized payload and cleanup assertions. First attempted
message_update sync did not match this StartEvent-only fixture and failed explicitly;
`/tmp/ui040-broad-fixes-focused.log` retained. Corrected related12 tests pass5.52s
`/tmp/ui040-broad-fixes-focused2.log`, no skips.

Inspection also found alternate RPC set_model/set_thinking/compact entrances lacked
parent-draft/terminal ownership interlocks. Three actual-reader regression tests failed
with mutation admitted (`/tmp/ui040-host-interlock-red.log`), then pass0.66s
`/tmp/ui040-host-interlock-current.log` after production repair. Child terminal input
likewise refuses while parent draft disk work owns the session. No permission weakening,
second model/tool loop or unrelated service/process cleanup. In-progress broad tests
were allowed to finish normally before the next current-source run.

Final60-file **833 passed/no skips432.13s**, `/tmp/ui040-broad-host-final.log`,
handle45571 exit0. Exact file selection `/tmp/ui040-broad-host-final-selection.json`
includes all Coding test_rpc files, private CLI/physical/source/child/custom/editor/
transient/approval/lifecycle/output/navigation gates, saved parent/child snapshot
regressions, Claw gateway input/image/navigation/planning and native TUI transport/
usage/modal coverage, plus shared receiver/store/panel/keymap. Source-final parent
implementation matrix gate167 pass42.83s remains the detailed feature evidence.
Current root Ruff/seven-namespace strict mypy508 clean
`/tmp/ui040-broad-host-current-static.log`. Updated Coding RPC source exact in built
wheel/sdist `/tmp/ui040-host-interlock-artifacts` verified SHA256
`/tmp/ui040-host-interlock-source.json`; prior eight-module source receipt covers
other unchanged feature modules. Changed-path git diff --check clean.

Requirement closure is UI-040 only: 64 stable IDs43 complete/6 implement/6 verify/
5 missing external/4 deferred;17 non-deferred unfinished. The complete goal is NOT
achieved. Current-source final frozen full-root regression, terminal/safety/transport,
statics/distributions/examples remain independent UI-049/053–056/064 requirements.
No non-current-Linux native proof claimed, no external provider/auth/browser/live
services/downloads/publication/user-service restart.

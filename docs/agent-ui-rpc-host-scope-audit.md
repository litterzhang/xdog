# UI-041 RPC editor/host invocation scope audit

Updated 2026-10-11. UI-041 remains **待实现**; this is a finite scope audit,
not blanket editor/host parity or a new requirement ID.

## Inspected existing contracts

- ExtensionAPI get_editor/set_editor: original interactive-command composer CAS.
  Runtime supplies host_editor only for xdog/command; tools/hooks/provider/context
  callbacks cannot forge availability. RPC additionally checks original command
  control/scope, independent extension_editor and one-use revision. Existing actual
  worker/client-CAS tests cover attachments/cursor/text/owner conflicts.
- Invocation-owned model/context usage/name/tool inventory are captured read-only
  contracts; they are not editor/session mutation grants.
- Reviewed RPC navigation uses extension_navigation independently and queues native
  staged control only after callback success; source/dialog/custom/children remain
  separate capabilities and owners.

## New explicit RPC name mutation contract

Coding RPC now advertises independently negotiated extension_session_name:1. Default
is off, exact integer0/1 capability preflight retained. Name read uses original host
session metadata; ExtensionAPI set_session_name requests existing validated rename
review. Only original interactive command control may propose it. The broker must
actually complete with True under the unchanged session/name/navigation revision,
then the callback must successfully return before native rename is queued. Neither
approval acknowledgment nor API True means persistence completed.

Queued rename uses RpcNavigation with expected_name CAS, original session/revision,
idle parent/children, disk-before-publication Session.rename_session. expected_name
is canonical text and only valid for explicit rename action. No editor/history/model/
permission mutation. Tool/hook/headless/editor-only/navigation-only clients cannot
borrow the naming capability. Failed/denied/canceled/withdrawn/foreign or name-changed
reviews cannot queue naming; persistence failure is not automatically retried.

Evidence: new test_rpc_extension_name.py actual worker cases apply/deny/callback fail/
withdraw/name conflict/old client, foreign answer and canceled late answer; actual CLI
independent name negotiation, review, callback result then native persistence receipt,
zero model marker/graceful quit. Existing native Coding/Claw naming tests and RPC
editor/navigation/capability atomicity regressions included. Initial44 passed45.63s
`/tmp/ui041-rpc-name-current.log`; final six-file110 passed/no skips80.81s
`/tmp/ui041-rpc-name-final-integrated.log`, handle93979 exit0. Root Ruff/seven-namespace
strict mypy508 clean `/tmp/ui041-rpc-name-final-static.log`; Coding wheel/sdist
`/tmp/ui041-rpc-name-artifacts`, handle97315 exit0, two changed RPC modules byte-exact
SHA256 receipt `/tmp/ui041-rpc-name-source.json`. Initial line-length diagnostic fixed.
No live provider/auth/browser, downloads, publishing or user-service restart.

## Remaining original UI-041 scope

1. Reconcile applicable command/custom/tool/hook/child editor invocation contracts
   against audited public API; retain ownership/protected composer restrictions while
   implementing concrete missing applicable scopes. Existing command-only behavior
   is not wholesale completion of the original broader-scope request.
2. Reconcile remaining host catalog/browse/inspection/navigation surfaces with actual
   RPC methods/independent capabilities. Name mutation above is implemented, not
   repeatedly counted as OPEN. Active tools/compaction/messages/queues belong UI-043.
3. Actual worker/CLI and applicable Claw/child/headless scope verification plus final
   current-Linux safety/transport/terminal gates. Current name test scope does not
   prove all these contracts; final frozen full-root remains independent.

64 stable IDs43 completed/6 implement/6 verify/5 missing external/4 deferred;
17 non-deferred unfinished. Only non-current-Linux native verification deferred.

## Independent RPC session/tree catalog checkpoint (2026-10-11)

RPC now advertises independently negotiated extension_session_catalog:1, default off.
An original interactive command may use ExtensionAPI.list_sessions/get_session_tree
through the installed catalog handler. Other callback scopes remain unavailable via
runtime command-only host_catalog flags and RPC original command control/scope checks.
Reads reuse native navigation_entries and the existing metadata page validator, off
owner loop through settled disk-worker ownership. No activation, editor, name mutation,
model request or permission grant is implied. Generation, actual session and navigation
revision are checked before/after read; withdrawal returns unavailable after settling
the original thread, not a stale page or replay. Existing request/membership revisions,
50-entry pages, bounded fields and missing-target refusal remain in force.

Actual worker session/tree opt-in and opt-out tests inspect copied durable catalog
entries, independent editor/navigation/name flags and zero model/history changes.
Actual private CLI independently negotiates catalog, executes worker command, receives
metadata page and quits gracefully without model marker. Added pagination test checks
53 sessions as50+3 disjoint pages, matching membership revision; adding a session then
refuses old membership revision. Missing tree target returns CATALOG-UNAVAILABLE.
Held disk read/withdrawal verifies no premature job release or retained catalog entry.

Initial4 failures `/tmp/ui041-rpc-catalog-current.log` misread the existing navigator
fixture's summary as JSON; assertions now inspect actual catalog entries and unchanged
fixture summary. Second gate `/tmp/ui041-rpc-catalog-fixed.log` had1 worker-startup
failure/3passes; retained, not success. Recheck4 pass6.04s
`/tmp/ui041-rpc-catalog-recheck.log`; later expanded gates pass. No timeout increase,
test deletion or scope waiver was used to substitute for these failures.
Final5-file66 passed/no skips70.82s `/tmp/ui041-rpc-catalog-final-integrated.log`,
handle82498 exit0 includes new catalog plus name/navigation/editor/capability atomicity
regressions. Root Ruff/seven-namespace strict mypy508 clean
`/tmp/ui041-rpc-catalog-current-static.log`. Coding wheel/sdist
`/tmp/ui041-rpc-catalog-artifacts`, handle80016 exit0; modified extension RPC source
byte-exact SHA256 `/tmp/ui041-rpc-catalog-source.json`. Changed-path diff check clean.
No downloads/live provider/auth/browser, publishing or user-service restart.

This closes the identified RPC session/tree catalog wiring gap, not original UI-041
as a whole. Remaining: applicable editor invocation-scope/reference reconciliation,
other named host browse/menu/inspection contracts and broader worker/CLI/child/headless/
Claw scope verification. Native and physical catalog integration outside these tests is
not claimed. Final goal-level frozen complete-root/Linux safety/terminal/transport/
static/artifact/example gates remain required.64 IDs43 complete/6 implement/6 verify/
5 external/4 deferred;17 non-deferred unfinished.

## Actual RPC client command menu (2026-10-11)

Independently negotiated extension_command_catalog now routes get_command_catalog
through the original command-scoped dialog broker to the actual RPC client. Server
never fabricates a terminal menu from its own registrations. Client replies use existing
bounded32-entry page/revision schema; original session, command scope and capability
generation checked before/after broker completion. Wrong-session answers refuse before
consuming the broker receipt. Tools/hooks/headless or catalog/navigation/editor-only
clients cannot borrow this menu grant. Withdrawal refuses late answers; metadata does
not authorize command execution. Worker uses copied pages, not mutable client objects.

Actual worker tests check40 client-only menu entries across pages and independent
reread, zero registered-command executions, wrong-session/withdrawal and default-off.
Actual private CLI reads its own client-only menu and quits without model marker.
Existing native both-host command-menu/RPC name/session catalog/navigation/editor/
capability atomicity regression included: seven-file73 passed/no skips79.30s
`/tmp/ui041-rpc-menu-integrated.log`, handle25400 exit0. Root Ruff/seven-namespace
strict mypy508 clean `/tmp/ui041-rpc-menu-final-static.log`; Coding wheel/sdist
`/tmp/ui041-rpc-menu-artifacts`, handle48661 exit0; modified RPC module byte-exact SHA256
`/tmp/ui041-rpc-menu-source.json`. Changed-path diff check clean. No downloads/live
provider/auth/browser/publishing/user-service restart.

UI-041 remains待实现 for original broader editor invocation-scope reconciliation,
remaining native browser/host contracts and applicable child/headless/RPC scope evidence.
Name/session-tree/client-menu slices are implemented, not repeated OPEN requirements.
Final frozen full-root/current-Linux safety/terminal/transport/artifact gates unchanged.
64 stable IDs43 complete/6 implement/6 verify/5 external/4 deferred;17 unfinished
non-deferred requirements.

## Independent host capability delta negotiation (2026-10-11)

Three red regressions `/tmp/ui041-independent-flags-red.log`, handle55356 exit1,
proved menu/catalog/name-only hello packets silently withdrew pre-existing editor/
navigation/dialog/form grants and destroyed editor receipts. New host families now
negotiate as independent deltas when no legacy dialog/editor/navigation fields are
provided. Omitted independent family fields preserve their current grants. Original
legacy UI capability batches retain existing omission-means-withdraw semantics, so
older clients are not silently reinterpreted. Explicit independent disable revokes
only its own family/generation; invalid mixed batches preflight before any changes.
Existing editor receipts survive an unrelated host-family enable/disable.

Current21 capability tests9.60s `/tmp/ui041-independent-flags-focused.log`; final
six-file75 passed/no skips77.63s `/tmp/ui041-independent-flags-final-integrated.log`,
handle20128 exit0 includes actual worker/CLI menu/session catalog/name, navigation/
editor and capability safety. Root Ruff/seven-namespace strict mypy508 clean
`/tmp/ui041-independent-flags-final-static.log`. Coding wheel/sdist
`/tmp/ui041-independent-flags-artifacts`, handle27565 exit0; modified module byte
exact SHA256 `/tmp/ui041-independent-flags-source.json`; changed-path diff check clean.
Initial static line lengths corrected, red assertions retained. No downloads/live
provider/auth/browser/publishing/user-service restart.

UI-041 remains待实现: this is a negotiated ownership fix for implemented slices,
not closure of remaining editor invocation/browse/host-scope contracts.64 IDs43 complete/
6 implement/6 verify/5 external/4 deferred;17 non-deferred unfinished. Goal final
frozen complete-root/Linux safety/terminal/transport/static/artifact gates unchanged.

## Deferred RPC client session-browser request (2026-10-11)

RPC now independently negotiates extension_session_browser (default off) for the
existing ExtensionAPI.open_sessions(tree=...) command API. It queues one original
command/session/navigation-revision/browser-generation-bound request; only successful
callback completion AND successful extension_result emission permit the subsequent
extension_browser_requested event. Event includes original command scope, session
revision, generation and sessions/tree target. It is a request to the client, not
proof a client displayed a browser, and never activates/restores a session. A client
may use existing read-only session_control list/tree to populate its browser; actual
activation remains separate explicit user-owned navigation.

Browser opening and deferred naming/navigation are mutually exclusive within the
same command, preventing a request bound to one session from following its mutation.
Command failure/cancel, capability withdrawal/re-enable, revision change or failed
command-result delivery do not emit an opening request. Browser-only negotiation
preserves unrelated editor/navigation/dialog grants under the existing independent
host-family delta rule. Tools/hooks and old/catalog-only clients do not gain browser
capability, and no parent composer or permission is borrowed.

Tests use the existing real navigator worker: sessions/tree, fail/old client, held
post-worker callback withdrawal/re-enable/revision/cancel, and failed result emission.
Actual CLI cases verify extension_result precedes opening request and client metadata
read uses the same original session without model calls/history/permission mutation.
No new physical-terminal/browser-client implementation claim: arbitrary client UI
handling is outside what this wire fixture proves and remains in overall scope audit.

Initial8 pass11.06s `/tmp/ui041-rpc-browser-current.log`; expanded browser/atomicity
34 pass24.97s `/tmp/ui041-rpc-browser-expanded.log`. Eight-file124 passed/no skips
95.52s `/tmp/ui041-rpc-browser-integrated.log`, handle38679 exit0, covers browser,
menus, catalogs, names, navigation, editor and capability atomicity. Final lint-only
notice shortening followed by11 browser tests15.84s
`/tmp/ui041-rpc-browser-final-focused.log`, handle84457 exit0. Root Ruff/seven-namespace
strict mypy508 clean `/tmp/ui041-rpc-browser-final-static.log`; Coding wheel/sdist
`/tmp/ui041-rpc-browser-final-artifacts`, handle72137 exit0; modified RPC module exact
bytes/SHA256 `/tmp/ui041-rpc-browser-source.json`. Changed-path diff check clean.
No downloads/live provider/auth/browser network calls, publishing or service restarts.

UI-041 remains待实现 for original broader editor invocation-scope/reference matching
and complete applicable host/child/headless/RPC scope verification. Implemented
name/session-tree/client-menu/browser-request slices are not repeatedly counted OPEN.
64 stable IDs43 complete/6 implement/6 verify/5 external/4 deferred;
17 non-deferred unfinished. Final frozen complete-root/Linux safety/terminal/transport/
static/distribution/example gates remain unchanged.

## Deferred host actions wait for command-result delivery (2026-10-11)

Two actual worker red regressions showed reviewed navigation/name changes could start
from a command done callback even when extension_result emission raised. Unlike the
browser guard, that path used a success flag set before cleanup/output completion.
`/tmp/ui041-deferred-delivery-red.log`, handle9937 exit1, retained.

RpcExtensions now marks success only after the command context has exited, final
snapshot construction has succeeded and the result has been sent. Failed/canceled
cleanup reports success:false and removes stale success output/content. Native
run_rpc_mode supplies RpcOutput.send, which waits for a whole JSON line to be written,
with a bounded10s command-result wait; synchronous custom/embed sinks retain their
own explicit emitter contract. Merely enqueueing native output is no longer sufficient
for deferred navigation/name/browser actions. Done callbacks check actual task success
as well as original session/revision/capability guards. Cancellation/failure while
waiting for result delivery discards deferred work, not replays it. This is transport
write completion, not a claim of remote application consumption or durable receipt.

New actual worker tests cover navigate/rename failed emit (previously red) and each
navigate/rename/browse with held async delivery followed by success, write failure or
cancellation. No native task starts before receipt; failures leave original session/
name/history/deny permissions unchanged and no model calls. Positive deliveries still
reach native rename/navigation or request browser population. Existing actual CLI and
native RPC output tests remain in the integrated group.

Focused11 pass16.39s `/tmp/ui041-deferred-delivery-async.log`; final12-file149 passed/
no skips113.80s `/tmp/ui041-deferred-delivery-integrated.log`, handle10317 exit0,
includes menu/catalog/browser/name/navigation/editor/capability atomicity/output/
permission-response and parent-draft interlock tests. Root Ruff/seven-namespace strict
mypy508 clean `/tmp/ui041-deferred-delivery-final-static.log`; Coding wheel/sdist
`/tmp/ui041-deferred-delivery-artifacts`, handle89474 exit0; both changed production
RPC modules byte-exact SHA256 `/tmp/ui041-deferred-delivery-source.json`. Initial
line-length diagnostics fixed. No downloads/live provider/auth/browser/publishing or
user-service restart; unrelated workspace changes retained.

UI-041 remains待实现 for broader editor invocation/host scope reconciliation and
applicable all-host/child/headless verification. This fixes an ownership boundary in
implemented slices, not full-goal completion.64 IDs43 complete/6 implement/6 verify/
5 external/4 deferred;17 non-deferred unfinished. Final frozen complete-root/current-
Linux safety/terminal/transport/static/distribution/example gates unchanged.

## Accepted-reply capability generation boundary (2026-10-11)

Reference lookup for remaining broader editor invocation semantics returned no usable
web content; an explicit primary GitHub fetch also failed with connection reset.
No new upstream applicability/permission claim is inferred from the failed lookup.
The existing command-only authority is not reclassified as complete broader parity.

Local source inspection found editor/name/navigation reply handling only rechecked a
boolean enabled flag after awaits. Each now has an effective-grant generation:
withdrawal and re-enable invalidate old accepted replies, even if the broker future
had already completed before the toggle. Original editor read cannot reinstall a CAS
receipt after revocation; accepted name/navigation reviews cannot queue native work.
Generation checks also surround checkpoint disk preparation. Same-state negotiation
and unrelated independent flags do not change these generations; existing legacy
omission-based withdrawal still counts as an effective transition. No new invocation
scope or permission is granted.

New tests hold the actual broker return after a real worker's editor/name/navigation
answer, toggle the corresponding capability off/on, and release: old reply is unavailable,
no editor replacement/native mutation, original history/deny policy/model-call count
preserved. First fixtures used asyncio.Event across owner loops, timing out both before
and after the patch (`/tmp/ui041-host-generation-red.log`,
`/tmp/ui041-host-generation-current.log`); these are **not valid red proof of the code
bug**. Corrected thread-safe synchronization passes3 tests4.54s
`/tmp/ui041-host-generation-fixed-test.log`. Additional capability test verifies only
effective transitions advance counters. Full10-file139 passed/no skips116.45s
`/tmp/ui041-host-generation-integrated.log`, handle78235 exit0; includes actual CLI/
worker name/catalog/menu/browser/editor/navigation/deferred delivery and capability
atomicity regressions. Root Ruff/seven-namespace strict mypy508 clean
`/tmp/ui041-host-generation-final-static.log`; Coding wheel/sdist
`/tmp/ui041-host-generation-artifacts`, handle88340 exit0, modified RPC module exact
bytes/SHA256 `/tmp/ui041-host-generation-source.json`. Changed-path diff check clean.
No dependencies installed/live provider/auth/browser calls, publishing or user-service restart.

UI-041 stays待实现: broader editor invocation/reference reconciliation and complete
applicable host/child/headless verification remain outstanding.64 stable IDs43 complete/
6 implement/6 verify/5 external/4 deferred;17 non-deferred unfinished. Final frozen
complete-root/current-Linux safety/terminal/transport/static/artifact/example gates intact.

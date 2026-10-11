# Agent UI goal: completion evidence audit

> **2026-10-10 用户范围更新 / 当前计数权威入口**
> 当前范围、完成条件、稳定需求编号及分类数量以
> `agent-ui-requirements.json` / `agent-ui-requirements.md` 为准。
> 本文以下内容是保留的历史审计/证据，不再把重复 OPEN 当当前独立需求。
> 仅非当前 Linux 的原生验证延期且非阻塞；不声称通过。
> Linux 功能、安全、生命周期、传输、终端与最终完整回归不缩减；
> 非 Linux 后端实现与原生验证分开记录。复算：`python tools/agent_ui_checklist.py --check`。


This audit keeps the original goal intact. It is NOT a completion declaration or
an up-to-date release comparison with Codex/Pi. OS-enforced sandboxing is excluded;
permission checks, owner/cancellation safety and every other recorded requirement
remain in scope. Detailed historical evidence is in `agent-ui-gap-plan.md`.

## What would prove completion

| Requirement | Authoritative implementation/evidence to inspect | Current conclusion |
| --- | --- | --- |
| File/path completion | Shared workspace index/file completion, both app providers, ignored/large workspace and physical Enter/Tab tests | Implemented and exercised on the recorded Linux path; retain degraded enumeration/platform limits. |
| Configurable shortcuts/external editor | Shared keymap/editor owner, both app handlers, current hints, terminal recovery tests | Linux evidence exists; native desktop/Windows evidence is still missing. |
| Steering and editable queues | Agent input queue, both owning-session acknowledgments/edit/remove/reorder/return receipts, reconnect tests | Implemented on recorded paths; fresh root integration remains a separate gate. |
| Sessions/branch trees | Coding session manager/staging, Claw transcript store/journal/gateway, native browsers and source scopes | Implemented on recorded paths, including durable names and authoritative Claw name reads; no OS power-loss/native-platform blanket claim. |
| Images/clipboard | Shared attachment and platform helpers, typed Agent input/persistence, actual composer/socket tests | Linux and simulated helper transport evidence exist; native AppKit/Windows/Kitty/iTerm execution remains unproven. |
| Full transcript interaction | Full canonical/source readers, shared viewer/selection/search/fullscreen, saved branches/timeline provenance | Implemented on recorded paths; native platform/manual behavior remains unproven. |
| Copy/export/share | Captured source/output review, private create-only files, clipboard adapters, no upload/replay/overwrite tests | Recorded Linux evidence exists; native desktop copy paths remain unproven. |
| Repository changes | Git review worker/browser, full paged read-only patches, physical focus/permission recovery | Recorded implementation/tests exist; keep read-only/no apply/revert behavior and platform limits. |
| MCP | Config/stdio/HTTP managers, schema/content/notifications/OAuth/credential ownership, both UIs and independent serving-SDK tests | PARTIAL. Existing independent SDK cases do not prove general current-protocol/provider interoperability. |
| Runtime extensions | Worker/broker/API, current declarations/guards, parent/child/app wiring and terminal lifecycle | PARTIAL. Native help/name/failure withdrawal do not prove every remaining host/render/input/provider API. |
| Planning | Session-owned plan state, read-only tool gates, explicit execution confirmation, restore/persistence | Implemented on recorded paths; no permission escalation or extra model loop allowed. |
| Interactive child supervision | Actual supervisor/factories, status/source/edit/cancel/dialog ownership, downward tool/profile limits and persistence | Recorded depth-one app evidence exists; richer host APIs and native-platform evidence remain open. |
| Safety/transport/scrollback invariants | Approval and late/stale/generation/cancellation tests; actual main-buffer renderer/private-tmux recovery | Pass149 frozen full-root green checkpoint exists; remaining platform/API invariants still require their own evidence. |
| Distribution/example/CI artifacts | All-four wheel+sdist membership/imports, workflow example test command, repository Ruff, all-seven strict mypy, full-root pytest | Pass149 full-root green (5355 passed/29 skipped) predates pass151. Pass150 has 17 workflow examples; pass151–168 focused tests and pass168 strict mypy478/Ruff/artifacts cover their named patches. See tracker evidence; these are not current full-root coverage. These gates do not close remaining feature/platform requirements. |
| Current comparison baseline | Retrieved primary Codex/Pi references matched against actual public registrations and app behavior | Pass 134 retrieved primary Codex/Pi documentation and recorded local installed versions in `agent-ui-reference-baseline.md`; this is a baseline, not conformance/goal-wide completion. |

## Concrete remaining evidence and implementation gaps

- The default `oauth_store.py` backend remains reviewed private local PLAINTEXT.
  Pass151 adds explicit Linux `secret-service` selection through both clients and
  `oauth_native_store.py`/`native_store_worker.py`, with no plaintext fallback.
  Actual encrypted roundtrip, locked-store refusal, manager restart/consent and
  both UI owners are tested against an isolated owned GNOME keyring service.
  This does not prove every Secret Service implementation, native-backend tmux
  behavior or macOS/Windows credential storage. Those requirements remain OPEN;
  vault at-rest protection belongs to the service, not XDOG.
- The extension public API and command declarations currently provide hooks/tools/
  commands/provider/UI contributions and scoped metadata. Pass 128 adds a public
  parent-command terminal-menu accessor; registered slash-argument completion
  callbacks now have the pass-129 registration-bound runtime/SDK foundation;
  pass 130 now wires parent cursor-local argument selection/coalescing and Claw
  transport. Pass 131 adds actual editor dismissal/focus/exit cancellation and settled worker
  ownership; pass 132 preserves display labels separately from insertion values.
  Pass 133 adds independently negotiated, copied full argument context for dependent
  later-argument suggestions. Richer suffix semantics and broader argument
  interoperability remain OPEN. This is
  a local source observation, NOT an unverified claim about a current upstream
  release; the broader host API row is not complete because native `/help` exists.
- Pass 135 implements opt-in Coding RPC extension management/commands and basic
  questions/typed forms/replies for commands and parent prompt callbacks, with actual
  subprocess/permission/cleanup evidence. Pass 140 adds command-only, independently
  negotiated client-owned editor CAS transport with actual client/subprocess evidence.
  Pass 141 adds native RPC paged discovery/staged navigation primitives and new
  conversation lease fencing. Pass 142 adds independently negotiated reviewed RPC
  extension navigation after actual broker acceptance/callback success. Combined
  raw canonical/binary and richer custom/child-terminal RPC UI plus reference
  interoperability remain OPEN. Pass 147 adds reviewed live child queue viewing/
  revision-checked edit/remove/reorder/return-to-child-draft in both native clients
  and Coding RPC. Pass 148 adds actual settlement-persisted unadmitted input recovery,
  reload without agent creation, reviewed non-destructive copy into a fresh read-only
  task draft, and committed-only durable discard. Unclean active-run crash durability
  and the remaining image-draft return/recovery/retention requirements remain OPEN;
  settlement checks do not prove them. Pass153 implements native child start/steer/
  follow-up file/clipboard/inspection editors, reviewed exact owner receipts, typed
  SDK/socket/RPC submission and separate image/text persistence budgets. Actual
  both-app 10 MiB transport and RPC subprocess/tmux evidence do not prove image
  queue return, cross-reconnect draft browsing, active-crash durability or general
  provider/decoder interoperability. Pass154 adds actual non-destructive stopped
  image input copy into separate fresh read-only drafts, verified binary socket/RPC
  framing and exact receipts, with 10 MiB/native-owner/subprocess/terminal evidence.
  Pass156 adds explicit initial-task recovery/discard, separate admission state,
  conservative interrupted/legacy uncertainty and native/RPC/terminal evidence.
  Pass157 adds only the live image-return runtime two-phase lease contract: actual
  queue byte preparation without removal, verified receipt/owner/revision-bound
  commitment, cancellation/expiry and exact copied retry receipts. Native/socket/
  RPC binary transport is now wired by pass158 with independent return capability/
  revocable return lease, verified preparation stream, separate explicit commit,
  actual CLI subprocess and closed-peer/re-enable negatives. Native physically
  painted removal review, Claw socket return and durable draft ownership remain
  OPEN; runtime/RPC evidence does not close those UI/platform requirements.
  Pass159 wires native Coding's two painted reviews, exact queue-view leases,
  verified precommit local backup and returned child-only draft, with actual 10 MiB
  owner tests and private-terminal evidence. Claw socket/native image return and
  durable active-crash recovery remain open; a memory backup is not crash storage. Comprehensive
  local/cross-context/reconnect draft browsing is not supplied by pass154 alone. Pass155
  adds a native local draft browser over current/old-context caches, with full
  reviewed non-destructive fresh read-only copy, current-only reopen, identity-
  fenced local discard and no execution/permission inheritance. This is not
  durable cross-process recovery or a remote RPC draft-browser implementation.
  Pass152 closes their default-route rendered-image admission prerequisite: actual
  Coding/Claw child factories validate retained user/tool and owned worker-hook
  images on the live adapter, without imposing the composer count on retained
  context or reviving withdrawn metadata. This is not attachment editor/transport
  implementation or full-capacity image persistence by itself; pass153 supplies the
  named submission/storage increment, not full return/recovery parity. Pass 146 wires scoped Coding RPC child supervision,
  complete child display sources and independent typed questions/approvals into the
  existing supervisor; actual controlled workers/CLI/revocation/disconnect evidence
  is required, not an arbitrary remote renderer/deeper delegation parity claim. Pass 145 adds explicit complete retained DISPLAY
  transcript RPC sources (live partial/saved head/branch/provenance timeline),
  shared verified receiver/spooling and native POSIX nonblocking output, with
  actual long-source/slow/closed-peer CLI and control/approval evidence. This
  does not replay every transient UI/custom/child/tool-progress artifact or prove
  unsupported native platform transports. Pass 144 adds combined cross-session branch/checkpoint
  staged admission to Coding SDK/native slash/RPC/reviewed extension paths: selected
  target preflight, one target save/publication and retained return head, without an
  intermediate resume. This is not a multi-file/power-loss transaction. Pass 143 closes the specific
  native Coding/Claw late-answer navigation/rename grant race using actual broker
  receipts, with real worker/socket regressions and both-client terminal checks;
  this is not proof of every native/gateway race. Claw's gateway is not evidence
  for the remaining RPC APIs.
- Pass 136 adds typed inline RPC image prompts with actual CLI/model-capability/
  persistence evidence and revalidates vision after turn-start hooks across Coding
  RPC/interactive and Claw. Pass 137 adds whole-rendered-context admission for actual metadata-backed adapters,
  including retained/tool/hook images. Pass 138 adds bounded native POSIX input and
  chunked RPC reference ownership. Opaque/missing-limit metadata, alternate input
  readers/platform cancellation and provider/decoder interoperability remain unproven.
- Raw-key/richer renderer/container and further provider/auth/MCP interoperability
  requirements remain on their original tracker rows. Require named API/behavior
  contracts and actual host/transport/terminal evidence before checking them off.
- Windows/macOS native desktop runs cannot be replaced by Linux protocol fixtures,
  private tmux, screenshots or passing pure parsing tests. Record the missing native
  evidence explicitly instead of declaring platform parity.
- Pass 139's full-root run stopped after **4,212 passed/29 skipped** at a terminal
  fixture requiring one-line JSON. Corrected typed/wrap-aware assertion plus the
  exact-node tail gives **5,132 passed/29 skipped** as complete SEGMENTED collection
  evidence. Production code stayed unchanged. This is still not an atomic full-root
  green run or proof of every capability/native-platform requirement above.

## Current checkpoint ownership

The single owned pass-139 root run terminated with **1 failed, 4,212 passed,
29 skipped** in `/tmp/pass139-workspace.log` (1,115.93 seconds). Its existing tool
handle was polled until authoritative exit, never restarted on an observation timeout.
The fault was a one-line rendering assertion after an added snapshot field changed
wrapping, not a provider/runtime failure. Strengthen typed JSON display assertions
without widening the terminal or relaxing activation/permission/focus guards.

Exact stopped-node continuation: **920 passed, 4,241 deselected**,
`/tmp/pass139-remainder.log`. The changed provider matrix + scalar/wrap assertions:
**17 passed**, `/tmp/pass139-terminal-fixed.log`. Full current collection now has
**5,161 cases**, with **5,132 passed/29 skipped** in segmented evidence. Production
runtime stayed unchanged; see the tracker for overlap and command results.

Pass149 now supplies that later atomic full-root green checkpoint: **5,355 passed,
29 skipped** in `/tmp/pass149-workspace.log` (1,413.78 seconds), `WORKSPACE_EXIT=0`.
Owned handle75509 was polled to actual exit; no observation-timeout restart. All924
snapshotted source/test/TOML/lock files were unchanged, with no new source files:
`/tmp/pass149-source-after.json`. Pass150 Ruff/all-seven strict mypy470 and the
explicit offline workflow example command (17 passed) are green. Earlier pass139
results remain historical segmented evidence, not substituted for this fresh run.

The unresolved feature/platform/interoperability requirements above still remain.
Even a frozen green workspace is not a declaration of complete parity.

Pass164 adds write-ahead accepted-child-input snapshots in both factories and an
actual owned SIGKILL recovery test with 2 MiB image data. Restart exposes conservative
uncertain snapshots, never automatically constructs an agent. This is NOT exact
consumption/edit/remove journaling, durable local drafts or physical power-loss
proof; those requirements remain open.

Pass165 persists acknowledged live edit/remove/move/text-return snapshots and tests
owned SIGKILL after image edit/removal. Exact consumption and two-phase live-image
return commit journaling remain open; prospective writes before lost acknowledgments
are still labelled uncertain and never automatically replayed.

Pass166 persists acknowledged live image-return commit removal snapshots with
pre/post-write lease checks and owned SIGKILL verification. Exact consumption/handled
admission journaling and durable local editor backups remain open; prospective
pre-acknowledgment states are uncertain, not automatically replayed.

Pass167 adds durable image-return handoff backups before removal, interrupted-load
merge and terminal-record retention, explicit reviewed durable-return recovery/discard,
and owned SIGKILL plus both native/terminal evidence. General local editor drafts
and exact consumption journaling remain open; conservative backup uncertainty is
not an assertion that the original work had no effects.

## Pass169/170 current integration result

Pass169 completed a frozen full-root run: **7 failed, 5,537 passed, 29 skipped**
(1,470.42 seconds), `/tmp/pass169-workspace.log`, `WORKSPACE_EXIT=1`. The same
owned handle99712/PID67627 was polled to actual termination; all1,017 snapshotted
source/test/config files remained unchanged (`/tmp/pass169-source-after.json`).
This is a red integration checkpoint, not replaced by pass149's older green run.

Pass170 inspects/fixes the seven named failures: guarded externally supplied OS GI
imports in the isolated native vault helper, precise dependency-audit treatment of
that worker only, actual vision metadata on the rich-tool image test adapter, and
terminal tests preserving restored child drafts instead of expecting data loss.
Affected groups **76 passed** (55.09s), `/tmp/pass170-fixes.log`; native/prerequisite/
packaging groups **42 passed** (17.23s), `/tmp/pass170-dependency.log`, overlap.
Missing-GI refusal is exercised through an actual isolated Python subprocess, with
redacted error/no service or fallback. Ruff/strict mypy478 and current Agent worker
artifacts are clean. A fresh full-root run after these fixes is still REQUIRED;
focused evidence is not an atomic green workspace or whole-goal completion.

## Pass171 frozen current workspace GREEN

After the pass170 corrections, a NEW frozen full-root command completed with
**5,545 passed,29 skipped** (1,487.69 seconds), `/tmp/pass171-workspace.log`,
`WORKSPACE_EXIT=0`. Same owned handle95694/PID122512 polled through actual exit;
no observation-timeout restart. All1,017 source/test/config files unchanged across
before/mid/after snapshots (`/tmp/pass171-source-after.json`). Fresh repository
Ruff and all-seven strict mypy478 are clean. This supersedes the red pass169
integration gate, not its historical evidence, and is NOT goal-wide parity.

Outstanding requirements remain: general durable editor drafts, exact input
consumption/handled journaling, richer raw/canonical/custom/render/RPC APIs and
remaining native platform/credential/clipboard/provider/MCP interoperability.
The 29 skipped cases are not verified capabilities. Keep the goal active.

Pass172 awaits remaining-queue persistence after identified child input admission/
handling and before model continuation. An owned post-flush SIGKILL case verifies
consumed input is absent from recovery. Pre-flush admission remains uncertain, not
an atomic history/queue transaction. General durable editor drafts remain open.

Pass173 adds the bounded POSIX private local child-draft codec/store, tested with
process roundtrip, concurrent capacity, unsafe paths and exact discard receipts.
Native application save/load/discard/autosave integration is still missing; this
storage foundation does not close general durable editor drafts or platform parity.

Pass174 wires explicit reviewed native child draft save, off-thread saved-catalog
load and digest-bound disk discard in BOTH clients. Saved rows are visibly labelled
and copy read-only, without execution/configuration inheritance. Tests cover new
UI owners, 10 MiB images, stale reads, failed saves and physical terminal workflows.
Autosave/general parent-editor persistence and native-platform/remote API evidence
remain open; explicit snapshots do not persist every unreviewed edit.

Pass175 adds metadata-only saved catalog listing and ID-scoped complete load to
both native clients. Over-capacity combined image catalogs remain discoverable;
selected recovery keeps exact bytes/read-only configuration. This is not autosave
or remote API/platform conformance.

Pass176 removes blocking live-thread joins from durable-draft UI polling and fixes
failed-start ownership. Actual held-worker/start-failure/rejected-receipt tests and
both native saved-draft regressions verify lifecycle behavior, not autosave parity.

Pass177 bounds saved-draft lock acquisition, with real separate-process contention
tests for save/load/catalog/discard and unchanged snapshot data. This improves
worker/quit stability but does not implement autosave or general filesystem watchdogs.

Pass178 exposes read-only Coding RPC saved-draft catalog and digest-bound reads
with separately negotiated binary capability and verified transfer. Actual CLI
subprocess evidence shows no model/history mutation. Remote save/discard, autosave
and remaining platform/host API requirements remain open.

Pass179 adds independently negotiated two-phase Coding RPC saved-snapshot discard,
with exact digest/metadata consent, generation revocation, worker-bound admission
and actual CLI evidence. This does not implement remote save/autosave or physical
client-review/platform conformance.

Pass180 adds independently negotiated two-phase Coding RPC saved-snapshot creation,
exact text/image review receipts, owned upload consumption and publication-bound
revocation. An actual CLI subprocess saves a 2 MiB image without model/history
mutation. Autosave/general editor persistence and broader platform/host requirements
remain open; explicit saved snapshots are not full goal completion.

Pass181 implements session-local opt-in native child autosave, debounce and digest-
bound updates of its own records. Both native owner tests and a completed-autosave
SIGKILL test preserve exact drafts without execution/permission restoration. Parent
editor persistence, debounce/in-flight crash windows and platform/API parity remain
open; this is not automatic submission or cross-platform power-loss proof.

Pass182 drops duplicate decoded image retention from completed autosave bookkeeping
and prunes removed-slot tracking without deleting saved snapshots. Same-slot updates
remain digest-bound; later reappearing slots cannot overwrite earlier recovery data.

Pass183 moves UI teardown behind actual exit admission in both clients. Held-write
tests verify repeated deferred quit preserves services and completed exit cleans
once; no full-goal/platform completion is inferred from this lifecycle fix.

Pass184 fixes saved-catalog reload accounting across both native clients: reviewed
local copy image ownership remains counted, while exclusive replaced catalog bytes
are released. Capacity refusal preserves the existing catalog and editable drafts.

Pass185 implements opt-in native parent autosave and reviewed saved-parent recovery/
discard, separate from child execution. Both actual UI owners preserve text/images,
long Unicode and current permissions; changed/nonempty composer refuses recovery.
Native terminal full-review evidence exists. Debounce/in-flight crash windows,
physical power-loss/native conformance and remote parent APIs remain open.

Pass186 binds parent recovery reads to their actual requesting view and releases
transient image/read references on Back/close/completed recovery. Held-worker tests
prove late results do not reopen or populate replacement views. Disk snapshots stay
unchanged; this does not close broader platform/API or crash-window requirements.

Pass187 extends parent read ownership to same-panel view leases and identities.
Delayed reads after form/back/browser navigation cannot steal focus, and successful
read receipts bind to the exact recovery browser. Actual both-client negatives
and terminal recovery evidence do not imply broader native-platform parity.

Pass188 adds native explicit parent save review and Ctrl+Alt+S without composer
consumption or autosave opt-in. Both actual owners reject changed drafts/repeated
consent; physical terminal reviews preserve text/images and make no model calls.
Broader platform/API and crash-window requirements remain open.

Pass189 fences native parent-save shortcut/review admission against modal dialog,
output and Coding approval ownership. Both clients' stale-review negatives preserve
drafts and admit no writes. This is focused safety evidence, not goal-wide parity.

Pass190 routes native parent save through configurable save_draft bindings, with
remap/disable/no-legacy-fallback and repeat/release negatives in both hosts. This
retains modal approval safety and does not imply remaining platform/API completion.

Pass191 adds read-only exact-attachment previews in local/saved parent/child draft
reviews. Preview return resets full receipt painting, changed sources invalidate
review, and no action is applied by image navigation. Component and both terminal
workflows verify these behaviors; platform image-protocol parity remains open.

Pass192 extends modal ownership guards to parent recovery reads/presentation and
stale copy/discard callbacks in both native clients. Actual held-reader and modal
policy tests preserve focus/drafts/files; wider platform/API parity remains open.

Pass193 completes explicit deferred quit after successful draft settlement, flushes
latest parent edits and cancels exit on new storage failure. Native held-worker
success/failure tests preserve services before exit; wider parity remains open.

## Current ledger update — UI-035 (2026-10-10)

Historical richer suffix semantics OPEN is now closed by shared literal token
scanning and actual both-host/worker/socket/Linux terminal evidence. See current
`agent-ui-requirements.md` UI-035 and `/tmp/ui035-tests-final.log` /
`/tmp/ui035-terminal-final.log`. Other host/RPC/provider/MCP/durability/environment
and final full-root gates remain independently open; non-Linux native verification
stays deferred. Remaining **25** =13 implementation+7 verification+5 external.

## Current ledger update — UI-051 (2026-10-10)

Current independent SDK2.3.1 manager schema/content/completion/watch matrix plus
real Coding/Claw stdio AND HTTP native-owner watch/cleanup tests pass without skips;
actual Linux terminal SDK workflows pass (stdio only). See UI-051 and
`/tmp/ui051-sdk-final.log` / `/tmp/ui051-terminal-final.log`. Wider protocol
capabilities, external deployment interoperability and final current-source Linux
terminal/safety/full-root gates remain independent, not implicitly closed.
Current remaining **24** =13 implementation+6 verification+5 missing environment;
4 non-Linux native-validation requirements remain deferred/non-blocking.

## Current ledger progress — UI-038 (2026-10-10, still open)

Parent canonical typed/binary RPC export now has explicit capability, owner/format
fencing, redaction/provenance, real CLI 2MiB image/long-source/slow-peer cleanup and
125-test relevant evidence (`/tmp/ui038-receiver-final.log`). Child canonical source
implementation and complete-scope acceptance remain outstanding; do not count a
partial parent implementation as a completed requirement. Authoritative remaining
count stays24. Final complete root regression and all other original gates remain.

## UI-038 child implementation update (2026-10-10)

Child canonical independently negotiated sources and actual CLI image/slow-peer/
owner-epoch/redaction evidence now implemented; current full named matrix158 passed
(`/tmp/ui038-child-verified.log`). UI-038 moves to 待验证, not completed, because
older owned unbounded-reader test handle73617/PID340375 and its CLI peer340485 were
still live and user instructed no interruption. See tracker for exact cause and
bounded test fix. Do not claim that old run ended or restart it on timeout.
Current remaining24 =12 implementation+7 verification+5 missing environment;
36 completed and4 deferred, final root/Linux lifecycle gates retained.

## Current progress — UI-039 parent transient events (2026-10-10)

Parent independently negotiated bounded transient event/lease/sequence stream now
implemented, with real parent tool-loop progress and actual CLI partial/withdrawal/
abort evidence (`/tmp/ui039-parent-verified.log`,70 passed). Child progress/custom
artifact transport remains unimplemented; UI-039 stays 待实现. Older unbounded test
PID340375/peer340485 still live at inspection, not interrupted or claimed cleaned.
Authoritative count stays24 unfinished; other Linux/final/external requirements
remain unchanged and non-Linux native validation remains deferred.

## Current progress — UI-039 child transient events (2026-10-10)

Child independent transient capability, real tool progress/CLI partial and original
hook chain now implemented with captured-generation/owner/epoch and bounded queue
checks. Current relevant gate113 passed (`/tmp/ui039-child-final.log`); still partial:
custom artifact and complete transport closeout remain. UI-039 stays 待实现; no
requirement removed or prematurely completed. Count remains24 unfinished. Earlier
owned unbounded test340375/340485 remains live, unmodified; final root lifecycle
verification is not claimed passed. Non-Linux native validation remains deferred.

## Current progress — UI-039 declarative contributions (2026-10-10)

Parent extension notice/status/widget/clear now have independently negotiated scoped
bounded/redacted contribution transport, preserved UI grants and no legacy downgrade
after withdrawal. Real workers and actual CLI verified; current named gate72 passed
(`/tmp/ui039-contributions-verified.log`). Child contributions and applicable richer
artifact/custom protocol plus full transport closeout remain open; UI-039 still
待实现 and accurate remaining count24 unchanged. Older unbounded test340375/340485
still live, not interrupted or claimed cleaned. No final full-root claim.

## UI-039 child declarative contributions progress (2026-10-10)

Actual child bootstrap/turn notice/status/widget routing now independently negotiated,
owner/epoch/generation-fenced and bounded/redacted. Current broader123-test gate passes
(`/tmp/ui039-cc-final.log`); richer artifact/custom/full slow-peer acceptance stays open.
UI-039 remains待实现, count24 unchanged. Old owned unbounded test340375/340485 still
live, unmodified; no final Linux lifecycle/full-root completion claim.

## UI-039 real flood/backpressure progress (2026-10-10)

Native RPC transient output now has a1MiB presentation ceiling reserving total
control/approval output memory, per-family visible loss markers/status counters.
Actual parent/child undrained128-update CLI flood quit/EOF/closed-output lifecycle
and real pipe control-memory/loss-recovery checks pass. Broader140-test gate and
final-status30-test current gate logged in `/tmp/ui039-flood-final.log` /
`/tmp/ui039-flood-status.log`. Richer artifact/custom contracts stay open; UI-039
remains待实现 and24 unfinished requirements unchanged. Old test340375/340485 still
live, not interrupted or claimed cleaned; final workspace regression remains open.

## UI-039 declared event coverage update (2026-10-10)

All14 public Agent event kinds now project safe cumulative typed messages/tool calls,
input receipts and tool-end text/details without images/signatures/raw deltas or
whole-history replay. Full current named gate188 passed `/tmp/ui039-acceptance.log`.
Custom protocol implementation remains owned once by UI-042, with UI-039 event
integration acceptance still required; neither requirement is closed by this test
matrix.24 unfinished unchanged. Older test340375/340485 remains live, not interrupted
or claimed cleaned. Final full-root/terminal/Linux safety gates remain mandatory.

## UI-042 parent custom protocol progress (2026-10-10)

Independent parent RPC custom-view negotiation/handler/client input/broker frame
path implemented with original painted completion gate, generation/geometry/sequence/
owner and withdrawal cleanup. Current related171-test suite and real CLI pass
(`/tmp/ui042-custom-current.log`). Child/nested/overlay/full client lifecycle remain
open; UI-042/039 not prematurely complete,24 unfinished unchanged. Old test340375/
340485 still live, not interrupted or claimed cleaned. Final full-root gates remain.

## UI-042 nested focus progress (2026-10-10)

Broker admission-before-publication protects nested input ownership; parent cache
invalidated until a fresh returned frame, typed nested custom/question/confirm and
ancestor cancellation tested in actual workers/CLI. Current gate176 passed
(`/tmp/ui042-nested-verified.log`). Child/overlay/full protocol acceptance stays open,
24 unfinished unchanged. Old unbounded test340375/340485 still live, unmodified;
final full-root/Linux terminal/safety completion not claimed.

## UI-042 child custom progress (2026-10-10)

Actual child worker custom frames/input, independent generation/owner/epoch,
nested broker input ownership and separate CLI tool approval now implemented.
Current199-test gate /tmp/ui042-child-final.log passes; overlay controls and complete
custom client/transport acceptance stay open. UI-042 remains待实现,24 unfinished
unchanged. Old unbounded invocation340375/340485 still live, not interrupted or
claimed cleaned; final root/Linux terminal and safety gates retained.

## UI-042 parent overlay progress (2026-10-10)

Independent parent live-overlay control publication and broker input/fresh-frame
ownership now implemented, actual worker/CLI177-case related gate passes
(`/tmp/ui042-overlay-current.log`). Child overlays and complete client/transport
acceptance remain open;24 unfinished unchanged. Old unbounded test340375/340485
still live, not interrupted or claimed cleaned. Final full-root completion unproven.

## UI-042 child overlay progress (2026-10-10)

Independent child overlay capability/control publication with actual broker input
ownership and generation/fresh-frame checks implemented; real worker/CLI verify
separate tool approval and parent policy/history isolation. Current related104-case
gate `/tmp/ui042-co-final.log` passes. Complete custom client/transport/terminal
acceptance stays open,24 unfinished unchanged. Old test340375/340485 remains live,
not interrupted or claimed cleaned. Final full-root gates retained.

## UI-042 mixed capability safety (2026-10-10)

Complete extension/cross-controller hello preflight now rejects malformed mixed
requests before partial UI grants/revocation. Actual CLI live frame preservation
and no partial grant tests plus related112-test gate pass `/tmp/ui042-atomic-final.log`.
Full custom/client/transport acceptance still open;24 unfinished unchanged. Old
unbounded test340375/340485 still live, not interrupted or claimed cleaned.

## UI-042 large custom-frame lifecycle evidence (2026-10-10)

Actual parent/child CLI120KiB custom frame prefix-only read plus undrained quit/EOF/
closed-output now verifies owning worker reaping, with separate child tool approval.
Current related68 checks pass (/tmp/ui042-backpressure-final.log); static checks
clean. Complete custom client/terminal acceptance remains open;24 unfinished
unchanged. Old owned unbounded test340375/340485 remains live, not interrupted or
claimed cleaned. No final full-root completion declaration.

## UI-042 in-flight frame admission safety (2026-10-10)

Parent/child successful input/resize admission now synchronously retires delivered
frame until fresh render, refusing stale callback input. Held-render and child
immediate negative plus related61-test current gate pass /tmp/ui042-inflight-final.log.
Initial new test invocation38150/PID401202 reached red terminal exit, not interrupted;
older unbounded340375/340485 still live. Complete custom/client/terminal acceptance
remains open,24 unfinished unchanged. No final workspace completion declaration.

## UI-042 superseded-render progress (2026-10-10)

Older render results completed after a newer geometry admission are explicitly
marked superseded by the broker and withheld by parent/child RPC receipt/transport
paths. Actual held-worker and broker plus related158-test gate passes
/tmp/ui042-superseded-final.log. Complete acceptance remains open,24 unfinished
unchanged. Old test340375/340485 still live, not interrupted or claimed cleaned.

## UI-042 key/paste contract verification (2026-10-10)

Actual parent/child custom workers and actual parent CLI now verify press-only /
enhanced repeat-release events, explicit press completion and Unicode multiline
atomic paste. Current related69-test gate /tmp/ui042-keyboard-final.log passes;
complete custom requirement audit remains open,24 unfinished unchanged. No production
change this increment, no interruption or completion claim for old340375/340485.

## UI-042 cross-thread frame memory bound (2026-10-10)

Parent/child frame publication now reserves16-frame/2MiB bounded queued delivery,
releases reservations on failure, and cancels owned views on overflow. Current
related59-test gate passes /tmp/ui042-delivery-final.log; current static/build bytes
verified. UI-042 broader acceptance stays open;24 unfinished unchanged. Older owned
unbounded340375/340485 still live, not interrupted or claimed cleaned.

## UI-042 explicit frame capacity error progress (2026-10-10)

Parent/child frame-overflow cancellation now reports named scoped capacity errors,
bounded/deduplicated independently, with stale scope/generation rejection. Actual
worker cancellation/unchanged policy and related76-test gate pass
/tmp/ui042-overflow-final.log. Full acceptance stays open;24 unfinished unchanged.
Old unbounded340375/340485 still live, unmodified; no final root completion claim.

## UI-042 output refusal safety (2026-10-10)

Parent/child frame authority is cached only after output admission; refused output
cancels the original view without cached grant. Actual worker tests and related64
checks pass /tmp/ui042-output-final.log; static/artifact bytes verified. Remaining
count24 unchanged; full contract and final regression gates stay open.

## UI-042 child terminal-data implementation (2026-10-10)

Independent scoped child terminal hook RPC implemented with exact registry/generation,
editable controls/atomic paste, post-hook safety and actual pending approval admission
gates. Real worker/CLI and related110-test gate pass /tmp/ui042-terminal-final.log.
Full requirement audit remains open;24 unfinished unchanged. Old unbounded340375/
340485 still live, not interrupted or claimed cleaned. Final Linux/root gates retained.

## UI-042 terminal cancellation/approval evidence (2026-10-10)

Early terminal task cancellation now emits exactly one original-owner failure
receipt without callback execution. Approval admitted after an actual hook result
blocks transformed data/consume delivery without retry. Related127-test current
gate passes /tmp/ui042-terminal-races-final.log; static/artifact checks pass.
UI-042 full acceptance remains open,24 unfinished unchanged. Older owned unbounded
340375/340485 remains live and untouched; no final root completion claim.

## UI-042 finite contract audit and current verification checkpoint (2026-10-10)

Previous turn PROGRESS: terminal cancellation/late-approval receipts. Consolidate
remaining custom contracts into `agent-ui-rpc-custom-contract-audit.md`, subordinate
to UI-042, no duplicate requirements. Add actual parent/child worker background
repaint verification and actual CLI held-terminal quit/EOF/closed-output/deadline
worker reaping. Source audit found custom key admission lacked authoritative pending
approval gating; fix BOTH paths and verify actual unpainted parent approval refusal
without retiring frame/sequence. These checks do not assert remote physical painting.
Current complete named custom/child/overlay/keyboard/refresh/queue/output/terminal/
capability/broker matrix **221 passed,no skips** (147.55s),
`/tmp/ui042-current-contract.log`; static Ruff/all-seven strict mypy486 clean
`/tmp/ui042-current-contract-static.log`. Current approval admission modules verified
in offline Coding wheel/sdist `/tmp/ui042-contract-wheels`. Only owned offline peers,
no downloads/model-auth calls/service restart/unrelated overwrite/test interruption.

UI-042 moves to 待验证, NOT complete: three finite remaining actions are structured
renderer parent/child RPC integration, physical current-Linux RPC terminal client,
and final integrated contract closure; fix any missing implementation they expose.
Do not repeat undefined broader API OPEN or silently waive those actions. UI-039
integration and final Linux safety/terminal/frozen root gates stay independent.
Original old invocationhandle73617/pytest340375/CLI340485 still confirmed live,
unmodified, not claimed cleaned. **24 unfinished** =11待实现+8待验证+5缺外部环境;
36已完成,4延期,64 total unique requirements. Goal active, original scope retained.

## UI-042 finite action1 — structured RPC renderer integration (2026-10-10)

Previous goal turn PROGRESS: finite custom audit and child terminal lifecycle evidence.
Recheck ledger24 unfinished and old unbounded340375/340485 live, not interrupted.
Run actual layout worker through parent/child RPC in inline/fullscreen/positioned
overlay, verifying row/column/box/virtual viewport/layer, Unicode/narrow/cursor bounds,
vertical/horizontal scroll, layered editor focus, nested question and unchanged
parent history/policy. Initial integration exposed REAL RPC sanitizer deleting ZWJ:
width-calibrated emoji expanded, breaking frame geometry. Use vetted safe_custom_line
then strip styles, preserving ZWJ/combining graphemes while dropping unsafe terminal
commands. Both parent/child frame projections use the same contract. Not a global
loosening of text/credential sanitization. Direct sanitizer regression added.

Initial red /tmp/ui042-layout-tests.log records width defect; a transient indentation
edit error /tmp/ui042-layout-fixed.log collected no tests; test child packet alias
corrected after /tmp/ui042-layout-current.log. Current owner/presentation cases6 pass
/tmp/ui042-layout-final.log. Final current full layout/custom/delivery/child/overlay/
keyboard/backpressure group **73 passed,no skips** (120.39s),
/tmp/ui042-layout-integration.log. Ruff/all-seven strict mypy486 clean
/tmp/ui042-layout-static.log; three affected modules byte-verified in offline Coding
wheel/sdist /tmp/ui042-layout-wheels, whitespace clean. New owned check terminal;
no downloads/model-auth calls/user-service restart/unrelated overwrite.

Finite audit action1 is VERIFIED; actions2 physical Linux RPC terminal and3 final
integrated contract closure remain unproven. UI-042 stays 待验证, not complete.
64 requirements:36 complete,11待实现,8待验证,5缺外部环境,4延期;
**24 non-deferred unfinished**. UI-039 integration and final Linux safety/full-root
requirements remain separate; older owned unbounded invocation still live and untouched.

## UI-042 finite action2 — physical Linux RPC terminal client (2026-10-10)

Previous turn PROGRESS: structured RPC renderer integration/ZWJ geometry fixed.
Owned test frontend launches actual offline Coding RPC subprocess, receives real
parent/child worker layout frames, and paints them through shipped ExtensionCustom/
TUI components inside private tmux. Physical Unicode paste, layer focus/obscured
editor refusal, narrow resize, nested input/return/new frame, final explicit Enter,
parent editor/hardware cursor and exact termios/native main-buffer recovery verified.
Child path has separate actual tool approval before view admission; invalid approval
text cannot grant tool/view. Positive painted revisions and physical resize/paste
receipts logged. No model call on parent command path, no filesystem mutation.

First frontend attempted a second input before fresh frame and relied on obsolete
resize pending counters. Fix test frontend to retire its local frame after enqueue,
wait for matching returned frame, and settle latest geometry accounting; service
stale-frame checks remain unchanged. Host Ctrl+C clears parent draft for explicit
/quit. Historical red physical logs retained, not replaced by implied success.
Final current owned physical fixture **2 passed** (9.12s),
/tmp/ui042-physical-current-fixture.log; physical/structured/keyboard group **15 passed**
(38.01s), /tmp/ui042-physical-verified.log. Ruff/all-seven strict mypy486 clean
/tmp/ui042-physical-final-static.log; whitespace clean. Only tests/offline fixture
changed, no distribution implementation change. New checks terminal; no downloads,
model-auth/user-service restart/unrelated overwrite. Old original unbounded test
340375/340485 still live at inspection, not interrupted or claimed cleaned.

Finite UI-042 audit actions1–2 verified; action3 final integrated contract closure
remains. UI-042 stays 待验证. This is a test frontend, not a shipped remote client,
Pi wire conformance or physical arbitrary-client painting evidence.64 requirements:
36 complete,11待实现,8待验证,5缺外部环境,4延期; **24 non-deferred unfinished**.
Final Linux/full-root/permission/transport gates remain unchanged. Goal active.

## UI-042 COMPLETE — frozen complete RPC/custom integrated contract (2026-10-10)

Previous goal turn PROGRESS: actual Linux RPC terminal acceptance action2 verified.
Final audit inspects public capability registration, actual command reader routing,
parent/child handlers/invocation scope, binary/permission boundaries, broker frame
painted/geometry/generation/sequence and disposal contracts. All finite actions1–3
in agent-ui-rpc-custom-contract-audit.md now have authoritative scope-matched evidence.
One complete current RPC+custom broker run **579 passed,no skips** (334.55s),
/tmp/ui042-final-rpc.log; includes actual Linux physical terminal parent/child paths.
All981 source/test/config files unchanged before/mid/after snapshots
/tmp/ui042-final-source-{before,mid,after}.json. New verification polled to actual
terminal exit, never restarted due to observation timeout.

Ruff/all-seven strict mypy486 clean /tmp/ui042-final-static.log. Current six Coding
contract modules byte-verified in offline wheel/sdist /tmp/ui042-final-wheels;
whitespace clean. No downloads, model/auth calls, publish, user-service restart or
unrelated source overwrite. Source freeze preserved during verification.

UI-042 marked 已完成 for its explicit applicable named RPC contract; previous
repeated generic full-client/terminal/API OPEN is superseded by this finite audit,
not counted again. No arbitrary third-party frontend/Pi wire/native nonLinux or
final whole-root completion claim. UI-039 event integration, UI-038 old invocation
closeout and final Linux/root regression/lifecycle gates remain independent.
Old unboundedtesthandle73617/pytest340375/CLI340485 still confirmed live; not
interrupted or declared reaped. Its outcome is not part of the passing new run.

Current unique counts:64 total,37已完成,11待实现,7待验证,5缺外部环境,4延期;
**23 non-deferred unfinished requirements**. Goal remains active, scope unchanged.

## UI-039 COMPLETE — actual custom-callback transient integration (2026-10-10)

Previous turn PROGRESS: UI-042 finite protocol completed after frozen579-case audit.
Verify UI-039's remaining actual custom callback event integration, not duplicate
UI-042 implementation. Parent/child original custom fixture callbacks emit notice,
status and widget; tests verify original scopes/generations, redaction, ordered
contributions before returned frame, no persistent-history impersonation, and
independent contribution withdrawal without canceling custom view authority. Actual
owned workers complete with one explicit callback per input; no extra model or tool
loop, no parent history/policy changes. Focused integration2 passed
/tmp/ui039-custom-integration.log; no production source change required.

One full current RPC/custom broker matrix **581 passed,no skips** (337.62s),
/tmp/ui039-final-rpc.log, includes actual CLI child/parent transient lifecycle,
contributions, slow/closed-output/control-memory, custom physical Linux terminal,
queue/ownership/approval/withdrawal negatives and all14 public AgentEvent projections.
Source snapshots before/mid/after unchanged across982 files:
/tmp/ui039-final-source-{before,mid,after}.json. Ruff/all-seven strict mypy486 clean
/tmp/ui039-final-static.log; whitespace clean. New owned run polled to actual exit,
not restarted on timeout; no production artifact changed, no dependency download/
model-auth calls/user-service restart/unrelated overwrite.

UI-039 marked 已完成 for explicit named transient/tool-progress/custom integration;
UI-042 owns custom protocol implementation once. Historical generic richer/custom
OPEN clauses superseded by these current acceptance records, not additional counts.
No all transient history replay/native nonLinux/Pi wire/general-client claims. Original
unbounded-testhandle73617/pytest340375/CLI340485 still live and untouched; its own
closeout remains UI-038/final UI-064 lifecycle, not claimed passed by new evidence.
Final whole-root/Linux terminal/security requirements remain separate.

Current64 unique requirements:38已完成,10待实现,7待验证,5缺外部环境,4延期;
**22 non-deferred unfinished**. Goal remains active with original scope unchanged.

## UI-036 — independently negotiated parent RPC terminal hook data (2026-10-10)

Previous turn PROGRESS: UI-039 completed with actual custom callback integration.
Inspect actual parent/child prompt hook runtime and RPC reader: parent terminal hook
transport absent while child terminal_input implemented. Add RpcTerminal with exact
independent terminal_input negotiation, captured connection owner/session/revision/
worker generation, bounded request identities and one active owned job. Only shared
editable-key or atomic-paste data; no prompt/model/tool submission or terminal writer.
Before/after approval/dialog/context lifecycle checks,5s deadline, transformation
revalidation, cancellation/withdrawal/EOF cleanup and single early-cancel receipt.
Reader integrates actual terminal_input/terminal_cancel; prompt/navigation/extension
and child terminal admission cannot race the owning parent terminal operation.

Actual worker transform/paste/consume, early cancel/withdraw, foreign/reused owner,
hanging cancel/deadline/reap, unsafe transform and late approval negatives verified.
Actual offline CLI activates extension, negotiates current worker metadata and returns
editing data without model/history mutation. Related parent+child terminal/RPC/control/
navigation/capability gate **70 passed**(67.63s), /tmp/ui036-parent-final.log; final
current parent source check **11 passed**(17.99s), /tmp/ui036-parent-current.log.
Ruff/all-seven strict mypy487 clean /tmp/ui036-static-verified.log; current terminal.py/
rpc_mode.py bytes verified in offline Coding wheel/sdist /tmp/ui036-wheels;
whitespace clean. New test processes terminal; no downloads/model-auth/user-service
restart/unrelated overwrite. Old unbounded340375/340485 still live, not interrupted.

UI-036 stays 待实现: broader raw input surface contracts/final audit still required,
not automatically completed from this concrete missing parent transport. Existing
native parent/child input, protected keys and permissions preserved; no vendor wire
compatibility or arbitrary terminal interception claim.64 unique requirements:
38 complete,10 implementation,7 verification,5 missing environment,4 deferred;
**22 non-deferred unfinished**. Full original Linux/root gates unchanged. Goal active.

## UI-036 — cross-parent/child unpublished dialog input ownership (2026-10-10)

Previous turn PROGRESS: parent RPC terminal data implemented/verified. Source audit
found parent hook ignored child pending-dialog ownership; child hook ignored parent
and other-child dialogs. Add authoritative ChildExtensions.has_pending_dialogs
accessor under its own lock. Parent/child RPC terminal admission and post-hook receipt
checks reject any parent/child pending dialog, including unpublished broker admission.
No invocation callbacks or parent history/permission effects introduced.

Actual broker admission tests deny parent hook before unpublished child question;
child question admitted after actual parent hook result rejects data/consume with
one callback/no retry. Child hook likewise denied during unpublished parent question,
zero callback count. Related parent/child terminal lifecycle/custom/RPC/capability
gate **84 passed**(92.74s), /tmp/ui036-dialog-final.log; first34-test gate overlaps.
Ruff/all-seven strict mypy487 clean /tmp/ui036-dialog-static.log; three affected
Coding/Agent modules byte-verified in offline wheels/sdists /tmp/ui036-dialog-wheels;
whitespace clean. New tests polled to terminal exit, no observation-timeout restart,
download/model-auth/user-service restart or unrelated overwrite.

UI-036 stays 待实现 pending broader input surface finite audit/acceptance, not
automatically closed by these lifecycle gates. Old unboundedtesthandle73617/pytest
340375/CLI340485 remains authoritatively live, untouched.64 unique requirements:
38 complete,10 implementation,7 verification,5 missing external environment,
4 deferred; **22 non-deferred unfinished**. Final Linux/full-root gates unchanged.

## UI-036 — actual parent/child CLI terminal-hook lifecycle (2026-10-10)

Previous turn PROGRESS: cross-dialog admission guards. Extend the existing bounded
owned CLI lifecycle fixture to parent and child hooks, rather than duplicate a peer.
Actual worker entered marker and exact /proc worker/root identity prove hook admission;
quit/EOF/closed-output/deadline cause CLI exit and owned worker reaping. Closed-output
case explicitly requests status to exercise a failed write while stdin remains open;
parent editing never invokes model, child producer settles, no tool mutation.
Current parent/child terminal/RPC/capability suite58 passed (64.44s),
/tmp/ui036-lifecycle-final.log; focused lifecycle8 passed (21.60s),
/tmp/ui036-parent-lifecycle.log. Ruff/all-seven strict mypy487 clean
/tmp/ui036-lifecycle-static.log; whitespace clean. Only test changed, no production
artifact changes. New tests terminal, no downloads/auth/model calls/service restart/
unrelated overwrite. Old unbounded pytest340375/CLI340485 still live and untouched.
UI-036 remains open for named broader surface acceptance audit.64 requirements:
38 completed,10 implementation,7 verification,5 missing environment,4 deferred;
22 non-deferred unfinished. Final whole-root/current Linux safety gates retained.

## UI-036 — prompt release boundary and finite surface audit (2026-10-10)

Previous turn PROGRESS: actual parent/child CLI hook cleanup verified. Inspect input
phase contract: release could be sent to a text-transform hook and become inserted
press text. Shared native prompt interception now excludes release before hook
without invalidating held edits; transformed releases refused, no partial submission.
RPC editing data rejects release on input/result, preserves encoded repeat; atomic
paste stays inert. Custom release support remains its independently declared UI-042
contract. Actual Coding/Claw owners verify release triggers zero worker callbacks.

Current shared/key/RPC/native owner group127 passed(72.53s),
/tmp/ui036-release-final.log; actual Linux both-client prompt/child editor pause/
recovery/resize/native terminal6 passed(14.59s), /tmp/ui036-release-terminal.log.
Ruff/all-seven strict mypy487 clean /tmp/ui036-release-current-static.log;
Coding/TUI current affected source bytes verified in offline wheel/sdist
/tmp/ui036-release-wheels, whitespace clean. New owned checks reached terminal exit;
old unbounded340375/340485 remain live, not interrupted/restarted/claimed cleaned.

Named input surface contract audit docs/agent-ui-input-surface-audit.md now defines
three finite remaining actions: reconcile concrete historical/reference raw contracts,
physical RPC editing-data application, and integrated final matrix. Preserve missing
applicable implementation rather than silently narrowing to editable-only input.
UI-036 stays待实现;64 unique requirements38 completed,10 implementation,7 verification,
5 missing environment,4 deferred;22 non-deferred unfinished. Final Linux/root gates
unchanged; no downloads/model-auth/user-service restart/unrelated overwrite.

## UI-036 — effective configured RPC input keymap (2026-10-10)

Previous turn PROGRESS: release boundary and finite input-surface audit. Actual RPC
editable_data used only default bindings, unlike native hosts' configured keymap.
Load bounded keybindings.json at parent/child controller construction and pass the
immutable effective keymap through input and transformed-result validation. Remapped
send/external editor/save/extension command keys cannot become hook editing input.
Invalid configured keymap fails closed for key data instead of silently treating
fallback defaults as authority. Atomic paste remains data, not parsed shortcuts.

Actual parent/child worker tests configured and malformed keymap refuse before hook,
zero callbacks; transformed remapped host key refused after one actual hook without
result/replay. Existing default/RPC/keymap/CLI lifecycle group **90 passed**(71.13s),
/tmp/ui036-keymap-final.log; focused45-case gate /tmp/ui036-keymap-tests.log overlaps.
Ruff/all-seven strict mypy487 clean /tmp/ui036-keymap-static.log; three current Coding
modules byte-verified in offline wheel/sdist /tmp/ui036-keymap-wheels; whitespace
clean. New owned checks terminal; no downloads/model-auth/service restart/unrelated
overwrite. Original unboundedtest340375/CLI340485 still live and untouched.

UI-036 remains待实现 pending finite raw contract reconciliation, physical RPC editing
data application and final input matrix.64 total:38 complete,10 implementation,
7 verification,5 missing environment,4 deferred;22 non-deferred unfinished.
Original current Linux safety/terminal/full-root requirements unchanged. Goal active.

## UI-036 finite action2 — physical RPC hook data application (2026-10-10)

Previous turn PROGRESS: configured RPC keymap guards. Actual owned Linux private
terminal frontend now invokes parent/child original RPC hooks and applies returned
data through shipped PromptTerminalInterceptor/InputPanel, not a fabricated string
or pure JSON assertion. Physical Unicode paste/hardware cursor, original encoded
repeat, release no-hook dispatch, held hook Escape original-owner cancellation,
paused plain-text recovery and final exact termios/main-buffer restoration verified.
Worker log proves actual callbacks/input kind, no parent model call, child producer
settles on exit. New test frontend does not claim a shipped remote client.

Physical parent/child2 passed(5.33s), /tmp/ui036-physical-edit.log; current related
hook/CLI/keymap/editor group **88 passed,no skips**(69.96s),
/tmp/ui036-physical-edit-final.log. Ruff/all-seven strict mypy487 clean
/tmp/ui036-physical-edit-static.log; whitespace clean. Only owned test frontend/tests
added, no production/distribution change. New checks polled to actual terminal exit;
no downloads/auth/model network/user-service restart/unrelated overwrite.

Finite input audit action2 VERIFIED; action1 concrete raw contract reconciliation
and action3 integrated input matrix remain open. UI-036 still待实现, no blanket raw
parity claim.64 requirements:38 completed,10 implementation,7 verification,
5 missing environment,4 deferred; **22 non-deferred unfinished**. Old original
unbounded test340375/340485 still live, untouched; final Linux/root safety gates retained.

## UI-036 — actual runtime shortcut-aware RPC editing boundary (2026-10-10)

Previous turn PROGRESS: physical RPC editor data application. Inspect current
runtime shortcuts and native ExtensionShortcuts conflict/override/disable rules;
RPC editing previously checked configured actions but not owned active extension
shortcut inventory. Parent/child input and transformed output now use actual runtime
shortcuts via shared native effective resolver. A registered action cannot be routed
as editing data; conflicting disabled shortcut does not revoke native editor action.
Atomic paste stays data. Actual parent/child workers verify registered shortcut input
refusal before callbacks and transformed shortcut result refusal after one callback.
Initial temporary fixture replacement accidentally also matched its nested enable
statement; restrict replacement to first occurrence, no production change for fixture.
Red /tmp/ui036-shortcut-inventory.log retained; current related hook/keymap/physical
editor/shortcut gate **60 passed** (49.05s), /tmp/ui036-shortcuts-final.log.
Ruff/all-seven strict mypy487 clean /tmp/ui036-shortcuts-static.log; three current
Coding modules byte-verified in offline wheel/sdist /tmp/ui036-shortcuts-wheels;
whitespace clean. New verification terminal; old340375/340485 still live, untouched.
Primary Pi fetch yielded no usable webpage; bounded curl failed connection reset,
so no new upstream comparison or completeness assertion is made. UI-036 finite
reference/raw-contract reconciliation remains open; no guessed API or scope reduction.
64 unique requirements:38 completed,10 implementation,7 verification,5 missing
external environment,4 deferred;22 non-deferred unfinished. Final current Linux
safety/terminal/full-root gates retained; no downloads/model-auth/service restart.

## UI-036 — approval open/close admission revision fences (2026-10-10)

Previous turn PROGRESS: runtime shortcut inventory safety. Inspect pending-only
approval checks: a question could open and close during a hook then disappear before
result delivery, allowing old edit across input ownership change. PermissionManager
now exposes monotonic admission_revision, incremented under its lock on actual
approval admission. Parent/child RPC hooks snapshot parent/child manager identities
and revisions; both before/after callback checks refuse changed approval admission
even after all pending requests settle. Manager inventory replacement also refuses.
No policy escalation, retry, input submission or rollback claim.

Tests hold actual worker hook results, admit and deny actual parent/child approvals,
then release hook: all four source combinations reject data/consume with one callback.
Initial related gate1 failed/69 passed in /tmp/ui036-approval-revision-final.log due
to missing runtime shortcut registration in one existing parent fixture. Do not hide
that red result: exact isolated keymap13 passed /tmp/ui036-approval-keymap-recheck.log,
and a fresh current complete related gate **70 passed**(74.24s),
/tmp/ui036-approval-current.log. No production shortcut behavior changed to suppress
that fixture. Ruff/all-seven strict mypy487 clean /tmp/ui036-approval-revision-static.log;
three current modules byte-verified in offline Coding wheel/sdist
/tmp/ui036-approval-revision-wheels; whitespace clean. New checks terminal; no
model-auth/download/user-service restart/unrelated overwrite. Old unbounded pytest
340375/CLI340485 still live and untouched.

UI-036 stays待实现 pending finite original raw contract reconciliation and final input
matrix; action2 physical RPC editor evidence remains verified.64 requirements:
38 completed,10 implementation,7 verification,5 missing environment,4 deferred;
**22 non-deferred unfinished**. Final current Linux/full-root requirements retained.

## UI-038 owned Linux hang cleanup and canonical acceptance (2026-10-10)

User-authorized exception applied only after exact identity/ownership/wait diagnosis.
Old test340375 misread an initial disabled capability and waited without deadline;
peer340485 waited for input. Normal peer quit woke pytest, which entered the next
old-loaded broken parameter. Revalidated that historical pytest and new direct
owned peer520456; SIGTERM only that pytest, normal quit peer. Original handle73617
exit143 is NOT passing evidence; all three processes gone, no unrelated/service
signals. Detailed evidence `agent-ui-rpc-canonical-audit.md` and
`/tmp/ui038-hung-cleanup.json`. Current test keeps undrained binary/child cleanup
assertions, uses bounded byte reads from startup and a shared capability deadline,
with two explicit stale/missing-receipt/no-prefetch regressions. Current64 targeted
and118 integrated passed/no skips; Ruff/strict seven-namespace mypy487 clean.
UI-038 now complete; UI-064 final matrix and UI-055 frozen root remain open.
**64 unique requirements:39 complete,10 implement,6 verify,5 missing external,
4 deferred;21 non-deferred unfinished.** Only Windows/macOS/other non-current-Linux
native validation is deferred; no scope change to Linux or implementation gaps.

## UI-036 current integrated named input matrix (2026-10-10)

Continued active goal immediately after UI-038 cleanup/acceptance. Current eleven
files spanning both native clients, shared input/stdin, real extension workers,
RPC parent/child lifecycle/keymap/approval-admission revision and physical editing
passed **178/no skips,117.29s** `/tmp/ui036-current-integrated-input.log`; actual
handle62744 exit0. Finite action3 checkpoint recorded, action1 original raw API
reconciliation still open (primary reference fetch again reset; no content/latest
claim). Keep UI-036待实现 rather than inventing or silently deleting broader scope.
No extra IDs or count changes:64 total,39 complete,10 implement,6 verify,5 external,
4 deferred;21 non-deferred unfinished. Final full-root/static/artifact/example and
Linux all-host safety/terminal requirements remain independent and unfinished.

## UI-046 prospective history/handled admission crash boundary (2026-10-10)

Previous goal turn PROGRESS: UI-038 complete and real178 integrated input evidence.
Current turn closes historical consumption/history flush-before window through
prospective typed history/handled feedback sidecar before public receipt; current
actual Coding/Claw child factories wired, initial admission included. Queue settlement
remains independently awaited; crash recovery explicitly uncertain/no auto replay,
not tool exact-once. Base-record digest fences orphan sidecar against newer terminal
including interrupted. Failed/canceled admission keeps uncertain recovery. Actual
Claw socket cancel race diagnosed (early storage guard refused cancellation); cancel/
close now enter existing writer-awaiting cleanup rather than requiring retry, ordinary
queue edits still denied. Dedicated held writer and real host tests prove it.
`agent-ui-input-admission-audit.md`: owned Linux SIGKILL six boundaries for transformed
image input/handled; final current229 passed/no skips70.23s; Ruff/strict mypy487 clean;
five source modules byte-verified in current agent/coding/claw wheel/sdist. Historical
red logs retained. UI-046 complete; current64 IDs:40 complete,9 implement,6 verify,
5 external,4 deferred; **20 non-deferred unfinished**. Original Linux/final-root and
other implementation requirements unchanged. Goal active, no publication/restarts.

## UI-047 draft debounce/inflight crash acceptance (2026-10-10)

Previous turn PROGRESS UI-046; continued full active goal. Current both-host parent/
child durability labels pending/writing/committed/uncertain, stale receipt not latest
edit, visible enable caveat. Owned Linux parent/child five-window SIGKILL recovers
only complete published old/new snapshot as uncertain/read-only, no policy/replay.
Initial link-publication private same-inode alias recovered without mutating reads;
explicit reviewed discard removes only verified alias. Actual Claw offline first-run
physical failure revealed absent app parent; pinned nofollow owned0700 creation fixed,
negative symlink/unsafe parent/external hardlink preserved. Actual both-client tmux
resize/cursor/main-buffer/termios passed. Detailed protocol/red/evidence
`agent-ui-draft-crash-audit.md`; final170/no skips14.46s, Ruff/strict mypy487 clean,
six changed modules current wheel/sdist byte verified. UI-047 complete, not per-key
or physical-loss guarantee. **64 IDs:41 complete,8 implement,6 verify,5 external,
4 deferred;19 non-deferred unfinished.** Final Linux/full-root gates remain required.

## UI-045 non-Linux credential implementation, native validation deferred (2026-10-11)

Previous goal turn PROGRESS UI-047; current actual explicit keychain/windows-credential
backend selection, isolated native ABI implementation, full bounded WinCred chunks/
manifest/mutex and noninteractive Keychain calls. No token argv/env/files/fallback.
Original manager single-use painted UI consent/restore/forget integrated both hosts;
Linux contract doubles clearly separate from native execution. Current66/no skips
18.58s; Ruff/strict mypy488 clean; three production module wheel/sdist exact. Detailed
scope/evidence `agent-ui-native-credential-audit.md`. UI-045 implementation complete;
UI-062 native macOS/Windows execution remains deferred/not passed, not a blocker.
64 unique IDs:42 complete,7 implement,6 verify,5 external,4 deferred;
**18 non-deferred unfinished**. Final Linux/root/static/artifact/safety gates retained.

## UI-048 finite client capability matrix and refusal safety (2026-10-11)

Previous turn PROGRESS UI-045 implementation completed. Inspected primary installed
SDK2.3.1 ClientCapabilitiesSchema and mapped concrete roots/sampling/elicitation/
tasks/experimental/extensions instead of undefined broader conformance. Current
shared honest wire inventory and strict bounded reverse refusal/ping responder with
real legacy stdio correlations and HTTP no-unsolicited/invalid-id tests implemented.
SDK/current transport/two native host groups78 pass/no skips34.78s,static489 clean,
four production modules wheel/sdist exact. `agent-ui-mcp-client-capability-audit.md`
records finite missing handler/consent/transport work. **UI-048 remains待实现**;
unsupported rejection is not feature completion.64 unique IDs:42 complete,7 implement,
6 verify,5 external,4 deferred;18 non-deferred unfinished. Final Linux/root scope intact.

## UI-048 explicit legacy roots metadata implementation (2026-10-11)

Previous turn PROGRESS finite capability matrix/strict reverse refusal. Current roots
config/connection-owned legacy handler actual stdio/HTTP SSE and both hosts implemented;
no implicit cwd/file/tool/model grants, bounded unique decoded file URIs, modern
capability omitted, disconnect/failure withdraw and reused transport resets negotiation.
Current103/no skips36.20s,static489 clean,five modules wheel/sdist exact; fixture-only
red import issue retained and corrected. Detailed remaining scope/evidence in
`agent-ui-mcp-client-capability-audit.md`. Dynamic reviewed roots/listChanged/modern
counterpart, sampling/elicitation/tasks remain UI-048待实现, not waived by static roots.
64 IDs unchanged:42 complete,7 implement,6 verify,5 external,4 deferred;18 unfinished.
Full Linux/final-root goal active; no publication/user-service restart.

## UI-048 dynamic roots review/notification manager protocol (2026-10-11)

Previous turn PROGRESS static roots real transports/hosts. Current connection/view/
revision/expiry/single-use prepare/commit/cancel protocol narrows/re-enables only
configured ceiling, empty-list withdrawal/listChanged notification, concurrent
publication guard and real bridge cancellation. Actual stdio/HTTP peer verifies
changed reverse list and notification without tool/file/model replay.110 current
integrated pass/no skips36.79s; static489 clean; two modules wheel/sdist exact.
`agent-ui-mcp-client-capability-audit.md` retains actual native dynamic review/Claw
wiring next action plus sampling/elicitation/tasks; SDK protocol is not native UI
completion. UI-048 still待实现.64 IDs unchanged42 complete/7 implement/6 verify/
5 external/4 deferred;18 non-deferred unfinished. Full Linux/final-root scope intact.

## UI-048 actual Coding dynamic roots review UI (2026-10-11)

Previous turn PROGRESS dynamic manager review/notification. Current shared full-page
root JSON/review component wired actual Coding native UI/session/worker, explicit
painted focus/resize/press/single-use proof and idle/view cancellation/close detach.
Parent draft/no model/history unchanged; real peer sees one notification. Claw fails
closed with no request, not prompt fallback; Claw independently negotiated dynamic
transport/UI still unfinished, as are two-host physical and modern/sampling/elicitation/
task contracts. Current115 pass/no skips32.14s,static490 clean,six changed modules
wheel/sdist exact; original dispatch red retained/fixed. Detail authoritative audit
`agent-ui-mcp-client-capability-audit.md`. UI-048待实现;64 IDs42 complete/7 implement/
6 verify/5 external/4 deferred;18 non-deferred unfinished. Goal remains active.

## UI-048 Claw dynamic roots and two-host physical verification (2026-10-11)

Previous turn PROGRESS Coding reviewed native roots. Current Claw independently
negotiates mcp_roots, connection/view/session/source revision/internal manager lease,
bounded async root jobs/non-reusable IDs/idle reservation; immediate revoke on close/
withdrawal/disconnect; actual held-notification cancel proves reader ping and real
settlement, no rollback/replay. Shared painted UI on actual Claw, foreign/old viewer
negatives and both shipped native Linux tmux full review/resize/parent cursor/native
buffer/termios now verified. Initial physical tests lacked asynchronous panel-close
checkpoint; red kept, corrected physical13 pass; current broader122/no skips36.90s,
static490 clean,two Claw modules wheel/sdist exact. Detailed audit in
agent-ui-mcp-client-capability-audit.md. Legacy roots named subcontracts now covered;
UI-048 remains待实现 for applicable modern/sampling/elicitation/tasks.64 stable IDs:
42 complete,7 implement,6 verify,5 external,4 deferred;18 non-deferred unfinished.
Goal active; original final Linux/full-root gates intact, no user-service restart.

## UI-048 roots paint ownership/capacity safety hardening (2026-10-11)

Previous turn PROGRESS Claw negotiated dynamic roots/two-host physical. Current
private review snapshot/isolated public copy, bounded notice/server/control metadata,
long Unicode all-wrapped-row/no-early-confirm proof; Claw256 admission-ID exhaustion
no longer denies original-view cancel/detach, without relaxing new admission/replay.
Actual socket exhaustion regression and retained full native/transport/UI matrix
134 passed/no skips37.13s,static490 clean,two production modules wheel/sdist exact.
Detailed checkpoint in agent-ui-mcp-client-capability-audit.md. Roots safety not a
sampling/elicitation/tasks completion claim. UI-048 remains待实现;64 IDs42 complete/
7 implement/6 verify/5 external/4 deferred;18 non-deferred unfinished. Goal active.

## UI-048 sampling reverse transport foundation (2026-10-11)

Previous turn PROGRESS roots paint/capacity hardening. Current legacy sampling async
reverse dispatcher4 jobs/256 nonreplay IDs integrates stdio/HTTP, explicit handler
opt-in/default no capability, exact cancellation/bounded copied JSON/secret-free
errors/no stale replacement delivery/awaited callback cleanup. Real stdio held
handler leaves request reader responsive; HTTP response correlation unblocked.
Actual source integrated114/no skips33.61s,static491 clean,three modules wheel/sdist
exact. Sampling validation/human review/model/actual two-host handler installation
NOT supplied yet; UI-048 stays待实现 with next actions in named audit.64 IDs unchanged
42 complete/7 implement/6 verify/5 external/4 deferred;18 unfinished. Goal active.

## UI-048 sampling codec/request-result review snapshot (2026-10-11)

Previous turn PROGRESS reverse reader-independent jobs. Current bounded sampling
request/result codec and exact digest retain explicit context/model/tools preferences
as review data, not permission; unknown/nonfinite/invalid content/schema refused.
Canonical result authority separate from mutable handler input; tool membership/input
schema checked, validation threads actually settle before canceled job releases lease.
Current143/no skips33.82s,static492 clean,two modules wheel/sdist exact. Intermediate
wrong validate import collection red retained/fixed. Complete optional schema/pairing
corpus and actual native sampling input/output review/model/permission/handler wiring
remain; ordinary hosts still do not install handler. Detailed finite actions in
agent-ui-mcp-client-capability-audit.md; UI-048待实现.64 IDs unchanged42 complete/
7 implement/6 verify/5 external/4 deferred;18 unfinished. Full Linux/root goal active.

## UI-048 two-phase sampling consent owner (2026-10-11)

Current SamplingOwner original-loop/view/phase/digest/one-use receipt requires input
selection before executor and output review before peer release. Selected model/tool
subset checked, host revocation actively cancels executor and waits actual finally.
Real owned stdio test peer proves zero executor before input approval, zero peer
private output before release, output denial secret-free and ping responsive. Current
157 pass/no skips34.09s,static493 clean,new owner wheel/sdist exact; details in named
MCP audit. This is callback/publisher infrastructure, not actual model/native UI wiring;
ordinary hosts still do not register sampling. UI-048待实现, remaining actual executor/
model/context/tool gate, two-host reviewed UI/Claw/physical and other protocol contracts
unchanged.64 IDs42 complete/7 implement/6 verify/5 external/4 deferred;18 unfinished.
Goal active; full Linux/root scope preserved, no user-service restart/publication.

## UI-048 configured sampling executor (2026-10-11)

Previous turn PROGRESS dual-phase sampling owner. Current same configured-adapter
single-request executor explicit model/token/context resolver/tool proposals, no
parent/global fallback/no tool execution; bounded actual context and model image/
output capabilities, reasoning/signature withholding, failed response refusal and
stalled iterator cancel/timeout actual cleanup. Offline adapter execution through
dual-phase owner proves request/options/no premature release.171 current integrated
pass/no skips34.15s,static494 clean,new executor wheel/sdist exact. Named audit records
unsupported neutral modalities and pending native fully painted sampling UI/manager
registration/Claw independent transport/two-host physical/content/remaining protocols.
UI-048 stays待实现;64 IDs42 complete/7 implement/6 verify/5 external/4 deferred;
18 non-deferred unfinished. Goal active, final Linux/full-root scope intact.

## UI-048 explicit sampling manager registration (2026-10-11)

Previous turn PROGRESS configured sampling executor. Current explicit before-connect
host registration routes dual-phase exact lease/phase/digest answers on actual manager
loop, honest status/legacy negotiation, immediate handler revocation + awaited cleanup.
Actual stdio/configured offline adapter proves input/output review/no premature model/
peer delivery, foreign detach refusal, executor-held settlement, failed startup and
no consent/capability replay after reconnect.177 current integrated/no skips34.94s,
static494 clean,current manager wheel/sdist exact. Named audit retains native host
sampling enable/full input-output review/Claw transport/physical/content and remaining
protocol work. UI-048待实现;64 IDs42 complete/7 implement/6 verify/5 external/4 deferred;
18 unfinished. Goal active, full Linux/final-root scope intact, no service restarts.

## UI-048 sampling full-paint review component (2026-10-11)

Previous turn PROGRESS explicit sampling manager registration. Current dependency-light
input selection/full request review and output release component: digest/private snapshot,
model/token/context/tool ceiling, focus/viewport/edit invalidation, all-page distinct press
and permanent host withdrawal. Exact ASCII-escaped JSON rows preserve boundary spaces
and terminal controls; initial word-wrap red retained/fixed without weakening assertions.
204 integrated/no skips34.86s,static495 clean,new tui component wheel/sdist exact.
Component not yet installed in native Coding/Claw; next is explicit enable/manager/event/
answer routing and protected focus/physical verification. UI-048 stays待实现 with all
remaining protocol work.64 IDs42 complete/7 implement/6 verify/5 external/4 deferred;
18 non-deferred unfinished. Goal active, final Linux/full-root scope intact.

## UI-048 actual Coding sampling dual-review UI/terminal (2026-10-11)

Previous turn PROGRESS full sampling review component. Current Coding explicit idle
view/disconnected server arm, manager registration/current session-model-adapter guard,
owned queue/dual full-paint receipt answer, close/deny synchronous revoke+async cleanup
and exclusive executor auth-changing boundary. Actual peer/offline configured adapter
and physical Linux native client prove no model before input approval, no peer output
before output approval, unchanged parent/history/deny permissions, resize/full review/
cursor/main-buffer/termios. Current228 integrated/no skips48.55s,static495 clean,
three modules wheel/sdist exact. Physical fixture cursor/default-clear and transient
hint waiting reds retained/corrected, no assertions weakened. Named audit preserves
Claw independent sampling control/UI/physical and broader catalog/context/content plus
other protocols. UI-048待实现,64 IDs42 complete/7 implement/6 verify/5 external/4 deferred;
18 non-deferred unfinished. Goal active, final Linux/full-root scope intact.

## UI-048 Claw independent sampling/native physical integration (2026-10-11)

Previous turn PROGRESS actual Coding sampling dual-review/native terminal. Current
Claw independent negotiated connection-local sampling owner, actual host-loop exclusive
group/child-idle execution and shared fully painted TUI input/output/lease answer,
foreign/old-viewer/detach/withdraw/disconnect guards. Real socket/offline adapter plus
shipped native Linux tmux prove no model before input review/no peer output before
release, unchanged parent history and cursor/main-buffer/termios restoration. Stalled
provider-finally withdrawal proves pong/reader remains responsive and original group
reservation releases only after actual cleanup. Two production races found/fixed:
transient initial-connect source sentinel and late answer ACK overwriting completion;
red logs retained in named MCP audit. Final238/no skips53.57s,static496 clean,three
Claw modules wheel/sdist exact. UI-048 remains待实现 for broader catalog/context/content
and elicitation/tasks/modern contracts;64 IDs42 complete/7 implement/6 verify/5 external/
4 deferred;18 non-deferred unfinished. Full Linux/root scope preserved, goal active.

## UI-048 same-adapter sampling catalog selection (2026-10-11)

Previous turn PROGRESS Claw actual sampling/physical integration. Current both hosts
publish same-adapter streaming model IDs, private metadata pin/resolver identity and
safe catalog withdrawal guard; alternate sampling model does not change parent model/
history/draft. Actual both-host review/peer tests prove alternate output and zero model
calls after catalog withdrawal. Regression found Claw local uuid shadow and wrappers
claiming models() while opaque bases lacked catalogs; production fix preserves explicit
opaque current ID, refuses true advertised catalog withdrawal. Original red logs kept,
full existing UI/physical cases retained. Current244/no skips53.87s,static496 clean,
five modules four-package wheel/sdist exact. Named MCP audit keeps context/content/
elicitation/tasks/modern remaining requirements. UI-048待实现;64 IDs42 complete/
7 implement/6 verify/5 external/4 deferred;18 unfinished. Goal active, scope intact.

## UI-048 sampling content pairing/optional metadata (2026-10-11)

Previous turn PROGRESS same-adapter model catalog. Current original sampling tool
call/result role/identity/pending settlement validates before review, parallel result
conversion exact order/names and no host tool execution. Output duplicate proposals/
forged tool results refuse; bounded icon metadata inert/no fetch, task numeric ttl
matches locally inspected SDK, progress bool rejection. Current259/no skips53.43s,
static496 clean,codec wheel/sdist exact; existing two-host UI/physical and SDK retained.
Named audit preserves explicit extra-context disclosure, modality/schema corpus and
elicitation/task/modern remaining work. UI-048待实现;64 IDs42 complete/7 implement/
6 verify/5 external/4 deferred;18 unfinished. Goal active, original final Linux/root intact.

## UI-048 additional context disclosure review protocol (2026-10-11)

Previous turn PROGRESS sampling content pairing. Current optional explicit context
provider/manager registration adds separate context full-paint phase between input
consent and execution. Freeze exact combined messages/selection/system prompt, digest/
one-use receipt; execute frozen request with context:none so resolver cannot reread
unreviewed history. Denial/no-provider stops before adapter; host revoke settles actual
resolver finally. Real owned peer/manager/offline adapter proves snapshot mutation
isolation and input/context/output gates.266 current integrated/no skips53.81s,
static496 clean,three modules wheel/sdist exact. Actual native hosts still do not install
context provider; scoped projection/privacy/two-host/physical and other protocol work
remain in named audit. UI-048待实现;64 IDs42 complete/7 implement/6 verify/5 external/
4 deferred;18 unfinished. Goal active, original final Linux/full-root scope intact.

## UI-048 actual two-host scoped sampling context (2026-10-11)

Previous turn PROGRESS context protocol. Current both native hosts install original
history/manager-alias scoped completed MCP-turn projection, redacted text/tool arguments,
no unrelated/local/system/memory/custom/reasoning/signature sharing. Ambiguous call IDs/
foreign result provenance refuse; original immutable capture verified. Actual two-host
full context approval/denial, no model before approval and parent history preservation;
physical both-host input/context/output/resize/cursor/main-buffer/termios passed.
Current277 integrated/no skips59.56s,static497 clean,four modules wheel/sdist exact;
fixture catalog-before-publication red retained/fixed by actual readiness checkpoint.
Named MCP audit retains scope interoperability/modality/schema/elicitation/tasks/modern
requirements. UI-048待实现;64 IDs42 complete/7 implement/6 verify/5 external/4 deferred;
18 non-deferred unfinished. Goal active; final Linux/full-root scope intact.

## UI-048 elicitation codec/reverse transport foundation (2026-10-11)

Previous turn PROGRESS two-host scoped sampling context/physical. Current elicitation
bounded primitive/titled enum form/default/required/constraint and inert safe HTTPS URL
request-result codec; exact response validation/refusal/no implicit browser or login.
Independent reverse handler default off, shared finite jobs/nonreplay IDs and original
cancel/secret-free output; real stdio review-wait reader responsiveness and HTTP mock
correlation verified.233 integrated/no skips46.70s,static498 clean,two modules wheel/
sdist exact. This is not human approval/native host registration; manager/native form/
URL full review/Claw/physical and task/modern/remaining interop retained in named audit.
UI-048待实现;64 IDs42 complete/7 implement/6 verify/5 external/4 deferred;18 unfinished.
Goal active, original final Linux/full-root scope intact; no user-service restart.

## UI-048 elicitation owner/manager registration (2026-10-11)

Previous turn PROGRESS elicitation codec/transport. Current original-loop/view/phase/
digest/single-use request then exact reply review, explicit manager register/answer/
detach and independent reverse family withdrawal preserving other jobs/no-replay IDs.
Admission and reply-validation threads now tracked through close/actual settlement.
Real owned peer/manager proves no private reply before release, foreign lease refusal,
disconnect/reconnect, URL inert acknowledgment and pending sampling coexistence.
246 current integrated/no skips47.72s,static499 clean,three modules wheel/sdist exact.
Native form/URL review and actual host registration/Claw/physical/external interaction/
task/modern remain; no owner/manager test substitutes for native consent evidence.
UI-048待实现;64 IDs42 complete/7 implement/6 verify/5 external/4 deferred;18 unfinished.
Goal active, final Linux/full-root scope intact; no publication/service restart.

## UI-048 elicitation full-paint component (2026-10-11)

Previous turn PROGRESS elicitation owner/manager. Current request/schema/URL full
review precedes typed editing/ack; local exact reply review precedes host proposal,
canonical response-phase remains separate. Private snapshot/digest/focus/viewport/edit
and press-only consent, required/type edit preservation, permanent withdrawal and
no-data decline. No browser/model/agent dependency. Corrected fixture control chord
red retained, no assertion removed.295 current integrated/no skips48.16s,static500
clean,new component wheel/sdist exact. Actual two-host enable/event/answer/withdraw/
protected focus/physical and URL interaction/tasks/modern remain in named MCP audit.
UI-048待实现;64 IDs42 complete/7 implement/6 verify/5 external/4 deferred;18 unfinished.
Goal active; final Linux/full-root scope intact, no publication/user-service restart.

## UI-048 actual Coding elicitation UI/physical integration (2026-10-11)

Previous turn PROGRESS elicitation review component. Current Coding explicit f arm/
Alt+f revoke, original idle-view/session-context guards and manager registration;
request/full typed proposal/canonical response review routes original receipts,
close revokes before cleanup, default off/no provider/history/permission change.
Real peer form/URL release/decline/close and shipped physical Linux tmux resize/
full review/parent cursor/main-buffer/termios prove no premature private reply or model
call. URL remains inert acknowledgment, not external-navigation completion.320 current
integrated/no skips61.58s,static500 clean,three production modules wheel/sdist exact.
Named MCP audit retains Claw independent elicitation/physical, constrained form edit
recovery/URL interaction/tasks/modern/remaining interop. UI-048待实现;64 IDs42 complete/
7 implement/6 verify/5 external/4 deferred;18 unfinished. Goal active, scope intact.

## UI-048 Claw independent elicitation/full native review (2026-10-11 local)

Previous turn PROGRESS actual Coding elicitation. Current Claw independent capability/
connection-view-session-revision-manager lease register/answer/detach, explicit default
OFF, async withdrawal/close, separate sampling authority and full request/proposal/
canonical response UI. Old/foreign/withdraw/disconnect and permanently disabled local
reviews tested. Shipped physical Linux form/URL acknowledgment full painting/resize/
parent cursor/main-buffer/termios passed, no model/history changes or automatic browser.
Fixture initially discarded review events racing ACK; red retained and fixed by deferred
receipt handling, no protocol weakening. Final330/no skips66.31s,static501 clean,
three Claw modules wheel/sdist exact. Named MCP audit retains constrained reply recovery,
URL interaction/completion, tasks/modern/remaining interop. UI-048待实现;64 IDs42 complete/
7 implement/6 verify/5 external/4 deferred;18 unfinished. Goal active, Linux/root intact.

## UI-048 constrained elicitation recovery (2026-10-11)

Previous turn PROGRESS Claw elicitation integration. Current schema-invalid replies
retain explicit drafts under fresh request-phase receipts instead of losing registration;
old receipt retired, fresh full request/proposal/canonical response review required.
Coding/Claw recoverable controls do not deliver invalid peer data. Actual two-host
and Linux physical corrected reply workflows preserve parent/history and zero model
calls.334 current integrated/no skips70.21s,static501 clean,four changed production
modules wheel/sdist exact. Named MCP audit retains URL interaction/completion/tasks/
modern/remaining interoperability. UI-048待实现;64 IDs42 complete/7 implement/
6 verify/5 external/4 deferred;18 unfinished. Goal active, final Linux/root scope intact.

## UI-048 legacy URL completion ownership (2026-10-11 local)

Previous turn PROGRESS constrained reply recovery. Current pending URL acknowledgment
and peer-reported completion separated, approved owner ID/deadline/dedup/foreign/early/
expired/closed guard. Legacy stdio/HTTP notice dispatch, current removed notice refusal,
both native UI pending/complete notices and physical URL review/parent recovery prove
no automatic login/credential update/model/tool call/request replay.129 current
integrated/no skips40.55s,static501 clean,seven modules wheel/sdist exact. External
URL opening/workflow not supplied; tasks/current/remaining interoperability and final
Linux/root requirements retained. UI-048待实现;64 IDs42 complete/7 implement/6 verify/
5 external/4 deferred;18 unfinished. Goal active; no publishing/user-service restart.

## UI-048 explicit reviewed URL opening (2026-10-11 local)

Previous goal checkpoint PROGRESS URL completion notice. Current two native hosts
admit optional one-use distinct o press only after full URL review and original
registration/session/connection/view/focus/approval/dialog/overlay guards. Linux bounded
owned xdg-open helper, no shell/BROWSER override or automatic replay; cleanup only
owned helper, not user browsers/services. Opening and protocol accept/external completion
remain separate. Actual host callbacks and private Linux tmux owned helper subprocess
prove exact one URL/no launch before gesture/no premature peer result plus normal
parent/cursor/main-buffer/termios recovery; no real desktop/browser/network page opened.
Final352 current integrated/no skips67.79s,static502 clean,five production modules
wheel/sdist bytes revalidated unchanged. Detailed named MCP audit records evidence and
limitations; task/current/modality/scope/independent conformance still open. UI-048
待实现,64 IDs42 complete/7 implement/6 verify/5 external/4 deferred;18 unfinished.
Goal active; final complete root/current Linux safety scope intact, no publication/restart.

## UI-048 task-augmentation boundary (2026-10-11 local)

Previous turn PROGRESS explicit reviewed URL opening. Current task/related-task
requests cannot silently execute as ordinary synchronous sampling/elicitation:
shared reverse refusal and direct owner/executor guards, no tasks advertisement.
Real stdio/HTTP contract and direct bypass checks219 passed/no skips41.14s,
static502 clean,four modules wheel/sdist exact. Task lifecycle is still unimplemented;
refusal is not functionality completion. Details/red evidence in named MCP audit.
UI-048待实现;64 IDs42 complete/7 implement/6 verify/5 external/4 deferred;
18 non-deferred unfinished. Goal active, original Linux/full-root scope intact.

## UI-048 task registry lifecycle foundation (2026-10-11 local)

Previous turn PROGRESS unadvertised task refusal boundary. Current bounded original-loop
connection task registry creation/state/list/result/review transition/cancel/expiry/close,
no private catalog or resumed/replayed work. Cancellation-before-first-step, suppressed
late callback result, actual finally capacity, all-sibling close before waiting and
canceled control cleanup verified. Initial worker-vs-caller cancellation red retained/
fixed without weakening tests.236 integrated/no skips41.56s,static503 clean,new module
wheel/sdist exact. Registry is not installed/advertised by transport or native hosts;
actual negotiation/task wire handlers/consent routing/real transports/two-host terminal
and modern interoperability remain in named MCP audit. UI-048待实现;64 IDs42 complete/
7 implement/6 verify/5 external/4 deferred;18 unfinished. Goal active, full scope intact.

## 2026-10-11 task wire-control checkpoint (not completion)

UI-048 now has explicitly registry-installed legacy tasks/list/get/result/cancel
routing, independent bounded review/result/control lanes and sibling-first disconnect
cleanup resilient to caller cancellation. Actual stdio and real Linux loopback HTTP
SSE/response-POST tests prove reader correlation/held-finally/cancel/no replay and
no private late delivery.255 passed/no skips30.06s current17-file gate,
`/tmp/ui048-task-controls-integrated.log`; root Ruff/strict mypy503 clean and agent
wheel/sdist registry/reverse exact bytes. Details in MCP client capability audit.
Task creation still fails closed; manager/two native hosts have no registry installed.
UI-048 is still待实现, not protocol/native parity complete. Counts unchanged:64 total,
42 complete/7 implement/6 verify/5 external/4 deferred;18 non-deferred unfinished.
Final frozen root and current-Linux terminal/safety/transport gates are not replaced.

## 2026-10-11 manager/transport task creation checkpoint (not completion)

UI-048 explicit tasks=True manager registration now negotiates legacy task request
families, routes fresh task identities through registry-worker-authorized original
sampling/elicitation consent owners, preserves exact augmentation in human review,
input_required state and private output withholding, and retires individual families
only after cleanup. Older negotiated versions withdraw tasks; default/direct bypass
refusals retained. Actual stdio and Linux HTTP SSE/result-POST evidence269 pass/no
skips31.00s, `/tmp/ui048-task-creation-final-integrated.log`; root Ruff/strict503
mypy clean and ten changed production modules exact agent wheel/sdist. Native
Coding/Claw have not opted in yet: no native/physical task parity claim. UI-048 still
待实现; task TTL/status/metadata and modern/modality/schema/scope reconciliation
remain.64 IDs42 complete/7 implement/6 verify/5 external/4 deferred;18 non-deferred
unfinished. Final frozen root and current Linux safety/terminal gates stay required.

## 2026-10-11 native task opt-in checkpoint (not completion)

UI-048 Coding/Claw separate j/k task-family opt-in now reaches original manager and
independent Claw connection controls; ordinary b/f remain unchanged and Alt+b/f
retires task records with owned cleanup. Both actual hosts preserve full-painted
request/output/exact-reply consent, private task result withholding, parent draft/
history/permissions and close-before-release safety. Four private Linux tmux physical
sampling/form task paths passed8.00s; current18-file172 pass/no skips12.35s,
`/tmp/ui048-native-tasks-integrated.log`; root Ruff/strict mypy503 clean, all-four
artifacts built and five native source modules byte-exact. Initial fixture failures and
repeat-arm regression retained in MCP capability audit. UI-048 still待实现 for active
TTL/status/meta/cancellation matrix/modern/remaining interoperability.64 IDs42
complete/7 implement/6 verify/5 external/4 deferred;18 non-deferred unfinished.
Final frozen root and current-Linux safety/transport/terminal gates are not replaced.

## 2026-10-11 automatic task TTL checkpoint (not completion)

UI-048 original-loop timers now cancel/retire expired pending and completed tasks
without polling, signal each deadline independently, retain capacity until actual
finally settles, delete private result bytes, and await timers/cleanup on disconnect.
Manual sweep also signals all siblings first. Actual both-host no-approval expiry and
real HTTP SSE/result-POST withholding verified; eight physical Linux task release/
expiry paths preserve parent editor/cursor/main-buffer/termios. Final19-file192 pass/
no skips37.28s `/tmp/ui048-task-ttl-final-integrated.log`, root Ruff/strict mypy503
clean, current registry wheel/sdist exact. UI-048 still待实现 for status/meta and
cancel/timeout/withdraw matrix/modern/remaining interop.64 IDs42 complete/7 implement/
6 verify/5 external/4 deferred;18 non-deferred unfinished. Frozen complete-root/current
Linux safety/terminal/transport/static/artifact/example requirements remain intact.

## 2026-10-11 undelivered task creation checkpoint (not completion)

UI-048 creation writer error/timeout/cancel now stops/settles the original task and
retires record/timer/private result rather than leaving unknown work until TTL; reverse
slot stays owned through real cleanup. Result-read timeout only ends that read, while
cancel-control timeout still waits operation finally. Red3 tests retained, real HTTP
response-POST500 and completed-private-result cleanup verified. Final19-file199 pass/
no skips38.01s `/tmp/ui048-task-delivery-final-integrated.log` includes both native
hosts and8 physical Linux task paths; root Ruff/strict mypy503 clean/two production
agent wheel-sdist exact. UI-048 still待实现 for status/meta/cancel matrix/modern/
remaining interoperability.64 IDs42 complete/7 implement/6 verify/5 external/4 deferred;
18 non-deferred unfinished. Frozen complete-root/current-Linux final gates intact.

## 2026-10-11 bounded task status checkpoint (not completion)

UI-048 status notifications now start after creation delivery, contain only original
registry public state, use16 bounded/coalescing monitors separate from request lanes,
and stop/settle on terminal/expiry/family revoke/close. Slow/failed observers do not
change operation outcome or expose private results. Actual native stdio and real HTTP
status POSTs verified. Current20-file205 pass/no skips
`/tmp/ui048-task-status-final-integrated.log`, root Ruff/strict mypy504 clean/three
production agent wheel-sdist byte exact. UI-048 still待实现 for task metadata/applicability/
cancel matrix and modern/modal/schema/scope reconciliation.64 IDs42 complete/7 implement/
6 verify/5 external/4 deferred;18 non-deferred unfinished. Frozen final Linux/full-root
regression/static/artifact/example gates unchanged.

## 2026-10-11 task contract/schema and early-cancel UI checkpoint (not completion)

`agent-ui-mcp-task-contract.md` now maps named legacy mechanics to evidence and
three finite remaining UI-048 reconciliations. Actual task request/response/status/
original result/related metadata independently checked through pinned official schemas,
not a serving task runtime claim.12 physical Linux task release/expire/peer_cancel paths
exposed and verified fix for late catalog erasing ended review notice. Final21-file213
pass/no skips44.88s `/tmp/ui048-task-contract-final-integrated.log`, root Ruff/
strict mypy504 clean/agent-TUI builds/MCP panel source exact. UI-048 remains待实现
for modern/related conversation/modal-schema-context applicability, not vague repeated
TTL/status/cancel mechanics.64 IDs42 complete/7 implement/6 verify/5 external/4 deferred;
18 non-deferred unfinished. Frozen complete-root/Linux final gates unchanged.

## 2026-10-11 sampling choice/capability checkpoint (not completion)

UI-048 two red-proven gaps fixed: optional toolChoice.mode is preserved/default auto;
explicit none rejects provider tool proposals before release. Manager-installed original
consent owners advertise tools/context only as installed; arbitrary/default callbacks
stay off, no implicit execution/permission grant.293 pass/no skips45.29s current24-file
`/tmp/ui048-sampling-contract-integrated.log`, includes independent schemas and12 Linux
physical task paths; Ruff/strict504 mypy clean/three agent source artifact bytes exact.
UI-048 still待实现 for related conversation/modern/modal-schema-context reconciliation.
64 IDs42 complete/7 implement/6 verify/5 external/4 deferred;18 non-deferred unfinished.
Frozen complete-root/Linux final regression/safety/terminal/static/artifact gates intact.

## 2026-10-11 required-tool sampling checkpoint (not completion)

UI-048 required-tool selection now fails before executor/context if no tools approved;
canonical replies require selected schema-valid tool_use, not provider/custom text-only
bypass. Three red regressions retained, actual both-host required task zero-model/private
result withheld verified.299 pass/no skips45.81s current24-file gate
`/tmp/ui048-required-tools-integrated.log` includes12 physical Linux task paths and
explicit official schemas; root Ruff/strict mypy504 clean/two agent artifact bytes exact.
UI-048 remains待实现 for related-task/modern/modal-schema-context reconciliation.
64 IDs42 complete/7 implement/6 verify/5 external/4 deferred;18 non-deferred unfinished.
Frozen final full-root/current-Linux verification requirements unchanged.

## 2026-10-11 structured context checkpoint (not completion)

UI-048 scoped completed MCP turns now preserve matching manager-provenanced
structured results (nested credentials redacted, host wrappers excluded, null/array/
boolean preserved); invalid provenance/oversize refuses rather than dropping data.
Both hosts require independent full context review and execute frozen captured structure,
not post-capture mutations.316 pass/no skips49.76s current26-file gate includes
physical context/task paths; added pinned schema21pass3.81s; final Ruff/strict504
mypy clean/context agent artifact bytes exact. UI-048 still待实现;64 stable IDs42
complete/7 implement/6 verify/5 external/4 deferred,18 non-deferred unfinished.
Frozen complete-root/current-Linux final requirements intact.

## 2026-10-11 tool-batch ordering checkpoint (not completion)

UI-048 new assistant tool batches now refuse while prior calls remain unsettled;
same-batch parallel and settled sequential histories retained. Red regression/owner
pre-review-executor refusal evidence preserved.319 pass/no skips49.72s current26-file
`/tmp/ui048-tool-batch-integrated.log`, root Ruff/strict504 mypy clean/codec agent
wheel-sdist bytes exact. UI-048 still待实现;64 IDs42 complete/7 implement/6 verify/
5 external/4 deferred;18 non-deferred unfinished. Frozen complete-root/Linux final
verification requirements remain unchanged.

## 2026-10-11 scoped context identity checkpoint (not completion)

UI-048 independent completed-turn reused IDs now get deterministic projected identities;
context-peer collisions rekey only independently validated context before exact review.
No original history/server IDs modified, no duplicate/unsettled input repaired. Actual
both-host context approval/deny plus frozen pairing verified.322 pass/no skips49.68s
current26-file gate `/tmp/ui048-context-id-integrated.log`; root Ruff/strict504 mypy
clean/two agent artifact bytes exact. UI-048 still待实现;64 IDs42 complete/7 implement/
6 verify/5 external/4 deferred;18 non-deferred unfinished. Frozen final Linux/full-root
verification gates unchanged.

## 2026-10-11 remote parent draft RPC foundation (not completion)

UI-040 Coding independent parent saved snapshot flags/default-off and original
connection/session/revision/generation controls now implement metadata catalog,
exact client-composer-digest save prepare/confirm, binary read and reviewed discard.
Single-use typed JSON receipts, changed/nonempty/foreign/revision/withdrawal and held
worker cleanup tests; actual private CLI zero-model/graceful quit.102 pass/no skips
58.02s final6-file `/tmp/ui040-parent-final-integrated.log`, Ruff/strict505 mypy clean,
two Coding RPC artifact source modules exact. `agent-ui-remote-parent-drafts.md`
explicitly retains client full-painted empty/unchanged composer adoption, Claw socket
counterpart, physical/failure matrix; server self-report is NOT proof of client recovery.
UI-040 remains待实现;64 IDs42 complete/7 implement/6 verify/5 external/4 deferred,
18 non-deferred unfinished. UI-048 retained, frozen complete-root/Linux final gates intact.

## 2026-10-11 remote parent receiver checkpoint (not completion)

UI-040 shared original-scope/manifest/sequence/hash/successful-final-receipt receiver
and full-painted one-use empty/unchanged composer recovery gate implemented; private
spool bounded/closed, repeated/canceled/expired/foreign/changed recovery cannot adopt.
Actual Coding RPC binary plus actual CLI reviewed recovery zero-model evidence; final
8-file `/tmp/ui040-parent-receiver-current-final.log` exit0, focused26pass1.81s,
Ruff/strict506 mypy clean/TUI-Coding artifacts receiver byte-exact. No ordinary Claw
remote UI/socket integration or physical remote parent claim yet. UI-040 stays待实现;
64 IDs42 complete/7 implement/6 verify/5 external/4 deferred;18 non-deferred unfinished.
Final frozen full-root/Linux safety/terminal/transport gates intact.

## 2026-10-11 Claw remote parent read checkpoint (not completion)

UI-040 independent Claw socket parent catalog/binary read now original connection/
group/session/view/revision/hash bound, client receiver/full review tested; capability
withdrawal remains reader-responsive and waits disk worker/reservation settlement.
80 pass/no skips6.24s `/tmp/ui040-claw-read-integrated.log`, root Ruff/strict507
mypy clean/two Claw artifact modules byte-exact. Save/discard capabilities0; actual
ChatApp remote UI/mutation/physical/failure matrix remain. UI-040 still待实现;64 IDs
42 complete/7 implement/6 verify/5 external/4 deferred,18 non-deferred unfinished.
Final frozen complete-root/current-Linux safety/terminal/transport gates unchanged.

## 2026-10-11 Claw parent snapshot mutation checkpoint (not completion)

UI-040 Claw independently negotiated save/discard now uses original owner/session/
view/revision and exact JSON/composer receipts, single-use review-before-disk mutation,
private store/hash/deadline fences. Real socket text/image/foreign/changed/replay/
withdrawal tests83 pass/no skips6.32s; Ruff/strict507 mypy clean, two Claw artifact
modules byte exact. Ordinary ChatApp full-painted UI/physical/failure matrix still
required, not substituted by server tests. UI-040 stays待实现;64 IDs42 complete/
7 implement/6 verify/5 external/4 deferred;18 non-deferred unfinished. Final frozen
full-root/current-Linux gates unchanged.

## 2026-10-11 actual ChatApp remote parent recovery checkpoint (not completion)

UI-040 `/draft remote` actual ChatApp metadata/read/background finish/exact review/
empty unchanged composer CAS now restores text/images without model/history/policy
mutation. Actual UI recovery/change-ABA/close6 paths and physical Linux text/image2
paths passed; local generation-stamp decoder mismatch fixed, active decode owns files.
91 pass/no skips8.96s `/tmp/ui040-chat-read-integrated.log`, Ruff/strict508 mypy clean,
Claw/TUI new panel/ChatApp source bytes exact. Remote save/discard UI/physical/full
failure matrix remain required. UI-040 stays待实现;64 IDs42 complete/7 implement/6
verify/5 external/4 deferred;18 non-deferred unfinished. Final frozen full-root/Linux
safety/terminal/transport/static/artifact/example gates intact.

## 2026-10-11 actual ChatApp remote disk mutation checkpoint (not completion)

UI-040 actual remote save/discard complete typed review and fresh confirmation now
preserve composer/history/permissions, bind original view and selected catalog, cancel
unconfirmed receipts on close. Dedicated remappable remote host save gesture preserves
unsent text; direct prepare fixes catalog reservation race. Four current-Linux physical
recovery/save/discard paths verified.134 pass/no skips20.85s current10-file gate,
`/tmp/ui040-chat-mutation-final-integrated.log`; Ruff/strict508 mypy clean/four
production Claw-TUI-Coding artifact modules byte exact. Large image remote save still
explicitly refuses400KiB ceiling; full-size image upload/failure matrix stays in UI-040,
not waived.64 IDs42 complete/7 implement/6 verify/5 external/4 deferred,18 non-deferred
unfinished. Frozen complete-root/current-Linux final verification gates unchanged.

## 2026-10-11 full-size parent image upload checkpoint (not completion)

UI-040 historical400KiB limit now removed via independent original-view chunked
upload/ACK/exact final hash and one-use refs before full save review. Offloop workers,
120s timers/withdraw/disconnect cleanup bounded; original composer/images/history/
permissions unchanged. Actual1MiB/10MiB-boundary save plus5 physical Linux paths
verified.142 pass/no skips33.59s `/tmp/ui040-upload-integrated.log`; root Ruff/
strict508 mypy clean/three Claw artifact modules exact. UI-040 still待实现 for expanded
failure/receipt-loss matrix and final acceptance.64 IDs42 complete/7 implement/6 verify/
5 external/4 deferred;18 non-deferred unfinished. Frozen complete-root/current-Linux
safety/terminal/transport/static/artifact/example gates remain intact.

## 2026-10-11 parent snapshot failure checkpoint (not completion)

UI-040 upload ACK failure now retires unknown refs/timers without reuse; queued Coding
RPC mutation rechecks review deadline at publication/unlink. Red2 regressions retained.
Both server lost committed save receipts preserve actual disk effect but refuse retry/
duplicate or fictional rollback.146 pass/no skips34.82s current10-file
`/tmp/ui040-failure-integrated.log`, Ruff/strict508 mypy clean/two server artifact
modules exact. UI-040 stays待实现 for finite hash/delivery/cancel/retained lifecycle
acceptance;64 IDs42 complete/7 implement/6 verify/5 external/4 deferred,18 non-deferred
unfinished. Frozen complete-root/current-Linux final gates intact.

## 2026-10-11 remote prepared-review retirement checkpoint (not completion)

UI-040 both controllers now automatically retire prepared private bytes on30s expiry,
cancel timers on confirm/cancel/withdraw/close, and drop undelivered review receipts
on write failure/cancel/timeout. Committed disk effects unchanged/no replay.134 pass/
no skips32.89s `/tmp/ui040-review-lifecycle-integrated.log`, Ruff/strict508 mypy clean/
two controller artifact source modules exact. UI-040 still待实现 for finite remaining
hash/cancel/delivery reconciliation;64 IDs42 complete/7 implement/6 verify/5 external/
4 deferred;18 non-deferred unfinished. Final frozen complete-root/Linux gates intact.

## 2026-10-11 parent hash/transport/cancel matrix checkpoint (not completion)

UI-040 sixteen new both-controller failure paths verify on-disk replacement refuses
old read/delete hashes and preserves replacement; begin/chunk/end failure cannot adopt;
disk error cannot replay consumed receipt; before-first-step/held-save close waits actual
worker and releases Claw reservation/temps with permissions preserved.166 pass/no skips
34.31s eleven-file `/tmp/ui040-hash-cancel-integrated.log`, Ruff/strict508 mypy clean.
No production edit/new artifacts this checkpoint. Remaining UI-040 now explicitly four
named items in remote-parent audit, including real in-flight UI-view cancel implementation
(not substituted by whole-connection close). UI-040 stays待实现;64 IDs42 complete/
7 implement/6 verify/5 external/4 deferred;18 non-deferred unfinished. Frozen Linux/
complete-root final gates unchanged.

## 2026-10-11 per-view in-flight parent cancellation (not completion)

UI-040 remaining named item1 now implemented: actual ChatApp close revokes matching
Claw read/prepare/upload view in socket reader, settles workers/retained resources in
background, refuses old view and isolates siblings. Actual held read/socket-live/new
view and upload repeat-cancel tested.153 pass/no skips33.01s
`/tmp/ui040-view-cancel-integrated.log`, Ruff/strict508 mypy clean/two Claw artifacts
byte-exact. UI-040 stays待实现 for named items2–4, no false full-goal claim;64 IDs42
complete/7 implement/6 verify/5 external/4 deferred,18 non-deferred unfinished. Frozen
complete-root/current-Linux final requirements retained.

## 2026-10-11 client decode ownership checkpoint (not completion)

UI-040 named item2 held client decode now preserves active spool until completion,
drops revoked private recovery before enqueue, and defers quit through actual cleanup.
Actual close/new-generation-handler/quit3 paths tested with original socket/history/
composer/source preservation.156 pass/no skips32.87s current9-file
`/tmp/ui040-client-decode-integrated.log`, Ruff/strict508 mypy clean/ChatApp artifact
bytes exact. UI-040 stays待实现 for items3–4 (Coding RPC physical/error; final receipt/
capacity reconciliation).64 IDs42 complete/7 implement/6 verify/5 external/4 deferred;
18 non-deferred unfinished. Frozen complete-root/current-Linux final gates intact.

## 2026-10-11 Coding RPC physical parent recovery checkpoint (not completion)

UI-040 item3 current Linux6 physical RPC text/image recovery/cancel/incomplete paths
now verified with actual offline CLI plus shipped receiver/full-review/editor/CAS;
no prompt/model, native main-buffer/cursor/termios cleanup.162 pass/no skips42.36s
`/tmp/ui040-rpc-physical-integrated.log`, root Ruff/strict508 mypy clean, production
unchanged/no new build claim. Items1–3 now closed by named evidence; UI-040 remains
待实现 only for item4 receipt/capacity/source-final acceptance reconciliation.64 IDs42
complete/7 implement/6 verify/5 external/4 deferred;18 non-deferred unfinished.
Frozen complete-root/current-Linux final goal requirements unchanged.

## 2026-10-11 remote recovery aggregate capacity checkpoint (not completion)

UI-040 receiver now enforces total pending manifest bytes/current host remaining
budget, rechecks before decode; ChatApp checks before read/review/adoption. Red capacity
regression retained and actual read/adopt overflow preserves composer/source.166 pass/
no skips42.96s `/tmp/ui040-capacity-integrated.log`, Ruff/strict508 mypy clean/two
Claw-TUI artifact source modules byte exact. UI-040 stays待实现 pending item4 source-
final acceptance reconciliation;64 IDs42 complete/7 implement/6 verify/5 external/
4 deferred,18 non-deferred unfinished. Frozen complete-root/Linux final gates intact.

## 2026-10-11 UI-040 implementation reconciliation — 待验证

Original parent saved catalog/read/binary/save/discard/client recovery acceptance now
mapped to current implementations/evidence in remote-parent audit. Final decode capacity
and duplicate terminal receipt ownership defects fixed, red regression retained.
Current167 pass/no skips42.83s plus root Ruff/strict508 mypy clean and eight current
API modules exact three-package artifacts. UI-040 transitions 待实现→待验证, **not**
已完成: broader current-source host prompt/navigation/extensions/approvals/gateway/
modal regressions still required. Final goal gates independent and unchanged.
64 unique IDs42 complete/6 implement/7 verify/5 missing external/4 deferred;
18 non-deferred unfinished. Historical OPEN and narrower scoped greens do not add
requirements or substitute for complete-root/current-Linux verification.

## 2026-10-11 UI-040 named requirement closure

UI-040 remote parent saved catalog/read/binary/two-phase save-discard/client full-painted
one-use empty-composer recovery now completed against original named matrix and
source-final broad host gate. Final60-file833 pass/no skips432.13s
`/tmp/ui040-broad-host-final.log`, handle45571 exit0, all Coding RPC plus Claw gateway/
input/image/navigation/planning/TUI transport/modal and shared store/receiver regressions.
Broad red829pass1fail startup-flood fixture synchronization repaired without weakening
assertions; separate actual-reader3 red interlock cases fixed for parent disk ownership
against model/thinking/compaction mutation. Current root Ruff/strict508 mypy clean;
updated RPC source exact artifacts, existing other feature receipt retained. Full details
in remote-parent audit. UI-040→已完成;64 IDs43 complete/6 implement/6 verify/5 external/
4 deferred;17 non-deferred unfinished. No goal completion claim: frozen complete-root/
current-Linux terminal/safety/transport/static/artifact/example final requirements remain.

## 2026-10-11 UI-041 independent RPC name mutation checkpoint (not completion)

RPC original command-owned extension_session_name now independently negotiates
reviewed metadata rename after actual broker consent AND callback success, using native
session/revision/expected_name persistence CAS. Old/navigation/editor-only and foreign/
canceled/failed/withdrawn/name-changed requests cannot borrow authority. Actual worker
and CLI persistence/no-model tests plus native two-host naming/RPC editor/navigation/
atomicity110 passed/no skips80.81s `/tmp/ui041-rpc-name-final-integrated.log`, root
Ruff/strict508 mypy clean/two RPC source modules Coding artifacts byte-exact.
`agent-ui-rpc-host-scope-audit.md` retains remaining invocation-scope contracts;
UI-041 stays待实现.64 IDs43 complete/6 implement/6 verify/5 external/4 deferred,
17 non-deferred unfinished. Frozen complete-root/current-Linux final gates unchanged.

## 2026-10-11 UI-041 independent RPC catalog checkpoint (not completion)

Original command-owned list_sessions/get_session_tree now reaches native paged
catalog through independently negotiated extension_session_catalog; editor/name/
navigation mutation remain separate. Session/revision/generation withdrawal and held
worker, membership revision, pagination/missing-target refusal verified with actual
worker/CLI.66 pass/no skips70.82s `/tmp/ui041-rpc-catalog-final-integrated.log`,
Ruff/strict508 mypy clean/Coding RPC module byte-exact wheel-sdist. Initial fixture
summary/startup failures retained in host-scope audit, not claimed passing. UI-041
stays待实现 for remaining invocation/host contract reconciliation.64 IDs43 complete/
6 implement/6 verify/5 external/4 deferred;17 non-deferred unfinished. Frozen final
complete-root/current-Linux verification gates unchanged.

## 2026-10-11 UI-041 actual RPC client-menu checkpoint (not completion)

Independent extension_command_catalog now obtains actual client menu via original
command broker, bounded page/revision schema and session/generation fences; no server
menu guessing or execution grant. Worker pagination/copy/wrong-session/withdrawal and
CLI verified.73 pass/no skips79.30s `/tmp/ui041-rpc-menu-integrated.log`, Ruff/strict508
mypy clean/Coding RPC artifact bytes exact. UI-041 stays待实现 for remaining invocation/
host scope reconciliation.64 IDs43 complete/6 implement/6 verify/5 external/4 deferred;
17 non-deferred unfinished. Final frozen complete-root/Linux gates unchanged.

## 2026-10-11 independent RPC host capability delta checkpoint (not completion)

UI-041 three red cases fixed: new menu/catalog/name-only capability negotiations
no longer withdraw unrelated editor/navigation/dialog/form grants or editor receipts.
Independent flags preserve each other; legacy batch semantics and atomic invalid
refusal retained.75 pass/no skips77.63s `/tmp/ui041-independent-flags-final-integrated.log`,
Ruff/strict508 mypy clean/modified RPC module exact Coding wheel-sdist. UI-041 stays
待实现 for remaining invocation/browse/host contracts.64 IDs43 complete/6 implement/
6 verify/5 external/4 deferred;17 non-deferred unfinished. Frozen final Linux/full-root
verification gates unchanged.

## 2026-10-11 UI-041 deferred RPC browser request checkpoint (not completion)

Independent extension_session_browser now permits original-command open_sessions
requests after callback/result success, fenced by session/revision/generation; never
automatic activation or a claim of client-painted browser. Failure/cancel/withdraw/
revision/result-write failure tests, actual worker/CLI metadata population verified.
124 pass/no skips95.52s eight-file gate `/tmp/ui041-rpc-browser-integrated.log`,
final browser11pass15.84s after notice shortening; Ruff/strict508 mypy clean/modified
RPC module Coding artifacts exact. UI-041 stays待实现 for broader invocation and
complete host-scope reconciliation.64 IDs43 complete/6 implement/6 verify/5 external/
4 deferred;17 non-deferred unfinished. Frozen complete-root/current-Linux final gates intact.

## 2026-10-11 UI-041 deferred action result-delivery boundary (not completion)

Actual-worker red2 navigation/name cases fixed: native deferred host actions now wait
for callback/context cleanup/final result whole-line write, using RpcOutput.send rather
than enqueue success. Output failure/cancel cannot launch native mutation/browser;
synchronous embed fallback explicitly retained.149 pass/no skips113.80s current12-file
`/tmp/ui041-deferred-delivery-integrated.log`, Ruff/strict508 mypy clean/two RPC Coding
artifact modules exact. UI-041 stays待实现 for broader invocation-scope reconciliation;
64 IDs43 complete/6 implement/6 verify/5 external/4 deferred,17 non-deferred unfinished.
Final frozen complete-root/current-Linux verification gates unchanged.

## 2026-10-11 UI-041 accepted-reply generation checkpoint (not completion)

Editor/name/navigation now fence awaited replies with effective capability generations:
withdraw/re-enable cannot revive old editor CAS or native mutation admission. Actual
worker/broker race tests corrected to thread-safe owner-loop synchronization; initial
cross-loop Event timeouts are retained but NOT red production evidence.139 pass/no
skips116.45s `/tmp/ui041-host-generation-integrated.log`, Ruff/strict508 mypy clean/
RPC Coding artifact source exact. Primary reference fetch unavailable; no broadened
scope inferred or waived. UI-041 remains待实现;64 IDs43 complete/6 implement/6 verify/
5 external/4 deferred;17 non-deferred unfinished. Frozen complete-root/Linux gates retained.

## 2026-10-11 UI-043 active-tool selection foundation (not completion)

Agent host-owned idle revision-CAS active subset is now separate from configured
inventory, persists through registration sync without implicit growth, and controls
both model definitions and execution lookup. Selected tools still need pre-tool
permission approval. Real extension worker reload/removal and both-host existing
inventory regression102 pass/no skips43.38s `/tmp/ui043-active-tools-hosts.log`,
Ruff/strict508 mypy clean/Agent artifact bytes exact. Core API not installed/advertised
as extension/native UI parity; review/host/child/headless/persistence and original
lifecycle/compaction/message/queue APIs still required. UI-043 and UI-041 remain待实现.
64 IDs43 complete/6 implement/6 verify/5 external/4 deferred;17 non-deferred unfinished.
Final frozen complete-root/current-Linux gates unchanged.

## 2026-10-11 UI-043 reviewed RPC active tools checkpoint (not completion)

Command-host ExtensionAPI active tool read/selection vocabulary now routes through
independent Coding RPC review; callback+result delivery success precedes idle Agent
revision-CAS application, permission unchanged. Default/tool/headless unavailable,
foreign/withdrawal/inventory-change refusal verified. Unchanged registration wrapper
cache fixes selection revision churn while preserving stale registration guards.
Actual worker/CLI158 pass/no skips105.50s `/tmp/ui043-tool-review-integrated.log`,
Ruff/strict509 mypy clean/five Agent-Coding artifact source modules exact. UI-043
stays待实现 for native/Claw/child/persistence and other original host-control contracts;
64 IDs43 complete/6 implement/6 verify/5 external/4 deferred,17 non-deferred unfinished.
UI-041 and final frozen complete-root/current-Linux verification remain intact.

## 2026-10-11 UI-043 native Coding tool-selection checkpoint (not completion)

Actual Coding extension UI now inspects/reviews selection via full-painted form,
actual broker accepted event and deferred callback-success Agent CAS. Configured tools,
composer/history/permission preserved; textual tool definitions match active selection.
Claw explicitly returns unavailable pending integration instead of misrouting unknown
operation as browse.90 pass/no skips81.15s `/tmp/ui043-native-tools-integrated.log`,
includes2 physical native Coding paths plus existing host/RPC/lifecycle tests; Ruff/
strict509 mypy clean/five production modules byte exact in four package artifacts.
UI-043 still待实现 for Claw/child/persistence/reporting/other original host APIs.64 IDs
43 complete/6 implement/6 verify/5 external/4 deferred;17 non-deferred unfinished.
UI-041 and frozen complete-root/current-Linux final verification unchanged.

## 2026-10-11 UI-043 Claw native/socket selection checkpoint (not completion)

Independent Claw active-tool capability now reaches original reserved host snapshot,
full-painted native review and callback-success/revision-CAS application; no permission
or tool execution grant. Applied status separate from queued API acknowledgment;
Claw applies before final result under reservation, no receipt-loss rollback/replay.
Withdrawal after approved held callback still refuses application; prompt descriptions
match active tools.114 pass/no skips92.61s `/tmp/ui043-claw-tools-integrated.log`,
Ruff/strict509 mypy clean/four Claw artifact modules exact, both native selectors and
four physical selector paths covered. UI-043 remains待实现 for child/persistence/
reporting/other host APIs;64 IDs43 complete/6 implement/6 verify/5 external/4 deferred,
17 non-deferred unfinished. UI-041 and frozen complete-root/Linux gates unchanged.

## 2026-10-11 UI-043 child active-tool ceiling checkpoint (not completion)

Both native child factories now capture explicit parent active-tool ceiling, reject
expanded child profile before worker admission, and retain ceiling through child runtime
hooks/dynamic registrations. Parent defaults keep existing behavior; later parent
expansion does not expand captured child tools.173 pass/no skips91.92s
`/tmp/ui043-child-active-integrated.log`, root Ruff/strict509 mypy clean/three Agent-
Coding-Claw artifact modules exact. No new physical child-selector claim. UI-043 remains
待实现 for persistence/reporting/remaining child scope and other original host APIs.
64 IDs43 complete/6 implement/6 verify/5 external/4 deferred;17 non-deferred unfinished.
UI-041 and frozen complete-root/current-Linux final verification gates intact.

## 2026-10-11 UI-043 Coding persistence/owned commit checkpoint (not completion)

Coding active-tool selection now survives restart/session/branch/checkpoint/compaction
recovery, without broadening saved empty/subset authority. Original tool inventory is
restored before saved extension choices are applied. Save-before-publish uses Agent
dry validation and a pre-replacement owner/CAS check. Native command worker and RPC
owned offloop persistence avoid terminal/reader blocking; cancel/withdraw/close retains
ownership until actual disk work settles, including truthful post-commit cancellation.

Current21-file208 pass/no skips143.27s `/tmp/ui043-durable-integrated.log` includes two
physical Linux Coding selector paths, real worker restores, storage barriers and
related SDK/navigation/staging/compaction/child/RPC/native naming regressions. Root Ruff
and strict509 mypy clean `/tmp/ui043-durable-current-static.log`; offline Agent/Coding
wheel-sdist and five byte-exact production modules `/tmp/ui043-durable-source.json`.
Full behavior/initial test-alias correction documented in
`docs/agent-ui-extension-host-controls.md`; no unrelated process/service changes.

Updated consolidated remaining scope explicitly retires historical statements that
Coding persistence/native selection or parent child ceilings were absent. Claw
persistence, inventory reporting and remaining applicable extension host APIs are not
claimed complete. UI-043 stays待实现;64 unique IDs43 complete/6 implement/6 verify/
5 missing external/4 deferred;17 non-deferred unfinished. Only non-current-Linux native
validation is deferred; final frozen complete-root/Linux safety/terminal/static/build/
example requirements remain. Goal stays active; scoped green is not final completion.

## 2026-10-11 UI-043 Claw persistence/compaction ceiling checkpoint (not completion)

Claw now stores/restores per-session/branch active-tool choices through private sidecars,
prepared navigation and the existing undo journal. SDK branch restore commits managed
files before live target publication. Actual13-write process-death recovery includes
selection sidecars. Reviewed native selection persists offloop under the held group/
child-idle owner with precommit identity/revision/cancel checks and truthful settlement.

A real compaction availability bypass was reproduced: silent FlushRunner exposed and
executed filesystem despite empty/current_time parent selections (2 red test cases,
owned temporary target only). Guarded compaction tools now filter by parent active
selection; default tools and existing planning/MCP/extension permission checks remain.
See `docs/agent-ui-extension-host-controls.md` for concrete behavior and initial test
fixture corrections, distinct from the production flush regression.

Final23-file164 pass/no skips `/tmp/ui043-claw-durable-final-integrated.log` includes four
physical Linux selectors, both-host child ceilings and Claw storage/navigation/compaction
regression. Root Ruff/strict509 mypy clean and six Claw wheel-sdist modules byte-exact
`/tmp/ui043-claw-durable-source.json`. No publication, downloads, live providers or
user-service restart. Earlier161-pass gate preceded the flush fix and is not final-source
proof. UI-043 stays待实现 for reporting/inventory-lifecycle persistence and remaining
applicable extension host APIs.64 IDs43 complete/6 implement/6 verify/5 external/4 deferred;
17 non-deferred unfinished. Final frozen full-root/Linux gates and UI-041 remain intact.

## 2026-10-11 UI-043 inventory/retirement checkpoint (not completion)

Both native `/tools` browsers and invocation inventories now distinguish configured,
selected/excluded, explicit/default, model API gate and legacy-unknown selection.
Snapshots/cache/page revisions include selection changes; old Claw inventory requests
retain the exact old envelope, while an explicit optional read includes selection.
Schema/search/export and owner/permission boundaries remain intact.

Actual unload red tests on both hosts exposed broader stale persisted selections.
Awaited strict-subset retirement now covers extension command/initialization/input/
context/pre/post/turn synchronization and MCP connection/forget inventory changes.
No expansion or execution permission is inferred. Storage is offloop and owner/revision
checked; failure retains narrowed runtime, surfaces an error and retries before a later
turn. Actual self-disposing tool callbacks prove persistence before the next model step,
and real MCP reconnects do not reselect retired aliases. Prepared navigation persists
only available target names; restart, cancellation and failure evidence is named in
`docs/agent-ui-extension-host-controls.md`.

Final24-file262 pass/no skips179.12s `/tmp/ui043-inventory-retirement-integrated.log`
includes four physical Coding/Claw selection+inventory paths, child/headless and callback
cache scopes, storage/MCP/navigation/journal regressions. Root Ruff/strict509 mypy clean;
ten production modules match all-four-package wheel/sdist bytes
`/tmp/ui043-inventory-retirement-source.json`. No user-service restarts, downloads or
live providers. Inventory/reporting and acknowledged retirement are retired from the
current remaining subsection, not counted as extra requirements. UI-043 remains待实现
for other original public host APIs.64 IDs43 complete/6 implement/6 verify/5 external/
4 deferred;17 non-deferred unfinished. Final frozen full-root/Linux gates remain intact.

## 2026-10-11 UI-043 canonical RPC compaction ownership checkpoint (not completion)

Public ExtensionAPI exports were reconciled against the named local requirement
families. Existing navigation, metadata, tool-selection and compaction hooks must not
be duplicated or confused with the still-missing extension compaction request and
message/queue controls. Matrix and current46-method source receipt are recorded in
`docs/agent-ui-extension-host-controls.md` and `/tmp/ui043-host-exports.json`. Primary
reference fetches failed this checkpoint; no latest/upstream compatibility claim.

A real canonical summary-stream test reproduced RPC reader blockage during direct
compact: even status could not be read. Both legacy compact and owned session_control
compact now use the existing navigation task/ownership interlocks, not a second model
loop. Status/abort/quit/EOF remain responsive. Canonical summary cleanup and shielded
commits settle before owner release; cancellation after actual commit reports changed
state instead of claiming rollback. Instructions and original owner/revision are
validated; malformed/foreign targets or cancellation cannot borrow another owner.

Final12-file191 pass/no skips123.27s `/tmp/ui043-rpc-compaction-integrated.log` covers
four actual RPC CLI endings and four Linux physical native compaction paths plus
storage, navigation, extension delivery, prompt lifecycle and parent-draft interlocks.
Root Ruff/strict509 mypy clean; Coding wheel-sdist two production modules byte-exact
`/tmp/ui043-rpc-compaction-source.json`. UI-043 stays待实现: this fixes a native prerequisite,
not the absent extension request/message/queue API surface.64 IDs43 complete/6 implement/
6 verify/5 external/4 deferred;17 non-deferred unfinished. Final frozen full-root/Linux
verification remains required; no publishing or user-service restart.

## 2026-10-11 UI-043 reviewed extension compaction request checkpoint (not completion)

The previously absent request now exists as ExtensionAPI.request_compaction with
bounded canonical instructions and reviewed parent-command admission. Coding native,
Coding RPC and Claw native/socket route to existing canonical compaction only after
actual broker acceptance and successful callback completion. RPC waits for complete
command-result delivery. Claw rechecks its command result plus original capability
generation at native dispatch. Literal cancel is data, not a slash-command cancellation.
Unsupported/tool/hook/headless contexts cannot borrow the interactive owner; broader
applicable invocation-scope reconciliation and message/queue APIs remain required.

Both hosts retain full-painted false-default review, existing permissions, selected
model/allowed-tool rules, original session/child ownership and unsent drafts. Failed
callbacks/delivery, withdrawal/re-enable and stale dispatch do not silently run/replay
compaction. Native receipts report completion separately from API queued=True.

A same-buffer compact/revocation test also exposed a real pre-admission missing receipt.
Claw now lets canceled-before-start jobs publish one canceled result without a model
call. It preserves repeated explicit cancellation during admitted commit settlement
and the existing precise before-summary result. Intermediate3 failures were corrected
without weakening those existing tests. Full evidence and initial harness corrections
are in docs/agent-ui-extension-host-controls.md.

Final21-file283 pass/no skips219.29s `/tmp/ui043-request-compaction-current-final.log`
includes four physical Coding/Claw approval/cancel paths and actual RPC CLI integration,
plus capability, late admission, canonical compaction and name/navigation regressions.
Root Ruff/strict509 mypy clean; final nine production modules match all-four-package
wheel/sdist `/tmp/ui043-request-compaction-current-source.json`. No live providers,
dependency downloads, publishing or user-service restart. Updated current export matrix
retires the historical absent-parent-request statement, not the broader original scope.
64 unique IDs43 complete/6 implement/6 verify/5 external/4 deferred;17 non-deferred
unfinished. UI-043 stays待实现 and final frozen complete-root/Linux gates remain intact.

# Unified Inbox / EventRouter / batch acceptance — 2026-10-18

**Current status: the single resumed independent review is complete; its4 Important
findings are fixed and verified in runtime commit `eae3486`. Source/installed gates
passed. This is an author post-fix acceptance, not a fabricated second reviewer
clean verdict. Final goal accounting follows successful Native task-done.**

## Current scope and provenance

The accepted spec/plan are
`docs/superpowers/specs/2026-10-16-unified-inbox-batch-routing-design.md` and
`docs/superpowers/plans/2026-10-16-unified-inbox-batch-routing.md`.
The original implementation commits are reachable from current base
`460e8dfd4b46e3b4ace526f81aa70efdf73c682d` (main/release2.4.2). This audit is in the
original checkout on `feat/eyes-scheduler-source`, with the later user-approved
scheduler source still uncommitted. It does not create a worktree or authorize
push/merge/publication.

Subsequent user-approved work removed coding's frontend JSON-lines RPC mode and
some agent/Claw UI/MCP surfaces. Current root AGENTS and retained native/SDK/ACP
interfaces supersede the old plan's RPC-adapter assertions. No removed frontend is
restored merely to satisfy a historical test name. Shared queue, provenance,
permissions, persistence, cancellation and protocol invariants remain required.
The old 2.4.1 results below are historical records, not proof of this current tree.

## Requirement-by-requirement evidence for retained interfaces

Paths in this table are under `packages/<package>/tests/` unless explicitly named.
The current whole-root run executes all retained tests; it is not a deselected
migration suite. Real transport/process/disk cases supplement deterministic model
streams and narrow failure injection. Tests demonstrate the named cases; neither
this author audit nor green tests stand in for independent review.

| Acceptance | Current authoritative evidence |
|---|---|
| 1. Generic Router / extensible Handler | Eyes `test_event_router.py`, `test_handler_registry.py`, runtime/executor tests; `handlers/contracts.py`, `handlers/registry.py`, `router.py` and `deliveries.py`. Two handler implementations have type-owned parsers/lifetimes and explicit routes; generic executor does not encode ACP payloads. |
| 2. Offline model-free Inbox and automatic activation | Coding ACP offline/enqueue/automatic-consumption cases and Eyes `test_end_to_end.py::test_five_offline_callbacks_automatically_form_one_tui_batch_and_survive_owner_exit`. Fresh wheel-only ingress forbids AgentSession/model imports. Five retained inputs become one actual owner turn without message approval. |
| 3. Online idle/busy automatic intake | Eyes `test_end_to_end.py::test_online_file_callbacks_form_next_five_message_batch_and_freeze_selection`, SDK-only and dual-route cases, coding automatic Inbox tests. Busy/next/late model contexts have input counts1/5/1; no resume or human-credit grant is used. |
| 4. Independent messages in one turn | Coding `test_batch_turns.py`, `test_session_batches.py`, `test_batch_display.py`. Actual Agent/model contexts retain five ordered independent inputs, canonical origin/provenance, per-input images and transforms; one lifecycle/Agent.prompt(list), not five sends or one concatenated message. |
| 5. Strict configurable limit | Coding `test_batch_config.py`, `test_count_limit_selects_ordered_batches_without_human_credit` and saved/settings/SDK cases. Default10, strict int1..128, bool rejected,1-at-a-time and3+2, global/project/saved/explicit override layering. |
| 6. Fixed selection and one owner | Coding session batch/controller/ownership tests, actual second-writer/process-death leases, navigation/generation fences, producer/control/compaction settlement. Late arrivals stay outside a selected batch; human priority/FIFO remains. |
| 7. Only tools require approval | Coding mixed-batch default ask/no-approver vs explicit allow-all tests, actual native tool approval after a later input, SDK/ACP permission/owner controls. Message admission has no synthetic approval tool, wake credit or authority-bearing resume. Removed RPC reader tests are not claimed as current evidence. |
| 8. Shared result and scoped cancel | Coding batch/native replay tests and actual direct/forwarded Unix `test_acp_batch_intake.py`: two prompt peers + enqueue share one batch, nonparticipant stays silent, one waiter detaches without retracting a running input, local abort settles the whole run. Message/request/attempt/batch identities remain distinct. |
| 9. Crash, migration and durable order | Coding `test_inbox_v2.py`, `test_inbox_crash.py`, owner-crash/lease tests. Actual process death exercises pending/claimed/running boundaries; old states retain attempt/history facts, running becomes interrupted and is not automatically replayed; persisted intake order survives reopen/concurrent writers. |
| 10. Identity, namespace, bounds and context fences | Coding Inbox/ACP ownership/ingress lifecycle and Eyes enqueue/Unix/control tests: same-ID content conflict, ACK loss, replacement namespace at same endpoint, cumulative bytes/model/image limits, UID/mode/type/symlink checks, branch/generation fencing. Rejected intake does not invent lifecycle, authority or a second Agent. |
| 11. ACP completion vs receipt, real error propagation | Coding actual Unix batch finish-I/O failures and ACK-before-model-completion cases; `test_batch_mode_errors.py` and ACP review regressions. SDK raises and print returns nonzero on finalization failure; handled input is system feedback, not a fabricated assistant response. Native/SDK/ACP replace the intentionally removed frontend RPC surface. |
| 12. Docs, skills, full gates, installed artifacts, review | Active AGENTS/protocol/READMEs, actual packaged-skill/example consumers, all8 source paths, Ruff/strict typing/Flow examples, four real wheel-only consumers and byte-for-byte source parity. The report, every finding and declined-item ruling, post-fix gates and final audit are recorded in `docs/unified-inbox-batch-review-resolution.md`. |

### Five review-focus mappings

1. **Publication atomicity:** partial-running and last-member-terminal tests plus
   direct/forwarded Unix `finish_io` cases. No model starts after a partial running
   publication and no peer is told success before final durable settlement.
2. **Shared cancellation:** actual multi-peer Unix and controller waiter-detach
   cases, plus local abort cleanup. Durable facts do not claim that an already
   observed input was removed from the model context.
3. **Setup vs authority:** owner activation/setup-barrier tests and native batch
   permission tests. Handler readiness does not become a user-approval token.
4. **Legacy/process death/order:** actual killed-owner/process cases, retained
   migration attempts, persisted counters and namespace/branch fences.
5. **Transforms/images/provenance/display:** actual mixed Agent contexts, native
   live/replay, file-to-ACP E2E, all-handled and mode-error cases. Each input and
   one shared answer remain visible without duplicate tool/assistant streams.

## Current post-review acceptance evidence

Review base: `8626efc`; reviewed pre-fix runtime base `460e8df` plus approved
scheduler work. The one interrupted review was resumed, returned4 Important
findings and executed64 targeted cases. Each finding was independently reproduced
by the author, fixed in **`eae34869ea58daa543a8c205c330931e9c13898f`**, and permanently
covered. No second reviewer verdict is asserted. Full report/dispositions and every
Declined-to-judge ruling: `docs/unified-inbox-batch-review-resolution.md`.

The four fixes preserve full submitted payload rather than narrowing the feature:
Native Escape stops real shared preparation and restores queued native drafts;
a legal exact1-MiB input executes with bounded internal correlation overhead and
normal successor work; partial claims are released only by matching attempt;
pre-ticket withdrawal is fenced and idempotent rather than poisoning the owner.
Raw intake/frame limits, unbound/human byte limits, tool policy and multi-input
batch caps remain enforced. Exact code/test evidence is in the resolution notes.

`uv run python scripts/test.py`: **6220 passed,34 skipped**, all8 configured paths
in76.90s. Log: `/tmp/unified-inbox-final-root.log`.

| Current configured path | Result |
|---|---|
| flow | 813 passed in 24.67s |
| claw | 643 passed in 29.82s |
| eyes | 239 passed in 62.98s (0:01:02) |
| ai | 729 passed, 7 skipped in 3.90s |
| tui | 682 passed in 15.03s |
| site | 42 passed in 2.47s |
| coding | 1226 passed, 12 skipped in 75.24s (0:01:15) |
| agent | 1846 passed, 15 skipped in 76.07s (0:01:16) |

Ruff passed packages + artifact script; strict mypy passed all8 namespaces (554
files); all17 Flow examples passed; diff checks passed. The final Native task-done
command repeats the whole-root gate after the documentation commit. Goal completion
requires that successful ledger entry; no test command is replaced by a status
restatement or by only the targeted regression subset.

### Exact current skip audit and observed auxiliary failure

Actual post-fix `pytest agent + ai + coding -q -rs -n 0`:
**3801 passed,34 skipped in145.42s**, log
`/tmp/unified-inbox-final-skips-serial.log`. The34 are15 agent +12 coding cases
requiring explicitly installed independent MCP SDK2.3.1, plus7 opt-in external
Codex/Claude CLI cases. No new routing/Inbox/batch/scheduler test was skipped.
Real providers/IDE integrations are not inferred from these optional skips.

An earlier parallel auxiliary skip-audit run observed one3-second output-panel
readiness timeout in
`test_transcript_output.py::test_live_private_export_and_share_require_review_preserve_draft[coding]`
(`/tmp/unified-inbox-final-skips.log`). The exact output test file then passed4
cases in0.33s in isolation, and the complete serial three-package audit passed.
The balanced whole-root gate also passed. No timeout/assertion was weakened and no
product fix is claimed for that unreproduced auxiliary timeout; it is disclosed
rather than omitted or mislabeled as another reviewer finding.

### Final rebuilt/installed payloads

Fresh5 wheels/5 sdists from the fixed runtime passed Twine in
`/tmp/unified-inbox-reviewed-dist.hR0pRB`. Four fresh wheel-only consumers in
`/tmp/xdog-acp-isolated.nb7sp8sm` passed: Eyes-only packaged file/scheduler/skill
validation and a real cron occurrence; independent Agent stdio; model-free coding
Unix enqueue/status/dedup; and a real SDK six-independent-input one-call owner.
The owner probe additionally executed an exact1048576-byte durable message and
its normal successor in two calls, with the owner healthy, outside the checkout.
The physical same-session writer lease was reusable only after explicit disposal.

`source-parity.json` byte-compares all archived Python members and Eyes'
skill/two examples to the final source, naming fix commit `eae3486` and explicitly
including the uncommitted scheduler extension. All10 hashes/counts are in the
review-resolution notes. Python3.13.9, ACP SDK0.12.1, source croniter6.2.2 and
isolated croniter6.2.4 were exercised. No paid provider/IDE, physical power failure,
TCP/Windows transport or OS sandbox was tested/claimed. The independent review
did not claim exhaustive croniter/timezone coverage or a comprehensive Claw
storage review. Those bounds do not stand in for untested promised functionality.

---

## Historical 2026-10-16 / 2.4.1 checkpoint (preserved, not current evidence)

The following record predates the user's subsequent frontend removal, Claw
integration, source refactors and scheduler extension. In particular, references
to RPC tests, unimplemented Claw integration and a not-yet-requested review describe
that checkpoint only. Current scope/status and eventual review disposition above
supersede them without rewriting their original test results or artifact hashes.

### Original verification record — 2026-10-16

**Status: source and installed-artifact gates passed; independent review pending.**
This is not yet a completed release claim. Final review dispositions, any required
fix pass and the post-review acceptance audit remain required.
The 2026-10-15 `eyes-verification.md` and `acp-review-resolution.md` are historical
checkpoints, not a review or verification of this revision.

Working checkout: `/data/workspaces/pyspace/xdog`, `feat/eyes-observation`.
No worktree, implementation subagent, automatic push/PR/merge/publication, live
provider credentials or external IDE run is part of this implementation.
Approved spec/plan: `docs/superpowers/{specs,plans}/2026-10-16-unified-inbox-batch-routing*`.
Core batch/UI commit: `2a307aa`; real file/packaged-contract closure: `3ac6ce2`.
Documentation and subsequent final verification commits are recorded below when verified.

## Acceptance 1–12: source evidence

Paths are repository-relative. Tests use real Agent/controller instances, files,
SDK peers, Unix sockets and subprocesses; narrow fault injection tests publication
and cleanup boundaries without replacing the actual tool/model loop.

| Acceptance | Authoritative source tests / observable facts |
|---|---|
| 1. Generic routing/handler contract | Eyes handler-registry/router/runtime tests prove independent non-ACP handler definitions/lifecycle/status, multiple explicit routes, type-owned parsing and no ACP serialization in executor/runtime. `handlers/contracts.py`, `handlers/registry.py`, `router.py`, `deliveries.py` are the actual interfaces. |
| 2. Offline model-free intake → automatic owner | coding `test_acp_offline_inbox.py`, `test_acp_enqueue_cli.py`, `test_inbox_auto_consume.py`; Eyes `test_end_to_end.py::test_five_offline_callbacks_automatically_form_one_tui_batch_and_survive_owner_exit`: five real file changes persist pending without an owner; opening TUI consumes five independent inputs in one Agent call, no message approval. |
| 3. Online automatic bounded intake | Eyes `test_end_to_end.py::test_online_file_callbacks_form_next_five_message_batch_and_freeze_selection`: actual file callbacks while busy produce current/next/late batches with input counts1/5/1, not a resumed credit gate. SDK-only and dual-route file tests retain independent route messages. |
| 4. Independent messages, provenance, attachments | coding `test_batch_turns.py::test_five_inputs_are_five_history_messages_in_one_turn`, `test_mixed_batch_preserves_images_and_input_transform_results`, `test_batch_captures_human_context_once_not_once_per_input`; `test_batch_display.py` proves all fixed-batch attachment metadata, five live/reopened entries and one response. |
| 5. Limit/config/order | `test_session_batches.py::test_count_limit_selects_ordered_batches_without_human_credit` and batch/settings/SDK tests cover default10, strict int1..128, booleans rejected,1-per-turn,3+2 selection, layered saved/project/global/explicit settings. |
| 6. Fixed selection, one owner | `test_session_batches.py`, `test_session_controller.py`, `test_session_ownership.py`, `test_acp_review_regressions.py` prove late-input separation, human priority, writer leases, generation/branch fences and real producer/control/compaction settlement. |
| 7. Only tools need approval | `test_batch_turns.py::test_mixed_batch_does_not_bypass_tool_permissions` tests default ask/no approver vs explicit allow-all; `test_batch_display.py` uses actual TUI allow/deny callbacks after later human input; `test_rpc_batches.py` uses the actual JSON-lines reader to answer actual restricted-tool allow_once/deny during a mixed batch. No message approval is requested. |
| 8. Shared output and scoped cancel | `test_session_batches.py::test_active_shared_batch_peer_cancel_detaches_only_its_waiter`, `test_local_abort_waits_for_cleanup_and_cancels_entire_batch`; real Unix direct/forwarded `test_acp_batch_intake.py` variants test two participating peers, nonparticipant silence, member cancellation and continued shared execution. Native/RPC project one shared stream and independent lifecycle IDs. |
| 9. Crash/migration/order | `test_inbox_v2.py::test_legacy_migration_retains_attempt_facts_and_blocks_running_replay`, `test_actual_process_death_obeys_claim_and_running_boundaries`, `test_intake_order_survives_restart_and_concurrent_writers`; `test_inbox_crash.py` / owner-crash tests prove unstarted claim recovery and running→interrupted without replay. |
| 10. Identity/capacity/privacy/fences | Inbox store/v2/crash, ACP review/ownership/ingress lifecycle and Eyes enqueue/unix/control suites retain same-ID/content conflict, ACK loss, replacement-store namespace, cumulative byte/context/image bounds, UID/mode/type/symlink checks and navigation fences. Rejected intake does not leak transient lifecycle or create another Agent. |
| 11. Final result vs ACK / mode errors | `test_acp_batch_intake.py` real SDK/Unix peers distinguish durable enqueue ACK from actual prompt completion and fail all waiters on terminal fsync error. `test_batch_mode_errors.py` proves SDK raises, print returns1, RPC keeps real partial content plus failed result; handled inputs produce system feedback without model/assistant invention. |
| 12. Docs, skills, checks, artifacts, review | Active protocol/AGENTS/package READMEs, packaged skill and JSON example describe automatic intake and batch identity. `test_skill_contract.py` parses both shipped definitions and rejects obsolete approval/credit instructions. `test_acp_artifact_contract.py` checks probe contracts/compilation; it is **not** a substitute for executing the fresh wheel-only probes. Review/final acceptance below remain open. |

## Five review-focus risks

The plan's conceptual scenario names map to these actual parameterized test nodes;
no duplicate alias tests or weaker single-input substitutes are claimed.

1. Partial running publication and terminal persistence: store/v2 tests and
   `test_session_batches.py::{test_partial_running_publication_never_starts_batch,test_last_member_terminal_failure_reports_failure_to_all_waiters}`;
   real direct/ingress Unix batch `fault=finish_io` verifies every participating
   peer receives error, not success before final disk receipts.
2. Shared peer cancellation: `test_active_shared_batch_peer_cancel_detaches_only_its_waiter`
   and direct/ingress Unix `fault=cancel_waiter`; durable running/succeeded facts
   are not rewritten to claim a removed model input. Local abort settles all work.
3. Setup vs authority: `test_inbox_auto_consume.py::test_owner_activation_waits_for_setup_not_message_approval`,
   actual TUI batch permission and RPC mixed-batch reader tests. The setup barrier
   installs handlers; it is not synthetic user permission.
4. Legacy/process death/order: actual process-death v2 and owner-crash tests;
   counter/record publication, retained attempts and current namespace determine
   recovery, not old approved flags or hashed filename ordering.
5. Transform/image/source/display: actual mixed Agent batch tests, real file E2E,
   Native attachment snapshot and live/reopen tests, plus all-handled/mode-error
   contracts. One response is not replayed through each member's adapter.

## Verified checkpoints (not final combined release evidence)

- Task6 commit `421b321`: all coding ACP/SDK, automatic Inbox, Eyes enqueue and
  generic Agent ACP matrix150 passed; actual task-done25.99s.
- Task7 commit `2a307aa`: complete coding suite1276 passed,12 skipped,57.11s;
  Ruff packages clean; strict mypy all eight namespaces628 files passed.
- Task8 `3ac6ce2`: real closure matrix25 passed/11.01s; entire Eyes186 passed/32.66s;
  shipped-skill/artifact-contract checks5 passed. The preceding old offline-review
  E2E failure is replaced by stronger automatic five-input and lifetime assertions,
  not skipped or deselected.
- Earlier intermediate root failures and their fixes are implementation checkpoints,
  not green evidence. Final source checks must run again after documentation and
  any review fixes.

## Combined source gates and skip audit

`0122c37` source check: `uv run python scripts/test.py` passed every configured path
in81.84s. The later `0690924` changes only the artifact probe and its regression:
a live SDK session correctly keeps its writer lease between calls until explicit
`dispose()`, then physical same-ID lease reacquisition succeeds. No runtime
ownership behavior was weakened to satisfy a probe.

| Configured path | Result |
|---|---|
| flow |813 passed |
| agent |1785 passed,48 skipped |
| claw |583 passed |
| ai |688 passed,7 skipped |
| site |42 passed |
| eyes |186 passed |
| tui |919 passed |
| coding |1278 passed,12 skipped |

The same source gate ran `ruff check packages scripts/verify_acp_artifacts.py`
(clean), strict mypy for all eight namespaces (628 files), and the shipped Flow
examples with `--allow-script-stub` (17 passed).

The subsequent current `0690924` skip audit ran the actual agent+ai+coding suites
with `-q -rs`: **3752 passed,67 skipped in49.18s**. The67 are48 agent and12 coding
independent-MCP-SDK2.3.1 opt-in cases, plus7 opt-in external Codex/Claude CLI cases.
They are existing optional integrations, not skipped Eyes/ACP/batch feature tests.
No live provider/IDE integration is inferred from these skips.

## Fresh built and installed artifacts

Source/probe commit: `0690924a408f63aa8ae5d202b8e4ae3b26b3519c`.
Python3.13.9, official ACP SDK0.12.1. Fresh final distribution directory:
`/tmp/xdog-batch-dist.8xXvDo`; fresh isolated consumers:
`/tmp/xdog-acp-isolated.1ksmwfl7`.

All five wheels and five sdists were rebuilt from final package/test/README
contents and all10 passed `twine check`. Four fresh wheel-only environments outside
the checkout passed without PYTHONPATH/source imports or provider credentials:

1. Eyes-only validates the shipped definition/skill and has no XDOG harness dependencies.
2. Agent-only official SDK stdio initialize/new/load/prompt/update yields one answer,
   with no coding/Eyes/claw/flow installation.
3. Coding Unix receiver enqueue/status/dedup leaves data pending with AgentSession
   and SDK-bootstrap imports explicitly forbidden.
4. Coding SDK owner consumes five durable callbacks plus its distinct human input
   in one actual six-message Agent/model turn, preserves five canonical external
   identities and one batch, requests no message approval, and proves writer lease
   reuse **after explicit disposal**.

An earlier fresh attempt failed the new probe's incorrect pre-disposal lease
assertion; those artifacts/results are not labeled the final success. The probe
was fixed against the actual lifetime contract, permanently covered by a real SDK
ownership regression, and **all five distributions were rebuilt again**.

Every Python member of every final wheel and sdist was byte-compared against the
current checkout: wheel members map to `packages/<name>/src/<member>`, sdist members
to `packages/<name>/<member-without-archive-top-directory>`. The Eyes skill and
example bytes also match source. `source-parity.json` in the distribution directory
records this source commit, interpreter, counts, hashes and resource proofs.

| Artifact | Python members matched | SHA-256 |
|---|---:|---|
| `xdog_agent-2.4.1-py3-none-any.whl` |138| `fec36f4ad8dd9b6b4ff4d0697d46a19f53a58128bf4ea6fda61cfdfa80c2efb5` |
| `xdog_agent-2.4.1.tar.gz` |349| `9d79bc57318c5d005f0068d2b443f90914e109ce0dd7098c4a6814a50ab2a25e` |
| `xdog_ai-2.4.1-py3-none-any.whl` |50| `a4c9e41315478352974674d5c0003b73da592e9f30a8d6a76f58180b92725c43` |
| `xdog_ai-2.4.1.tar.gz` |91| `291ca06afa9c3735bdc130d106598a78714295baabcf800e1890728b36a78b33` |
| `xdog_coding-2.4.1-py3-none-any.whl` |123| `7de2a9bfd30c6d25d0b518d005eab5229cad20764566a3768b11e9ac995dc4cc` |
| `xdog_coding-2.4.1.tar.gz` |279| `7f1b7a14735ae410603d4bd0bbbd098bdc5d66b7a9a963ef43100e5a62bcdbf1` |
| `xdog_eyes-2.4.1-py3-none-any.whl` |29| `ea7af8fa1cc98de5bde1a89084217fb5d943f86c6905d548c0ca3d3907a717fb` |
| `xdog_eyes-2.4.1.tar.gz` |48| `53ff24cec65829dd289ee02706caf86d32c8a14dd4ba18094fc7ff2fdc36ded6` |
| `xdog_tui-2.4.1-py3-none-any.whl` |116| `3fdb127c9577f121ca2d15592317bdedd083cee2b5dde5119b656f08379d7b4c` |
| `xdog_tui-2.4.1.tar.gz` |203| `a06a6495b8241bee9f59cb1cc60056f97c7206850263a12548d864cacfd0f8a7` |

## Remaining review and final acceptance gates

One fresh independent read-only whole-branch review is required for
`8626efc..implementation HEAD`, including all tasks and the five focus risks,
not merely these documentation edits. It has not yet been requested. Important/
Critical findings require one permanent RED→GREEN fix pass and full re-verification;
affected artifacts must be rebuilt after source fixes. No second reviewer verdict
is implied by the author's final tests. The active goal cannot be marked complete
until review disposition and final source/artifact parity are evidenced.

## Explicit limits

No real paid provider/model, external IDE, TCP/Windows transport, automatic daemon
installation or Claw-specific adapter has been tested or introduced. Standard ACP
and XDOG Unix transport are distinct. File polling is not exhaustive auditing.
Exactly-once side effects are not promised: unknown prompt outcomes pause for
inspection; running crashes are not replayed. The new Claw integration idea needs
its group/session addressing design resolved separately; it is not fabricated as
part of this coding/Inbox verification.

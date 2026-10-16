# Unified Inbox / EventRouter / batch verification — 2026-10-16

**Status: in progress, not a completed release claim.** Core implementation and
source integration checks below have passed. The final combined workspace checks,
fresh installed artifacts, skip audit and new independent review are still required.
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
| 12. Docs, skills, checks, artifacts, review | Active protocol/AGENTS/package READMEs, packaged skill and JSON example describe automatic intake and batch identity. `test_skill_contract.py` parses both shipped definitions and rejects obsolete approval/credit instructions. `test_acp_artifact_contract.py` checks probe contracts/compilation; it is **not** a substitute for executing the fresh wheel-only probes. Final gates below remain open. |

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

## Remaining final gates

- Combined eight-package `scripts/test.py`, Ruff including artifact script, strict
  mypy, shipped Flow examples and actual skip-reason audit: not yet recorded here.
- Five fresh wheels and five sdists, Twine, four fresh wheel-only consumers (Eyes,
  generic Agent stdio, coding model-free intake, coding SDK batch owner): not yet run.
  Record interpreter, artifact hashes and shipped-Python/resource parity with final
  source. Never reuse an older wheel and label it rebuilt.
- One fresh independent read-only whole-branch review `8626efc..implementation HEAD`:
  not yet requested. Important/Critical findings require one tested fix pass and
  full re-verification/rebuild of affected artifacts; no second reviewer verdict
  is implied by author verification.
- Final acceptance audit/commits: incomplete until all preceding gates are evidenced.

## Explicit limits

No real paid provider/model, external IDE, TCP/Windows transport, automatic daemon
installation or Claw-specific adapter has been tested or introduced. Standard ACP
and XDOG Unix transport are distinct. File polling is not exhaustive auditing.
Exactly-once side effects are not promised: unknown prompt outcomes pause for
inspection; running crashes are not replayed. The new Claw integration idea needs
its group/session addressing design resolved separately; it is not fabricated as
part of this coding/Inbox verification.

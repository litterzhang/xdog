# ACP / Eyes / session inbox verification — 2026-10-15

> **Historical checkpoint (2026-10-15), not the current message contract.**
> Message approval, human wake credit and one-message-only turns are superseded
> by the approved 2026-10-16 unified Inbox/batch design. Recorded past results
> and review findings below are preserved, not relabeled as fresh verification.
> Current protocol: `docs/acp-session-routing.md`; current acceptance evidence:
> `docs/unified-inbox-batch-verification.md`.

Implemented in the requested checkout `/data/workspaces/pyspace/xdog`, feature
branch `feat/eyes-observation`, without a worktree, merge, push, PR or publication.
Specs: ACP session routing, ACP endpoint callback and session inbox designs dated
2026-10-15. The 2026-10-14 host design is historical/superseded.

**Status:** implementation/deletion complete. The required independent whole-branch
review found five Important issues; all five were reproduced, fixed with permanent
RED→GREEN tests, and verified by a fresh full suite and rebuilt isolated artifacts.
See `acp-review-resolution.md`; no second reviewer verdict is implied.

## Acceptance evidence

Paths in the table are repository-relative test files. Test implementations use
real owner/controller instances, files, subprocesses and official ACP SDK peers;
SDK-only peer fixtures do not import XDOG. Scoped unit fault injection complements
those integrations, not replacements for them.

| ID / requirement | Authoritative evidence |
|---|---|
| O1 generic Agent ACP initialize/new/load/prompt/update/cancel without coding | `packages/agent/tests/test_acp_{adapter,backend,stdio}.py`; isolated agent wheel consumer |
| O2 human priority, queueing and full producer/control settlement | `packages/coding/tests/test_session_controller.py`, `test_session_turns.py`, `test_native_session_queue.py`, `test_inbox_controller.py` |
| O3 single writable owner, before-load lease, no duplicate Agent in bridge | `packages/coding/tests/test_session_ownership.py`, `test_session_router.py`, `test_acp_bridge.py` (real owner subprocess) |
| O4 invalid/inactive/session/cwd/generation/UID/mode/symlink rejection | coding `test_acp_backend.py`, `test_session_ownership.py`, `test_acp_ingress_lifecycle.py`, `test_inbox_store.py`; Eyes `test_acp_unix.py`, `test_local_control.py` |
| O5 source/evidence/state/assistant/tool live and replay display, no human masquerade | coding `test_session_message_display.py`, `test_session_history_display.py`, `test_inbox_display.py`; Eyes `test_end_to_end.py` |
| O6 scoped cancel, EOF/crash/navigation/compaction/approval cleanup | coding `test_session_controller.py`, `test_acp_ingress.py`, `test_acp_ingress_lifecycle.py`, `test_acp_backend.py`, native/RPC cancellation suites; Eyes `test_acp_enqueue.py` |
| O7 fail-closed default permission, no approver/mode escalation, real TUI/RPC approval | coding `test_inbox_controller.py`, `test_inbox_commands.py`, `test_inbox_display.py`, `test_acp_backend.py`; Eyes `test_acp_handler.py` |
| O8 self-edit anti-loop and truthful headless budget | Eyes `test_end_to_end.py::test_file_to_acp_coding_turn_then_self_edit_budget_pause`, `test_acp_handler.py`; coding `test_acp_backend.py`, `test_inbox_controller.py` |
| O9 final response vs update/ACK, unknown prompt no-replay, only not-sent auto retry | Eyes `test_acp_handler.py`, `test_acp_delivery.py`, `test_executor.py`, `test_acp_enqueue.py` |
| O10 real subprocess/stdIO/Unix and full file→target session closure | coding `test_acp_bridge.py`; Eyes `test_runtime.py`, `test_end_to_end.py` |
| O11 old implementation absent, historical decoder still usable | coding `test_no_legacy_event_host.py`; agent `test_external_event_messages.py`; physical deletion inventory below |
| O12 global lint/types/tests/examples and artifacts | commands/results below; independent review findings resolved by tested fix pass |
| E1 generic Unix ACP without coding imports/executable/spawn | Eyes `test_acp_unix.py`; `test_runtime.py::test_cli_uses_unix_acp_without_coding_cli` |
| E2 explicit connection/session; all targets checked before zero-source admission | Eyes `test_loader.py`, `test_runtime.py::test_all_handlers_must_connect_before_any_source_scan` |
| E3 existing owner + ingress + Eyes without second AgentSession, TUI/live replay | coding `test_acp_ingress.py`; Eyes `test_end_to_end.py` |
| E4 no TUI/owner, complete enqueue/status with no AgentSession/model/owner socket | coding `test_acp_offline_inbox.py`, `test_acp_enqueue_cli.py`; isolated coding wheel import blocker |
| E5 TUI exit preserves observation; receiver exit pauses; ACKed data remains | Eyes `test_end_to_end.py` (offline owner lifetime and explicit receiver recovery) |
| E6 show pending on open, zero model calls, explicit selected review | coding `test_inbox_display.py`, `test_inbox_controller.py`; Eyes offline-to-TUI E2E |
| E7 competing starters, stale/live sockets, failed/cancelled startup, inode cleanup | coding `test_acp_ingress_lifecycle.py`; Eyes `test_local_control.py`, `test_runtime.py` |
| E8 unknown prompt vs durable same-ID retry, branch/store/generation fences | Eyes `test_acp_enqueue.py`, `test_acp_handler.py`, `test_acp_delivery.py`; coding `test_inbox_store.py` |
| E9 preserved third-party stdio, absent SDK/future-version non-ACP, CLI attach | Eyes SDK-only fixture tests; coding `test_acp_optional_imports.py`, `test_acp_cli.py`, `test_acp_bridge.py`; isolated environments |
| E10 complete deletion/artifacts/global checks/review | deletion/build evidence below; independent review/fix evidence below |
| I1 real stdio offline enqueue without load/new/model | coding `test_acp_enqueue_cli.py::test_standard_stdio_adapter_can_enqueue_without_load_or_model` |
| I2 durable identity/content conflict/concurrency/quota/ACK-loss | coding `test_inbox_store.py`, `test_inbox_crash.py`, `test_acp_offline_inbox.py`; Eyes `test_acp_enqueue.py` |
| I3 absent/cwd/private-files/forged-control fail closed | coding `test_inbox_store.py`, `test_acp_offline_inbox.py`, `test_no_legacy_event_host.py` |
| I4 no implicit consumption on open, one controller after explicit review | coding `test_inbox_controller.py`, `test_inbox_display.py`; Eyes offline E2E |
| I5 selected approval/content CAS/current branch/generation/no extra credit | coding `test_inbox_controller.py`; native query-credit regression in `test_inbox_display.py` |
| I6 actual process death at running→interrupted, no replay, fresh reviewed attempt | coding `test_inbox_crash.py`, `test_inbox_controller.py::test_real_owner_crash_leaves_interrupted_attempt_and_retry_is_explicit` |
| I7 observation outlives owner, no source crash restart/replay, stop preserves inbox | Eyes `test_end_to_end.py`, `test_runtime.py::test_crashed_runtime_is_not_restarted_by_status_and_explicit_start_rebaselines` |
| I8 live/reloaded TUI/RPC, original prompt/attach interoperable | coding display/controller/RPC/bridge tests and real Eyes E2E |
| I9 no old host/capabilities; shared ACP backend and separate inbox | deletion audit; standard stdio enqueue and model-free Unix ingress tests |
| I10 complete verification and explicit unverified boundaries | results below; independent review/fix evidence below |

## Physical deletion and compatibility

Removed complete `coding/core/external_events/`, Eyes `transport/` and
`handlers/session_notify.py`, `docs/eyes-host-protocol.md` and its v1 fixture.
AgentSession no longer exposes an event-host field/enable method, capability
rotation, event-context environment masking or the legacy new-input route.
Old host/inbox/protocol-specific tests were replaced by the stronger protocol,
ownership, persistence and permission tests above; generic per-tool environment
isolation tests remain and use a neutral test variable.

`test_no_legacy_event_host.py` audits shipped source absence and reads actual
persisted `xdog.external_event.v1` history through TUI projection. New model input
uses `xdog.session_input.v1`. `XDOG_SESSION_ID` is a nonsecret current-session hint
that preserves caller environment and refreshes after navigation, not a token.

## Fresh verification (post-review fixed tree)

- `uv run python scripts/test.py`: all eight paths passed in 77.97s after the review fixes. Counts:
  flow 813; claw 583; eyes 169; agent 1785/48 skipped; site 42;
  ai 688/7 skipped; tui 919; coding 1158/12 skipped. An explicit `-rs` audit passed 3614 tests and confirmed all 67 skips:
  60 require the unrelated pinned independent MCP SDK 2.3.1, six are opt-in
  Codex CLI integrations, and one is opt-in Claude Code --bare integration.
  No new ACP/session/inbox feature test is skipped.
- `uv run ruff check packages`: passed.
- Strict mypy over all eight namespaces: passed, 620 source files.
- `uv run xdog-flow test packages/flow/examples/ --allow-script-stub`: 17 passed.
- Shipped Eyes v2 example: actual CLI validate passed.
- Agent and Eyes actual wheel/sdist pytest inspection: 4 passed, checking protocol
  source, SDK Python marker, typing/license/skill/example and no retired modules.
- `uv build --package` for ai/agent/tui/coding/eyes: all five wheels and five
  sdists built. `uvx twine check`: all ten passed.
- `uv run python scripts/verify_acp_artifacts.py DIST`: fresh wheel-only Eyes,
  ai+agent-only and coding environments outside repo, with no PYTHONPATH/editable
  fallback, all passed. SDK baseline 0.12.1. Eyes has no ai/agent/coding/claw/flow/tui
  installed; its CLI validates the packaged example/skill. Agent runs official SDK
  stdio initialize/new/load/prompt/update with deterministic model and no coding.
  Coding runs real Unix enqueue/status/dedup while AgentSession/sdk model-bootstrap
  imports are forbidden; no model or owner is created. Data remains after close.
- Packaged skill behavior: read-only baseline consumer produced obsolete v1 and
  could not satisfy offline intake; fresh consumer of updated skill produced valid
  v2 explicit endpoint/session enqueue and required user review/no model startup.
  No code implementation was delegated; final code review is a separate gate.
- Final wheel/source audit: all 448 shipped Python files across the five wheels
  were byte-compared against the post-review source and matched. All seven retired
  implementation/document/fixture paths (including both mode adapters) were absent.
- `git diff --check`: passed.

Build paths/logs are reproducible from the commands and script; temporary paths
in one run are evidence, not configuration or a public API. No publisher,
credential, system daemon, remote service, live model or external IDE was invoked.

## Final review and exclusions

One fresh independent whole-branch read-only review covered the actual main
merge-base through the pre-review implementation. Five Important findings were
all fixed with 17 permanent regression cases, plus broad existing compaction/SDK/
RPC compatibility tests. The final eight-package run is green. The finding table,
re-grading, exact resolutions and declined-item dispositions are recorded in
`acp-review-resolution.md`. No second independent approval is claimed; the
approved Native workflow verifies the fix pass with tests.

All five distributions were rebuilt after fixes into fresh output directories;
all ten wheel/sdist Twine checks and fresh wheel-only Eyes/agent/coding environment
smokes passed again. SDK-only navigation and both runtime/SDK compaction now share
the proper owner/settlement fence, terminal errors are explicit, attach output is
request-scoped and all RPC thinking levels reserve the same owner.

First-release exclusions remain TCP/HTTP/Windows transport, claw/flow-specific
adapters, API/web/script sources, automatic harness/observer startup, crash replay
of active registrations or tool side effects, exhaustive filesystem audit and
hosted broker/database requirements. Actual provider/paid-model and external IDE
integration were not exercised and are not implied by deterministic SDK tests.
No merge/main rollout/push/PR/publish or external provisioning was performed.

> **历史归档，不是当前接口、待办或执行授权。**
> 原日期、结论和验证范围保留；当前入口见 [文档索引](../../README.md)。

# Repository retirement sweep — 2026-11-04

This sweep removes obsolete XDOG interfaces, stored-format adapters and unused
application wrappers. It does not erase user storage, silently migrate data,
release permissions or replay stored effects. The initial sweep did not restart
services; a subsequent user-reported gateway failure required the controlled
Gateway/Eyes restart described below.

## Removed

- TUI component aliases, PromptEditor/BoundedDetails forwarding modules, getValue,
  visible_width and boolean editor focus aliases; current components and
  the distinct EditorComponent protocol remain. Old parent-draft command aliases
  and Coding's inactive keybinding module are gone.
- Coding path alias/home wrapper, unused per-session bash handle, compaction text
  wrapper and singleton turn bridge. Actual work uses the existing batch owner.
- Claw SessionManager alias and unused GroupRuntime navigation compatibility
  method; the application's owned control path remains. Missing/corrupt catalogs
  are rejected instead of rebuilding from old indexes or guessed archive files.
- AccessState planning decoder, plan sidecar imports, legacy_plan retention and
  permission migration. Missing/old access data is not writable authority.
- ChildConfig instructions and persisted-budget/inheritance conversions, ignored
  parent-prompt argument, task-prefix helper and snapshot-less read-only
  continuation. New/persisted settings share the same validation contract.
- Object-enum-to-properties approximation in child tool advertisements. Exact
  captured schemas and live permission checks remain authoritative.
- OpenAI proxy synthetic reasoning-ID migration. Native opaque inputs pass through
  unchanged. Structured-result flat field/type maps are rejected; only formal
  offline Draft 2020-12 result schema objects are used.
- Goal old-store probes/banners and host-wait decoding. Current v2 core fields
  are required and unknown fields are rejected; question metadata is optional,
  while a nonempty question must be complete. Historical audit documents are not
  executable support contracts.
- Coding Inbox v1/v2 approval-state migration and intake-order backfill. Version-3
  records have no legacy_order. Current execution ledgers require their explicit
  admission/timestamp fields. Claw no longer ALTERs old input timestamp schemas.
- Unused Agent fastjsonschema dependency and stale mypy overrides. The active
  Eyes/Core artifact verifier is named verify_claw_eyes_artifact.py, without an
  old-filename alias.
- Obsolete StdinBuffer read_buffered/read_complete/has_data/on_data paths. The
  actual incremental terminal framer, raw-mode restoration and supported keys remain.
- Claw extension-model persistence method aliases and AI image_generation CLI alias.
  The canonical get_model/save_model and image command remain.
- Redundant Coding/Claw skills extras; xdog-skills remains a default dependency.
- Old tool inventories without selection metadata and Claw include_selection
  downgrade. Current inventories always retain exact selection facts.
- Untyped MCP preview blocks and older-viewer wire downgrades; content_type is
  required. This is separate from supported external MCP transport negotiation.
- Eyes duplicate RuntimeLocation.cwd/control-wire cwd. Control/owner format v2
  explicitly names the daemon directory, with no v1 decoder or in-place record conversion.

## Regression fixes found during verification

- Btrfs may retain a directory listing snapshot on a descriptor opened before
  waiting for flock. Draft-store admission now opens a fresh descriptor relative
  to the pinned directory *after* obtaining the lock; concurrent writers cannot
  bypass the existing capacity bound.
- Child permission hooks explicitly classify native effects instead of relying
  on removed name-based permission fallbacks. Filesystem classification runs
  off-loop and rechecks original call/owner/permission epochs after that yield;
  a revoked admission cannot borrow an otherwise safe read exemption.

- Real gateway startup exposed two over-strict validation regressions: nullable
  current-v3 receipt audit timestamps and optional current-v2 Goal question
  metadata were incorrectly treated as corruption. Both now preserve missing
  facts without inventing timestamps/questions, rewriting history, releasing
  waits or replaying effects. Unknown formats/schemas still fail explicitly.

## Intentionally retained

Actual supported model/MCP wire transports and terminal key protocols are not
obsolete XDOG API aliases. Unknown-capability handling, cancellation, crashed-work
recovery, original-owner retirement fences, archive viewing and optional package
fallbacks remain where they are current functional requirements. For example,
Claw's scoring functions and Coding's host compaction heuristic are actively used,
not dead code merely because an old comment used the word "legacy".

## User-reported gateway recovery

The long-running editable gateway first called the deleted external_event module
from its already-loaded old code. After restart, validation also incorrectly
rejected absent nullable receipt timestamps and optional Goal question metadata;
the regression fixes above retain these current-format facts without rewriting
records. The real input and both Goal registries passed read-only validation.

The independently running Eyes daemon then needed a controlled v1-to-v2 process
restart. Its original process identity was verified before SIGTERM; registration
storage was retained. Gateway startup and an actual inbound Weixin turn/reply
succeeded afterward. Eyes had one paused registration with an explicit ACP
reconnect/rebaseline requirement; it was not silently resumed. Four Weixin
accounts reported expired sessions; one main account remained watching.

## Verification

Final whole-workspace run, from the repository root:

```bash
TMPDIR=/var/tmp uv run pytest -q -n 4 --durations=30 \
  --timeout=120 --timeout-method=thread
```

- **10,854 passed, 34 skipped**, four existing multiprocessing/fork warnings;
  all nine configured test paths, 597.30 seconds. No case-selection filter.
- Ruff (`packages test_support scripts`), strict mypy across all nine packages
  (**688 source files**), and `git diff --check` passed.
- All **17 Flow examples** passed.
- All nine distributions built wheel + sdist and passed Twine; final runtime
  wheels passed isolated offline ACP intake/output/native-control/Eyes checks.
- The current-v3 nullable receipt regression and current-v2 optional Goal metadata
  regression have explicit byte-preserving/no-replay tests.

Earlier runs are not reported as successes: the global six-worker run had two
cold-readiness deadline failures (10,852 passed, two failed, 548.27 seconds).
Tests now separate cold-start readiness from the actual shutdown/cancellation
assertions, preserving their effect/owner fences and short operation deadlines.
The real cleanup/reply behavior was not weakened. Earlier stale expectations for
removed aliases/extras were migrated, not satisfied by restoring compatibility.

The initial runs also exhausted `/tmp`'s per-user quota. Only this task's exact
unused temporary venvs were reclaimed; subsequent runs used `/var/tmp`. Runtime
fixtures still isolate their own short private sockets. These are local results,
not a claim that GitHub CI ran or that all live channel credentials are healthy.

Timing does not support blaming only per-package scheduling: the six-worker
whole-workspace run still took 548.27 seconds and the four-worker run 597.30.
Cold subprocess/daemon/PTY/persistence and compaction integration cases dominate
the slow-case list. The user's earlier one-minute baseline has not been measured
against this checkout/environment; repeated verification increased total elapsed
work time separately from the duration of a single run.

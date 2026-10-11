# RPC parent custom-frame shutdown regression — October 11, 2026

Confirmed failure: the existing actual-CLI `quit-parent` and `eof-parent` cases in
`packages/coding/tests/test_rpc_custom_backpressure.py` timed out at their unchanged
8-second process-exit bound with a large custom frame deliberately left unread.
The Claw owner's independent exact-file rerun reproduced **2 failed, 4 passed** in
`/tmp/ui064/rpc-backpressure-rerun.log`. Claw's ACK patch is not on this Coding RPC
path. The ongoing full-root run was not interrupted or duplicated.

## Diagnosis

An isolated diagnostic wrapper (temporary files only, no initial production edits)
recorded coroutine wait chains for the exact failing parent case:

- after-run compaction/tool/name job sets were **all empty**, and the observed
  parent producer was `None`;
- the controller was closed, but its command had a historical cancellation count
  of one and was waiting in `owned_run -> run -> RpcOutput.send -> FutureIter`;
- the output writer was waiting for the unread pipe to become writable.

The command catches `CancelledError` so an open peer can receive a canceled-result
receipt. During shutdown it was also starting that delivery-fenced receipt, behind
the unread frame. This prevented the existing bounded `RpcOutput.aclose()` from
running. A second case was confirmed: explicit command cancellation could already
have been caught and entered blocked result delivery before EOF/quit. The historical
`task.cancelling() > 0` count caused shutdown to skip the new cancellation signal
needed for that new await.

Evidence: `/tmp/ui044-rpc-backpressure-diagnostic/stacks.log` and its failing exact
pytest log. The new deterministic lifecycle tests failed **2/3** before the fix:
`/tmp/ui044-rpc-shutdown-late-red.log`.

## Minimal correction

Only production file changed for this repair:
`packages/coding/src/xdog/coding/modes/rpc/extensions.py`.

- A closed controller does not start a new command-result send. Its deferred
  successful-delivery flag remains false.
- A successful result enables deferred work only while the controller remains open.
- Shutdown signals its unfinished owned command task even if an earlier cancellation
  was already caught. Existing shielded worker/durable cleanup still settles before
  the controller returns.

No output-writer, after-run producer/barrier, or Claw ACK logic was changed. The
normal open-peer command-result delivery fence remains in place. No partial frame
is replayed, dropped from an active output queue, or rewritten to let shutdown pass.
The ordinary final output shutdown remains responsible for bounded abandoned-peer
cleanup.

## Strong regressions without weakening existing assertions

- `test_rpc_shutdown_delivery.py`: deterministic close-before-send and
  cancel-then-blocked-send-then-close cases, a one-second controller bound, callback
  cleanup, no deferred editor/context effects, unchanged history/permissions; also
  verifies explicit cancellation still delivers its canceled result to an open peer.
- Existing six actual-CLI backpressure cases retain their exact node IDs, unread
  real-frame prefix checks, **8-second exit bounds**, return-code checks and actual
  worker PID reaping assertions.
- Two added actual parent cases issue explicit cancellation, observe that the
  canceled-result send has actually begun behind the unread frame, then send quit
  or EOF. They retain the same exit and worker-reaping assertions.
- A default-off `XDOG_RPC_CANCELED_SEND_MARKER` option in the existing offline CLI
  fixture supplies that synchronization point. It does not replace the real unread
  output-prefix proof or change normal fixture behavior.

Changed test paths:

- `packages/coding/tests/test_rpc_shutdown_delivery.py` (new)
- `packages/coding/tests/test_rpc_custom_backpressure.py` (two additional cases)
- `packages/coding/tests/fixtures/rpc_client.py` (opt-in synchronization only)

## Verification

All runs used root configuration and existing dependencies:
`uv run --no-sync --offline python -m pytest -c pyproject.toml ...`.

- Focused lifecycle plus actual CLI matrix: **11 passed**, no skips, **26.17 s**,
  `/tmp/ui044-rpc-shutdown-fixed.log`.
- Bounded regression union adding ordered/stalled/closed output, successful/failed/
  canceled native-operation delivery fences, ordinary extension controls and
  extension-requested compaction: **59 passed**, no skips, **76.58 s**,
  `/tmp/ui044-rpc-shutdown-regression.log`.
- Scoped Ruff clean: `/tmp/ui044-rpc-shutdown-ruff.log`.
- Strict mypy for four relevant production modules clean (unused-config notes only):
  `/tmp/ui044-rpc-shutdown-mypy.log`.

This is a confirmed local shutdown repair, not a full-root pass or external provider
interoperability claim. UI-052 is unchanged. No dependency download, live auth/paid
provider call, user-service restart, ledger edit, or unrelated worktree replacement.
Main was notified of the diagnosis before production edits and must use a fresh
final-root gate after all confirmed repairs.

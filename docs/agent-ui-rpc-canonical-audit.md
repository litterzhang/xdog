# UI-038: canonical/binary RPC source acceptance and owned hang cleanup

Current Linux checkpoint: 2026-10-10. Subordinate to the stable requirement ledger;
no new requirement IDs, no OS sandbox claim and no non-Linux native validation.

## Named acceptance evidence

- Parent live/saved/branch/timeline typed canonical histories preserve explicitly
  authorized image bytes, provenance and partial text, while removing credentials,
  redacted reasoning, signatures and private response metadata. No history activation,
  model submission, permission change or implicit resource read.
- Real CLI saved/branch/timeline sources carry >2MiB image data and long Unicode
  history through ordered chunks, byte counts and SHA-256; display negotiation cannot
  authorize canonical export. Receiver rejects format, owner, checksum/order/capacity
  mismatches. Held spools are closed on cancellation, revision/withdrawal/close.
- Child canonical capability is independent of parent canonical/display flags;
  child owner/epoch and source format are checked. Real CLI uploaded 2MiB image and
  partial child transcript export never become parent history. Child revocation and
  source abort clean owned spools; parent/display withdrawal cannot borrow authority.
- Real CLI undrained child binary output exits under quit, EOF and closed output;
  the active owned child producer settles. Parent slow/closed-output cases retain
  their existing real undrained pipe assertions (no draining workaround).

Current integrated command:

```sh
uv run pytest -c pyproject.toml \
 packages/coding/tests/test_rpc_child_canonical.py \
 packages/coding/tests/test_rpc_canonical.py \
 packages/coding/tests/test_rpc_transcript_cli.py \
 packages/coding/tests/test_rpc_transcripts.py \
 packages/coding/tests/test_rpc_children_cli.py \
 packages/tui/tests/test_transcript_transfer.py -q --tb=short -rs
```

**118 passed, no skips, 43.35s**, `/tmp/ui038-cleanup-integrated-current.log`;
initial narrower run **64 passed, 27.53s**, `/tmp/ui038-cleanup-current.log`.
Root Ruff and seven-namespace strict mypy **487 source files clean**,
`/tmp/ui038-cleanup-static.log`. All tool handles polled to actual exit.
A first wider invocation named a nonexistent receiver test file and collected no
cases (exit4, `/tmp/ui038-cleanup-integrated.log`); corrected command above is
passing evidence, not that failed collection.

## Historical hung invocation: diagnosed and settled, NOT passing evidence

User explicitly authorized safe-checkpoint handling of confirmed owned hung tests.
Identities rechecked immediately before cleanup:

- PID340375: own pytest, start 2026-10-10 17:54:51, cwd repository, exact
  `test_rpc_child_canonical.py` invocation; owned uv/shell chain and handle73617.
  Linux `/proc` wait channel `anon_pipe_read` after ~4h51m.
- PID340485: its direct child, exact offline `fixtures/rpc_client.py`, start
  17:54:54, owned pytest-1478 `test_actual_cli_child_binary_b0` directory;
  verified stdin pipe16768318, wait `ep_poll`.
- Original shell embeds the historical loaded test: unbounded TextIO `read(kind)`,
  taking the first `child_capabilities`. Initial transcript-only hello already
  queued a disabled child capability reply. The later canonical request therefore
  used the stale receipt and was denied, while pytest waited forever for
  `source_begin` and peer waited for more stdin. This is a current-Linux test
  protocol bug, **not cross-platform validation**.

Normal `quit` was written to the verified peer's stdin; PID340485 exited. The
historical pytest registered the first case failure then began the next parameter
using its already-loaded unbounded implementation. Its new direct child PID520456
had the same fixture, owned pytest-1478 `...binary_b1` cwd, while the same pytest
again blocked in `anon_pipe_read`. Immediately revalidated pytest start identity,
peer cmdline/parent/cwd, sent SIGTERM **only to this historical pytest**, and normal
RPC quit to the second peer. No process-group signal, SIGKILL, service restart or
unrelated process cleanup. Parent and both peers disappeared; original handle73617
ended **143**, historical log `/tmp/ui038-child-backpressure.log` has `21 dots + F`.
It is interrupted/red evidence, **never counted as passing**.

Durable identity/action receipt: `/tmp/ui038-hung-cleanup.json`. The unrelated old
extension worker mentioned in investigation was not touched.

## Test hardening preserving the original assertions

Current backpressure test keeps real image upload, canonical source admission,
undrained stdout, bounded peer exit and producer-settled assertions. Added a single
bounded byte reader from ready through capability negotiation, avoiding TextIO
prefetch hiding later packets from selector readiness. Expected canonical flag
filtering shares the same six-second deadline, so repeated stale receipts cannot
reset it. Each receipt has a 1MiB line bound; source bytes are not prefetched/drained.
Two focused regressions prove an open pipe with only disabled capabilities times
out and valid negotiation skips stale replies without consuming source bytes.

UI-038 is complete for its named scope. UI-064 final all-host safety matrix and
UI-055 final frozen full-root regression remain open; this 118-case slice does not
replace them. All other original requirements and the four explicitly deferred
non-current-Linux native verification IDs remain unchanged.

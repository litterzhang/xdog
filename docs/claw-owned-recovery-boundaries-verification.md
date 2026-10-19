# Claw current-pointer, transport and channel IO boundaries — source checkpoint

Date: 2026-10-19. Continuation of owned-transcript-io Task3. This checkpoint does
not complete Task3, the parent47-row audit, final review or current artifacts.

## Verified boundaries

- If the committed current pointer is absent but this group has known archived
  data, ordinary preparation does not guess an archive or create a new executable
  context. Explicit new/reset cannot silently wash away the unknown binding or
  inherit default writable permission. The original files remain readonly
  inspectable; recover the original binding or explicitly select a fresh data root.
  Genuinely empty groups still initialize normally. No archive activation,
  migration, automatic data repair or approval-bearing message delivery is added.
- Cold access status reads use the existing owned IO helper and recheck original
  context/generation before returning; repeated cancellation cannot release the
  actual group operation while its reader is still running. Existing active
  access snapshots remain on their original loop.
- Optional Weixin account lookup, channel cache load/write and poll cursor IO run
  on owned workers. Worker reads return detached token/map data; only the channel
  owner adopts memory state and starts the monitor. Cancellation settles actual
  reads and safely adopts known loaded state for cleanup without starting another
  monitor/model. Unloaded teardown does not overwrite unknown cache files with
  empty constructor state. Writes receive detached token/map snapshots, not the
  live channel or global token map. Formats/account namespace/recipient logic and
  polling admission-before-cursor advancement are unchanged.
- Unix endpoint filesystem preparation/bind/mode runs on an owned worker. The
  descriptor is not listening until after0600 mode is established and the actual
  asyncio server adopts it on the owner loop. Startup rejects a configured
  regular file, symlink/unknown owner or live receiver rather than unlinking it.
  Cleanup uses exact acquired device/inode identity, closes late resources on
  cancellation, and never unlinks a replacement user file. This is an owned
  transport resource, not another event handler/execution host.

## Actual evidence

Only scripted model/external network boundaries and precise physical IO fault or
barrier locations are controlled; real stores, locks, private Unix descriptors,
channel cache files, configured roots and shutdown are exercised.

- Actual restart after missing-pointer damage originally created a new context
  and called a forbidden model (RED0.92s). Normal preparation now refuses; both
  explicit new/reset are refused too. Actual archive bytes stay unchanged and
  readable, with no new pointer or Agent/model. Valid native/shared metadata and
  empty roots retain existing behavior.
- Added missing-pointer/new/reset plus cold access cancellation cases were
  RED2/0.97s, then related context/access/bootstrap49 pass6.09s.
- Real configured account/channel/polling file-open trace was RED2/1.12s.
  Account with no token never connects any real external service; channel tests
  replace only the API boundary. Real channel/monitor/intake33 pass2.96s.
- Existing non-socket endpoint bytes were unlinked/replaced at startup
  (RED0.57s); now they survive rejection and cleanup. Real endpoint/startup/native
  cases30 pass3.94s; late-acquisition repeated cancel,0600-before-publication and
  replacement inode protection plus related boundaries20 pass3.63s.

Full unfiltered nine paths: **6931 passed,34 skipped**,189.18s; Claw1105/125.19s
(`claw-owned-boundaries-root9.log`). Strict all9 mypy600 files, Ruff packages,
Flow17, lock/diff pass. A local variable was renamed to distinguish the ACP Path
from the native bound-descriptor resource after mypy caught inferred type reuse;
no semantic runtime change was made during the gate.

Command `uv run python scripts/test.py --jobs 2`, using the existing short
`TMPDIR=/data/t.Vy5SaQ`, `UV_CACHE_DIR=/data/t.Vy5SaQ/uv-cache`,
`TMUX_TMPDIR=/data/t.Vy5SaQ`. Original terminal/socket tests/deadlines are retained;
no earlier default4 timing/full-root observation is claimed fixed or hidden.

## Required remaining source work

The active Task3 must still inspect/fix actual dynamic group resource acquisition
and legacy native reset dispatch IO/reader ownership, plus partial per-group
resource allocation failures. New resource factories must not publish a mutable
Runtime from a worker or lose a late resource after cancellation. Existing
navigation/explicit context operations must keep real group fencing and readonly
inheritance without allowing unknown pointers to become new execution authority.

Then finish all remaining IO/recovery source inspection, current all9/static
regressions and Task3 completion. Return to parent47 acceptance audit and the one
whole-branch reviewer; rebuild and verify current wheel/sdist/runtime resources
and actual Goal-only/Coding/Claw/ACP/native/output consumers only after required
runtime fixes. Existing cold snapshots are old milestones, not current proof.
No push/merge/publish, paid model or existing user data/service mutation occurs.

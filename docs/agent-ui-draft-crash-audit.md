# UI-047: draft debounce/inflight crash windows

Current Linux checkpoint: 2026-10-10. Covers parent/child opt-in local autosave;
no automatic draft adoption/submission, old policy restoration or per-key / physical
power-loss guarantee. UI-058 remains independent and unfinished.

## Visible durability contract

Both native hosts' stable status row now includes current parent/child autosave
state, independently of model activity and protected input focus:

- `pending`: latest observed editable snapshot differs from the acknowledged disk
  receipt; debounce can lose these edits if process dies. No false durability claim.
- `writing`: owned worker still holds write/receipt capacity. A completed but unpolled
  receipt remains writing; edits during it remain pending after the old receipt lands.
- `committed`: current observed snapshot matches a successfully polled disk receipt.
  This is a named snapshot guarantee, not each keystroke or physical power loss.
- `uncertain`: storage failure disables repeat writes until explicit re-enable;
  inspect saved records, do not assume rollback or blindly retry.
- `off`/`empty`: hidden in status row. Autosave defaults off after restart.

Explicit enable notice explains pending/writing vs committed. Parent errors reference
`/draft saved`, not the child recovery command. Existing saved browser full-review,
copy into empty composer, ownership, images, conservative read-only/fresh-context
recovery and explicit discard stay unchanged.

Status states use observed fingerprints and original snapshot receipts, not a second
retained binary/text copy. Width-bounded shared renderer prioritizes the durability
label; state changes request a repaint even without provider/transport events.

## Necessary crash/first-run fixes

- Real SIGKILL after initial exclusive link publication can leave the record and its
  private temporary alias with nlink2. Reads now recognize only exactly one validated
  same-directory `.draft-<32hex>` alias of the complete private owned record inode;
  external/unknown hardlinks remain refused. **Load/catalog do not delete anything.**
  Explicit receipt-reviewed discard can delete that exact alias and record, not
  arbitrary temporary files. No background sweeping or unrelated cleanup.
- Actual offline Claw physical test revealed first-run missing application directory,
  which previously disabled autosave. Claw opts into private application-parent creation
  off its UI thread; pinned O_NOFOLLOW traversal, newly created0700 directories/fsync,
  final owned0700 parent required. Existing ancestors are not chmodded; intermediate
  symlinks and unsafe final application parent refuse. Generic store default remains
  non-recursive; no creation from model/user-provided arbitrary draft paths.

## Current named evidence

`packages/tui/tests/test_draft_crash_windows.py` exercises real owned Linux SIGKILL
for parent/child provenance at debounce, before update, after rename, completed but
unpolled receipt and editing while a prior writer is held. Reload checks exact old
or newly published complete snapshots, explicit uncertainty/conservative policy,
never latest unacknowledged edit or automatic submission. Initial link-publication
kill validates safe non-mutating alias recovery and later explicit discard.

`test_child_draft_autosave.py` proves pending/writing/latest receipt transitions and
failure-stop/uncertainty; `test_saved_child_drafts.py` proves actual both-host parent/
child status, edit-during-write and final latest commit. Existing deferred-quit flush
and disk ownership/read-review/save/discard tests retained.

`test_draft_durability_terminal.py` runs both shipped native clients in private Linux
tmux, offline provider/no live Claw service: committed/pending editing, cursor, resize,
native scrollback and exact termios restoration. Claw uses fresh offline data dirs.
No dependency install, auth/provider network call, user-service restart or publication.

Final integrated ten-file current-source gate includes stores/autosave/IO/panels,
crash matrix, real native-host drafts, physical terminals, compact/footer and Claw
terminal protocol: **170 passed, no skips,14.46s**, `/tmp/ui047-complete-current.log`,
handle25304 exit0. Root Ruff and seven-namespace strict mypy **487 files clean**,
`/tmp/ui047-complete-static.log`, handle53622 exit0. Current tui/coding/claw wheel
and sdist built `/tmp/ui047-artifacts`; six changed production modules compared
byte-for-byte with both artifacts, `/tmp/ui047-source-receipt.json`.

## Red evidence retained

First standalone physical invocation collected under package config and failed the
cross-package test-helper import; explicit root `-c pyproject.toml` corrected it.
Initial root physical gate1 passed/1 failed, `/tmp/ui047-physical-current.log`, showed
Claw missing first-run parent directory, not a platform-deferred issue. Production
private initialization fixed it and added symlink/mode/hardlink negative tests, no
mock-only bypass. Early Ruff formatting reds corrected before final static gate.

UI-047 acceptance complete. Final frozen full-root UI-055, final Linux safety/UI-064
and all other unfinished IDs remain required; this slice is not goal completion.

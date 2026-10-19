# Goal cumulative state and exact bounded reads — source verification

Date: 2026-10-19. Corrects F06 found during parent47 source audit. This is not
whole47/final review/current installed-artifact parity or overall completion.

## Root cause / actual RED

- Goal codec applied1MiB to the WHOLE accumulated authoritative Goal.160 legal
  tasks*8000-char descriptions could not be persisted. A reverse-ordered valid
 1500-task DAG also failed Python recursion depth. Actual typed Goal/GoalStore
  tests RED2/2.03s; no missing API/fake store.
- Parent add_task committed the real version2/61 tasks, then returned is_error
  because full status exceeded256KiB. A real SDK Agent worker's original context
  and a list70 valid Goals also became unreadable: RED3/1.94s. Source checkpoint
  Goal data limits were therefore a hidden lifetime quota, not just wire safety.

## Runtime boundary

- Authoritative snapshots/revisions keep the SAME exact typed format/journal,
  without an aggregate business byte/count quota. Iterative DAG validation keeps
  original task order and rejects missing dependencies/cycles without stack-depth
  limits. Physical SQLite/machine storage constraints naturally remain.
- Individual field/report/notice/artifact and evidence limits remain unchanged;
  original tool frame is256KiB. This does not enlarge model context/input/image
  capacities or grant permissions. Bound structured reviewer-result limits are
  still individual report limits, not silently removed or converted to auto-pass.
- Large immutable snapshot preparation/rendering uses owned workers. Bounded
  mutable artifact data is severed before the first await; canceled preparation
  settles before store close. Reopened store proves no write was admitted by a
  canceled preparation. Owner/schema/lease/corrupt-byte guards remain.
- Goal tools return existing full JSON for small complete values. Large results
  use explicit `xdog.goal_data_page.v1` readable canonical ASCII JSON fragments,
  exact sha256/selector/offset/total/next_offset/next_query. Raw fragment<=64KiB,
  final frame<=256KiB. No decoding tool or write permission is needed to read.
- A large mutation response is successful and marked snapshot_committed. Its
  continuation reads that immutable Goal/version; it never asks to repeat the
  mutation. Parent read can choose only a Goal/version in the same owner journal.
- Bound context accepts section/offset/limit only, using original worker/reviewer
  Goal/task/attempt/review version. A current criteria change cannot enter earlier
  pages. No owner/Goal/attempt/version override, file selector or arbitrary host
  method. Parent tool still cannot supply a worker report/raw reviewer verdict.
- Large lists use revision-fenced exact pages; a changed collection is an explicit
  error requiring restart, not silent splicing. Host owner is rechecked after
  aggregate rendering. Accepted business facts never depend on UI display.

## Actual tests and limits

`test_cumulative_state.py`: >1MiB actual put/get/reopen/historical version; valid
reverse1500 DAG; repeated-cancel actual encoding/lease/close and no admitted write.
`test_data_pages.py`: exact checksums/byte order/all-page reconstruction, current
mutation after first page, original actual SDK worker and readonly reviewer
contexts after criteria revision, collection revision change, hostile offset/
limit/version/owner queries, unchanged small JSON API, owned render and owner
change, actual packaged SkillManager reference discovery.

These tests use real SQLite/Agent/ChildSupervisor/filesystem/host SDK objects.
Only model boundary is scripted: actual worker3 and reviewer2 calls. They do not
substitute a fake host lifecycle for the required actual Claw/Coding integration.
Unchanged actual two-host Goal adapter/lifecycle/view suites plus Goal:145 passed
39.37s. Final Goal91 passed12.10s before the full root gate.

The packaged reference/role docs teach next_query, immutable version, offset,
readable text and snapshot_committed/no-action-replay. Reference baseline lacked
that API information (RED1/.32s); actual SkillManager now loads the guidance.
No paid/subagent skill-pressure run was authorized/performed, so this is API
reference/discovery proof, not a claim that a live LLM always follows prose.

## Current source gates

Unfiltered `uv run python scripts/test.py --jobs 2`: **7035 passed,34 skipped**,
212.21s; Claw1193/140.00s. Strict all9 mypy602 source files, full Ruff packages/
scripts, Flow17, lock/diff pass. Logs `/data/t.Vy5SaQ/goal-cumulative-final-root9.log`
and `goal-cumulative-final-static.log`. Original socket/terminal/deadline coverage
retained; no excluded case, xfail or timeout change. No runtime edit during gate.

## Remaining full objective

Return parent final-acceptanceTask2: finish47 source/test verdicts, one fresh
wholebranch review and required fixes, then fresh current wheels/sdists/Twine/
parity and real isolated Goal-only/Coding/Claw/ACP/native consumers. Older cold
snapshots predate these changes. No push/merge/publish/paid provider, user service,
credentials operation or existing user data change. Overall goal remains active.

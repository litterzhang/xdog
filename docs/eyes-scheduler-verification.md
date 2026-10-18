# Eyes scheduler verification — 2026-10-18

Implementation is in the original checkout on `feat/eyes-scheduler-source`, based
on `460e8dfd4b46e3b4ace526f81aa70efdf73c682d`, with uncommitted working-tree changes.
No worktree, automatic scheduler commit/push, installed service or paid provider
was introduced. The interrupted original Inbox/batch review was subsequently
resumed and included scheduler integration; it reported no new material scheduler
finding. Its four coding/Inbox findings, author fixes and scope limits are in
`docs/unified-inbox-batch-review-resolution.md`; no second reviewer verdict is
implied. The source/artifact numbers below are the scheduler's pre-fix checkpoint,
not a substitute for the newer fixed-runtime acceptance record.
Usage and exact timing limits: [scheduler source](eyes-scheduler-source.md).

## Coverage and source gates

The 32 source/loader cases and 14 executor/runtime cases, plus the new scheduled
CLI-isolation variant, add47 executable cases. They cover:

- Zoned one-off, finite interval and explicit-timezone five-field cron round-trips.
- Mutually exclusive modes, invalid finite/range/boolean/datetime/cron/timezone
  values, impossible dates, random-expression rejection and source/detector/route
  compatibility before activation.
- Monotonic interval timing, latest-occurrence coalescing, pause skipping, one-off
  completion, DST-aware calendar calculation and clock rollback non-rearm.
- Actual official-SDK Unix prompts for each mode, shared routing, mixed file/time
  evidence and bounded delivery with no fabricated file paths.
- Real model-free coding ingress → pending Inbox → sole SDK owner, retaining one
  scheduled canonical input plus its independent human input in one model call.
- Real Claw ACP enqueue → existing group queue/AgentSession, with no TUI.
- Expired one-off refusal before allocating receivers; repeating first period
  begins after every receiver connects, not at the start of a slow negotiation.
- A sub-microsecond cron boundary does not choose a future tick or skip the next
  occurrence due to datetime/float rounding.
- Packaged examples/skill JSON parse through the real loader; scheduled validation
  is model-free and imports neither the ACP SDK nor any XDOG harness.

Initial missing-source and missing-executor failures were observed before their
implementation. The startup-phase and cron rounding regressions each reproduced
RED before the permanent fixes. The existing SDK-only dependency assertions failed
on the newly declared croniter dependency; the wheel/CI allowlists now approve
exactly the bounded ACP SDK + croniter, and still reject every harness/other extra.
The actual updated CI wheel-check Python block was executed against final artifacts.

`uv run python scripts/test.py` completed all8 configured paths in81.26s:

| Package | Result |
|---|---|
| flow | 813 passed in 25.80s |
| claw | 643 passed in 32.52s |
| eyes | 239 passed in 67.58s (0:01:07) |
| ai | 729 passed, 7 skipped in 3.75s |
| tui | 682 passed in 14.71s |
| site | 42 passed in 2.50s |
| coding | 1213 passed, 12 skipped in 79.17s (0:01:19) |
| agent | 1842 passed, 15 skipped in 80.26s (0:01:20) |

Total: **6203 passed,34 skipped**. All scheduler-specific cases ran; skips are the
existing optional external integrations. Full log: `/tmp/eyes-scheduler-full-tests.log`.
Ruff over packages and the artifact script passed; strict mypy over all8 namespaces
passed (554 source files); all17 Flow examples passed; `git diff --check` passed.

## Fresh wheel-only consumers

All five wheels and five sdists (ai/agent/tui/coding/eyes) were freshly built and
passed Twine. Source tests used croniter6.2.2; fresh Eyes-only installation resolved
croniter6.2.4. Both exercised the scheduler calculation. Official ACP SDK0.12.1 and
Python3.13.9 were used.

Distribution directory: `/tmp/eyes-scheduler-dist.r1OKjM`.
Installed environments: `/tmp/xdog-acp-isolated.7pei6_eu`.
The executed `scripts/verify_acp_artifacts.py` checks:

1. Eyes-only file and scheduled definitions, shipped skill/examples, and a real
   timezone-aware cron occurrence, without installing/importing any XDOG harness
   or importing the ACP SDK during validation.
2. Generic Agent-only official-SDK stdio initialize/new/load/prompt/update.
3. Coding-only Unix enqueue/status/dedup with AgentSession/model imports forbidden.
4. Coding SDK owner: five retained callbacks plus one independent human input,
   one actual model call and physical writer-lease reuse after explicit disposal.

Every Python member in every final wheel/sdist was byte-compared to checkout;
Eyes' skill and both examples were also byte-compared. The distribution directory
contains `source-parity.json` with full SHA-256 values and counts. Eyes artifacts:

| Artifact | Python members | Resource members | SHA-256 |
|---|---:|---:|---|
| `xdog_eyes-2.4.2-py3-none-any.whl` | 31 | 3 | `199430045740ce02f239ccbc221c5597ddd08a1a7bb8657dee8b4c33553d2f6d` |
| `xdog_eyes-2.4.2.tar.gz` | 52 | 3 | `bb99dcb1b67bdda8fdf0adcc846096263893adbf475c3abc5d14bc795fe51a5d` |

## Limits not claimed as tested capabilities

No automatic registration restart, durable timer cursor, catch-up replay,
real-time precision, system cron, TCP/Windows transport or paid model/IDE test.
No exactly-once tool-side-effect guarantee. Calendar semantics use the declared
croniter range and installed IANA timezone data; this is not an exhaustive test
of every timezone/DST rule. Sources only collect evidence; receiver availability,
Inbox ownership and the target's existing tool policy still govern execution.
Skill examples were mechanically consumed/validated; no model-based skill-pressure
review or independent code-review verdict is implied by these source tests.

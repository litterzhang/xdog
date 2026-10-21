# Child-runtime acceptance contracts (repository tests only)

No production module imports this directory. There is no fallback to an Agent
runtime or import of either application in these contracts. The application test
collectors bind **real production types**, not runtime mocks:

- `packages/coding/tests/test_child_runtime_contract.py`
- `packages/claw/tests/test_child_runtime_contract.py`

Both collectors import the same test classes. `runtime_type` names the actual
class; `runtime_factory` supplies its real constructor (Claw additionally receives
the application-owned loop port). `child_store_type`, `child_store_module` and
`child_extensions_type` select that same host's storage and extension code.
`runtime_module` locates the actual image-return implementation for deterministic
byte-work/expiry gates, rather than patching a retired SDK implementation.

## Source map

| Former Agent tests | Shared contract |
| --- | --- |
| `test_supervision.py` | `lifecycle.py` |
| `test_supervision_{compaction,config,images,protocol,queue,recovery,store,subscriptions,tool}.py` | same suffix `.py` |
| `test_supervision_return.py` | `returns.py` |
| `test_main_agent_child_recovery.py` | `main_recovery.py` |
| `test_initial_task_recovery.py` | `initial_recovery.py` |
| `test_child_input_journal.py` | `journal.py` |
| `test_child_extension_input_journal.py` | `extension_journal.py` |
| `test_child_admission_checkpoint.py` (host/storage cases) | `admission.py` |
| `test_child_extension_readonly.py` | `extension_readonly.py` |
| `test_extension_terminal_input.py` (child-only negative surface) | `extension_readonly.py` |
| `test_child_presets.py` (runtime cases) | `presets.py` |
| `test_cross_loop_dispatch.py` (child runtime cases) | `cross_loop.py`, `dispatch.py` |

Pure SDK config/model/image/protocol/queue/transfer/capacity tests stay in Agent.
The generic MCP and extension dispatch tests also collect the shared cross-loop
contract, with their own SDK-only fixture; the generic `settle_on` tests remain
in Agent and import it from `xdog.agent.cross_loop`.

## Ownership, safety and retained evidence

`ownership.py` checks different **actual lifecycle owners** without changing the
business contract: Coding's service thread must be joined after work settles;
Claw must settle children, storage and close futures while its existing host
loop remains running, unchanged, and without creating a child service thread.
The test fixture owns/stops that application loop only after runtime checks.
Raw MCP dispatch shutdown remains an SDK test; application runtime shutdown tests
use an actual child provider request and its cancellation cleanup.

Admission still requires a fresh, idle Agent with empty history. Valid parent
history is rejected before any model call; malformed tool batches retain their
precise pair-validation failure. The actual post-admission provider-failure case
uses fresh history, with a separate negative parent-history case. Segmented
history, large complete tool pairs, model/image limits, archive-before-summary,
lossless bytes, queue revisions/selection, permission hooks, late cancellation,
original owners, write-before-ACK and no-replay assertions remain in the suite.
Legacy `instructions` is checked as first-user task material, never child/parent
system authority. The already-authored protocol and fixture corrections are
retained; ParentHandle security remains covered by `test_child_sdk_policy.py`.

`fixtures/*.py.txt` are the original SIGKILL scenario bodies, with only the host
imports parameterized. `processes.py` writes a concrete script importing the
selected production runtime/store; `python -I` subprocesses import no test
support, conftest, runtime alias from Agent, or repository test module. ACK-loss,
crash boundaries, byte identity and non-replay checks execute separately for both
hosts. Workers kill only their own explicitly created subprocesses.

## Run

From the repository root:

```sh
uv run pytest -n 0 -q packages/coding/tests/test_child_runtime_contract.py
uv run pytest -n 0 -q packages/claw/tests/test_child_runtime_contract.py
uv run pytest -n 0 -q packages/agent/tests
```

Normal package-worker collection includes each host's collector automatically;
there is no root conftest importing optional applications, custom collection
plugin, runtime shim, new dependency, or skip-on-missing-host behavior. Original
platform-specific SIGKILL/fsync guards are retained.

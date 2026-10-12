# XDOG gateway shutdown hang — October 12, 2026

## User-visible failure

The WeChat channel stopped receiving messages, while the gateway PID still appeared
alive. Investigation was read-only against the live XDOG process; its credentials,
configuration, and gateway lifecycle were not changed.

Observed on October12,2026, Asia/Shanghai:

- PID4577 started at08:57:40.
- WeChat polling and send requests were returning HTTP200 before the outage.
- At10:20:10 the gateway logged `Received shutdown signal`, then
  `Shutting down gateway...`, `Monitor cancelled`, and `Monitor stopped`.
- No `Gateway stopped.` completion followed. The gateway socket was no longer
  listening, but the process and PID file remained.
- Logs alone do not identify who sent the shutdown signal or distinguish SIGTERM
  from SIGINT; the handler logs the same message for both.

This identifies a shutdown/lifecycle failure, not a missing WeChat enable flag or
proof of an expired token. Existing credentials were not revalidated through a new
login, and HTTP200 alone is not a recipient-read receipt.

## Live stack evidence

Read-only GDB attachments used `-nx`, disabled automatic script loading and debug
information downloads, and detached on exit. The only manually loaded helper was
the installed CPython3.13 GDB reader. No inferior function calls or memory writes
were performed. Full diagnostic output remains private under
`/tmp/xdog-gateway-stop-debug/`; only frame locations were displayed.

The Python stacks showed:

| Thread | Observed wait |
| --- | --- |
| Main thread4577 | `threading._shutdown`; native stack in `wait_for_thread_shutdown` / `ThreadHandle_join`. |
| Thread7095 | `aiosqlite.core._connection_worker_thread`, blocked in `SimpleQueue.get()`. |
| Thread7682 | The child-supervisor owner loop, still inside `asyncio.run_forever()`. |

The SQLite connection thread is created without daemon mode. Its unclosed queue
worker therefore prevents Python interpreter exit. The surviving child-owner loop
also demonstrates that resource teardown had not completed.

## Root causes

### 1. The top-level coroutine did not await its own shutdown

Previously the signal callback scheduled `self.stop()` as an unowned background
task. `stop()` immediately set `_shutdown_event`, while `run_forever()` waited only
for that event and then returned. `asyncio.run()` began its global cancellation and
loop cleanup **before the gateway cleanup coroutine finished**.

A slow shutdown made this deterministic: the top-level runner returned while the
orchestrator cleanup still owned resources. Repeated signals could also create
competing cleanup tasks. The canceled bash pipe-reader warning in the live log is
consistent with interrupted asynchronous cleanup, not a WeChat login error.

### 2. Memory database cleanup was absent from the owning lifecycle

`HybridMemorySearch` and its indexer had `close()` methods, but `MemoryManager` did
not expose them and `Orchestrator.stop()` did not close memory. Even directly
awaiting the old stop method left a real SQLite connection/thread alive.

These defects are independent: merely waiting for the old stop routine is not
enough if that routine never releases the memory connection.

## Local implementation changes

Only these three production files changed:

- `core/runtime/gateway.py`: one shared shutdown task; concurrent/repeated stop
  requests join it, and caller cancellation waits for actual cleanup settlement
  before propagating. Signal handlers request shutdown; `run_forever()` awaits
  cleanup in `finally` before removing handlers or returning. A completed successful
  stop can be followed by a fresh start.
- `core/memory/manager.py`: an asynchronous `close()` forwards to the optional
  backend. Keyword-only storage is a safe no-op; hybrid storage closes the indexer.
- `core/runtime/orchestrator.py`: channel workers settle before their resources are
  closed. MCP and memory cleanup are awaited, including memory cleanup after a
  channel/MCP failure; failures remain explicit rather than being reported as success.

No thread was changed to daemon mode to hide the leak. No cleanup deadline was
inflated, and no live XDOG process was force-killed as a substitute for a fix.

## Regression evidence

New tests: `packages/claw/tests/test_gateway_shutdown.py` and its offline process
fixture `packages/claw/tests/fixtures/gateway_shutdown_peer.py`.

The pre-fix tests independently demonstrated:

- early `run_forever()` return and competing/interruptible cleanup;
- an unclosed real memory connection after direct stop;
- a SIGTERM child process returning without its cleanup marker;
- a SIGTERM child process with memory hanging at process exit.

The first test invocation had two fixture-path setup errors; those are not counted
as product failures. After creating the fixture, both actual SIGTERM cases failed
for the expected lifecycle reasons (`process-red.log`). Only confirmed test-owned
process groups were cleaned up by the test harness.

The final related suite passed **167 tests** (gateway, lifecycle, orchestrator,
scheduler, queue, WeChat monitor/channel, memory, and children). It includes actual
SIGTERM process exit with real SQLite and child-supervisor threads, repeated
signals, direct stop, cancellation, restart-after-stop, and channel/MCP error cleanup.
Evidence: `/tmp/xdog-gateway-stop-debug/current-regression.{log,xml}`.

Full `ruff check packages` and strict mypy across all seven namespaces passed
(549 source files). A private Claw wheel/sdist build passed; all three changed
production modules were verified byte-exact in both artifacts. No artifact was
uploaded. Summary/hashes: `/tmp/xdog-gateway-stop-debug/final-evidence.json`.

The live-process stack match and these isolated reproductions support the diagnosis;
a broad new full-workspace or live WeChat end-to-end pass is not claimed.

## OpenClaw service cleanup (separate service)

`openclaw-gateway.service` was a different Node-based gateway. It was repeatedly
failing its installed Node-version check. Per the user's cleanup request:

1. Its user unit was privately backed up.
2. `systemctl --user disable --now openclaw-gateway.service` stopped it and removed
   the enablement symlink.
3. The backed-up user unit file was removed and systemd user units reloaded.
4. Verification reported `LoadState=not-found`, `ActiveState=inactive`, `SubState=dead`.

Backup: `/home/zhang/.codex/backups/openclaw-gateway-service-p9gg8_lm/`.
The shared systemd drop-in, Node installation, OpenClaw application data, and XDOG
configuration/credentials were preserved. `reset-failed` found the already-unloaded
unit absent; it was not necessary to stop the restart loop.

## Deployment boundary

This patch is released separately as **v2.3.2**, without replacing the v2.3.1 tag.
Git refs and the publishing workflow determine the actual release outcome; the
investigation's local tests are not proof of PyPI publication. Editing source cannot
repair a process already in interpreter finalization. The wedged XDOG process was
not restarted during this investigation or release preparation. Restoring its WeChat
service requires a separately authorized normal stop/start, with bounded
identity-checked escalation only if ordinary termination fails.

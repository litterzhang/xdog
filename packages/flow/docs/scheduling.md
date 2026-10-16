# flow — Workflow Scheduling & `xdog-flow scheduling install`

Status: **v1 shipped** (Linux/systemd) · Audience: flow maintainers · Prerequisite:
skim `cli-agent.md` (the bundle is the deployable artifact a scheduler wraps).

Today a workflow runs once, on demand (`xdog-flow run`, or `python <bundle>`).
This doc designs **scheduling** — making a workflow fire on its own — plus an
`xdog-flow scheduling install` command that builds a portable bundle and installs the
scheduler on Linux. Two scheduling modes:

1. **Active / timer** — fire on a schedule (every N minutes, or a cron expression).
2. **Passive / hook** — fire when an external **signal** arrives (a webhook, a file
   drop, a queue message), reusing flow's existing signal primitive.

The guiding principle (same as every prior feature): **the scheduler wraps the
built artifact; it does not change execution.** `interpret == compile` and the
whole engine are untouched — a scheduler is just something that *invokes* the
bundle with the right environment.

---

## 1. What already exists (the substrate)

Two facts make this a thin layer, not a new engine:

- **The bundle is a self-contained runnable.** `xdog-flow generate --portable`
  emits a directory whose `__main__.py` runs the workflow via `python <bundle>`,
  reading its inputs from **environment variables**: `FLOW_INPUTS` (JSON merged
  into `$in`), `FLOW_PROVIDER`, `FLOW_SIGNALS`, `FLOW_MAX_TOKENS`,
  `FLOW_RUN_ID`/`FLOW_CHECKPOINT_DIR`. So "run this workflow with these inputs /
  this signal delivered" is already expressible as a subprocess + env — exactly a
  scheduler's job.
- **flow already has a signal primitive.** A `human` node with a `signal` pauses
  the run (`WorkflowPaused`) until that signal is present in the `FLOW_SIGNALS`
  set (executor.py ~566). Passive scheduling is *delivering a signal* to a
  workflow — the mechanism is built; scheduling wires an external event to it.

The scheduler is therefore **OS glue** around a runnable artifact, not code inside
the engine.

---

## 2. Where the schedule lives — a `schedule` block in workflow.json

A top-level optional `schedule` block on the workflow (mirrors `max_concurrency`
etc. — a `WorkflowDef` field, parsed + validated + round-tripped):

```jsonc
{
  "name": "cli-triage",
  "schedule": {
    "mode": "timer",                 // "timer" | "hook"
    // --- timer mode ---
    "every": "15m",                  // "30s"/"15m"/"2h"/"1d", OR:
    "cron":  "*/15 * * * *",         //   a 5-field cron expression (mutually exclusive)
    "inputs": { "report": "..." },   // optional fixed $in for each firing
    "timeout": "40m",                // bound on ONE firing (default "1h")
    "jitter":  "15m",                // spread firings across a window (timer only)
    // --- hook mode ---
    "signal": "new-ticket",          // the signal delivered on each event
    "listen": { "type": "http", "path": "/hooks/triage", "port": 8787 }
  }
}
```

- A workflow with **no** `schedule` block behaves exactly as today (run-once).
- `mode` selects the mechanism; `timer` uses `every` **or** `cron` (validation
  rejects both/neither); `hook` uses `signal` + a `listen` transport.
- `inputs` is optional per-firing seed data (env `FLOW_INPUTS`); secrets follow the
  `${ENV_VAR}` rule (never inline a token — resolved at fire time).
- `timeout` bounds one firing. It is **not** a nicety: a `Type=oneshot` unit
  inherits systemd's `DefaultTimeoutStartSec` — 90 seconds on most distributions —
  which would kill essentially any workflow that talks to a model. The installer
  therefore always writes an explicit `TimeoutStartSec`, defaulting to `1h`.
- `jitter` spreads firings across a window (`RandomizedDelaySec`), so several
  workflows sharing an hour boundary do not all start at the same instant.

The block is **declarative config for the installer**, not something the engine
reads at run time — the engine still just runs once per invocation.

---

## 3. `xdog-flow scheduling install`

Scheduling lifecycle operations are grouped under one CLI namespace:

```
xdog-flow scheduling install <workflow.json> [--name NAME] [--dry-run]
xdog-flow scheduling uninstall <NAME> [--dry-run]
xdog-flow scheduling list
```

`install` requires a workflow source. `uninstall` and `list` operate on the local
install registry and do not take a workflow file.

**Install** steps (Linux v1):
1. **Build.** `build_bundle(wf, <prefix>/<name>)` — a `--portable` bundle is the
   deployable unit (self-contained; for a pure-CLI workflow it vendors nothing).
2. **Emit units** from the `schedule` block (§4 / §5) into the user systemd dir
   (`~/.config/systemd/user/`), or a crontab line as a fallback (§6).
3. **Enable** the unit (`systemctl --user enable --now <name>.timer` /
   `.service`), unless `--dry-run` (which prints the unit files without installing).

Bundles default to `~/.local/share/xdog-flow/bundles/<name>/`; units reference
them by absolute path. `--name` defaults to the workflow name.

**`scheduling list`** shows each installed workflow, its mode (timer/hook), and — for a
timer — its next-fire time (from `systemctl --user list-timers`); for a hook, the
listener's active/failed state and bound transport. It reads an install registry
(a small JSON manifest under `~/.local/share/xdog-flow/`, written on install) so
it knows *which* units are flow's, independent of systemd's global list.

**`scheduling uninstall <NAME>`** is the inverse of install: it disables the
unit, removes generated unit files and bundle, and drops the registry entry. An
unknown name is a clear error.

Nothing here touches the engine; the `scheduling` command group delegates to
`flow.scheduler`, which renders and installs the OS units.

---

## 4. Active / timer scheduling — how it works

A **systemd user timer** fires a **oneshot service** that runs the bundle. Two
generated units:

`<name>.service` (the work):
```ini
[Unit]
Description=flow workflow: cli-triage

[Service]
Type=oneshot
# Absolute path to the installed bundle; env carries per-firing inputs.
Environment=FLOW_INPUTS={"report":"..."}
Environment=FLOW_RUN_ID=cli-triage
Environment=FLOW_CHECKPOINT_DIR=%S/xdog-flow/cli-triage/ckpt
ExecStart=/usr/bin/python3 /home/user/.local/share/xdog-flow/cli-triage
```

`install` provisions the bundle as a **uv project** and points `ExecStart` at the
resulting `.venv`. The bundle ships a `pyproject.toml`, so one `uv sync` picks a
matching CPython (downloading one if the host has none), creates `.venv`, and
installs the dependencies. uv itself is installed if missing.

This is the default because the alternative is a trap: `/usr/bin/python3` has none
of the bundle's dependencies on a typical host, so a unit pointed at it fails on
its first firing — hours later, with only a systemd status to explain why.

```bash
xdog-flow scheduling install wf.json                    # uv-provisioned (default)
xdog-flow scheduling install wf.json --no-venv \
    --python ~/proj/.venv/bin/python                    # reuse an existing env
```

The bundle carries the workflow's own directory with it: a `run: "module:func"`
script node compiles to a real import, which the interpreter satisfies by putting
the workflow's directory on `sys.path`. A unit runs from somewhere else entirely,
so `xdog-flow scheduling install` copies the workflow's sibling `*.py` modules into
the bundle — the whole set, because those modules routinely reach for peers flow
cannot see (a helper import, or a subprocess spawned as
`Path(__file__).parent / "other.py"`).

`<name>.timer` (the schedule):
```ini
[Unit]
Description=timer for flow workflow: cli-triage

[Timer]
# from "every": OnUnitActiveSec=15min ; from "cron": OnCalendar=*/15 * * * *
OnCalendar=*/15 * * * *
RandomizedDelaySec=15min        # only when "jitter" is set
Persistent=true                 # catch up a missed firing after downtime

[Install]
WantedBy=timers.target
```

- **`every: "15m"`** → `OnUnitActiveSec=15min` (relative, drift-free interval).
- **`cron: "*/15 * * * *"`** → `OnCalendar=<translated>` (systemd calendar syntax
  is a near-superset of cron; the installer translates the 5-field cron to
  `OnCalendar`, rejecting constructs systemd can't express).
- Each firing is an **independent one-shot run** of the bundle — no long-lived
  process, no in-engine scheduler loop. This keeps the engine stateless and the
  failure model simple (a crashed run doesn't wedge the schedule; the next tick
  fires clean).
- `Persistent=true` gives at-least-catch-up semantics after the machine was off.
- Logs go to the journal (`journalctl --user -u <name>.service`).

**Why systemd timer over an in-process loop:** no daemon to keep alive, survives
reboot, native logging + backoff (`RestartSec`), and the run is the exact same
`python <bundle>` a human would type — so "scheduled" and "manual" runs are
identical (no divergent code path, mirroring `interpret == compile`).

---

## 5. Passive / hook scheduling — how it works

The event source varies (HTTP webhook, file-drop, queue), but the **core is one
idea**: an external event → deliver a **signal** to a fresh run of the bundle.

**One shared listener, not one-per-workflow.** All hook workflows on a host are
served by a **single** systemd user service — `xdog-flow-listener.service` — that
reads the install registry (§3) and **routes** each event to the right bundle. So
N hook workflows share one process and one port; installing another hook workflow
adds a route and reloads the listener, not a new daemon. This avoids N idle
processes and port collisions (two workflows can't both grab 8787). The listener
is created when the first hook workflow is installed and removed when the last is
deleted.

`xdog-flow-listener.service` (one per host):
```ini
[Service]
Type=simple
# The listener reads the registry to know every hook workflow's route + bundle.
ExecStart=/usr/bin/python3 -m flow.scheduler.listener --registry %S/xdog-flow/registry.json
Restart=on-failure

[Install]
WantedBy=default.target
```

Routing per transport:
- **`http`**: one bound `port` for the whole host; the request **`path`** selects
  the workflow (`POST /hooks/triage` → triage, `POST /hooks/deploy` → deploy). The
  request body (JSON) becomes that workflow's `FLOW_INPUTS`; the listener spawns
  its `python <bundle>` with `FLOW_SIGNALS=<that workflow's signal>`. Returns the
  run's `$output` (or 202 + a run id for async).
- **`file`**: the one listener watches **each** hook workflow's declared directory;
  a dropped file routes to that workflow (its content → `FLOW_INPUTS`).
- **`socket`**: a Unix socket per workflow is the exception — systemd socket
  activation is per-socket, so a socket-mode workflow may get its own
  `.socket`+`.service` pair rather than joining the shared http/file listener.
  (Documented so the model is honest; http/file are the shared common case.)

Each event → **one bundle subprocess** with `FLOW_SIGNALS` carrying that
workflow's declared signal. The workflow's `human`-node-with-that-signal proceeds
instead of pausing — so the *same* workflow runs manually (paused at the signal)
or via hook (signal delivered). The passive path reuses the existing pause/resume
primitive verbatim; scheduling only supplies the signal from an external event.

### 5.1 Who starts (and supervises) the listener

**systemd owns the listener's lifecycle — flow writes no daemon/supervision code.**
The single shared listener program (`flow.scheduler.listener`) routes http/file
transports; `install` brings it up via systemd. What differs is how systemd starts
each transport:

- **Resident (http, file)** — the one `xdog-flow-listener.service` (`Type=simple`)
  that `install` enables on the first hook workflow
  (`systemctl --user enable --now xdog-flow-listener.service`). systemd starts it,
  restarts on crash (`Restart=on-failure`), and re-launches on boot/login
  (`WantedBy=default.target`). Adding/removing a hook workflow updates the registry
  and reloads (`systemctl --user reload-or-restart`) — no new service.
- **On-demand (socket)** — a per-workflow systemd **`.socket` + `.service` pair**
  using socket activation: systemd holds the socket and starts the service on the
  first connection. Socket-mode workflows do not join the shared listener (§5,
  socket bullet); this is the one per-workflow case.
- **On-demand (socket)** — a systemd **`.socket` + `.service` pair** using **socket
  activation**: systemd itself holds the listening socket and starts the service
  only on the first connection (and can stop it when idle). The listener need not
  run continuously; systemd is the front door. `install` enables the `.socket`.
- **No-systemd fallback** — `install` cannot supervise a long-lived process
  without an init system, so it prints a ready-to-run
  `nohup python -m flow.scheduler.listener --config … &` line (or writes it into
  the user's shell-profile startup) and documents that the user owns keeping it
  alive. Timer mode's crontab fallback (§6) needs no daemon; hook mode does — this
  is the honest limitation of a systemd-less host.

So "who starts the listener" is **systemd** (resident service, or socket-activated
pair), installed and enabled by `xdog-flow scheduling install`; the only case where the user
starts it by hand is a host without systemd.

**Why a thin listener, not a framework:** flow doesn't own the event source. The
listener is a minimal adapter (event → env → subprocess) so a webhook, a cron-job
POST, a CI callback, or a message-queue bridge all funnel through the same
"deliver a signal" door. Anything richer (auth, retries, fan-in) is the event
source's concern or a later transport.

---

## 6. Fallbacks & portability

- **crontab fallback** (when systemd-user is unavailable, e.g. a minimal
  container): `install` writes a crontab line `*/15 * * * * FLOW_INPUTS=... python
  <bundle>` for timer mode. Hook mode has no crontab analogue — it needs a
  long-lived listener, so on a systemd-less host the listener is started via a
  provided `nohup`/`&`-style shim or documented as manual.
- **Linux only.** systemd user units cover the supported deployment. The
  unit-rendering interface is not a promise of non-Linux backends.
- The scheduler layer is **optional** — it lives in `flow.scheduler` and is only
  imported by `xdog-flow scheduling install`; `run`/`generate`/the engine never touch it.

---

## 7. `interpret == compile` and the engine

Untouched. Scheduling wraps the **artifact**:

- A **timer** run is `python <bundle>` — the same self-contained module the
  compiler already produces and the parity tests already gate. Scheduled ==
  manual, byte for byte.
- A **hook** run is the same bundle with `FLOW_SIGNALS` set — the signal path is
  the existing human-node mechanism, covered by `test_human.py`.

So no new execution semantics, no new parity surface: the scheduler is tested by
asserting it **renders the right unit files / env** and that the listener spawns
the bundle with the right `FLOW_SIGNALS`/`FLOW_INPUTS` — not by re-testing
execution.

---

## 8. Security

- **No secrets in unit files.** `schedule.inputs` and `mcp_servers.env` use
  `${ENV_VAR}`; the installed unit references the *variable* (systemd
  `Environment=` can pull from a root-only `EnvironmentFile=`), never the literal
  token. `install --dry-run` shows exactly what would be written so a secret can't
  leak unnoticed.
- **User-scoped by default.** Units install under `~/.config/systemd/user/` and
  run as the invoking user — no root, no system-wide daemon.
- **Hook listener is minimal + explicit.** It binds only the declared port/path,
  runs each event as an isolated subprocess (a crash can't corrupt the listener),
  and does not expose the workflow beyond the declared transport. Auth (a shared
  secret / bearer header) is a `listen` option, off by default with a warning when
  a port is public.

---

## 9. v1 scope & non-goals

**v1 delivers:** the `schedule` block (model + loader + serialize); `xdog-flow
install`/`scheduling list`/`scheduling uninstall`; timer mode via systemd user timer
(interval + cron) with a crontab fallback; hook mode via a single listener
supervised by systemd — **http and file as resident services, socket via
socket-activation** — plus a no-systemd `nohup` fallback; a small install registry
(JSON manifest) backing `scheduling list`/`scheduling uninstall`; `--dry-run` unit preview; docs + one
scheduled example.

**Non-goals (v1):**
- No macOS/`launchd` or Windows (Linux/systemd first; interface leaves room).
- No distributed scheduling / multi-host coordination (single machine, unchanged
  non-goal).
- No in-engine scheduler loop or long-lived workflow process — every firing is a
  fresh one-shot bundle run.
- No built-in retry/alerting beyond systemd's (`Restart=`, `OnFailure=`); richer
  ops integration is later.
- No hook transports beyond http/file/socket in v1; a queue bridge (SQS/NATS) is a
  follow-up transport behind the same "deliver a signal" interface. (All three v1
  transports share one listener supervised by systemd — §5.1.)

---

## 10. Risks

1. **cron → `OnCalendar` translation.** systemd calendar syntax differs from cron
   in edge cases (step values, day-of-week). *Mitigation:* translate the common
   subset, validate at install, and fall back to a crontab line (real cron) when a
   cron expression can't be faithfully translated — never silently mis-schedule.
2. **Absolute paths / relocation.** Units hard-code the bundle path; moving the
   bundle breaks the timer. *Mitigation:* `install` owns the bundle location
   (`--prefix`), `scheduling uninstall` cleans it, and `scheduling list` shows the path;
   re-install to relocate.
3. **Hook listener as an attack surface.** A public port running a workflow is
   dangerous. *Mitigation:* bind localhost by default, require an explicit
   `listen.host`/auth to expose it, isolate each run in a subprocess, and warn
   loudly in `install` output.
4. **Missed firings / overlap.** A long run overlapping the next tick, or downtime.
   *Mitigation:* `Persistent=true` for catch-up; `OnUnitActiveSec` (not
   `OnCalendar`) for drift-free intervals; document that a run longer than its
   period will queue (systemd serializes a oneshot service by default).
5. **Secret handling in `Environment=`.** A literal secret in a unit is
   world-readable in some setups. *Mitigation:* `${ENV}` + `EnvironmentFile=` with
   `0600`, and the `--dry-run` audit.

---

## 11. Phased delivery (TDD)

1. **Model + loader.** `WorkflowDef.schedule` (a small frozen dataclass: mode,
   every/cron, inputs, signal, listen); parse + validate (mode-specific rules,
   every-xor-cron) + serialize round-trip. Unit tests only.
2. **Unit rendering.** `flow.scheduler.systemd` renders `.service`/`.timer` (timer)
   and `.service` (hook) from a `schedule` block; a crontab renderer. Pure
   string-render tests (assert the emitted unit text), no OS side effects.
3. **`xdog-flow scheduling` command group.** Wire `install`, `uninstall`, and
   `list`; build bundles, render units, call `systemctl --user`, and maintain the
   registry. Tests stub `systemctl` and assert files, registry entries, and cleanup.
4. **Shared hook listener.** `flow.scheduler.listener` — ONE listener service
   routing all installed hook workflows (http by `path`, file by watched dir); it
   spawns the right bundle with `FLOW_SIGNALS`/`FLOW_INPUTS`. Tests drive the
   listener in-process against a fake bundle, asserting routing + the right env +
   the signal reaching a human node, with two hook workflows sharing the listener.
5. **Docs + example.** A scheduled example (a `timer` triage and a `hook` triage)
   and a README note on `journalctl` and `scheduling uninstall`.

---

## 12. Verification

- `uv run ruff check packages/flow/src` · `uv run mypy --strict packages/flow/src`
  · `uv run pytest packages/flow/tests -q` (unit rendering + stubbed systemctl +
  in-process listener — no real timers or ports in CI).
- End-to-end (manual, Linux): `xdog-flow scheduling install examples/cli_triage_timer.json`,
  confirm `systemctl --user list-timers` shows it, wait a tick, check
  `journalctl --user -u cli-triage.service`, then use `xdog-flow scheduling list`
  and `xdog-flow scheduling uninstall cli-triage`. For hook, install two workflows
  and confirm one shared `xdog-flow-listener.service`,
  `curl` each webhook path, see each run fire.
- `git checkout -- uv.lock` before commit.

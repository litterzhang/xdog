# Scheduling workflows on Linux

Scheduling is an explicit OS deployment operation around a generated workflow
bundle. It does not add an in-engine scheduling loop or a Flow management daemon.
The installed `flow` tool can invoke these non-interactive CLI commands under the
host's normal permissions; installing the package/skill alone installs no timer
or listener. Eyes scheduler sources are a separate observation feature, not a
Flow deployment backend.

## Timer definition

Add a top-level `schedule` block to an otherwise valid workflow:

```json
{
  "mode": "timer",
  "every": "15m",
  "inputs": {"topic": "release readiness"},
  "timeout": "40m",
  "jitter": "2m"
}
```

Use exactly one of `every` (positive integer + `s`/`m`/`h`/`d`) or five-field
`cron`, for example `"cron": "0 9 * * 1"`. `timeout` bounds the oneshot service
(default `1h`); `jitter` spreads timer firings. Inputs seed `$in` through
`FLOW_INPUTS`. The workflow engine still runs once per invocation.

Timer installation renders a `.service` and `.timer`:

- `every` becomes `OnUnitActiveSec`, with an initial `OnBootSec=1min`;
- `cron` is translated to systemd `OnCalendar` (not every cron construct is
  expressible; rendering can reject one);
- the timer has `Persistent=true`; inspect systemd's actual timer behavior, not
  an exactly-once or all-missed-occurrences replay guarantee;
- the service runs the generated bundle, not a different scheduling executor.

## CLI lifecycle

From the repository root after `uv sync`:

```bash
# Preview only: no bundle writes, dependency provisioning or systemctl calls.
uv run xdog-flow scheduling install packages/flow/examples/digest_timer.json --dry-run

# Real installation: review before running these mutating commands.
uv run xdog-flow scheduling install packages/flow/examples/digest_timer.json
uv run xdog-flow scheduling list
uv run xdog-flow scheduling stop WORKFLOW_NAME
uv run xdog-flow scheduling start WORKFLOW_NAME
uv run xdog-flow scheduling uninstall WORKFLOW_NAME
```

`WORKFLOW_NAME` is the installed workflow name (`--name` overrides the JSON name).
`install` takes a workflow file; the other lifecycle commands operate on the
install registry, not a source file. `stop`, `start` and `uninstall` also accept
`--dry-run` for existing entries. `list` prints registry facts, not verified live
next-fire times; inspect `systemctl --user` / `journalctl --user` for OS status.

Timer install enables and arms the timer by default. `--no-start` omits immediate
arming for **timer** installation but still enables it for a later login/boot;
it is not a durable disabled state. `stop` stops the timer and its service,
including an active timer run. `start` rearms it. Uninstall stops it before
removing units, bundle and registry entry. These controls cannot undo completed
script/model/tool effects; inspect interrupted effects before a new run.

Default locations:

| Artifact | Location |
| --- | --- |
| Units | `~/.config/systemd/user/` |
| Registry | `~/.local/share/xdog/flow/registry.json` |
| Bundle | `~/.local/share/xdog/flow/<installed-name>/` |

There is no public CLI `--prefix` option. These Flow locations are not the Eyes
user-local daemon directory or a project observer runtime.

## Provisioning and generated dependencies

Real install builds a portable bundle. By default it provisions a bundle `.venv`
with `uv sync` (including installing uv if needed), which may download packages
or Python. For timers, the unit uses that environment's interpreter. To use an
already provisioned environment for a timer:

```bash
uv run xdog-flow scheduling install packages/flow/examples/digest_timer.json \
  --no-venv --python /absolute/environment/bin/python
```

Generated output has the same graph behavior as the interpreter, not a promise
of no dependencies. SDK nodes need `xdog.agent`/`xdog.ai`; subflows still call
`xdog.flow`. Portable packaging vendors selected sources, not every transitive
child/custom dependency. Inspect the artifact and requirements; see
[subflows](subflow.md). Sibling Python modules and local workflow skills can be
copied with the bundle, but arbitrary imported packages are still your concern.

## Hooks: distinguish configuration from live transport support

A hook block declares a signal and listener metadata, for example:

```json
{
  "mode": "hook",
  "signal": "new-ticket",
  "listen": {"type": "http", "path": "/hooks/triage", "port": 8787}
}
```

The router turns event JSON into `FLOW_INPUTS` and supplies `FLOW_SIGNALS` to a
fresh bundle subprocess. A matching human node can then proceed. Hook entries
share `xdog-flow-listener.service`; stop/start disables/enables their registry
routes and reloads the listener, not a guaranteed cancellation of a previously
spawned bundle. `--no-start` is currently applied to timers, **not** hook setup.

Current deployment limitations matter:

- The listener entry point serves HTTP on loopback (default port 8787), using
  the registry's paths. It returns HTTP 202 after spawning; this is not the
  workflow's result or a durable execution receipt.
- A file-delivery router exists internally, and validation accepts file/socket
  listener metadata, but the shipped listener entry point does not start file
  watchers or socket-activated services. Do not treat schema acceptance as
  working file/socket deployment.
- The rendered listener service currently names `flow.scheduler.listener`,
  while the actual module is `xdog.flow.scheduler.listener`. Hook deployment
  needs operator review/correction; it is not an out-of-the-box verified path.
- The default listener and its spawned bundles use `/usr/bin/python3`; they do
  not inherit the timer bundle's uv interpreter. Review installed imports and
  environment rather than assuming timer provisioning makes hooks runnable.
- No built-in HTTP authentication option is enforced by the listener. Keep it
  private; do not expose it as an authenticated workflow service.

There is a crontab **renderer** in the implementation, but the public installer
currently uses systemd; it does not detect systemd absence and install a cron or
nohup fallback. Non-Linux adapters and distributed scheduling are out of scope.

## Trust, secrets and confinement

Workflow scripts and imports are trusted code, not sandboxed programs.
`--workspace` records a work directory; `--confined` enables cooperative checks,
and repeatable `--allow-path` supplies extra trees. These are installer-owned
choices, not access a workflow can grant itself. Confinement rejects unsupported
inline scripts, bash tools and external CLI backends; it is not an OS sandbox.

Do not place secrets in `schedule.inputs`: they are serialized into unit
configuration and visible in previews. There is no general secret-resolution
promise for those inputs. `${ENV_VAR}` handling for CLI MCP configuration is a
separate runtime feature and still requires the right service environment.

`install --dry-run` skips OS mutations/provisioning but **still loads and validates
the workflow**, which may import custom modules. Only use trusted definitions.
Verification can safely exercise help, shipped validation and unit previews;
real installs, model runs and user systemd operations are separate explicit work.

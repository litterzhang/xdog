# Scheduling and file observation through Eyes

Claw has no task scheduler, managed-observer runtime, event relay or Eyes report
manager. Installing `xdog-eyes` contributes a tool through normal capability
discovery; opening the tool binding ensures the independent Eyes daemon is
running. Discovery/import alone remains inert. Tool permissions still apply.

The main Agent follows the packaged, application-neutral `eyes` skill. It writes
an ordinary Eyes v2 JSON file, then calls:

```json
{"action": "create", "owner": "my-watches", "workspace": "/project", "definition_path": "eyes.json"}
```

Sources, detectors, handlers and routes are all defined by the JSON. Multiple
sources/routes are supported. Handler destinations may be any valid explicit
ACP endpoints/sessions, not just the current group. Optional handler `cwd` gives
the target workspace independently from source roots. Claw does not inspect or
rewrite the definition or restrict its destinations through an Eyes adapter.
The receiver retains its normal access/protocol checks.

For a one-off use `at` with a future zoned timestamp. For repetition use
`every_seconds` (monotonic fixed phase) or five-field `cron` with an explicit IANA
`timezone`. This is native Eyes schema, not a separate Claw timer grammar.
Save the user's reason and requested next action in handler `instruction`.

## Normal ACP input, enabled by default

Every Claw gateway starts its private Unix ACP receiver at
`<data_dir>/acp/agent.sock`, or the explicitly configured `acp_socket_path`.
This does not depend on Eyes, Goal, TUI, or observers. The main Agent's runtime
prompt exposes its actual endpoint, opaque `session_id`, `cwd`, and enqueue
payload shape. It needs to know how others can send a normal ACP message, not
how Eyes runs sources or monitors delivery.

The daemon delivers through the JSON's ACP handler directly:

```text
Eyes daemon → normal ACP input → Core Inbox → current main Agent
```

ACP does not choose a chat reply recipient. The main Agent uses the normal
message tool if the request needs a user notification. Ordinary user messages
keep their usual reply path. There is no captured creator reply, observer output
source, per-observer host DB or automatic report-routing exception.
Eyes tracks observation and handler delivery only; a queued receipt is not
Agent completion or proof that a user received a message.

## Management and lifetime

The `eyes` tool supports `create`, `list`, `status`, `pause`, `resume`,
and `cancel`. CLI uses the same daemon registration IDs and management namespace.
`cancel_notifications` is removed: Eyes does not manage downstream chat outputs.
Stopping collection does not cancel accepted inputs or reverse their effects.
Closing Claw/tool binding leaves the daemon's accepted registrations intact;
the daemon owns recovery. Unknown sends never become automatic effect replay.

No old observer journal is read or migrated. Existing data is left untouched.
Old accepted Core input/output records remain Core facts, not observer records
to import or resend. Retired implicit output routes are not reactivated.

The old task scheduler also remains removed: no `tasks_file`, no
`scheduled_tasks.json` import and no legacy migration. Create observations only
for explicit requested work; there is no `eyes_enabled` switch or automatic
package installation. The skill contains no Claw-specific policy.

Owner is supplied by the caller for every tool operation; Claw/Eyes do not impose
a session/group naming scheme. Keep the chosen owner with your task materials and
reuse it with the same workspace. Eyes persists it as registration metadata next
to the accepted definition, independently of the destination. Default daemon data
lives in `~/.local/share/xdog/eyes`; explicit directory overrides remain supported.

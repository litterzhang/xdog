# ACP input origins and optional chat reply targets

ACP is an input transport into the application's existing group Inbox. It is not
an installed chat channel and does not identify a user reply recipient.

## Default behavior

- `xdog/enqueue` keeps its durable queued/duplicate ACK and original message ID.
  Standard `session/prompt` waits for the original receipt and returns its stop
  reason and `_meta.xdog` execution identifiers, **without agent-message chunks**.
- Both paths execute on the group's existing main Agent, under the same group
  controller and tool policy. Results remain in the Core input receipt and
  session transcript; there is no second agent, channel broadcast or extra turn.
- Host input metadata records `input_origin: acp`; the input has no reply channel
  or default `ReplyTarget`. Even an `ExternalInput` carrying a nonempty channel
  label cannot infer a reply target. Untrusted source/provenance cannot choose one.
- Standard prompt execution errors remain ACP request errors. Enqueue validation,
  capacity errors, cancellation/detachment, durable receipts and ACK-loss
  idempotency retain their existing semantics.
- The shipped TUI sends `acp_updates: 0` by default, so automatic ACP completions do
  not appear as live chat replies. The existing explicit `acp_updates: 1`
  diagnostic subscription remains available. Historical transcript display can
  still use `channel: acp` as a source label; it is not delivery routing.

## Explicit reads

`xdog/inbox_status` retains its namespace, status, attempt ID, execution session ID,
error and cursor fields. Selecting `message_id` additionally returns a bounded
`response` with `response_offset` (default `0`) and `response_next_offset` in that
message row. Pass `response_next_offset` as the next request's `response_offset`
until it is `null`. Offsets count Python Unicode characters, not encoded bytes;
each response page contains at most 16,384 characters. The offset must be a strict
integer in `0..1048576` and requires a selected message. Cursor/list pages do not
include response bodies. Error previews remain limited to 1,024 characters.

Each selected result's state, attempt/session identity and response come from one
immutable receipt snapshot. Callers reading across an explicit retry must compare
`attempt_id` and restart pagination if it changes. These are explicit result reads,
not unsolicited ACP chat messages. They neither execute a model nor retry tools.

## Existing explicit delivery is independent

Ordinary Weixin and TUI user messages still reply through their original captured
channel bindings. Trusted installed output sources may supply an optional frozen
original `ReplyReference`; this remains distinct from the ACP input origin.
Accepted routes are not reselected after ACK loss or restart.

The existing, separately developed `message channels` / single-channel
`message send` implementation is retained. A main Agent handling an ACP input may
explicitly invoke that tool under its original permissions. Its per-message output
receipt is independent from the ordinary saved turn result, and sending does not
change the default reply route. There is no automatic fallback or broadcast.

## Installed capability owner restoration audit

No Goal-specific branch was added to Claw. The existing generic lifecycle already:

1. Activates the group input scope and stable Inbox namespace.
2. Opens `GroupCapabilityHost` and the installed group providers without preparing
   an AgentSession or calling a model.
3. Publishes the initial `owner.available` snapshot when a provider subscribes.
4. Publishes availability changes when the actual ACP endpoint is installed or
   retired, whether it precedes or follows capability initialization.
5. Settles capability jobs, retires the original owner, and closes its resources
   before releasing storage on application shutdown.

`test_capability_owner_recovery.py` restores real persisted Goal records with both
startup orders. Active/task-wait records restore their owned observation and can
persist one coalesced ACP reminder without constructing an AgentSession. Paused,
user-wait and completed records neither release their wait nor enqueue
work. Repeated availability hints do not duplicate accepted reminders, and endpoint
retirement stops the owned observation. This tests Goal as an installed capability;
all production host code remains schema-neutral.

## Limits and deployment

- This changes the default standard-prompt reply behavior; clients that consumed
  streamed ACP answer chunks must explicitly query the receipt instead.
- Explicit diagnostic completion subscriptions and transcript/history inspection
  remain possible. This is default reply suppression, not transcript redaction.
- Existing persisted inputs/routes are not migrated or rewritten. A duplicate ID
  retains its original accepted envelope. The existing receipt result-size limit
  and uncertain-send/interrupted-execution review requirements still apply.
- Current verification uses temporary test data, scripted model transports and
  isolated Unix endpoints. No running user gateway is restarted. Earlier runs,
  before daemon isolation was added, were found to register test-only workspaces
  in the default Eyes daemon; evidence-preserving cleanup is coordinated
  separately, not silently performed by this fixture.
- Existing processes must load the updated code through a separately authorized
  normal deployment; this change does not hot-patch them.

### Verification scope

Run `uv run ruff check packages/claw`, `uv run mypy --strict -p xdog.claw` and
`uv run pytest packages/claw/tests -q` from the repository root. Focused coverage
includes `test_acp_silent_inputs.py`, `test_acp_backend.py`, `test_acp_gateway.py`,
`test_capability_owner_recovery.py`, existing output-source routing tests and the
uncommitted outbound-message / Goal-question delivery tests.

The admitted public wait states in `test_goal_host.py` are `user` and `tasks`;
the same test covers user pause and the requirement for explicit user resume.
Separate coverage rejects new `wait(host)` without a revision or current-binding
change, and restores a historical host-wait fixture without rewriting it,
automatically releasing it, starting observation or executing a model. Historical
fixture creation uses the existing Goal test helper, never a production API that
claims to admit a new host wait.

### Test-owned Eyes daemon isolation

`tests/conftest.py` supplies an autouse, function-scoped fixture for all Claw tests,
including synchronous tests that launch child interpreters. It overrides both
`XDG_STATE_HOME` and `XDOG_EYES_RUNTIME_DIR` with a fresh short `/tmp/claw-eyes-*`
root; child processes inherit those values. Ambient user runtime overrides cannot
redirect these tests to a real user's daemon. No daemon starts just to create the
fixture or to run cleanup.

At teardown the fixture addresses only its own explicit directory and original
generation, submits `daemon-stop`, waits for endpoint removal and original lease
release, and reaps a child started by the current interpreter. It does not signal
arbitrary PIDs or stop a daemon found in the default user directory. Only after
successful settlement is that private test root removed; a failed teardown leaves
its private state intact for diagnosis and fails the test.

`test_eyes_daemon_isolation.py` invokes the actual installed `eyes` AgentTool in
both the parent and child interpreters. Create/list/status/cancel use generic Claw
capability ports and share the fixture-owned daemon rather than the default
instance; no host-side Eyes SDK or Eyes CLI invocation is involved. The three-process
managed observer recovery test seeds an already accepted historical checkpoint
against a past wall time: the now-independent daemon uses real wall time, not the
old host-injected fixed future clock. Its exactly-one execution and report/no-replay
assertions are retained.

### Host source boundary

Claw application source contains no direct Eyes implementation import or Eyes CLI
invocation. `test_application_boundary.py` checks every Claw product Python source
for ordinary/dynamic imports and literal CLI calls, and tests that the detector
rejects representative violations while permitting generic capability/tool IO.
The Eyes extension internally uses private `_daemon_control.DaemonControl` to
implement its registered tool. This is not a public client SDK. Goal's separately
installed main-owner capability uses CLI subprocesses instead; Claw owns neither
that domain policy nor a feature-specific runner. The real tool/isolation test
proves that enforcing this source boundary does not merely remove the live tool.

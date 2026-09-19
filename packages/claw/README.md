# xdog-claw

**Agent orchestration runtime.**

Runs and supervises long-lived agent sessions: scheduling, a gateway, tool
memory, and persistence backed by SQLite.

## Interactive terminal

`uv run xdog-claw tui` connects to the configured gateway using the main terminal
buffer and native scrollback. It shares Coding's Unicode multiline editor and
bounded layout. Goals/todos summarize retained state; queued messages keep their
full text while only a compact preview appears.

Ctrl+O opens recent tool/reasoning details. Left/Right changes entries and Page
Up/Page Down scrolls. Escape closes autocomplete/details before cancelling a
run. Cancellation and busy rejection restore submitted and queued text ahead
of a newer draft; input submitted during cancellation stays editable. Ctrl+Z
suspends on POSIX. Remote coding and fullscreen application modes are excluded.
See [terminal verification](../../docs/tui-pi-parity.md) for tests and limitations.

## Messaging replies

Messaging channels such as WeChat send each completed assistant text message as
it becomes available, including progress messages between tool calls. They do
not wait for the whole agent turn or resend an aggregated response at the end.
Reasoning, tool arguments/results, and empty messages are not sent as replies.
Long messages still use the normal channel chunking. WeChat's typing indicator
stays active until the turn finishes.

This is message-level delivery, not token streaming. WeChat polls for new
messages independently while the agent works. A FIFO inbox holds up to 50
pending messages, and a single worker dispatches them in arrival order through
the orchestrator. A later arrival does not change the active message's reply
recipient or typing indicator. When the inbox fills, polling pauses until space
is available rather than dropping messages.

The inbox is in-memory, not a durable job queue: stopping/restarting the gateway
cancels the active handler and discards pending messages. Wait for replies
before restarting; messages left pending may need to be resent.

## Part of xdog

This package is one piece of [xdog](https://github.com/litterzhang/xdog), a
local-first toolkit for building, running and scheduling LLM workflows. The
centrepiece is [`xdog-flow`](https://pypi.org/project/xdog-flow/) — a typed
workflow format and compiler.

Documentation: **https://xdog.942295.xyz**

## Licence

Copyright (c) 2026 HugeMan <942295.xyz>

GNU Affero General Public License v3.0 or later — see
[LICENSE](https://github.com/litterzhang/xdog/blob/main/LICENSE).

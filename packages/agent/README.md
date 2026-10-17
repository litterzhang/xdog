# xdog-agent

> Platform scope: **Linux only**. Non-Linux adapters and native-platform support
> are outside the current product scope; historical audit evidence is not a support promise.

**Agent runtime: tool calling, structured output, steering.**

The agent loop that sits on top of [`xdog-ai`](https://pypi.org/project/xdog-ai/):
tool registration and dispatch, structured output contracts, and mid-run steering.

## Part of xdog

This package is one piece of [xdog](https://github.com/litterzhang/xdog), a
local-first toolkit for building, running and scheduling LLM workflows. The
centrepiece is [`xdog-flow`](https://pypi.org/project/xdog-flow/) — a typed
workflow format and compiler.

Documentation: **https://xdog.942295.xyz**

## Licence

Copyright (c) 2026 HugeMan <942295.xyz>

GNU Affero General Public License v3.0 or later — see
[LICENSE](https://github.com/litterzhang/xdog/blob/main/LICENSE). Output compiled
by `xdog-flow` is exempt; see the
[Generated Output Exception](https://github.com/litterzhang/xdog/blob/main/LICENSE-EXCEPTION.md).

### Identified steering and follow-up inputs

For interactive clients, `queue_input(id, text, "steer" | "follow_up")` returns an
immutable `QueuedInput`. `pending_inputs()` snapshots its lane and selected state;
`change_input(id, "edit" | "remove" | "move", ...)` acts only before a checkpoint
selects it. Move direction is -1/+1 within its lane. All these operations belong
on the agent's owning event loop; GUI threads must marshal requests to that loop.
Existing `steer()` and `follow_up()` APIs remain unchanged.

`InputConsumedEvent` is emitted only after admission to agent history, not on
acceptance/dequeue. Selected but unadmitted inputs remain recoverable. After a
run settles, `drain_inputs(prefix="")` returns/removes unconsumed identified
inputs; a prefix scopes cleanup without removing other owners or legacy inputs.
Persistence remains the caller's responsibility. IDs/receipts do not replace
pre-tool hooks, grant permission, or provide OS isolation.

### Explicit stdio MCP tool clients

`xdog.agent.mcp.McpManager` owns configured stdio connections on one background
asyncio loop, so SDK callers can use proxies across per-turn event loops. Declaring
servers does **not** launch commands. `connect`, `refresh`, `disconnect`, `status`,
`tools` and `aclose` are the explicit lifecycle surface; synchronous `close`
requests owned cleanup without blocking terminal restoration. Hosts own command
trust, idle tool-set changes and permission hooks.

Configuration is `{server_id: {command, args, env, timeout, allow_tools}}`.
Commands are argv (no shell); cwd is host-selected. IDs are bounded ASCII names,
metadata/status never exposes configured environment values, and unsupported
transports fail visibly. `allow_tools` is host policy, not a protocol annotation.
The current discovery probe uses MCP 2026-07-28 request metadata and
`server/discover`; recognized version rejection is not treated as a legacy peer.
Only method-not-found triggers legacy initialize/initialized for supported
2025-11-25/2025-06-18/2025-03-26/2024-11-05 versions. Sampling and autonomous
model execution are not supported. Elicitation and MCP Tasks are also removed;
only explicitly configured Roots disclosure is retained for owned requests.

Paginated tool discovery validates identities and object JSON Schemas (Draft
2020-12), rejects external schema references instead of fetching them, and produces
stable namespaced aliases capped below provider tool-name limits. Input and
advertised structured output are validated. Text/images and inert resource data
are adapted to the existing `AgentTool` interface; linked resources are not
fetched. Server execution errors and unsupported content are visible errors, not
silent success/drop. Tool annotations never authorize execution.

Requests are correlated/multiplexed with bounded frames, stderr is separately
bounded/drained, timeout/cancellation fences late responses, and EOF/malformed
streams disable the connection. Legacy cancellation is best effort, not rollback;
current stdio never sends forbidden unsolicited cancellation notifications.
Neither calls nor connections automatically replay/reconnect. Owned child/process
groups are terminated/escalated/reaped at disconnect/shutdown. Connection/schema
mutation must occur while the host's agent is idle. HTTP and inert context previews
and explicitly reviewed public-PKCE OAuth are described below. Removed input/task
capabilities are not a promise of future model/callback delegation.

### Explicit HTTP MCP connections (current and legacy)

A server can use `{"transport":"http","url":"https://host/mcp"}` instead of
stdio argv. Connections still start only on explicit host management. The current
2026-07-28 HTTP path sends matching per-request protocol/client metadata and HTTP
`Mcp-Method`, `Mcp-Client-Info`, `MCP-Protocol-Version`, tool `Mcp-Name`, and valid
`x-mcp-header` argument mirrors. Modern discovery reads `supportedVersions` and
server identity from `_meta`, not the legacy `protocolVersion` result shape. This
also corrects the initial stdio discovery implementation/fixture from pass 11.

JSON and response-scoped SSE are decoded with bounded line/event/body sizes and
correlated IDs. UTF-8 may cross network chunks; multiline data and CR/LF delimiters
are supported. Incomplete/oversized/mismatched/current-unsolicited responses fail
visibly, never become successful tool data. Header mirrors validate properties-only
primitive paths, case-insensitive uniqueness and integer range, encode unsafe or
ambiguous values with the prescribed base64 sentinel, and retain arguments in JSON.
An invalid header tool is rejected with status diagnostics, not silently registered.

Legacy Streamable HTTP fallback distinguishes non-protocol HTTP rejection from
recognized modern negotiation/unknown-method errors. Legacy initialize/initialized,
protocol/session headers, optional GET notification stream, best-effort cancellation
and owned session DELETE are supported. Session expiry requires explicit reconnect;
POST tool calls are never replayed. Cursor-based legacy SSE resumption is supported
as described below; this is not autonomous connection recovery or POST retry.

HTTP endpoints are explicit and reject URL userinfo/fragments/reserved protocol
header overrides. TLS is default; non-loopback plaintext requires explicit
`allow_insecure_http: true`. `headers` supplies static headers and `headers_env`
maps header names to environment-variable names read at connect. Credential values
are absent from status/errors. No redirects, ambient proxy inheritance, resource
fetches, token refresh or OAuth/browser activity occurs automatically. 401/403 and
redirects are explicit failures without downgrade/credential forwarding. Interactive
OAuth uses the explicit public-PKCE surface below, never an automatic auth retry.


### Configured MCP Roots and retained transport continuations

Roots are **configured disclosure metadata only**. A server config may include
`"roots": [{"uri":"file:///absolute/workspace","name":"Workspace"}]`, at most
16 validated local absolute file URIs. Nothing enumerates the filesystem, infers
Roots from cwd, grants tool access or creates an OS sandbox. Explicit connect
admits that configured disclosure to the selected peer; disconnect withdraws it.
There is no mutable Roots picker, enable/replace review or Roots-change UI.

Legacy `roots/list` answers stay within the configured connection ceiling. Modern
2026-07-28 `input_required` responses remain supported for owned `tools/call`,
`prompts/get` and `resources/read`: only configured Roots and bounded opaque
`requestState` continuations are accepted. Original request arguments and identity
remain fenced; continuation rounds, embedded inputs, bytes and deadlines are
bounded. An unsupported sibling input rejects the round before any partial Roots
disclosure. Disconnect/cancel settles the original owner, not a successor.

**Sampling, Elicitation and MCP Tasks are removed** from SDK/native/gateway
registration, capability advertisement and review/control surfaces. Legacy
requests are refused; modern embedded unsupported families/task augmentation fail
closed, with no callback, model/tool work or partial completion. The former
Sampling/Elicitation/Tasks/Roots-management keys are not replacement controls.
Basic extension input/select/confirm and provider OAuth callbacks are separate
retained interfaces, not MCP Elicitation.

Ordinary modern Roots/request-state continuations do not authorize retrying an
uncertain operation. Active resource/catalog/combined subscriptions are removed.
Ordinary response-scoped SSE and request telemetry remain. Legacy cursor-based
**GET** recovery retains the
original identity/deadline and never repeats a tool **POST**; current stateless
HTTP does not acquire legacy GET resumption. No disconnect, failed response or
continuation silently reconnects, replays POSTs or expands configured disclosure.


### Inert MCP resources and prompt previews

`McpManager.catalog(server, kind)` accepts `resource`, `template`, or `prompt`
and validates bounded pagination/identities/arguments. Resource-only and
prompt-only peers can connect without advertising or requesting tools.
`resource(server, uri)` and `prompt(server, name, arguments)` return frozen
`McpDocument` data; they do not call a model, fetch linked URIs or insert messages.
HTTP reads/gets reuse the owned transport and URI/name header mirrors. Prompt
arguments are named strings, with required/unknown names checked against the
catalog. Deferred/interactive results and invalid roles/content fail explicitly.

Text previews quote resource and user/assistant prompt roles as untrusted data.
Binary blocks retain base64 and MIME metadata for raw export, but `draft_text()`
refuses binary rather than silently dropping it. This is not model image-resource
insertion or automatic prompt execution. Current host browsers expose explicit
connection/catalog/read/preview/draft/export gestures; connection/schema changes
still use host idle ownership and existing tool permission policies.


### Cursor-based legacy SSE recovery

Legacy HTTP can resume an interrupted POST response (including initialization) with
**GET** to the same configured endpoint using its server-issued `Last-Event-ID`.
The original integer request ID, issued session ID, requested/negotiated legacy
protocol version and absolute request timeout remain in force. A GET retrieves that response; it never sends another
`tools/call` POST. Concurrent requests own independent cursors. Optional legacy
notification GET streams have separate cursor ownership and bounded resumption.

Only complete SSE events commit a cursor, including empty priming events. A
partial successor cannot skip unreceived content. UTF-8 IDs (up to 1,024 bytes),
byte-split CR/LF/CRLF, NUL-ID ignoring and empty-ID reset are handled explicitly;
unsafe HTTP-header controls/oversized IDs and invalid JSON/identities fail. Server
`retry` intervals are bounded to 50 ms–5 s; there are at most three recovery GETs.
No cursor/reset, exhaustion, authentication failure, redirect, unavailable stream
or expired session fails visibly without replay, credential forwarding or automatic
initialize/reconnect. After a notification stream exhausts, disconnect/connect
explicitly to restart it. Remote effects cannot be rolled back by cancellation.

Current stateless HTTP does **not** use GET resumption. Request-scoped progress/log
notifications are tolerated as telemetry, not tool/model content; unrelated server
requests/notifications remain errors. Current resource reads mirror the URI in
`Mcp-Name` (not an invented `Mcp-Uri`). Cancellation/disconnect closes owned reads
and bounded waits, and existing Coding approvals/Claw `allow_tools` gates remain
unchanged. Real third-party replay retention behavior remains unverified.


### Task planning skill and host access scope

`xdog-planning` is a builtin packaged skill for ordinary objective/scope/dependency
notes, acceptance criteria and evidence-backed progress. It grants no permissions,
creates no parallel task database and starts no second Agent loop. The former
`xdog.agent.planning`/`PlanOwner`/`PlanState`, dedicated plan inspector and
`update_plan` tool are removed.

`xdog.agent.access` supplies the separate host-owned `AccessState`/`AccessScope`.
Coding/Claw expose `/readonly on|off|status` as native user controls, never model or
extension tools. Restriction changes persist before publication and require idle
ownership. Only explicit user release can clear read-only scope; release leaves
ordinary permissions, MCP/extension allow-tools gates and child access unchanged.
Saved access/legacy planning state is decoded fail-closed, not turned into executable
plan commands or restored tool grants. Skills/task text cannot release the scope.

SDK embedders still supply pre-tool policy. The native read-only allowlist is local
filesystem read/ls/grep/find and current time; shell, external/extension and child
control calls are not inferred safe. Permission gates are not an OS sandbox.

### Cancellation-safe owned work

`xdog.agent.async_work` contains lightweight shared primitives, not another runtime
or permission layer:

- `run_owned(callable, *args, **kwargs)` offloads synchronous work with caller
  contextvars. Cancellation waits for the actual thread and consumes a late
  failure; a canceled delivery is never replaced by a successful return value.
- `wait_owned(future_or_task)` joins an **already-owned** Future/Task without
  propagating caller cancellation to it. Its actual error takes precedence;
  otherwise deferred caller cancellation is raised after successful settlement.

These contracts intentionally differ. Keep locks, capacity slots, writer leases,
transaction receipts and the single shared close task at the calling owner until
its helper settles. Neither helper retries, replays effects, creates an Agent,
changes tool permissions, or owns a service lifecycle. The existing
`supervision_transfer.settled_image_work(work=...)` import, name and one-argument
signature remain compatible.

### Explicit extension process owners

`xdog.agent.extensions.ExtensionAPI` exposes the trusted Python registration API;
`xdog.agent.extensions.runtime.ExtensionRuntime` owns activation, namespaced tool/
command proxies, events, hook transforms, declarative UI and disposal. It reuses
MCP's bounded owned stdio process and manager loop primitives, with private extension
methods; it is not a hosted control plane, new agent loop, or OS sandbox. Worker
startup uses a trusted absolute bootstrap with safe-path mode, relative extension
imports get isolated package identities, and settings cross the owned pipe rather
than process argv. Python code can access the same filesystem/network as the user.
Hosts must supply explicit trust, enforce their permission gates AFTER transformed
arguments, and route structured UI to their own event loop instead of letting an
extension write raw terminal control sequences. See client READMEs for the v1 scope
and the remaining broader extension API gap.

Plugin terminal key/paste/release interception and observation are not supported.
There is no `on_terminal_input` registration, terminal callback worker protocol or
terminal event capability. Native input, shortcuts and approval focus stay owned by
the clients; submitted-input hooks remain separate and do not receive raw keystrokes.

The private extension-worker RPC protocol, MCP JSON-RPC and ACP transport are
retained. They are distinct from Coding's removed `--rpc` JSON-lines frontend;
there is no Coding frontend draft/editor/custom-UI or progress-receipt API.


### Durable extension entries and context hooks

Extensions can now call `api.append_entry("note", {"text": "durable data"})` from
an active callback. Host-owned JSON entries are bounded, independently namespaced
per extension and committed before the callback receipt is returned. Subsequent
callback context contains that extension's `context["entries"]`; unload/reload
retains it. Session/new/branch activation follows the selected durable snapshot,
not process globals. Old owners cannot publish entry writes after navigation.
Close invalidates late writes, so shutdown callbacks should not append entries.

Register `api.context(callback)` to transform the ephemeral message list before
EACH model invocation. The callback receives serialized typed user/assistant/tool
content plus its entry snapshot and returns a message list. It may inject summaries
or filter complete history batches, but does not edit the canonical transcript,
system prompt, static cache prefix, host read-only scope or tool permissions. Coding also
composes an existing SDK transform before its extension pipeline.

```python
def setup(api):
    async def remember(args, context):
        api.append_entry("note", {"text": args})
        return "Saved entry"
    async def model_context(messages, context):
        notes = [row["data"]["text"] for row in context["entries"] if row["kind"] == "note"]
        if notes:
            messages.append({"role": "user", "content": [
                {"type": "text", "text": "Context note: " + notes[-1]}]})
        return messages
    api.command("remember", "Persist a note", remember)
    api.context(model_context)
```

Unknown/system roles, unsupported parts, invalid images/signatures and incomplete/
invented tool-result batches fail explicitly, not by silently dropping content.
No-op transforms preserve original typed messages. Images and signed thinking/tool
parts survive chunked full-context transfers beyond the individual extension-worker
RPC frame limit.
Transfers are checksummed, order/identity fenced and spooled off-loop, with a 128 MiB
explicit context limit, 64 KiB blocks and an absolute whole-transfer deadline.

Entry capacity is 128 entries across a session, 16 KiB per entry and 512 KiB total.
Invalid/corrupt stored state fails instead of quietly resetting it. Persistence
failure keeps the previous snapshot. Cross-loop cancellation settles committed
metadata/file ownership before the host releases its lifecycle reservation; it
cannot roll back an already completed write. Context-hook failures disable the
extension and do not trigger another model call or automatic replay.

Durable entries and context hooks do not grant editor/session mutation, raw terminal
interception or custom-layout authority. Registered shortcuts, basic dialogs, rich
results and session-owned providers have their separate contracts below. Extension
code is still trusted code with the user's privileges, not an OS sandbox.


### Interactive extension dialogs

Active extension callbacks now support `await api.select(title, choices)`,
`await api.input(title, message="", default="")` and
`await api.confirm(title, message)`. Optional `timeout` is bounded to 0.1–600 s.
Select/input return `None` on cancel/no UI; confirm returns `False`. Cancel answers
this dialog only—it is not tool approval, permission revocation or automatic turn
submission. Trusted extension code decides what to do with the answer.

The worker suspends the callback and returns an owned pending-callback/dialog
receipt. The host displays a focused basic dialog, then resumes that SAME callback
with the validated answer; it does not rerun a command/tool to obtain the answer.
Multiple sequential dialogs work. Without an interactive host, canceled values are
returned immediately rather than hanging unattended execution.

Basic dialogs sanitize control codes, scroll long details, preserve the main editor
draft and input cursor, and require a rendered visible question/action before Enter can
answer. Select/confirm pastes do not submit; tiny/resize/release/repeat/unpainted
selection input cannot confirm. Escape/Ctrl+C cancel the dialog. Tool approval and
output review retain higher focus priority. An affirmative extension confirmation
never grants the underlying agent tool permission or switches host read-only scope.

Dialog IDs are host-generated and scoped to their session/request/run/connection.
Timeout, abort, navigation, disconnect and shutdown settle/cancel pending futures;
late/foreign answers cannot resume a successor's callback. Claw command waits are
connection-owned background jobs so the socket can still process UI replies and
other controls while the group remains reserved. Context-hook dialogs still share
the context transfer's absolute deadline; the UI does not renew model/transport
budgets. Callback effects remain trusted code, not OS-enforced isolation.

These are basic input/select/confirm dialogs, not plugin forms or a layout API.
Custom renderers, nested dialogs, extension overlays and terminal interception are
removed. Registered command shortcuts and submitted-input hooks remain separate;
none of these interfaces grants tool permission or full Pi API compatibility.


### Child-supervision runtime

`xdog.agent.supervision.ChildSupervisor` owns a separate asyncio loop and bounded
children, with start/inspect/steer/follow-up/wait/cancel/close operations and reserved
`agent_spawn`/`agent_control` tool adapters. Each factory supplies a fresh `Agent`
and MUST implement host permission/read-only policies and safe context inheritance.
Coding/Claw now provide permission-aware factories and `/agents` commands on top.
The module alone does not grant access or provide a UI. No generic access string
is an approval or a sandbox.

Main-Agent `agent_control(action="queue", id=..., text=JSON)` and SDK
`ChildSupervisor.control("queue", ...)` retain the owner-scoped queue/recovery API.
Edit/remove/move/return, explicit `recover`/`recover_images`/`recover_task`, and
separate durable discard remain guarded by revisions, selected-input/byte limits,
write-ahead journals and caller permission hooks. The main-Agent wrapper returns
images as typed `ImageContent` with bounded receipt metadata, not raw base64 text.
Native child queue panels expose inspection only; they cannot mutate or recover
those records. Restoring records/copying recovery never restarts child execution.

Limits: three children, 16 retained records, 16 model requests, a 32,000 reported-token
stop threshold (not exact billing), 1–600-second execution deadline (default 120),
32 identified queued inputs and 512 KiB / 2,000-message retained transcripts. The
request wrapper clamps output limits; provider input accounting can overshoot on
one request. Children cannot recursively delegate through `agent_spawn` or `agent_control`.
Persistence callbacks and all parallel tool siblings settle before slots release;
failed callbacks are visible. Transcripts carry a stable checkpoint index; inherited
context with incomplete tool batches is rejected before making a request.
Synchronous `close()` requests disposal; `await aclose()` settles the owner thread.

`Agent.set_stream_fn()` replaces an idle request adapter. Owner-loop-only
`await agent.cancel_and_wait()` cancels and joins the producer, including hook
cleanup; `abort()` remains the normal cooperative cross-thread stop request.
Async cancellation cannot terminate blocking/native or cancellation-suppressing
code, revoke approvals, or roll back tool effects.


Factories may be asynchronous; execution deadlines include asynchronous preparation.
Optional host admission/start/finalization/event/forget callbacks support stable
snapshots, atomic interruption markers, extension cleanup and durable catalogs.
`idle_scope()` freezes child admission throughout a host's idle-only context/policy
change. `ChildStore` scopes records by session/branch; loading never resumes work.
Both clients retain parent validation/context authority while independently owning
child extension workers/tool executors/entries. Focused child UIs and independently owned select/input/confirm child dialogs are
now wired in both applications. Task-specific labels/instructions, tool subsets, fresh context and downward budgets
are now applied through validated `ChildConfig`. Per-child model/reasoning is now wired through the actual request adapter; named
specialist presets are now wired too; complex extension forms/renderers are removed.


`ChildExtensions` captures the origin UI binding at admission, creates an independent
per-child `DialogBroker`, and fences replies by child/session/lease identity. Detach
returns canceled values to pending and subsequent questions, without replay/adoption.
Inherited parent validation still runs under `silent_extension_ui()`; it never
borrows a child's or parent's unrelated question reservation. Actual child callback
questions are UI answers, not host tool approvals or host access changes.


`ChildConfig` is frozen and validated independently of UI policy. `ChildSupervisor.start`
accepts a bounded configuration dictionary; the factory receives its corresponding
`ChildSpec`, and the supervisor enforces actual tool-subset and request/output limits.
Specialist instructions compose with parent restrictions using `child_prompt()`.
Unsupported tool names fail before a model request; zero selected tools is an empty
executable set. `parse_child_start()` supports explicit lexical flags without shell
execution and preserves the task suffix. Configurations persist/restore without work
replay. These settings never provide approval, a second tool loop, sandboxing or a
automatic provider activation or model fallback.


Provider adapters from `stream_fn_from_provider()` implement optional
`ModelStreamFn.resolve_model()` cached metadata. `resolve_child_model()` uses that
actual adapter for canonical routing, reasoning/capability/limits—never an ambient
`ai.load()` fallback or model sync. Custom adapters lacking the contract fail closed
on explicit overrides. Model switches remove continuity from the child copy and
recheck transformed image/context data before forwarding. Requested config and
constructed effective choices persist separately. Existing host gates, budgets,
settlement and default inherited behavior remain intact.


`ChildPresets` is a frozen data-only catalog of bounded `ChildConfig` templates.
`resolve()` expands sparse explicit overrides without changing access/host policy.
The supervisor exposes the catalog through `agent_control` `presets` and applies the
same expansion to `agent_spawn`/direct starts. Unknown/chained or privilege-bearing
templates fail before admission. Persisted records contain expanded settings and
provenance, not live template pointers or automatic replay instructions.


### Native child editor recovery is transient

Coding and Claw expose only reviewed same-session child input reopen and image
preview. They have no child editor disk save/load/delete, cross-session catalog
or autosave controls; `/agents saved` refuses without disk/background work. Existing
child snapshot files are not inspected, migrated or deleted. Reopening preserves
current editing text/images/settings/cursor, never a prior execution approval.
Native primary `/draft` persistence remains separate.

This does not remove actual Agent child journals, initial-task records, stopped
Agent/SDK image/task recovery or Agent-tool-owned queued-image return/backups. Those durable
records are execution/recovery data, not child editor autosave or a resurrected
cross-session draft UI. Generic worker/provider OAuth callbacks and read-only
extension menus also remain distinct from the removed MCP input/task families.


### Explicit MCP OAuth profile and SDK

HTTP server configuration may include an explicit pre-registered public-client
profile. This currently uses a restricted pinned-issuer discovery profile, not
complete automatic OAuth registration/interoperability:

```json
{
  "remote": {
    "transport": "http",
    "url": "https://resource.example/mcp",
    "oauth": {
      "issuer": "https://issuer.example/tenant",
      "client_id": "pre-registered-cli-client",
      "scopes": ["tools:read"],
      "callback_port": 0
    }
  }
}
```

The SDK exposes `await manager.manage_auth("remote", "prepare")`. Inspect the
returned resource/issuer/client/scopes/endpoints before explicitly calling
`manage_auth("remote", "start", review_id)`. Open the returned authorization URL
manually in the operator's browser—no auto-launch/code paste. The issuer's registered
client must accept the implementation's loopback callback at
`http://127.0.0.1:PORT/callback`; port zero selects a free port, not a promise that
an arbitrary issuer accepts it. Status/cancel/forget are explicit actions too.

Actual S256/code exchange, state/issuer/resource/scope checks, bounded callback
ownership and memory-only exact-resource credentials are implemented. A subsequent
explicit connect still registers tools; authorization does not change tool approvals
or `allow_tools`. Forget erases local credentials/disconnects proxies, not remote
revocation. Tokens stay private in memory unless separately reviewed for an explicit
supported store; they locally expire within one hour and are never automatically
refreshed/replayed. OAuth cannot mix Authorization header credentials or remote
plaintext resource URLs. HTTPS verification remains on; optional `ca_file` is an explicit absolute trust-file path.

The MCP client is pre-registered/public-PKCE only. Resource/issuer discovery uses
bounded credential-free GETs, exact resource/issuer matching, operator scope ceilings,
issuer-origin endpoints, TLS verification and uncompressed JSON. This is not hosted
client-metadata registration or DCR. Unsupported registration/authentication modes
are refused, not silently downgraded. Explicit refresh and both Linux credential
stores are retained separately; no operation implies automatic login or tool trust.


UI hosts use `manage_auth(..., lease=..., alive=...)` and `detach_auth(lease)` to fence
consent and late credential commitment. A foreign lease cannot prepare/start/cancel/
forget another view's transaction. Metadata cancellations settle actual coroutine
ownership; terminal/client close may be awaited idempotently after synchronous close.
Host guards are not tool approvals. Completed safe status can include a consumed
review for local forget; waiting status exposes no reusable live URI/receipt.
Public-client consent, explicit refresh and storage keep distinct receipts. Full
external-provider interoperability is not inferred from this bounded profile.

Whole-page consent proof belongs to the actually focused review. Blur/preemption,
resize or changed endpoints/scopes clears confirmability and requires fresh full
review; background painting does not grant authorization or tool permission.


### Authorization metadata discovery

Resource denial challenges now retain their `resource_metadata` pointer and
operation-specific scopes for an explicit subsequent authorization, without replaying
failed MCP POSTs/tools. Quoted multi-scheme headers are parsed with bounds; malformed
or ambiguous challenges are not silently discarded. Without a pointer, path-specific
then root protected-resource metadata are tried. Issuers use ordered OAuth and OIDC
well-known candidates; only absent GET routes (404/405), never bad identity/200 data,
redirects or token/code errors, permit fallback.

Pointer metadata uses no existing MCP credentials/cookies. Its origin must be the
configured resource/issuer or an explicitly approved HTTPS origin in the optional
OAuth `metadata_origins` array (at most eight origin-only URLs). This approval does
not change the pinned issuer or authorization/token endpoint trust. Resource/issuer
identities still match exactly. Challenge scopes are operation-specific and take
priority without assuming they are listed in `scopes_supported`; the configured
scopes remain an operator ceiling. Scope-less listings are displayed as no request,
and over-ceiling requirements fail rather than silently expanding access. Actual
review data carries discovery URLs and scope-source provenance. Consent/permissions/
leases/explicit connect remain separate. Client-metadata and dynamic registration
are removed; explicit public refresh and both retained Linux stores do not imply
universal external-provider interoperability.


### Pre-registered public-client boundary

MCP OAuth accepts only an existing **pre-registered public client ID**, S256 PKCE
and token authentication **`none`**. `registration: "pre-registered"` and
`token_auth_method: "none"` are the defaults and the only supported values.
Metadata-client-ID/hosted-client-document registration, dynamic registration,
confidential client-secret methods and private-key JWT/signing are removed.
Unsupported profiles fail closed before authentication/network work; no secret
source is read, signer started, registration POST sent or method/store fallback
attempted. There is no remote client-registration management API.

Pinned **resource/issuer metadata discovery** remains part of explicit public-PKCE
authorization; it is not client metadata registration. Resource, issuer, scopes,
TLS trust, loopback callback and actual endpoint review still apply. Authorization
never connects tools or grants their permission. Explicit reviewed refresh and
credential save/restore below remain independent operations, not automatic login
or recovery of an unsupported profile. Old records bound to removed authentication/
registration methods are not silently migrated or downgraded to public grants.


### Explicit in-memory OAuth refresh

Refresh is an operator action, never a background token retry. If the issuer
supplied a refresh token, open authorization status and press **r**, review the
resource, actual grant scopes, pinned endpoints, client ID and public method
`none`, then confirm with a fresh physical Enter. This sends one resource-bound
refresh POST, not a new browser login or client registration. Claw requires
`mcp_refresh:1`; unsupported gateways cannot submit it.

SDK hosts use `manage_auth("remote", "refresh_prepare")` followed by
`manage_auth("remote", "refresh", review_id)` under the same live consent lease.
Receipts are single-use and expire after five minutes. Refresh uses the previous
grant's scopes, not the broader configured ceiling. Valid replies may narrow,
never widen, scopes. A new refresh token replaces the prior one only after reply
validation; omission retains the old token only after a valid response.

The refresh capability is consumed before exchange. Cancellation, lost/invalid
responses or revoked ownership discard it rather than replay an uncertain token.
**c** cancels a pending refresh; leaving/disconnecting settles owned work and
fences late commits. A still-valid old access token may remain usable after
refresh failure; authorize again if needed. An existing saved snapshot is removed
before rotating refresh, so restart cannot replay stale credentials. A completed
replacement grant is saved only by another explicit reviewed save.

Refresh never replays MCP requests, connects tools, changes approvals or falls
back to another client/authentication/storage method. The two retained Linux
credential backends are documented separately; neither implies startup restore,
automatic refresh, remote revocation or universal issuer interoperability.


### Explicit Linux MCP credential storage

MCP credentials retain two explicitly selected Linux backends:
`McpManager(..., credential_dir=..., credential_backend="file")` (the default
backend) or `credential_backend="secret-service"`. Without a credential directory,
the manager remains memory-only. Neither backend silently substitutes for another;
unknown/unsupported/unavailable choices fail closed.

For a completed resource-bound grant, use `manage_auth(...,
"credentials_save_prepare")`, review the returned resource/profile/scopes/TLS/
backend disclosure, then confirm `"credentials_save"` with that exact `review_id`.
`credentials_restore_prepare`/`credentials_restore` and
`credentials_forget_prepare`/`credentials_forget` use separate owned receipts.
Restore checks exact resource, pre-registered public profile, trust-file content,
expiry and scope binding; it performs no discovery, token request, connection,
model call or tool approval. Nothing automatically saves or restores at startup.

**file** is private local **plaintext**, not encrypted/keychain storage. It requires
owned no-follow 0700 directories/0600 regular files, bounded records and atomic
fsynced writes. Unsafe links, ownership/mode/type violations or uncertain writes
refuse without an alternate storage path. File permissions are not an OS sandbox.

**secret-service** uses an already running Linux Secret Service and unlocked
default collection, existing session D-Bus address, `/usr/bin/python3` GI bindings
and libsecret typelib. The directory is a namespace, not a plaintext token path.
XDOG does not install/start/unlock a vault, create a collection or initiate login.
The helper uses an encrypted Secret Service session and owned timeout/cancellation
cleanup. At-rest protection is the vault's responsibility. Locked, missing, unsafe
or ambiguous storage refuses with **no plaintext fallback**.

Save/erase/restore are explicit reviewed actions. An admitted storage mutation may
have committed even when cancellation loses its receipt: inspect the selected
store rather than replay. Public diagnostics/history never contain token values.
Saved snapshots are invalidated before rotating refresh; a replacement grant is
not saved implicitly. Forget is local erasure, not remote revocation. Provider
OAuth uses its separate explicit Secret Service vault contract below.


### Explicit MCP completions

`await manager.complete(server, kind, name, argument, prefix, context)` returns a
frozen `McpCompletion` (publicly exported from `xdog.agent.mcp`). `kind` is prompt or
template; name is the catalog prompt name or exact URI template. Context contains
other already-edited named string arguments, excluding the current argument.
The server must explicitly advertise completions and the reference/variables must
still exist in its catalog; this call never connects a server or registers tools.

The request uses `completion/complete`, ref/prompt or ref/resource and optional
context.arguments on the existing current/legacy stdio/HTTP connection. Bounded
responses retain ranking, up to 100 strings, optional total and hasMore; invalid,
oversized or deferred results fail rather than silently truncate/coerce. Catalog
URI-template entries expose named-variable metadata to explicit SDK callers.
One active/one queued explicit intent per server and a cancellable 200 ms interval
bound traffic; canceled/disconnected intents settle without retry or successor
replay. The configured timeout bounds queue wait, catalog and POST together.
No prompt/resource retrieval, tool execution or URL following occurs.

This lower-level MCP completion API remains available to explicit SDK callers;
Coding and Claw no longer expose the manual named-argument completion picker or
negotiate its native UI transport. Native prompt arguments use an explicit JSON
object, and resource reads use an explicitly supplied resolved URI. Extension
command argument completion is separate and retained. Suggestions never grant
tool permission or authorization. Opaque resource completion support is described
below; these SDK APIs do not imply native completion controls.

### Opaque resource-reference completions

`manager.complete(server, "resource", uri, documented_argument, prefix, context)`
now supports ref/resource outside advertised named templates. The URI is opaque:
no catalog/schema is invented or fetched, no URI is followed, and no resource is
read. The explicit caller supplies the name/context; the server validates their
meaning. The current argument must not also appear in context. Existing byte/count,
capability, one-active/one-successor, overall timeout and cancellation/connection
fences remain authoritative. Prompt/template catalog validation is unchanged.

Both stdio and HTTP negotiation now admit a completion-only server when its
completions capability is an object, without inventing tools/resource/prompt support.
The frozen result and bounded codec include kind resource. The shared
`resource_completion_document()` formatter remains available to SDK callers as
untrusted inert data, explicitly NOT a resource read or resolved URI. Native opaque-
resource completion pickers and their Claw UI negotiation are removed; ordinary
explicit resource reads, previews and insert/export controls remain separate.

Opaque references are supported by the explicit SDK API, not by a native picker.
Negotiation, ownership, bounds and cancellation retain their SDK-level checks;
offline peer evidence does not establish all third-party interoperability.

### Validated inert MCP content and retained annotations

Tool results and resource/prompt previews share bounded validation for text, audio,
embedded resources, resource links, annotations and opaque `_meta` dictionaries.
Nonfinite/non-JSON metadata, malformed or excessive base64, unsafe/empty resource
URIs, conflicting text/blob payloads, invalid annotation audiences/priorities and
invalid timestamp forms fail explicitly. Metadata does not grant permission or
change a message's actual role. A rejected result is not retried or silently repaired.

Binary input and decoded payloads are bounded separately (4 MiB decoded per block),
with the existing 8 MiB transport/content ceiling and 1 MiB retained metadata ceiling.
Actual typed model image projection retains the existing PNG/JPEG/WebP admission
checks. Inert prompt image previews still retain other encoded formats for explicit
inspection/export; they do not claim provider support or silently draft binary data.

Text/image block annotations and vendor fields survive as indexed
`details["mcp_content_metadata"]` and a visible `[MCP content metadata]` text block.
This preserves metadata in the normal model/tool UI, source viewers and persisted
sessions without copying image bytes into details. Prompt/resource previews retain
inner/outer metadata in their existing text fields, including alongside binary
summaries; the Claw document codec remains compatible rather than inventing new fields.

Audio, embedded blobs and resource links remain inert retained data. The client
never follows the URI, opens a file, plays audio or automatically admits binary
preview data to a model. Explicit export/inspection and existing tool-policy gates
remain authoritative. Audio playback/native model audio support, further binary
artifact APIs and broader content/provider interoperability remain open.

The opt-in independent serving-SDK checks construct a content corpus using its
published schemas and exercise real stdio/HTTP mixed-content tools. They are not
claims of every codec, issuer or native-platform implementation being verified.

### Typed empty MCP binary previews and gateway compatibility

`McpBlock.content_type` preserves text, image, audio, embedded text/blob and link
identity in previews and private JSON exports. `is_binary` uses the declared type,
not only nonempty bytes. Empty audio/image/blob payloads remain binary, display
**0 bytes**, retain their annotations and cannot silently become draftable text.
Genuine empty text remains text. Existing nonempty legacy blocks still decode.

Claw negotiates `mcp_content_types:1` independently. Upgraded gateway/viewer pairs
transport explicit types through the existing checksummed, session-fenced chunks.
For older viewers the gateway retains the legacy shape for ordinary text/nonempty
binary data, but refuses a downgrade that would lose an empty binary's identity.
Capability changes during a read/transfer fence commitment without replay. An
upgraded client receiving untyped previews from an older gateway cannot infer their
payload type from MIME/metadata or insert them; preview/private export remain
available and the update requirement is explicit.

Typed private exports retain both an empty `data` string and its content type.
Destination entry now retains the submitted path after its nested editor clears,
with real focused cursor propagation, blur and edit/review transitions. Enter first
reviews the path; only a fresh visible review permits saving. No implicit overwrite,
upload, provider admission, playback or tool-permission change is introduced.
These checks do not establish audio codec/playback or complete content/platform parity.

### MCP JSON Schema and typed structured output

Current-protocol MCP results now retain **any finite JSON value** in structuredContent:
objects, arrays, strings, numbers, booleans and explicit null. Missing content is
tracked separately from null by `structured_content_present`; advertised output
schemas must be satisfied. Legacy negotiated profiles still require object results.
A successful text summary no longer hides canonical structured data: when no exact
canonical JSON text block is already present, a labelled structured-output block is
added to the tool content used by the model, full-source viewer and persistence.
Compact rows may still collapse it; **Ctrl+O** or the transcript viewer inspects it.

JSON Schema defaults to 2020-12; recognized declared draft-04/06/07/2019-09/2020-12
semantics are used rather than silently substituting another dialect. Local pointers,
anchors/dynamic references and bundled resource IDs resolve only inside the advertised
document. External documents are never fetched, including through an ID/base change.
Unknown dialects, unresolved references and unsupported required vocabularies fail
closed. Unreferenced examples/defaults remain data; explicitly referenced targets
are checked as schemas. Normal nullable unions, boolean property schemas and
conditionals are supported. MCP arguments remain objects; model parameters get an
object-root intersection while validation keeps all raw advertised constraints.

MCP uses an optional sync/async AgentTool argument validator before permission hooks
and validates effective arguments again immediately before the remote call. Coding
and Claw extension pre-hooks use the same check when they rewrite arguments. Existing
permission/read-only/allow_tools rules still decide execution; a schema/readOnly hint
never approves a call. Coding's parameter prompt formatter now handles boolean/union
property declarations rather than crashing on a valid schema.

The validation helper is local, owned and cancellable, started only for explicitly
connected tool schemas. It runs isolated Python imports (`-I`) with only the trusted installed/vendored
library root explicitly added, without a second
agent/model loop or schema URL retrieval. A **three-second** deadline includes queue
wait/validation; timeout/cancel kills and reaps that exact helper, including a child
still being created. A later explicit validation may start a fresh local helper;
remote MCP calls are never retried by it. Serialized schema caching is capped at
32 entries/4 MiB. Schema/value bytes, depth and node counts are bounded; these are
application limits, **not** OS-enforced filesystem/network/memory sandboxing.

Bounded JSON tool details now survive save/reload/branches in both clients, preserving
typed structured output and null-presence flags. Live/non-JSON handles stay transient.
Transcripts may contain sensitive structured values: keep them private. Actual current/
legacy peers, both app permission paths and Linux/private-tmux inspectors are verified;
provider-specific full-schema acceptance and mixed-dialect/native-platform
interoperability are not inferred. Format-assertion/custom required vocabularies,
ambiguous HTTP header mappings through schema composition, richer content/client
capabilities and external credential/provider interoperability gaps remain
open. Earlier object-only/current structured-result limits are superseded by this pass.

Schema packaging includes a direct `referencing` dependency and the portable SDK
bundle dependency list. The helper resolves its own trusted library root, so vendored
bundles work without accepting project/PYTHONPATH shadow modules. An actual vendored
helper test covers this; generated-output/vendored-package licence boundaries are
unchanged. Lock updates only declare the already-resolved dependency, not unrelated
version upgrades.

### Rich extension tool results

Trusted extension tools are no longer text-only. Existing string callbacks are
unchanged. A callback may return a typed `AgentToolResult`, a content/structuredContent/
details object, or use the validated builder:

```python
return api.result([
    {"type": "text", "text": "Result summary"},
    {"type": "image", "data": png_base64, "mimeType": "image/png"},
], structured=[False, 0, None], details={"count": 0, "enabled": False})
```

Text and PNG/JPEG/WebP images remain typed through the one existing agent loop,
model context, tool UI and transcript persistence. Structured JSON (including
explicit null) remains canonical inspectable content, not hidden behind a summary.
Extension metadata is separate: `extension_details_present` distinguishes omission
from explicit null and `extension_details` retains the exact bounded JSON value.
It is not converted to a model message or interpreted as host permissions. Typed
AgentToolResult content/details is accepted too; structured data uses the builder
or explicit structuredContent envelope.

Rich tool data is snapshotted and independently checked at worker and host boundaries.
Unknown content kinds/extra fields, invalid image base64/MIME, nonfinite/non-JSON
values, coercions and oversized frames fail rather than silently discard data.
Limits: 64 content blocks, four images, 2 MiB decoded per image, 1 MiB per rich text
block, 256 KiB each for structured data/details, and 3 MiB for the encoded rich
result envelope. These are explicit API/frame limits, not OS-enforced sandboxing.
No file/URI is implicitly opened and no image is automatically saved or attached
as a new user prompt. Raise an exception for tool errors; rich isError/audio/resource
payloads are not silently guessed in this API version. Commands remain bounded text.

Coding approvals, Claw extension_allow_tools, read-only and child inherited permission
restrictions still gate execution. Text-only post-hooks do not replace or drop a
result containing images. Legacy text callbacks/post-hooks retain their behavior.
Both UIs display tool images without printing base64. Claw now forwards final tool
image parts (previously missing from its display events), with a bounded live image
budget. Larger generic tool images are visibly marked as omitted from the live
preview; original data stays in canonical model/transcript history, not in an
oversized socket line. The new rich-extension envelope fits that transport budget.

Independent real workers, denied/allowed live app paths, typed persistence, inherited
child output, malformed/unsupported results, large-preview notices and both private-
tmux image/JSON inspectors are verified. These typed tool results do not grant
extension editor/session mutations, custom rendering or terminal interception.
Audio/resource/error-image interoperability, remaining MCP/credential and native-
platform gaps remain separate from the retained tool-result contract.

### Rich extension command artifacts

Extension command callbacks now accept the same typed text/image/structured JSON/
details result builder as tool callbacks. Legacy string commands remain strings.
The SDK `runtime.command()` returns either str or a frozen `ExtensionCommandResult`
with extension/command identity, a validated result snapshot and safe summary.
For example, a command can return:

```python
return api.result([
    {"type":"text","text":"Command report"},
    {"type":"image","data":png_base64,"mimeType":"image/png"},
], structured=[False, 0, None], details={"enabled":False})
```

Both interactive clients append a **read-only command artifact**, not a tool result
or agent message. Images render through the existing image component; base64 is not
printed as prose. **Ctrl+O**/the transcript viewer inspects the complete text, typed
JSON and details. Transcript copy/export can inspect its textual system-source
record. Original image bytes and typed metadata remain owned in the local artifact;
this does not pretend a text-only transcript export includes binary images.

Returning `/quit`, a URI or other command-looking text is data, never executed.
Rich artifacts cannot trigger a model prompt, mutate the main draft, approve tools
or change host read-only scope or permission modes. The prior draft/focus and normal approval
priority remain; callbacks still require trusted activation/idle ownership. Results
are fenced by command/session/connection identity; canceled/stale/foreign output is
not appended to a new owner and callbacks are not replayed. Artifacts are local UI
results, not newly persisted canonical agent-history entries or automatically saved
files. Legacy command/builtin skill prompt behavior is unchanged.

Claw independently negotiates `extension_command_results: 1` in hello/pong. Its
controlled response carries a validated artifact separately from the safe output
summary. Older viewers get a visible update-required summary without image bytes,
not a dict stringification containing base64; no callback is rerun to degrade it.
New clients reject unnegotiated/malformed artifacts rather than guessing semantics.
The rich envelope's existing byte/count/image/JSON limits apply, keeping control
responses below the receiving transport budget.

Independent real command workers, both production UI paths, permission/draft/history
non-mutation, system-source inspection, stale-owner rejection, older-viewer behavior,
cancellation and malformed returns are tested, plus both private-tmux image/JSON
inspectors and parent terminal restoration. Returning an artifact does not grant
editor/session/navigation mutations, custom rendering/layouts or raw terminal access;
those extension interfaces are removed. Read-only command inspection, provider
registration and explicit artifact export/reload have their own contracts below.

### Lossless command artifact export and read-only reload

`/export PATH artifact` captures the latest **owned** rich command artifact, including
original image bytes, text, structured JSON and typed details. It is private JSON
only (`json` may be explicit); implicit binary copy/share or lossy text/Markdown
artifact export is refused. Supplying a path does NOT bypass review. The existing
output panel shows image MIME/size/checksum and a no-redaction/sensitive-binary warning,
without rendering base64. Enter writes only the reviewed immutable snapshot.

Files use the existing private 0600, same-directory staging/fsync and no-overwrite
commit path. Cancel, hidden/tiny/resize review, owner/context changes and stale jobs
cannot authorize an unreviewed write. A commit already made is not remotely undone;
cancellation is not rollback. No upload, provider request, tool call or automatic
artifact save is introduced. Ordinary textual transcript exports retain their prior
semantics and are not falsely labelled binary exports.

`/artifact PATH` explicitly reads a bounded regular artifact file off the UI thread
and appends a read-only image/JSON command display. It does not activate extensions,
execute stored command names/text/URIs, change permissions or host read-only scope, or send input to
the model. The 4 MiB file envelope is versioned as xdog.extension-command/v1; its
SHA-256 covers canonical validated identity/content/metadata. Checksums detect
corruption, NOT authenticity: treat files as untrusted data. Unsupported versions,
tampered/malformed data, image/MIME/type failures and FIFO/nonregular inputs are
refused. Original image bytes and false/zero/null/omission remain exact on reload.

Artifacts are selected by session/connection/history epoch. Old-owner results cannot
be exported or appended to a new owner; loading snapshots publishes only under the
captured context. A read can be canceled by closing/changing owner, suppressing late
publication. An OS-blocked filesystem thread cannot be forcibly interrupted; these
checks/time/memory bounds are not OS-enforced sandboxing or filesystem confinement.
Loaded artifacts become owned display data and can be re-exported after a new review.

Both actual clients and private-tmux workflows verify binary save/reload, explicit
review, private mode/no overwrite, owner fencing and no-model/draft non-mutation.
SDK artifact_bytes/artifact_from_bytes/read_artifact expose the same validated codec.
Artifact export/reload does not restore removed extension custom rendering, host
mutations or terminal interception. Further MCP/credential and native-platform
interoperability remains separate work.

### Command session metadata

Read-only command metadata never opens or activates a session browser.

### Failed extension inventory and cancellation

Failed/disconnected/unloaded worker registrations withdraw from the current tool
inventory. Historical alias identities and stale-executor/argument guards remain;
withdrawal does not authorize an old approved call or unload a mandatory validator.
Projection settles on the owning Agent/command/child loop, including callback
errors and cancellation. Other healthy workers/native tools remain unchanged.
Child refreshes retain their explicit selected-tool subset. A post-hook failure
preserves the actual tool result instead of replacing it with invented success.

Failure catalog updates are descriptive, owner-scoped metadata: they cannot adopt
a different session, activate/reload a worker or approve tool execution. Old or
foreign cached contributions never become current merely because a callback failed.
Reload/unload remain explicit user actions; uncertain callbacks are not replayed.

### Registered command argument completion

Trusted Python commands may register an independent bounded data callback:

```python
async def complete(prefix, context):
    return [{"value": "release", "label": "Release", "description": "Argument data"}]
api.command("deploy", "Deploy command", execute_deploy, complete=complete)
```

`runtime.command_completions()` exposes current registration identities, and
`await session.complete_command(command, prefix, identity)` in BOTH client SDKs
queries the corresponding worker without executing its command callback or model.
The callback has no interactive UI/editor/navigation/session-mutation authority.
Items are plain single-line text (32 unique entries/64 KiB result); the prefix and
value/label/description bounds are validated on both sides. Failed/timed-out
uncertain callbacks settle/reap their workers and withdraw current declarations;
no retry/replay or fallback callback is implied.

Replacement/disposal changes the command completion identity. Stale references
and registration changes during callback execution yield no completion data;
an old disposer cannot remove a replacement. Both client SDK wrappers settle
their owning Agent's tool projection if that worker fails.

Both interactive parent editors now query registered argument callbacks in the
background. Suggestions replace only the cursor-local token (including its right
suffix); quoted whitespace stays inside that token, with quotes passed literally
rather than shell-expanded. **Tab or Enter inserts without submitting**. A subsequent
Enter executes the typed command only through its normal command/permission path.
Pending queries cannot turn Enter into an accidental command submission. Escape
dismisses the suggestions and ignores their late result; Tab reopens explicitly.

One query is in flight plus one coalesced latest draft. Original session/context,
command registration, full draft and cursor are revalidated before inserting.
Changed cursor/draft/registration/connection results never overwrite the current
editor. Claw separately negotiates `extension_argument_completion: 1`, runs the
query under the actual idle group/worker owner, and does not activate a cold session
or borrow the UI-command/model approval context. Previous gateways return unavailable.

Escape dismissal, loss of editor focus, hidden suggestions and application exit
now signal the owned completion task immediately. One cancellation is sent per
in-flight owner; its slot remains held until actual cleanup settles, so repeated
Escape cannot interrupt worker reaping or launch a successor early. Uncertain
callbacks are not replayed: their worker is reaped and current registrations are
withdrawn; explicit reload is required if the extension remains desired. Coding
settles its owned completion threads before retiring the extension manager at exit.
Claw separately negotiates `extension_argument_cancel: 1` and pins cancel messages
to the original request/connection/group/session. Older cancel-unsupported peers
retain the bounded deadline fallback and never receive guessed cancellation.

Argument menus now render each supplied `label` as presentation and show its actual
insertion `value` in brackets when they differ, alongside the description. Duplicate
labels are allowed but duplicate insertion values are not; selection and replacement
stay keyed to the value. Unicode/narrow layouts use the shared bounded renderer.
Changing only a label refreshes presentation without selecting a different value.
Unsafe/oversized label or description data rejects the entire result, not a partial
menu. Coding reconstructs owned row copies after immutable cross-thread delivery.

Completion callbacks may read `context["argument_context"]` when the current
caller supports full argument context. It is a copied dict with `arguments`
(the captured text after the command), `cursor`, `start` and `end` (Unicode
code-point offsets relative to that argument body). The token prefix is checked
against `arguments[start:cursor]`; this enables later-argument suggestions based
on preceding choices without reading the rest of a session or granting editor
mutation authority. Host selection still replaces its original cursor-local span.
Callback edits to this dict do not change the editor or its captured request.

Prefix-only SDK/legacy queries explicitly supply `None` for that context; they
cannot impersonate a full draft. New Claw editor queries independently negotiate
`extension_argument_context: 1`. Unnegotiated/invalid/foreign-scope metadata is
refused before callback admission. Withdrawing support cancels captured contextual
jobs (including before task startup), refuses late contextual output and does not
silently replay the query as prefix-only. A user may explicitly edit/Tab to request
new data under the remaining capability. Existing prefix-only callbacks remain
supported; `argument_input=` is optional on both SDK wrappers.

More expressive suffix semantics and broader argument API interoperability remain
open. Callback-supplied spaces/quotes are literal replacement data; the host
never silently adds shell quoting or interprets the resulting argument. Independent
child prompt completion is not claimed. Native file/slash-name completion is retained.

### Extension terminal command-menu inspection

```python
page = await api.get_command_catalog(query="", offset=0, revision="")
# Continue using page["next_offset"] and page["revision"].
```

A live native host may bind read-only menu inspection to an eligible owned command,
tool, lifecycle/pre/post-tool, submitted-input or context callback. It reads the
same sanitized terminal menu used by `/help` and completion, in 32-entry pages:
normalized name/description, source label, session identity, revision and explicit
pagination only. Host availability and exact invocation/session/child admission
remain authoritative; a detached/headless or unsupported callback gets `None`,
not another owner's menu. Independent child callbacks cannot borrow the parent's
menu/editor. Provider/setup callbacks receive no menu control authority.

Worker-supplied routing/host identity is refused. The host's trusted invocation
identity is separate from plugin request fields and carries no mutation grant.
Changed menu/session/registration invalidates a former revision and returns
`status="stale"`, no entries and no next page. Restart at offset zero explicitly;
there is no silent merge, command execution, model input or automatic replay.
Claw separately negotiates `extension_command_catalog:1` and its generation;
old session/inspection support does not substitute for that exact bridge.
Session/tree catalogs below retain their separately scoped read-only contract.


### Read-only extension session metadata

`await api.get_session_name()` returns the actual owning session's name, or
`None` when unavailable; an empty name is not a borrowed parent value.
`get_session`, `get_model`, `get_context_usage`, `get_active_tools` and
`get_tool_inventory` retain bounded, owner-projected read-only data. Session/tree
and command catalogs remain read-only; `has_pending_messages` is a boolean query,
not a queue control API. Callback admission, role and session/child provenance
remain checked; queries neither start a model nor grant permissions.

Extensions cannot read/replace the unsent editor, rename/navigate sessions,
select tools, request compaction or enqueue prompt/context/follow-up input through
host control APIs. Native USER `/name`, `/model`, `/compact`, `/sessions` and
`/tools` remain independent. Business tool/provider/command/hook registration and
basic input/confirm/select/status remain supported.

Claw's read-only session bridge uses `extension_session:1` and
`extension_session_inspection:1`; terminal command-menu inspection separately uses
`extension_command_catalog:1` and its generation. No editor/navigation/mutation
capability or separate extension name/active-tools flag is needed. Native
`session_name_read` and `tool_inventory_selection` controls remain independent.
Session/tree catalogs read the bound backend directly; listing them does not grant
plugin navigation authority.

### Read-only command model and context inspection

Interactive extension command callbacks can inspect the actual active owner:

```python
model = await api.get_model()
usage = await api.get_context_usage()
```

`get_model()` returns a bounded public snapshot: session ID, actual selected model
ID/name/provider, effective context/prompt ceilings, declared output-token limit,
reasoning/tool/input/effort/vision capabilities and current thinking level. Unknown
capabilities remain `None`, not fabricated support. Header credentials, base URLs,
endpoint/protocol internals and other provider-private configuration are excluded.
Stale metadata for another model is not used; results are fresh JSON snapshots,
not mutable provider/model objects. Zero vision constraints mean unreported limits,
consistent with the original model metadata, not a zero-image permission setting.

`get_context_usage()` reports the last matching assistant response's input, output,
cache-read and cache-write counters, their sum, the effective prompt ceiling and
percentage. It explicitly labels `source: "last-response"` and
`messages_since_usage`: later messages, tools/images, changed system prefixes and
future prompts are **not** presented as a measured current prompt. Missing usage
or only another model's measurements yields `source: "unavailable"` with
`tokens`, `percent`, `components` and age all `None`. No character/token estimate,
provider request, model activation or hidden-history/system-prompt disclosure is
introduced. The percentage can exceed 100 rather than silently clipping it.

Coding reads its captured idle command session. Claw resolves the read against the
original active runtime/broker under the existing reserved command group, not
cached UI footer/catalog data or a client-supplied measurement. Both methods preserve
session, draft, permission/read-only policy and native terminal focus.

Claw separately requires `extension_session_inspection:1` in addition to
`extension_session:1`; older session-capable viewers receive an unavailable result,
not unknown dialogs, borrowed authority or a silently fabricated snapshot. This
command bridge still uses its original negotiated ownership. The invocation
snapshot support below extends read-only inspection to other callback scopes;
these reads cannot mutate models, and provider authentication has a separate contract.

### Invocation-owned inspection in tools, hooks, children and headless runs

`get_model()` and `get_context_usage()` also work in tool, lifecycle, pre/post-tool,
context/input/provider callbacks and headless commands when their actual
runtime supplies an inspection producer. Coding and Claw bind their own Agent
sessions automatically. Standalone `ExtensionRuntime` users may explicitly pass
an `inspection=` callback producing an owner-validated envelope; a runtime without
one continues to report unavailable rather than inventing model/usage data.

These non-command getters read the **callback-entry snapshot**, not a live query
across arbitrary awaits. Every call returns a fresh validated copy. A new invocation
receives a new snapshot, so a later model/context change cannot silently rewrite the
old callback's observation. The worker receives only bounded public fields; hidden
prompts/history, credentials/endpoints and model/provider work are not introduced.
Caller-supplied context cannot override the protected inspection/session/child
identity. Inconsistent or foreign producer snapshots become unavailable.

This is independent of command authority: tools/hooks/children cannot borrow another
invocation's `get_session` or catalog authority. Editor/session/navigation mutations
are removed for all extension callbacks. Interactive parent commands retain the
fresh read-only host bridge and Claw inspection capability gate;
non-UI read-only callbacks do not send new dialogs to older viewers.

Child factories now bind the actual independent child Agent before initialization
and startup hooks, retaining parent validation and the same inherited permission
restrictions. Child snapshots use their own model/limits/options/history, never the
parent's inspection producer. Inherited entries retain their original provenance;
inherited parent response counters are excluded from the child's measurements until
the child actually receives its own response. Changing/closing the original parent
session invalidates the child producer rather than adopting another conversation.

The owned-worker tests cover headless tools/hooks/context/commands, original task
copies, model changes and spoofed caller identity. Both real clients exercise UI and
headless parent/child callbacks with independent model selection, explicit tool-policy
allow/deny and persisted snapshots. This closes the earlier command-only inspection
limitation, not every native/provider/auth/host API or native-platform requirement.

### Command session/tree catalogs

Interactive extension commands can now read actual host navigation metadata without
opening a dialog or activating context:

```python
first = await api.list_sessions(query="project")
if first is not None and first["has_more"]:
    second = await api.list_sessions(query="project", offset=first["next_offset"], revision=first["revision"])
tree = await api.get_session_tree()  # current host session
branch = await api.get_session_tree(target_id, branch_id=branch_id)
```

Pages contain scope, owner_session, resolved tree session_id/branch_id, query,
offset/total, at most 50 entries, has_more/next_offset and membership revision.
Entries are inert bounded id/title/detail/kind/session/branch/checkpoint/depth/active
metadata and safe checkpoint previews—not full hidden conversations or system
prompts. Text/type/ID/offset/count/frame validation rejects malformed responses
rather than coercing them. Page encoding is capped at 256 KiB, query at 1000 UTF-8
bytes and catalog traversal at one million entries.

Command session pages use stable identity order, not the native browser's recency
order, so the command's own durable entries cannot move sessions between pages.
Pass the returned revision with continuation calls to refuse a changed membership/
owner/query/target rather than mix catalogs. It is a membership/topology identity
revision, NOT a frozen full-history/metadata version; previews/active flags may be
fresh on each read. Restart at offset zero after a None refusal. Leaving revision
empty explicitly requests a fresh current page. Native browser behavior is unchanged.

Coding reads the existing session-manager/tree sources off-thread under captured
command/session context; Claw reads the owning GroupRuntime catalog, not a client
cache or launch-directory guess. Claw tree targets remain restricted to that group.
Host catalog operations preserve their actual filesystem owner until creation/read
settles on cancellation, then refuse late adoption. An OS-blocked thread cannot be
forcibly killed; these limits/ownership checks are not an OS sandbox.

Only actual interactive extension-worker command invocations receive catalog
authority. Tools/hooks/context/child/unattended callbacks return None; original worker
invocation and host extension/command/group/session ownership are independently
checked. Missing/foreign/unreadable targets or membership changes yield unavailable
without disabling the extension or replaying reads. Invalid plugin request/response
codecs still fail closed. No model turn, permission/read-only mutation or session activation is introduced.

Real workers, both live hosts' 61-entry multi-page catalogs, search/tree/branch
previews, revision ordering/changed membership, foreign-group refusal, cancellation
settlement and production terminal catalog commands are verified. These catalogs
are read-only: they do not expose the removed navigation/editor/session mutation,
custom-rendering or terminal-interception APIs. MCP/credential/native-platform
interoperability remains subject to its own limits.

### Runtime-registered extension shortcuts

Trusted extensions can register a shortcut and its actual command callback:

```python
async def inspect_model(args, context):
    model = await api.get_model()
    return model["id"] if model is not None else "Model metadata unavailable"
api.shortcut("inspect", "alt+k", "Inspect the current model", inspect_model)
```

Registration contributes the namespaced `/ext-EXTENSION-NAME` command plus a
canonical default key; pressing it uses the EXISTING interactive command owner,
not another tool loop or arbitrary input interception. Plugins must already be
explicitly trusted/activated; a key does not discover/load an extension. Callbacks
retain normal idle/child/tool-policy/session/connection checks and only the retained
read-only command host queries and basic dialogs. No model turn or tool approval
is implicitly created by a shortcut itself.

**`/keys`** lists effective extension keys and disabled conflicts. Built-in and USER
editor bindings always win. When different extensions claim one effective key,
ALL colliding shortcuts are disabled (manual commands remain available). Typing,
unmodified editor/dialog keys, Escape, Ctrl+C/D/L/Z and confirmation/recovery routes
cannot be captured. Release/repeat events cannot duplicate callback invocation.
Registration capacity is 32 per extension/64 across active registrations.

The existing `keybindings.json` can remap or unbind a registered shortcut using its
full lowercase command name, for example:

```json
{"ext-keyed-compose":"alt+x","ext-keyed-blocked":[]}
```

Multiple explicit keys may be provided as a list. Overrides still reject duplicate/
built-in collisions and reserved routes. Configuring an inactive command does NOT
activate a plugin; `/keys` reports it unavailable. Changes take effect when the
client loads its keymap, like existing prompt bindings. Terminal encodings can make
some Ctrl/Alt combinations indistinguishable from Enter/Tab; choose an unambiguous
key or a terminal with extended keyboard support. Alt+K/Alt+X are physically tested.

Dispatch is confined to the ready idle prompt: approvals, extension dialogs,
viewers, completion pickers, child/MCP panels, overlays, output reviews,
active commands/model work and closing/disconnected owners retain priority. Unload/
failure/reload updates registrations; stale keys cannot adopt a new callback. Coding
now refreshes actual registration snapshots after extension commands even if a plugin
publishes no status or notices. Claw separately advertises `extension_shortcuts: 1`;
missing support is both dispatch-disabled and reported in effective help.

Real workers, both UI command/editor paths, user remap/unbind/inactive overrides,
reserved/default/user/inter-extension conflicts, contextual suppression, repeat,
load/reload/unload and both private-tmux default/remapped key paths are verified.
Arbitrary terminal interception and custom renderers are removed, not pending
shortcut features. Provider registration and read-only host queries retain their
separate contracts; MCP/platform interoperability is not implied by a shortcut.

### Submitted-input extension hooks

Trusted extensions can register `api.on_input(callback)` for newly admitted human
input, before its response model call or canonical user-message commit:

```python
async def prepare(text, metadata, context):
    # metadata: prompt/steer/follow_up source, active channel and image metadata.
    if text.startswith("reject:"):
        return {"action":"reject","reason":"Explain why this input was refused"}
    return {"action":"transform","text":"Prepared input: " + text}
api.on_input(prepare)
```

None or `{ "action": "continue" }` leaves text unchanged; a string is shorthand
for transformed text. Explicit transform/reject objects have exact typed fields;
unknown/handled/image/command-action payloads are NOT guessed. Text is limited to
1 MiB UTF-8, rejection reasons to 2000 bytes and callbacks to 32 per extension.
Callbacks run in deterministic registration/activation order in their actual
owned worker. Missing/malformed/failed/timed-out hook decisions refuse admission
and quarantine the failed worker; known failed filters cannot silently pass raw
input until explicitly unloaded/reloaded. A normal explicit rejection keeps the
extension active. Callback side effects are not rollback and are not replayed.

The Agent's optional `input_transform` hook now covers initial input, identified
steering/follow-ups and plain explicit continuation before canonical admission.
Transforms preserve queued identity/recovery receipts: consumption happens only
when the transformed message is committed. Rejected selected identified input
remains recoverable with original text/images; plain rejected steering/follow-up
messages enter an explicit-recovery quarantine (`agent.drain_rejected_messages()`),
not a live queue that could replay them on the next prompt. Initial rejected
input belongs to its caller, not a fabricated assistant/provider response. Hook
cancellation is settled before idle/owner release. Preexisting Coding SDK input
hooks are composed rather than replaced.

The extension API sees text plus image MIME/encoded-size metadata, never original
base64. Its output cannot silently remove/replace images: transformed messages keep
original typed attachments/timestamps. All later model capability, tool argument,
approval, host read-only and child permission gates remain authoritative. A transformed
string resembling `/quit`/a URI stays model input data, not a reparsed host command.
Editor/session/navigation mutation authority is absent from both hook and command APIs.

Both interactive clients show the actual transformed user text/queued consumption,
with visible notices. Initial refusal restores original draft/attachments without
model input or an automatic replay. Coding uses the current worker-owned image
batch for recovery, not a potentially lagging UI active-image pointer. Claw applies
its existing per-connection/run input-ID normalization/fencing to transform/refusal
receipts too. Child agents use their own extension worker/input history, not parent
history. Claw scheduler/goal/system initial inputs are explicitly NOT human input
hooks; queued human steering/follow-up remains intercepted. Channel metadata is the
active turn channel, not a claim of a separate queued sender's identity.

Real workers, generic initial/steer/follow-up/continuation/abort receipts, both live
clients' typed attachments/canonical history/refusal/recovery, actual queued follow-up
images/consumption, child isolation/system exclusion and private-tmux transform/
refusal recovery are tested. Submitted-input transformation and the handled outcomes
below remain supported; neither exposes raw-key observation/interception, custom
rendering or host mutations. MCP/credential/native-platform interoperability remains
subject to its own limits.

Pre-turn compaction of PREVIOUS history retains its existing lifecycle; the input
hook is a pre-admission/user-response check, not a veto of independent maintenance
work. Failed reload of a previously registered input filter remains failed/required,
not an implicit unload: only successful reload or explicit unload changes that
policy. Coding print mode reports a refused input as a nonzero error, not a
fabricated successful model answer. The removed Coding frontend RPC has no error
or completion-receipt interface.

Terminal verification also caught a generic resize race during output review:
painting after dimensions changed during tick callbacks could skip the next loop's
cursor query. That paint is now deferred until the normal input loop re-anchors.
Existing visible-confirmation/query assertions were retained, not weakened.

### Explicit handled-input outcomes

Submitted-input hooks may now return an explicit handled decision with bounded
feedback, for example:

```python
if text.startswith("local-help:"):
    return {"action":"handled","feedback":"Extension handled this request; no model answer was generated"}
```

Feedback is required, nonempty and at most 64 KiB UTF-8; extra image/command/action
fields are rejected. Handled is NOT rejection and NOT canonical/model history
admission. It stops the remaining input-hook chain for that request and emits a
separate `InputHandledEvent` logical-consumption receipt with original source/ID/
attachments. Identified inputs are removed exactly once; they do not also emit
InputConsumedEvent or return for automatic replay. SDK callers can inspect/drain
handled receipts with `agent.drain_handled_inputs()`.

If all newly supplied input is handled, the host does not invoke the response model
with old history just to obtain an answer. Other genuinely queued unhandled requests
may still run normally, preserving ONE_AT_A_TIME steering boundaries. Explicit
`agent.prompt([])` retains its preexisting old-context continuation semantics: it
contains no submitted request to intercept. If one request is handled and a later
request rejects/fails, the handled receipt remains committed rather than falsely
rolled back; remaining original input stays recoverable. Trusted callback side
effects are not an OS sandbox or rollback transaction.

Original image bytes are never given to the hook, and handled decisions cannot
silently discard attachments. Both interactive clients retain them in the editable
composer without restoring/replaying the consumed text. Queue/run/connection ID
fences apply to the handled receipt too. Feedback is labelled system/extension data,
not fabricated assistant prose or tool approval. Permission, host read-only, tool and model
capability gates are unchanged; returned command-looking feedback is never executed.

Print mode outputs explicit feedback and returns success for handled-only input;
Coding JSON output emits a valid system/input_handled envelope (optional real
assistant output is separate), not a counterfeit model message. Claw sends a
content-free final signal after actual handled feedback, releasing run ownership without synthesizing a response. Other
non-TUI channels receive extension feedback as channel output, not model generation.

Child supervision retains labelled input feedback as separate bounded metadata
(64 entries/64 KiB), with zero model calls when the task is handled. It survives child
record reload and appears as SYSTEM feedback in source inspection, not invented
user/assistant history. Parent conversations remain unchanged. New shipped clients
are jointly verified; older-viewer handled-outcome interoperability remains unproven
rather than being inferred from JSON accepting extra event types.

Generic initial/queued/old-history/queue-mode receipts, both live initial and queued
image paths, child result/source metadata, text/JSON completion and production
terminal model-call counters are tested. Plugin terminal key/paste/release
interception and observation APIs are removed, as are custom renderers and extension
host mutations. Handled outcomes preserve the ordinary permission and ownership
boundaries; remaining MCP/credential/platform interoperability is separate.


### Session-owned extension provider terminal events

Trusted extensions may declare callback providers with
`api.register_provider(name, models, callback)`. Their session-local model aliases
are `ext-EXTENSION-PROVIDER/MODEL`; they do not modify the global provider registry.
The callback returns an async iterator of bounded JSON events (`text`, `thinking`,
`tool_call`, `usage`, `done`, or `error`). An explicitly selected alias never falls
back to another provider or silently replays a failed request.

A deliberate public failure or cancellation has its own terminal event:

```python
yield {
    "type": "error",
    "message": "Service temporarily unavailable",  # Public diagnostic, not an exception dump.
    "stop_reason": "error",                       # Or "aborted" for provider cancellation.
    "response_id": "optional-provider-response-id",
}
```

`message` must be nonempty and at most 8,192 UTF-8 bytes. It is intentionally
visible in the UI and persisted history: never put credentials or private
exception diagnostics here. Unexpected callback/validation/cleanup exceptions
instead produce the generic no-fallback failure. Terminal messages retain prior
text, reasoning, signatures and usage; proposed tools in an error/aborted message
are not executed. Coding and Claw keep partial text visible, distinguish provider
cancellation, and persist the terminal metadata. Child supervision reports a
provider-aborted child as canceled rather than completed or failed.

Async generators and custom async iterators with an optional asynchronous
`aclose()` are supported. Normal/typed-terminal and explicit close operations
invoke that iterator's cleanup once; the host settles owned input-transfer/stream
cleanup before publishing completion. Cleanup failure cannot report success.
Uncertain interrupted worker RPCs still quarantine/reap their owned worker; forced
termination cannot promise that extension cleanup code ran or undo its effects.
The existing agent tool loop and host permission hooks remain authoritative;
trusted extension workers and permission checks are not OS sandboxes.

### Explicit extension provider OAuth and retained vault storage

Trusted provider declarations can retain a private `ProviderOAuth` binding through
`api.register_provider(..., oauth=...)`. Login/refresh/key/model callbacks execute
inside the owned extension worker; credentials and callback objects are not public
catalog/UI data. Generic `ProviderAuth` bindings remain separate. These provider
callbacks are not MCP registration methods or an Elicitation capability.

Both native hosts expose `/extensions auth list` or `menu`, explicit `login ID`,
`refresh ID` and `logout ID`, plus `/login [EXTENSION_PROVIDER_ID]` as a thin
selection/login alias. Every operation requires its original idle session/provider
registration and a separate owned review; URL opening is a distinct reviewed user
gesture, never automatic. Sensitive replies go only to the original worker, not
model history or a replacement question. Cancellation/reload/disconnect retires
that owner without automatic callback replay. Login/refresh does not grant tools,
select a model or change permissions. Logout clears worker memory, not saved data.

Provider-defined OAuth callbacks are independent of MCP's public-PKCE profile.
Env/resolver credentials remain private and lazy. A grant that expires while an
async key callback is suspended cannot admit a business stream; failure does not
silently log in, refresh, replay a callback or choose another provider.

Provider credential persistence is retained through explicit
`/extensions auth save ID secret-service /ABSOLUTE/NAMESPACE`, with equivalent
`restore` and `forget` actions. This is **Linux Secret Service vault only**, with
an explicit backend/absolute namespace and provider source/profile binding. It
shares the owned helper's no-start/no-unlock, cancellation/receipt and no-replay
protections. No automatic save/restore/login occurs; missing/locked/unsafe vaults
refuse. There is **no plaintext file fallback** for provider credentials. MCP's
separately selected `file` or `secret-service` credential backend does not change
this provider-vault contract.


#### Live provider declarations

Provider declarations may also change during an owned command or lifecycle hook:

```python
remove = api.register_provider("local", models, callback)
api.register_provider("local", updated_models, updated_callback, replace=True)
remove()  # Safe: an old receipt cannot remove the replacement.
api.unregister_provider("local")  # Explicit removal; returns whether it existed.
```

Duplicate names remain errors unless `replace=True` is explicit. Changed catalogs
are sent only when their bounded revision changes, validated atomically by the
current worker owner, and reflected in both clients' `/model` controls without
restarting the session. Initial declarations and live changes share the same
limits: four providers per extension, 32 models per provider, 64 models per session.
Invalid snapshots cannot publish a partial catalog, even from a callback reply.
Provider-only updates do not enqueue unchanged terminal/UI snapshots.

Each stream pins its registration identity independently of unrelated provider
additions. Replacing/removing its declaration refuses further output and settles
that owned stream; it does not call the replacement or base provider automatically.
Current declarations constrain offered tools, reasoning options, output limits and
actual image context, even if a caller has stale model metadata. Clients refresh
selected-model limits after commands and turn-start lifecycle hooks, before prompt preparation
and compaction. Removing a selected alias preserves the user's explicit choice;
its next use fails rather than silently selecting another provider.

Declarations and Python callbacks belong to their worker/session. A saved model
choice is not a serialized callback or credential. An extension must redeclare
needed callbacks during setup/session startup, using its existing trusted settings
or durable entries as appropriate. Independent child workers do not borrow a
parent's mutable callback registry or automatically replay its commands.

#### Retained MCP interoperability scope

The independent serving-SDK fixture covers retained discovery, schemas, ordinary
tool/resource/prompt/completion paths and owned transport cleanup. Active watch
APIs and their subscription-specific checks are no longer current capabilities.
See `tests/fixtures/mcp_official_sdk/README.md` for the optional local fixture setup;
this is not a claim of general third-party conformance or completed release gates.

#### Inert resource links in prompt previews

MCP prompts may return `resource_link` blocks, not only text, binary data or embedded
resource contents. These are now retained as **link metadata, NOT fetched content**.
The complete JSON block—including annotations and vendor metadata—remains available
in preview/search, raw export and explicit editable-draft insertion. URI/name/string
bounds, finite JSON retention, size validation and the existing user/assistant-only
prompt-role policy remain enforced. No link dereference, file/URL read, tool grant,
model turn or automatic draft insertion is introduced.

Independent serving-SDK tests also cover explicit resource reads, prompt catalogs,
named prompt completion, URI-template completion, and prompt retrieval over stdio
and HTTP. Suggestions do not retrieve prompts/resources: choosing a suggestion
edits the argument form, and a separate gesture retrieves the preview. A further
explicit gesture inserts untrusted preview data into the composer, still unsent.

### Rich execution failures are results, not discarded content

Tools may return `AgentToolResult(content=..., details=..., is_error=True)` to
report a deliberate execution failure while retaining typed text/images/metadata.
The error flag must be a real boolean. The existing sequential/parallel tool loop
passes it to finalization hooks, tool-end events and persisted `ToolResultMessage`
data; final flags remain aligned if a trusted hook explicitly overrides status.
Transport exceptions, cancellations and invalid flags still produce error outcomes.

MCP `isError` results now use this path instead of raising an exception containing
only flattened text. Images and finite structured diagnostics remain inspectable
and available in the next context, full-source viewers and persisted history.
The advertised **success** output schema is not applied to a deliberate error
payload; content/encoding/size/JSON validation still applies. Callers invoking a
proxy tool directly must inspect `result.is_error`, not expect an exception for a
standards-valid `isError` response. Invalid responses/transport errors still raise.

Trusted extension tools can use `api.result(..., is_error=True)` or a typed
`AgentToolResult` with that flag. Both worker/host boundaries validate it. This is
an execution-result flag, not a successful command-artifact/navigation receipt;
command artifacts reject it rather than turning a failed callback into a successful
artifact.
Coding/Claw keep their existing denial/error post-hook protections and permission
policy: retaining diagnostics neither executes a denied call nor turns it into
success. No automatic retry, second tool loop, file/image read or model turn is
added. The normal agent may continue reasoning from the marked tool error.

#### Provider prompt, reasoning and vision metadata

Callback provider model declarations can include `max_prompt_tokens` (zero means
unreported), `supported_efforts` (unknown or an explicit bounded list), and
`vision_limits` (`max_prompt_images`, `max_prompt_image_size`,
`supported_media_types`). Prompt ceilings cannot exceed the total context window;
limits/MIME/effort metadata are validated before catalog admission. Model IDs may
include ordinary native routing punctuation such as dots, slashes and colons,
without changing their session-owned namespace or resolving them as URLs/files.

These declarations become actual frozen `Model` metadata—not display-only flags.
Coding uses the separate prompt ceiling for its context limit; Claw refreshes it
and checks compaction against the stricter prompt ceiling. Independent children
inherit actual limits and validate requested efforts through their existing model
resolver. Vision count/byte/MIME and unsupported reasoning requests are rejected
before callback invocation; a stale caller cannot bypass the currently declared
capabilities. Omitted metadata retains the original behavior.

When a provider advertises only specific efforts, choose an advertised level or
`off`; incompatible configured startup preferences fail with cleanup, not a hidden
fallback/downgrade. Coding's explicit preference setter likewise rejects unsupported
levels without altering live options or persisted settings. Callback/context model
budgets and existing host permission/tool restrictions remain unchanged.

`CatalogStreamFn` is an optional extension of the existing cached `ModelStreamFn`
contract. Its `models()` enumerates the same configured adapter's active cached
models—never another runtime, catalog sync, login or provider activation.
`stream_fn_from_provider()` implements this contract, and session-owned extension
adapters compose it with their independent declarations. Non-catalog adapters keep
working; they do not gain an ambient catalog or hidden fallback.

### Owned event/hook listener cleanup

`api.on(event, callback)`, `before_tool`, `after_tool`, `context` and `on_input`
return idempotent zero-argument disposers. Each disposer removes its own registration,
not another registration of the same callback or a later replacement. Existing
extensions may ignore the return value as before. Registrations require callables
and are bounded to 32 per event/hook kind.

Dispatch snapshots the listeners already admitted for that invocation. Self-removal
cannot accidentally skip the next callback, and listeners added during dispatch
start on the next invocation rather than unpredictably running in the current one.
No callbacks or disposers cross the worker protocol.

The worker reports a bounded monotonic hook declaration when registrations change.
The host adopts it only from the current worker owner; stale/duplicate metadata cannot
re-arm disposed listeners and conflicting same-revision data fails. New event/input/
context/tool hooks become live without worker restart; explicit disposal updates the
same caches. Removing an input hook is an owned action, not a failed-validator escape:
failed/unloaded/retired workers cannot clear another owner's required validation.
Tool permissions, session/child/UI leases and no-replay cancellation remain unchanged.

### Simple extension status

`api.status(key, text)` / `api.clear_status(key)` publish or remove bounded,
owner-scoped text status. Failed/unloaded/retired workers cannot change another
extension's contribution. Widget layout, custom frames and nested/overlay control
APIs are removed; normal progress and host permission focus remain authoritative.

### Live owned command and shortcut catalogs

`api.command(name, description, callback, replace=False)` now returns an idempotent
registration disposer. Duplicate names still fail by default, including ambiguous
case-only aliases. `replace=True` explicitly replaces the same name; an old disposer
cannot remove its replacement. Callable/name/description and 32-command limits are
checked before mutation.

`api.shortcut(...)` returns a disposer for its owned binding and command. Removing a
current command removes dangling bindings for that alias; removing an old shortcut
cannot delete a later command replacement. Existing host reserved-key/conflict/
remapping and prompt-only ownership rules remain unchanged. Callers ignoring return
values retain their existing setup behavior.

The actual worker reports bounded monotonic command/shortcut declarations when
registrations change. Current-owner catalogs update without reconnect/reload, and
both clients refresh autocomplete and shortcut resolution from the existing complete
snapshot. Stale/retired declarations cannot re-arm disposed aliases, conflicting
same-revision declarations fail, and host aggregate capacities remain enforced.
An already admitted callback keeps its original execution; disposal affects future
admission rather than silently replaying or switching that callback. Unknown removed
commands are refused without calling a now-missing worker command or disabling a
healthy extension. This is metadata management, not an automatic model turn or
permission/session/editor mutation.

### Live owned tool registration and stale-approval protection

`api.tool(name, description, parameters, callback, replace=False)` returns an
idempotent disposer. Replacement is explicit, validates the schema before changing
registration and allocates a fresh identity even when the alias/schema looks the
same. An old disposer cannot remove its replacement. Count/name/callback/schema
limits remain bounded; callbacks never cross the wire.

The worker reports monotonic tool identity declarations and the host rediscovers/
compiles the actual schemas on its existing MCP owner. SDK argument validators and
executors capture the registration/process identity they were built for. Old validators,
executors and outstanding approvals cannot adopt replacement callbacks or schemas.
The worker also checks that identity immediately before callback admission, returning
a definitive execution error rather than invoking/replaying the wrong callback.

Tool inventory is projected on the owning Agent loop after context hooks, and each
model request plus its execution batch shares that inventory snapshot. Tools created
or removed inside a callback are reflected in the next model request on the SAME
loop, including actual independent child agents; no second in-process tool loop or
worker-reader-thread Agent mutation is introduced. Parent/child permissions, read-only,
extension allowlists and normal Coding approval still apply to every new tool.

Generic MCP tools leave optional adapter provenance unset; trusted extension adapters
alone populate/use it. Historical extension aliases are bounded and retained for
cleanly removing old session inventories. Alias identity is not a sandbox or tool
permission, and metadata registration never calls a model or activates a provider.

Live inventory refresh preserves native model capability gates: locally registered
tools remain available to the owning session, but `supports_tool_calls=False` keeps
API definitions out of every refreshed request and execution batch. Adding tools in
a context hook does not grant a text-only model native execution; fabricated calls
are refused. Supported/unknown-capability models may receive newly registered tools
even when the turn began with an empty inventory. Provider replacement follows the
current cached capability, not the original catalog's support or an old send-tools flag.

### MCP browsing and manual refresh

Active resource/catalog/combined notification watches are removed from the SDK,
transports and native UI. There are no watch/unwatch methods, editable subscription
filters, watch jobs or background subscription listeners. Former **w** resource
watch and **Alt+T/P/U/W** catalog/combined controls are not supported.

Ordinary MCP tools, resource/prompt catalogs, explicit reads/completions and manual
refresh remain. Passive tool-catalog invalidation on supported legacy notices only
marks cached definitions dirty; it does not fetch a catalog/resource, register tools
or start a model turn. Configured Roots and exact request-state continuations,
response-scoped SSE, cancellation and bounded legacy GET recovery remain separate
transport features. They never authorize uncertain tool POST replay or automatic
reconnect. Normal MCP chat/tool traffic and private extension-worker JSON-RPC are
not removed by this simplification.


### Read-only invocation tool inventory

`await api.get_tool_inventory(query="", offset=0, revision="")` returns a paged
snapshot of the actual owning Agent's configured model-facing tool definitions.
This works in commands, tools, hooks, context/provider callbacks and headless
invocations where the host supplies an inventory. Parent and child runtimes project
independent owners; explicit child tool profiles are applied before startup hooks
and retained during final startup inventory projection.

Pages include `session_id`, `owner_id`, `model`, `api_enabled`, `source`, `revision`,
`status`, `query`, `offset`, `total`, `next_offset`, and `tools`. Each tool contains
only its public `name`, `description`, and finite JSON `parameters` schema. No
executor, argument-validation callback, tool context, provider credentials, approval
receipt or permission state is included. Schemas are model-facing data: authors
must not embed secrets in their public definitions. Reading them never resolves
schema references, executes tools, registers new tools or submits a model turn.

`api_enabled` reflects the Agent's capability gate (`supports_tool_calls is not
False`), not a claim that a provider definitely supports every schema or any tool
is approved. Text-only/explicitly unsupported models can still inspect configured
local definitions with this flag False. Permission/read-only/child access gates are
unchanged and apply separately on actual execution.

At most 32 definitions return per page. Continue with `next_offset` and the same
revision to read every definition; the complete captured inventory is bounded to
2,048 tools and 512 KiB of finite JSON. Empty inventories are valid. Unavailable,
foreign, excessive or unstable owner snapshots return None rather than claiming
an empty/approved tool set or failing the extension solely for optional inspection.
Invalid query/offset/revision input is rejected. Search is a case-insensitive literal
substring over name/description, not an executable expression.

Snapshots are captured at callback admission, copied for each read and do not
silently refresh during an awaited callback. A later invocation sees newly projected
Agent definitions. The metadata revision binds owner/model/capability/definitions;
a stale revision returns status `stale`, no rows and no continuation. It is NOT an
executor registration identity, approval or authorization token. Live extension/MCP
registration metadata remains distinct from the Agent inventory until the owning
loop projects it normally; inspection never mutates that loop or a global registry.

Host SDKs can supply `runtime.tool_inventory = lambda: capture_tools(agent,
session_id, owner_id)` using `xdog.agent.extensions.tool_inventory.capture`. The
runtime overwrites caller-supplied inspection fields and the worker validates the
actual owner before installing an invocation context. Existing UI/gateway messages
are unchanged: no new viewer capability or automatic UI/model action is inferred.

Unchanged tool-inventory metadata is now cached by content fingerprint in the owned
extension worker. Hosts negotiate the private `xdog/info` capability
`tool_inventory_cache:1`; older workers continue receiving inline snapshots. The
first acknowledged invocation transfers the full snapshot, then subsequent calls
carry a reference instead of repeating schemas. Parent/session/model/capability and
schema changes produce a different fingerprint. This optimizes IPC bytes, not the
permission policy or the actual configured tool inventory.

Both sides keep at most eight snapshot identities; the worker owns at most eight
512-KiB snapshots. Resolving a reference validates the owner and returns a copy,
so an already admitted callback retains its snapshot even after cache eviction.
Missing references are explicitly unavailable. The host clears that cache entry
and transfers metadata on a later invocation, never automatically replaying the
current command/tool/hook. Reload starts a new worker/cache; stale references cannot
resolve to a successor's snapshot.

Optional inventory metadata is omitted for an invocation when it would exceed the
existing 8-MiB extension-worker RPC frame budget. That invocation reports inventory
unavailable, not empty/approved, and a smaller later invocation can inspect it
normally. The generic transport still rejects genuinely oversized requests; no frame limit, tool argument,
registration proof or approval gate is relaxed. Caller-supplied inline/ref fields
remain overwritten by the host's actual projection.

The native `/tools` viewer also distinguishes valid inventory readiness from ordinary
source-index readiness. Failed/loading/withdrawn/disconnected inventories cannot be
exported under another scope or replaced with canonical conversation records.
Reconnect clears old view admission; after handshake, explicit refresh reads the
actual current owner. A valid zero-tool snapshot remains distinguishable from an
unavailable snapshot. This does not automatically replay tool calls or agent input.

### Owned compaction extension hooks

Trusted extensions in Coding and Claw can register two lifecycle callbacks:

```python
def setup(api):
    def before(context):
        prepared = context["compaction"]
        # None / {} continues with the selected session's normal summarizer.
        if not prepared["messages"]:
            return None
        # Or explicitly veto: {"cancel": True, "reason": "Preserve this context"}.
        # A custom summary replaces ONLY the summary request, not permissions.
        return {
            "summary": "A complete summary of the older conversation...",
            "first_kept_index": prepared["first_kept_index"],
            "details": {"format": "my-summary-v1"},
        }

    def committed(context):
        receipt = context["compaction"]
        api.append_entry("note", {"compacted_messages": receipt["before"]})

    dispose_before = api.on("session_before_compact", before)
    dispose_after = api.on("session_compact", committed)
```

The before callback receives a copied preparation containing the complete typed
`messages`, default `first_kept_index`, `original_count`, estimated `tokens_before`,
`context_limit`, `automatic`, optional user `instructions`, and prior committed
metadata in `previous`. It is not a clipped transcript preview: large preparations
use the existing owned/checksummed transfer (128 MiB maximum, not one 8 MiB frame).
Callbacks and extensions receive independent copies; changing the preparation
cannot edit canonical history or the copy used by another callback.

Returning `None`/`{}` continues; `{ "cancel": true, "reason": "..." }` vetoes without
replacing history. A custom result requires a nonempty summary up to 256 KiB;
optional details must be finite JSON up to 64 KiB. A supplied retention index must
start a complete user turn and retain the latest user turn and its tool exchanges.
Indices describe the original preparation, not the compacted conversation. Only
one custom provider may win: conflicting summaries, invalid results, failed hooks,
and unsupported worker operation versions fail closed, without a model fallback.
Existing registration disposers remain owned and idempotent.

`session_compact` is emitted only after disk **and** in-memory history commit. It
receives source, retention index, original count, copied details, automatic/manual
flag, and before/after counts. Source/details metadata survives session persistence
and branch restore. An admitted commit and its observer dispatch settle before
compaction ownership is released, including repeated caller cancellation.

These callbacks have `api.has_ui == False` (an availability hint, not authority)
and cannot borrow command editor, browser/navigation, or dialog authority. Trusted
extension code still has the process user's permissions; this is **not** an OS
sandbox. Claw retains its normal guarded durable-memory flush/archive stage even
when an extension supplies the summary. Automatic veto keeps existing context;
manual veto is reported explicitly. Cancellation during a silent before callback
retires its worker; reload or unload explicitly rather than replaying its effects.

Committed compaction metadata may additionally contain `recovery_branch_id`. In
Coding and Claw this identifies a durable, complete pre-compaction checkpoint in
the owning session tree. It is an opaque navigation identity, not a filesystem path
or permission grant. The before hook's `previous` metadata retains prior recovery
links; repeated compaction does not replace earlier checkpoints. Core history and
access/extension state recovery still uses normal host navigation and its idle,
ownership, and complete-tool-batch checks.

Hosts preparing an extension owner before session navigation may create an
`ExtensionEntries` owner with a buffered persistence sink, then call host-side
`bind_persistence(callback)` after successful admission. This transfers the existing
validated entry owner to durable live persistence without replaying setup callbacks.
It is not exposed as a worker permission or a navigation operation. The host still
owns admission, cancellation, current session/model identity, and external side-effect
limitations; explicitly trusted extension code is not sandboxed by this mechanism.

### Complete rendered-context image admission

The shared Agent loop now checks typed images in the **actual rendered Context**
after configured context hooks/message conversion and before every model adapter
request. Retained user images, tool-result images and explicitly hook-added images
all count against available model image limits. The validator never prunes images
or silently converts them to text to make a request succeed. Rejection retains
canonical messages/tool results and produces an explicit error.

Metadata comes only from the configured stream adapter's cached `resolve_model`
interface; registered providers use the same contract. Declared positive count,
byte and media limits and advertised image input are enforced. This does NOT apply
the four-new-attachment composer cap to a model advertising a larger whole-context
limit. Unspecified byte limits use the existing neutral 32 MiB payload safety bound,
not an invented vendor limit. Base64/typed MIME checks are not a complete raster
decoder or a guarantee of provider wire-format conformance.

Validation runs off the asynchronous loop. Recheck the same adapter's image profile
after validation: changed/disappeared/malformed metadata refuses the request,
without guessing successor limits or replay. A cooperative abort during validation
also prevents the adapter request after byte checks settle.

Opaque SDK streams (or transparent wrappers returning no metadata) remain
caller-owned: the validator does NOT borrow another/global provider's catalog or
claim that unknown limits passed. Such callers need to supply their own capability
contract/validation. Native/registered metadata-backed app paths are exercised;
unknown-provider/raster/native-platform interoperability remains open.

## Agent Client Protocol (ACP)

`xdog.agent.acp` is an opt-in, harness-neutral adapter/backend contract using the
official Python ACP SDK; it does not import coding, Eyes, TUI, claw or flow.
`xdog-agent acp` exposes standard stdio initialize/new/load/prompt/update/cancel
interoperation. Permission/tool policy stays in the injected backend. JSON/frame
validation rejects duplicate/nonfinite/reserved routing metadata; stdout is only
protocol. Progress is not final prompt completion.

The direct dependency is `agent-client-protocol>=0.12.1,<0.13` on Python 3.12–3.14
(the current SDK is excluded by marker on 3.15+). Normal non-ACP imports remain
independent of that SDK. Coding's session ownership and durable inbox extensions
are product policy, not a second generic Agent loop. Historical external-event
records retain only codec/projection compatibility, not a live event host.
`Agent.prompt` also accepts a list of independent Agent messages in one turn;
coding's bounded SessionController batches use that path without another tool
loop. Coding's automatic durable Inbox, batch IDs and message-cancellation policy
are harness policy, not forced into the generic ACP backend.
See [ACP session routing](../../docs/acp-session-routing.md).


### Basic extension interaction scope

Trusted extensions can use `api.input`, `api.confirm` and `api.select` with normal
cancellation, plus bounded notifications and simple text status. Arbitrary custom
renderers, typed plugin forms, nested dialogs, extension overlays/fullscreen frames
and widget layouts are removed. Native approval and recovery reviews remain host-
owned; they expose no plugin layout or editor/session-mutation authority. Core TUI
overlays, transcript fullscreen, Unicode, clipping, cursor and tool-approval behavior
remain.

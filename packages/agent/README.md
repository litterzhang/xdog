# xdog-agent

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
2025-11-25/2025-06-18/2025-03-26/2024-11-05 versions. No sampling, roots, elicitation,
interactive-input or autonomous client capability is advertised.

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
are described below; OAuth and further negotiated capabilities remain open in
`docs/agent-ui-gap-plan.md`.

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
OAuth and further negotiated client capabilities remain OPEN.


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


### Session-owned structured planning metadata

`xdog.agent.planning` provides frozen `PlanState`/`PlanStep` values and `PlanOwner`
for caller-owned persistence and its structured `update_plan` tool. It does not
install global policy, register a process-wide tool or create another agent loop.
The Coding/Claw hosts own mode controls and pre-tool restrictions. The model tool
only changes bounded steps/explanation under an expected revision; host mode is
not an accepted model argument. Competing updates cannot overwrite the same
revision, failed persistence retains the prior state, and async ownership settles
metadata writes before cancellation releases the caller. In-process metadata
controls are not a hostile-code boundary or OS sandbox.


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
system prompt, static cache prefix, planning mode or tool permissions. Coding also
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
parts survive chunked full-context transfers beyond the individual RPC frame limit.
Transfers are checksummed, order/identity fenced and spooled off-loop, with a 128 MiB
explicit context limit, 64 KiB blocks and an absolute whole-transfer deadline.

Entry capacity is 128 entries across a session, 16 KiB per entry and 512 KiB total.
Invalid/corrupt stored state fails instead of quietly resetting it. Persistence
failure keeps the previous snapshot. Cross-loop cancellation settles committed
metadata/file ownership before the host releases its lifecycle reservation; it
cannot roll back an already completed write. Context-hook failures disable the
extension and do not trigger another model call or automatic replay.

This closes the basic durable-entry/pre-model-context gap, not the whole extension
API row: custom renderers/dialogs, shortcuts/input interception, provider registration,
richer command context and binary tool results remain unfinished. Extension code
is still trusted code with the user's privileges, not an OS sandbox.


### Interactive extension dialogs

Active extension callbacks now support `await api.select(title, choices)`,
`await api.input(title, message="", default="")` and
`await api.confirm(title, message)`. Optional `timeout` is bounded to 0.1–600 s.
Select/input return `None` on cancel/no UI; confirm returns `False`. Cancel answers
this dialog only—it is not tool approval, permission revocation or automatic turn
submission. Trusted extension code decides what to do with the answer.

The worker suspends the callback and returns an owned pending-callback/dialog
receipt. The host displays a focused bounded form, then resumes that SAME callback
with the validated answer; it does not rerun a command/tool to obtain the answer.
Multiple sequential dialogs work. Without an interactive host, canceled values are
returned immediately rather than hanging unattended execution.

Forms sanitize control codes, scroll long details, preserve the main editor draft
and input cursor, and require a rendered visible question/action before Enter can
answer. Select/confirm pastes do not submit; tiny/resize/release/repeat/unpainted
selection input cannot confirm. Escape/Ctrl+C cancel the dialog. Tool approval and
output review retain higher focus priority. An affirmative extension confirmation
never grants the underlying agent tool permission or switches planning mode.

Dialog IDs are host-generated and scoped to their session/request/run/connection.
Timeout, abort, navigation, disconnect and shutdown settle/cancel pending futures;
late/foreign answers cannot resume a successor's callback. Claw command waits are
connection-owned background jobs so the socket can still process UI replies and
other controls while the group remains reserved. Context-hook dialogs still share
the context transfer's absolute deadline; the UI does not renew model/transport
budgets. Callback effects remain trusted code, not OS-enforced isolation.

Arbitrary custom renderers/overlays, richer forms, input/shortcut interception,
provider registration and further extension command/binary capabilities remain
unfinished; these dialogs do not declare full Pi API compatibility.


### Child-supervision runtime

`xdog.agent.supervision.ChildSupervisor` owns a separate asyncio loop and bounded
children, with start/inspect/steer/follow-up/wait/cancel/close operations and reserved
`agent_spawn`/`agent_control` tool adapters. Each factory supplies a fresh `Agent`
and MUST implement host permission/planning policies and safe context inheritance.
Coding/Claw now provide permission-aware factories and `/agents` commands on top.
The module alone does not grant access or provide a UI. No generic access string
is an approval or a sandbox.

Limits: three children, 16 retained records, 16 model requests, a 32,000 reported-token
stop threshold (not exact billing), 1–600-second execution deadline (default 120),
32 identified queued inputs and 512 KiB / 2,000-message retained transcripts. The
request wrapper clamps output limits; provider input accounting can overshoot on
one request. Children cannot borrow delegation or parent `update_plan` tools.
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
specialist presets are now wired too; arbitrary richer forms/renderers remain incomplete.


`ChildExtensions` captures the origin UI binding at admission, creates an independent
per-child `DialogBroker`, and fences replies by child/session/lease identity. Detach
returns canceled values to pending and subsequent questions, without replay/adoption.
Inherited parent validation still runs under `silent_extension_ui()`; it never
borrows a child's or parent's unrelated question reservation. Actual child callback
questions are UI answers, not host tool approvals or planning changes.


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
revocation. Tokens are neither logged nor persisted, locally expire within one hour,
and are never automatically refreshed/replayed. OAuth cannot mix Authorization
header credentials or remote plaintext resource URLs. HTTPS verification remains on;
optional `ca_file` is an explicit absolute trust-file path.

Current limits: resource/issuer well-known paths only, issuer-origin endpoints,
query-free URLs, explicit scopes/public-client metadata and uncompressed JSON. WWW-Authenticate/path/root/OAuth/OIDC discovery is implemented in pass 29;
Hosted public-client metadata and explicit legacy DCR are implemented in pass 30;
Pre-registered Basic/POST confidential methods are implemented in pass 31;
other methods and credential persistence/refresh remain open. Coding/Claw consent UI is wired in pass 28.
Primary documentation is now retrievable; full interoperability remains open. The broader
MCP parity goal stays active; these SDK primitives are not an OS sandbox.


UI hosts use `manage_auth(..., lease=..., alive=...)` and `detach_auth(lease)` to fence
consent and late credential commitment. A foreign lease cannot prepare/start/cancel/
forget another view's transaction. Metadata cancellations settle actual coroutine
ownership; terminal/client close may be awaited idempotently after synchronous close.
Host guards are not tool approvals. Completed safe status can include a consumed
review for local forget; waiting status exposes no reusable live URI/receipt. Broader
OAuth registration/discovery/persistence/refresh remains on the original audit.


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
leases/explicit connect remain separate. Registration and persistence/refresh are
still pending; successful offline discovery is not proof of every external provider.


### Client registration alternatives

OAuth profiles accept `registration` with an explicit strategy:

- `pre-registered` (default): existing public client_id behavior.
- `metadata`: client_id is the operator's hosted HTTPS JSON document URL (with a
  path), and callback_port is fixed/nonzero. The issuer must advertise metadata
  support; the document's client_id/name/public method/code types and exact
  `http://127.0.0.1:PORT/callback` registration are validated without linked-asset
  fetching, redirects or implicit document hosting. No hidden DCR fallback occurs.
- `dynamic`: omit client_id. The issuer must advertise a pinned-origin HTTPS
  registration endpoint. The full review explicitly describes creating a remote
  native public-client record. Only AFTER consent does one JSON POST register the
  exact allocated callback/code/public method/scopes; a validated issued ID is used
  for the normal resource-bound PKCE flow. No secret/management token is stored/sent.

Example dynamic profile (legacy/deprecated registration is explicitly selected):

```json
{
  "issuer": "https://issuer.example",
  "registration": "dynamic",
  "client_name": "My XDOG CLI",
  "scopes": ["tools:read"]
}
```

Cancel cannot undo a remote record. Unknown outcome/invalid response consumes the
receipt, closes the listener and never replays registration; fresh review is required.
Client metadata requires operator-hosted reachable data and the supported fixed
callback, not automatic hosting or general port-variance compatibility. Same Coding/
Claw consent, lease, status/local forget, explicit connection and tool-policy gates
apply. Claw negotiates mcp_registration capability. Credential/registration persistence,
refresh, confidential client methods and remote management remain pending; offline
peer tests are not a claim that every issuer supports these profiles.


### Pre-registered confidential token methods

An OAuth profile can explicitly choose `token_auth_method: client_secret_basic`,
`client_secret_post`, or `client_secret_jwt` and
`client_secret_env: MY_MCP_CLIENT_SECRET`. Default none
remains public. These methods require pre-registered client_id; metadata/DCR profiles
cannot silently adopt secrets. Literal client_secret/config-file credentials and
unimplemented methods fail rather than downgrade. Issuer method metadata must match.

The full consent shows method and environment variable NAME only. Secret values are
resolved solely from the configured source, checked before discovery/start and anew
at exchange; they are not stored on profiles/reviews/status/browser URLs or in files.
Basic correctly form-encodes both credential components before base64; POST uses its
form body, never both. Credential-bearing data is restricted to the exact reviewed
pinned HTTPS token endpoint—not resource/metadata/registration hosts. Errors remain
secret-safe. Missing/unsafe/removed sources fail closed, with no code/request replay.

JWT authentication uses HS256 only, with an environment secret of at least 32
bytes. The issuer must advertise both `client_secret_jwt` and HS256 signing support;
there is no algorithm/method fallback. Each code exchange or explicit refresh signs
a fresh assertion: client ID as issuer/subject, the exact reviewed token endpoint
as audience, a random JTI, and a 60-second lifetime. Neither the raw secret nor the
assertion is retained in public reviews/status or saved credential snapshots. The
consent panel shows the algorithm, lifetime, audience and source variable name.
Redirects and uncertain token responses never trigger replay. Private-key JWT, mTLS,
native encrypted credential stores and broader issuer interoperability remain open.

Existing PKCE/resource/scope, Coding approvals, Claw allow_tools/planning and view/
connection leases remain authoritative. Claw negotiates mcp_client_auth and verifies
reviewed method/source fields before confidential start. Authorization is not tool
approval or implicit connection. Explicit refresh and reviewed local plaintext
snapshots are documented below; private-key/mTLS methods and full service/platform
interoperability remain future work.

### Explicit in-memory OAuth refresh

Refresh is an operator action, never a background token retry. After an issuer
actually supplies a refresh token, open the server's authorization status and press
**r**. Review every page of the resource, actual granted scopes, pinned endpoints,
client ID and authentication method/source, then press **Enter**. This sends one
resource-bound refresh POST, not a new browser login or client registration. Claw
requires gateway capability `mcp_refresh: 1`; unsupported gateways cannot submit it.

The SDK equivalents are `manage_auth("remote", "refresh_prepare")`, followed by
`manage_auth("remote", "refresh", review_id)` under the same live consent lease.
Receipts are single-use and expire after five minutes. The request uses the actual
prior grant's scopes, not the broader original configuration. Valid replies may
narrow scopes but cannot widen them. Issued refresh tokens rotate; if a valid reply
omits rotation, the prior token is retained only after that reply is validated.

The old refresh capability is consumed before exchange. Cancellation, a lost
response, invalid response or revoked owner discards it: uncertain rotation must
not replay the old token. **c** cancels a pending refresh; closing/disconnecting the
view settles its task and fences late commits. A still-valid old access token can
remain usable on refresh failure; authorize again to restore refresh capability.
Failed refresh preparation without an existing lease does not reserve the server
against another view. Local forget erases both access and refresh credentials.

Refresh never replays MCP requests, automatically connects tools, changes approvals,
registers a client or widens permissions. Credentials remain memory-only: durable
credential/registration storage and reload, further client methods and remote
revocation/management remain open. Issuers may not issue refresh tokens (the dynamic
registration request still declares the code grant only). Offline peer/terminal
coverage is not evidence of live identity-provider compatibility. This section
supersedes earlier descriptions of explicit refresh as entirely unimplemented;
automatic refresh and persistence are still not supported.

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
URI-template entries expose named-variable metadata for the hosts' completion UI.
One active/one queued explicit intent per server and a cancellable 200 ms interval
bound traffic; canceled/disconnected intents settle without retry or successor
replay. The configured timeout bounds queue wait, catalog and POST together.
No prompt/resource retrieval, tool execution or URL following occurs.

Coding and Claw wire this API into explicit Ctrl+Space/Tab/Enter named-variable
forms; Claw negotiates mcp_completion and carries frozen results via its existing
bounded checksummed session-fenced data transport. Suggestions are inert input,
never tool permission or authorization. These changes supersede earlier notes that
completion is entirely unimplemented; subscriptions, further negotiated/schema
features, durable credentials, extensions and native-platform evidence remain open.
Opaque/plain-resource completion references outside advertised named templates
are still open; prompt/template support does not waive that remaining scope.

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
The frozen result and bounded IPC codec include kind resource. The shared
`resource_completion_document()` formatter produces only local operator-selected/
edited untrusted data, explicitly NOT a resource read/resolved URI; both hosts use
it for separately requested preview/insertion without remote retrieval/model input.
Claw negotiates mcp_resource_completion independently of earlier named completion.

Earlier opaque-completion exclusions are superseded, not used to shrink the goal.
Subscriptions, richer server/client/schema negotiation, credentials/persistence,
extension APIs and native-platform evidence remain open. Tests are independent
owned offline peers and production app/terminal paths, not live interoperability.

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
permission/planning/allow_tools rules still decide execution; a schema/readOnly hint
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
capabilities, subscriptions, credential persistence and extension/platform gaps remain
open. Earlier object-only/current structured-result limits are superseded by this pass.

Schema packaging includes a direct `referencing` dependency and the portable SDK
bundle dependency list. The helper resolves its own trusted library root, so vendored
bundles work without accepting project/PYTHONPATH shadow modules. An actual vendored
helper test covers this; generated-output/vendored-package licence boundaries are
unchanged. Lock updates only declare the already-resolved dependency, not unrelated
version upgrades.

### Reviewed typed extension forms

Trusted Python extensions can now use `await api.form(title, fields, message="...",
timeout=300)` in their owned command/tool callbacks. It returns a typed named
object, or **None** on cancel, timeout, headless/unattended execution or unsupported
viewer. Defaults are offered to a live user, never silently submitted headlessly.
Example:

```python
answer = await api.form("Configure task", [
    {"name": "name", "kind": "text", "required": True, "min_length": 1},
    {"name": "count", "kind": "integer", "default": 0, "minimum": 0, "maximum": 10},
    {"name": "enabled", "kind": "boolean", "default": False},
    {"name": "mode", "kind": "choice", "default": None,
     "choices": [{"label": "None", "value": None}, {"label": "Zero", "value": 0}]},
    {"name": "notes", "kind": "text", "multiline": True},
])
```

Fields support text, integer, finite number, boolean and labelled typed scalar
choices; required/default, descriptions, text length and numeric ranges are
validated independently by the host. Unknown constraints/field names, duplicate
names/typed choices and coercions fail. **False**, **0**, empty text, explicit null
choices and omitted optional fields remain distinct. Required means a field must
be present; use min_length to require nonempty text. Forms are flat/bounded (1–32
fields, 128 KiB declaration and 64 KiB answer), not arbitrary code/custom rendering
or nested JSON Schema UI. Integers are limited to the interoperable ±(2^53−1) range.

**Tab/Shift+Tab** commits/navigates fields. Text/numbers edit normally; **Space or
Left/Right** cycles boolean/choice values and unset. **Enter** adds a line in a
multiline field, otherwise opens full-form review; **Ctrl+Enter** reviews from any
field. **PgUp/PgDn** pages help/review. Every review page must paint before Enter
submits; **Ctrl+E** returns to editing and **Esc/Ctrl+C** cancels. Invalid drafts stay
editable, and resize/visibility changes invalidate prior review. Field help, limits,
requiredness and selected/omitted values remain inspectable in bounded rows.

Forms are extension input data, never host tool approval, planning/permission-mode
changes or automatic agent submissions. Coding approvals retain priority. Parent
and child callbacks use their existing session/connection/UI leases and independent
brokers; malformed/foreign answers cannot resolve a form. Cancel, disconnect and
unattended paths do not replay the callback or invent defaults.

Claw negotiates `extension_forms: 1` in the client handshake and server pong. Older
viewers receive cancellation rather than an unrenderable form; basic select/input/
confirm compatibility is unchanged. Both live parent/child paths and Linux/private-
tmux command forms are tested. Arbitrary renderers/nested layouts, interception,
provider registration, richer extension commands and native-platform validation
remain open; this pass does not waive them. No OS sandbox is added.

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

Coding approvals, Claw extension_allow_tools, planning and child inherited permission
restrictions still gate execution. Text-only post-hooks do not replace or drop a
result containing images. Legacy text callbacks/post-hooks retain their behavior.
Both UIs display tool images without printing base64. Claw now forwards final tool
image parts (previously missing from its display events), with a bounded live image
budget. Larger generic tool images are visibly marked as omitted from the live
preview; original data stays in canonical model/transcript history, not in an
oversized socket line. The new rich-extension envelope fits that transport budget.

Independent real workers, denied/allowed live app paths, typed persistence, inherited
child output, malformed/unsupported results, large-preview notices and both private-
tmux image/JSON inspectors are verified. Rich command contexts/returns, arbitrary
renderers, input interception/provider registration, audio/resource/error-image
interop, remaining MCP/credential and native-platform gaps stay open. This section
supersedes earlier text-only TOOL limits; it does not waive the remaining goal.

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
or change planning/permission modes. The prior draft/focus and normal approval
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
inspectors and parent terminal restoration. Rich command HOST ACTION CONTEXT (safe
session/navigation/editor callbacks), arbitrary rendering/layouts, interception,
provider registration, binary artifact export/persistence and remaining MCP/platform
gaps stay open. Returning an artifact does not waive those requirements.

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
execute stored command names/text/URIs, change permissions/planning or send input to
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
Remaining custom rendering/command host actions/interception/providers, further
MCP/credential and native-platform requirements remain open.

### Command-only editor host context

Trusted command callbacks can now use `snapshot = await api.get_editor()` and
`await api.set_editor(new_text, snapshot["revision"])`. The captured object contains
text, character cursor, image count and an opaque revision receipt. A replacement
returns bool; conflict/unavailable contexts return False/None without overwriting
the user's draft. Example:

```python
snapshot = await api.get_editor()
if snapshot is not None:
    applied = await api.set_editor(snapshot["text"] + "\nPrepared by command", snapshot["revision"])
```

This capability exists only during an actual interactive extension COMMAND, under
that extension's current command/session/connection owner. It is unavailable to
ordinary tool/lifecycle/context/pre-/post-hook callbacks, inherited child tools,
unattended/headless callers and unsupported viewers. A forged worker flag cannot
borrow another operation's editor context; the host checks the original RPC action
and current UI command/extension ownership independently.

Revision receipts are random, single-use and bound to the original text, cursor and
attachment identities. User edits/caret moves/attachment changes, new commands or
changed session/connection/history epoch invalidate replacement. Invalid/terminal-
control/oversized text is rejected rather than silently sanitized into a different
draft. Text replacement preserves typed attachments and normal undo behavior; it
never sends a model turn, changes permissions/planning or executes draft text/URIs.
Limit: 64 KiB UTF-8 editor snapshot/replacement and explicit bounded request timeout.

Coding's approval/output/overlay priority defers host editor service rather than
changing the draft behind another dialog. The existing owned pending callback and
validated UI-reply path carries editor requests/receipts; it is not a second agent
loop. Cancel/exit/owner changes settle or refuse requests, and callbacks are not
replayed. Claw independently negotiates `extension_editor: 1` in hello/pong; older
viewers cancel the request and leave command/editor behavior unchanged.

Both real command workers, UI CAS-success/conflict cases (including retained Coding
attachments), wrong owner/receipt/cursor/image changes, invalid control data,
headless/tool/older-viewer refusal and private-tmux typed draft transforms are tested.
Session/navigation/model host-action APIs, broader command context, custom rendering,
interception/provider registration and remaining MCP/native-platform goals stay open.
Editor access alone does not redefine or complete that larger scope.

### Command session metadata and native browser context

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

Interactive parent COMMAND callbacks may read all entries in the same current
terminal menu used by `/help` and completion, in 32-entry pages. Entries contain
only normalized command name and sanitized description, with a terminal-menu
source label, session identity, revision and explicit pagination. This is NOT
execution approval, activation, model input or a claim that every remote command
is available. Tools/headless/independent-child callbacks return `None` instead of
borrowing the parent editor's command context.

Each response is scoped to its invoking session/command/context. Caller-selected
session/branch/action fields are refused; copied pages cannot mutate the host.
Changed menu metadata or another callback invalidates an old revision and returns
`status="stale"`, no entries and no next page. Restart at offset zero explicitly;
there is no silent partial merge, command callback execution or automatic replay.
Claw separately negotiates `extension_command_catalog: 1`; previous session/tree/
model/name/inspection capability does not substitute for that viewer support.

### Extension session names

```python
name = await api.get_session_name()        # str (including "") or None if unavailable
queued = await api.set_session_name("Release review")
queued_clear = await api.set_session_name("")
```

Interactive parent COMMAND callbacks inspect the actual session owner; Claw reads
through its reserved gateway/broker, not the viewer's cached footer. Outside an
interactive command, reads use an invocation-owned, copied metadata snapshot when
available. Tools, hooks and headless callbacks cannot borrow rename authority.
Independent children and unadmitted/stale workers never borrow a parent name;
without separately supplied owning metadata they receive `None`, not an empty name.

Setting a name opens a focus-owned, paginated host review showing the exact current
owner, expected old name and proposed new name. `True` means **queued after callback
success**, not saved. Failed/canceled callbacks discard it. The existing native
metadata owner applies the change only if the name still matches the reviewed old
name, preserving history/model/tools/permissions and the current persistence owner.
Names are plain Unicode text with the native 256-byte/control bounds. The API uses
`""` to clear; `"-"` is a literal name (unlike the native `/name -` clear shortcut).

Claw independently negotiates `extension_session_name: 1`; older/withdrawn viewers
cannot substitute model-inspection or navigation support. Missing name data/optional
frame-budget exhaustion is unavailable, never a cached parent name or a replay.
This metadata feature does not add extension history mutation or model execution.

Interactive extension COMMAND callbacks can use `await api.get_session()` for a
bounded snapshot of session_id, workspace and group_id (Coding uses an empty group).
No hidden system prompt, full conversation or foreign-session data is included.
`await api.open_sessions(tree=False)` queues the native session browser; tree=True
queues the current session's branch/checkpoint tree. A True receipt means **queued**,
not that a session was activated or that the callback has finished.

The host opens that browser only AFTER the same command callback settles successfully
under the same session/connection/history owner. Failure, cancel, stale command IDs,
owner/epoch changes and unavailable viewers discard the deferred request. Existing
Coding/Claw session providers, group/session control protocol, idle exclusion,
branch/checkpoint validation and persistence remain authoritative. The operator
must separately select/activate/create a session through the ordinary browser;
opening it never switches session, launches a model turn or changes the draft.

These APIs use the existing owned/validated pending host-request path. They are
unavailable to tool/hooks/context/child callbacks, headless execution and older
viewers. Original RPC action and active extension/command ownership are checked at
the host, independently of a worker's capability claims. Claw negotiates
`extension_session: 1` in hello/pong separately from editor/form/rich-result controls.
Unsupported viewers cancel the request rather than render an unknown session dialog.

The parent draft/focus, typed attachments and permission/planning policy remain
unchanged; closing the browser restores the editor. Both real workers, successful/
failed live commands, existing navigation regressions, stale-owner checks, tool/
headless/older-viewer refusal and private-tmux browser entry/return are tested.
This is session inspection plus explicit native browsing, NOT direct session mutation
or a paginated extension catalog API. Those host-action/context gaps, model control,
custom renderers/interception/providers and remaining MCP/platform requirements stay
open and are not waived by this increment.

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
session, draft, permission/planning policy and native terminal focus.

Claw separately requires `extension_session_inspection:1` in addition to
`extension_session:1`; older session-capable viewers receive an unavailable result,
not unknown dialogs, borrowed authority or a silently fabricated snapshot. This
command bridge still uses its original negotiated ownership. The invocation
snapshot support below extends read-only inspection to other callback scopes;
model mutations/provider authentication and other host APIs remain separate work.

### Invocation-owned inspection in tools, hooks, children and headless runs

`get_model()` and `get_context_usage()` also work in tool, lifecycle, pre/post-tool,
context/input/custom/provider callbacks and headless commands when their actual
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

This is independent of command authority: tools/hooks/children still cannot borrow
`get_session`, native browsing/navigation or editor mutation. Interactive parent
commands retain the existing fresh host bridge and Claw inspection capability gate;
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

### Reviewed direct command navigation

Interactive command callbacks can now request:

```python
queued = await api.navigate_session("resume", session_id=target_id)
# Also: "new", "branch" (branch_id), "goto" (zero-based index), or "fork".
```

The API queues at most one reviewed intent per command. Its bool receipt means
**queued after review**, NOT activated/committed. New requires no target fields;
resume requires an explicit session; branch requires an explicit branch; goto
requires a zero-based checkpoint index. Empty session for branch/goto means the
captured current owner. Fork is limited to the current session/head, with optional
zero-based index. IDs/field combinations and checkpoint types/ranges are validated;
returned strings/paths cannot substitute a host command.

The UI defaults to denial. Right/Space selects approval, Enter opens whole-target
review, PgUp/PgDn reviews every page and another Enter confirms. Current owner and
the exact action/session/branch/index are displayed, with a queued-not-activated
warning. Paste/repeat Enter, hidden/tiny/changed-layout review and default denial do
not approve a mutation. Normal host approval retains priority. No tool/model/
permission change is authorized by this form.

Only AFTER the original callback completes successfully under the same command/
extension/session/connection/history owner does the host dispatch the existing
native navigation control. Failure, cancel or owner change discards the intent.
Existing idle/child gates, target/group ownership, safe checkpoint/tool-boundary
checks and persisted plan/extension state remain authoritative; invalid or vanished
targets fail without a substitute selection. Claw uses its group-owned session
protocol; Coding uses its normal serialized commands. Fork saves a checkpoint
branch; branch/goto activate context according to existing client semantics.

The main draft/attachments are not submitted or discarded, permission/planning
policy is not overridden, and no provider request is launched. Claw separately
negotiates `extension_navigation: 1`; viewers supporting only metadata/browser
requests cancel direct navigation before receiving unfamiliar review semantics.
Tool/hooks/context/child/headless callers cannot borrow command authority.

Real current-host dispatch for all five actions, denial/failed callbacks, missing
targets, stale owners, exact-review bounds and older-viewer behavior are covered,
plus private-tmux reviewed new-session synchronization in both clients. Direct
navigation is now implemented; paginated extension session/tree catalog APIs,
model/other host context, custom rendering/interception/providers and remaining
MCP/credential/native-platform requirements stay open. No subset waives them.

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

Only actual interactive command RPCs receive catalog authority. Tools/hooks/context/
child/unattended callbacks return None; original RPC and host extension/command/group/
session ownership are independently checked. Missing/foreign/unreadable targets or
membership changes yield unavailable without disabling the extension or replaying
reads. Invalid plugin request/response codecs still fail closed. No model turn,
permission/planning mutation or session activation is introduced.

Real workers, both live hosts' 61-entry multi-page catalogs, search/tree/branch
previews, revision ordering/changed membership, foreign-group refusal, cancellation
settlement and production terminal catalog commands are verified. This supersedes
catalog APIs being listed absent; other model/host-action APIs, custom rendering/
interception/providers and remaining MCP/credential/native-platform gaps stay open.

### Runtime-registered extension shortcuts

Trusted extensions can register a shortcut and its actual command callback:

```python
async def compose(args, context):
    snapshot = await api.get_editor()
    if snapshot is not None:
        await api.set_editor(snapshot["text"] + " prepared", snapshot["revision"])
    return "Draft prepared; not submitted"
api.shortcut("compose", "alt+k", "Prepare the command draft", compose)
```

Registration contributes the namespaced `/ext-EXTENSION-NAME` command plus a
canonical default key; pressing it uses the EXISTING interactive command owner,
not another tool loop or arbitrary input interception. Plugins must already be
explicitly trusted/activated; a key does not discover/load an extension. Callbacks
retain normal idle/child/tool-policy/session/connection checks and any command host
APIs. No model turn or tool approval is implicitly created by a shortcut itself.

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
viewers, completion pickers, child/MCP/plan panels, overlays, output reviews,
active commands/model work and closing/disconnected owners retain priority. Unload/
failure/reload updates registrations; stale keys cannot adopt a new callback. Coding
now refreshes actual registration snapshots after extension commands even if a plugin
has no widget/notices. Claw separately advertises `extension_shortcuts: 1`; missing
support is both dispatch-disabled and reported in effective help.

Real workers, both UI command/editor paths, user remap/unbind/inactive overrides,
reserved/default/user/inter-extension conflicts, contextual suppression, repeat,
load/reload/unload and both private-tmux default/remapped key paths are verified.
Arbitrary input interception and custom renderers remain separate OPEN requirements,
as do provider registration, other host APIs and remaining MCP/platform work.

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
approval, planning and child permission gates remain authoritative. A transformed
string resembling `/quit`/a URI stays model input data, not a reparsed host command.
Hook callbacks cannot borrow editor/session/navigation command authority.

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
refusal recovery are tested. Arbitrary raw-key interception, handled-input dispatch,
custom renderers/providers and remaining MCP/credential/native-platform gaps stay
open; submitted text transforms do not waive those distinct requirements.

Pre-turn compaction of PREVIOUS history retains its existing lifecycle; the input
hook is a pre-admission/user-response check, not a veto of independent maintenance
work. Failed reload of a previously registered input filter remains failed/required,
not an implicit unload: only successful reload or explicit unload changes that
policy. Print mode reports a refused input as a nonzero error; RPC emits an error,
not an empty successful message_end. No synthetic assistant answer is fabricated.

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
not fabricated assistant prose or tool approval. Permissions/planning/tool/model
capability gates are unchanged; returned command-looking feedback is never executed.

Print mode outputs explicit feedback and returns success for handled-only input;
JSON mode emits a valid system/input_handled envelope (optional real assistant output
is separate). RPC emits input_handled receipts and an explicit completion marker,
not a counterfeit model message. Claw sends a content-free final signal after actual
handled feedback, releasing run ownership without synthesizing a response. Other
non-TUI channels receive extension feedback as channel output, not model generation.

Child supervision retains labelled input feedback as separate bounded metadata
(64 entries/64 KiB), with zero model calls when the task is handled. It survives child
record reload and appears as SYSTEM feedback in source inspection, not invented
user/assistant history. Parent conversations remain unchanged. New shipped clients
are jointly verified; older-viewer handled-outcome interoperability remains unproven
rather than being inferred from JSON accepting extra event types.

Generic initial/queued/old-history/queue-mode receipts, both live initial and queued
image paths, child result/source metadata, text/JSON/RPC completion and production
terminal model-call counters are tested. Raw-key interception, arbitrary custom
renderers/providers/other host capabilities and remaining MCP/credential/platform
gaps stay open. Handled outcomes no longer stay listed absent, but do not waive them.


### Owned custom extension views

Trusted extensions can use `await api.custom(title, render, on_input, ...)` in both
interactive clients, including independently owned child workers. Headless and
ordinary-dialog-only hosts return `None`. A host advertises the separate custom
capability only when it has an owned `ExtensionRuntime.custom_handler`;
`DialogBroker.ask_custom` keeps the original callback/future authoritative.

```python
async def render(width, height):
    return ["Choose an option"[:width], "Enter selects; Escape cancels"[:width]][:height]

async def on_input(event):
    if event["kind"] == "key" and event["key"] == "enter":
        return {"done": True, "value": {"selected": False, "count": 0}}
    return None

result = await api.custom("Choose", render, on_input)
```

The Python callbacks stay in the trusted owned worker: `render(width, height)`
returns a list of single-line strings or `{"lines": [...], "cursor": [row, column]}`.
Input returns `None` or an explicit `{"done": True, "value": JSON}` proposal. Resize
never invokes input or completes a view. The original command/tool remains suspended
and resumes once through its validated original UI reply. Renderers cannot propose
completion, replay a command or create nested dialog ownership. Callback effects
are not rolled back, and answers do not authorize any tools or change permissions.

Background custom-view state can now call `api.request_render()` on the worker's
asyncio loop. It returns `False` outside an available, live owning custom invocation
(including headless/retired views), and otherwise coalesces a repaint request. Tasks
created by the invoking command/tool retain that view's owner; cancel/reap those tasks
in the invocation's `finally` block. Calling it from `render` itself is rejected to
avoid a self-triggering render loop. No key/paste event, tool call, or completion is
fabricated. Hosts retain one coalesced repaint alongside the existing bounded FIFO
and cap background repaint admission at 30 FPS. Hidden/zero-height/suspended views
retain a dirty flag and repaint when their original view becomes visible again.

Background changes immediately revoke both queued and in-flight completion proof.
The user must act against a newly painted frame; a repaint cannot return a custom
answer or authorize a tool. These updates use existing bounded custom-frame events
in Coding and Claw, including child workers; no new viewer input capability or
unadvertised generic MCP client capability is inferred. SDK hosts wiring custom UI
should wire both `runtime.custom_handler = broker.ask_custom` and
`runtime.custom_refresh_handler = broker.refresh_custom`. The private trusted worker
notification is not accepted by ordinary MCP transports.

Host-generated dialog IDs and a 32-request pending/in-flight FIFO remain authoritative.
Events include kind, viewport dimensions, the latest painted revision and a view
generation. Blur, hidden/zero row budgets, invalidation and geometry changes fence
old frames even at the same dimensions; completion checks both revision and epoch.
Keys have exact boolean modifiers, paste is at most 16,000 UTF-8 bytes, and
Escape/Ctrl+C cancel the view while redraw, suspend and exit remain host-owned.
Malformed key encodings and queue/owner refusals cannot fall back to raw terminal
input or automatically replay. Busy/refusal feedback stays visible.

Frames have at most 200 lines, 8 KiB per line and 128 KiB total. Both clients sanitize
lines: vetted SGR styles/colors are supported, but OSC links/clipboard, cursor/erase
commands, graphics, alternate-screen controls and private host cursor markers are
removed. Styles reset per line. Optional cursors must fit the viewport and are
placed by the host at whole-grapheme boundaries. Answers are finite JSON snapshots
bounded to 64 KiB; ordinary dialog answers may cancel but cannot submit custom data.
No worker or socket I/O runs inside the terminal component's render method.

Claw negotiates `extension_custom: 1` and binds requests/frames to the original
connection/group/session and child UI lease. Older viewers and explicit capability
withdrawal cancel unsupported custom questions rather than receiving unknown UI.
Disconnect cannot transfer an existing view to a new client. Dialogs, approvals,
output panels and overlays retain their ownership/focus precedence, and the physical
resize guard covers every forwarded key and atomic paste, not just Enter/Space.
Cancellation/deadline/error cleanup settles frame tasks before releasing the lease.

This is a bounded custom-view API, verified on the owned Linux terminal path.
Inline remains the default. `fullscreen=True` requests a temporary host-owned
alternate presentation, with approval/output/overlay preemption, suspend/resume
and native main-buffer restoration. Callbacks still cannot emit alternate-screen
commands themselves. The host re-homes alternate painters on resize without
clearing or modifying saved main-buffer rows.

`overlay={...}` requests a capturing, positioned **host overlay** in the native
main buffer, rather than replacing the whole view or rendering a pretend popup
inside the extension's own frame. It is mutually exclusive with `fullscreen=True`.
For example:

```python
result = await api.custom("Inspect", render, on_input,
                          overlay={"width": "75%", "max_height": 12,
                                   "anchor": "top-right", "margin": 1})
```

Placement accepts `width`, `min_width`, `max_height`, `anchor`, `offset_x`,
`offset_y`, `row`, `col`, and `margin`. Sizes/row/column positions accept bounded
integers or whole percentages from 0–100% (sizes must be positive); offsets may
be negative. Anchors are center, top/bottom-left/right/center, or left/right-center.
Margins are a nonnegative integer or a dictionary of top/right/bottom/left cells.
The host clamps placement to the actual terminal and supplies the allocated
viewport, not the underlying editor's dimensions. No callable visibility predicate
or plugin-supplied focus callback crosses this protocol. The separately negotiated
live-overlay mode below supports a boolean non-capturing preference.

The original owning panel retains its draft/state across nested questions, but
its overlay handle is released while hidden/preempted. Resuming creates fresh
viewport proof; cancellation/completion/disconnect releases only that handle,
never another host overlay. Closing restores the actual parent/host focus, not
a retired nested input. Screen-relative overlays re-anchor the visible owned tail
on resize without clearing the terminal or replaying native scrollback.

Claw requires **both** `extension_custom_rich:1` and the independently negotiated
`extension_custom_overlay:1`. Older rich-capable viewers still cannot accidentally
render an overlay inline; missing/withdrawn capability cancels the owned question.
The same connection/group/session/child leases and tool-permission gates apply.
Additional host
render APIs remain separate open requirements; this is not full rendering parity.

`message=` opens a sanitized, paginated introduction. F1 switches between host
metadata and the actual view; PgUp/PgDn/Home/End navigate details. These keys and
pasted text do not reach the custom input callback while metadata is displayed.
The view requires a fresh frame after each transition.

`key_events=("press", "repeat", "release")` explicitly enables enhanced delivery;
the default is only `("press",)`. The callback receives a normalized `event_type`
for keys, with exact ctrl/alt/shift booleans. Both host and worker enforce opt-in.
Reserved cancellation/redraw/suspend/exit controls never reach custom callbacks,
and their repeat/releases cannot accidentally repeat a host exit/cancellation.
Basic press-only requests retain the original v1 wire fields for older servers.
Claw negotiates `extension_custom_rich:1` for fullscreen, metadata and enhanced
input declarations; unsupported/withdrawn capabilities cancel the request, not
silently downgrade it to inline or omit key kinds.

Owned nested questions and custom views are supported from a custom input
callback. The parent view is suspended, then the SAME component/state is restored
with fresh viewport evidence after the child closes. An input callback may await
`api.input/select/confirm/form/custom`; rendering cannot open questions. Frame
callbacks use separate owned IDs and do not acquire command editor/session/
catalog/navigation authority. The host derives parent IDs from its live renderer
lease; plugin-supplied parent identifiers are never authority.

Nesting is bounded to four displayed levels and the existing eight-question broker
quota. Escape cancels the visible child, not the hidden parent. Parent cancellation,
deadline or disposal cascades to descendants and settles owned tasks. Aborting an
uncertain nested callback chain reaps/quarantines its worker rather than replaying
it. A recognized expired question's late answer never answers a subsequent question
under the same callback; retired IDs are bounded and dropped on callback completion.

Claw additionally negotiates `extension_custom_nested:1` and preserves original
connection/group/session/child UI leases. Unsupported/withdrawn nested UI is canceled
as a child question, never adopted as an unrelated root or given another lease.
Parent input/completion proof is invalidated across child suspension; update your
view state from the nested result, render it, then complete on a fresh parent input.
A completion proposal from the pre-question frame cannot silently commit afterward.

Richer custom containers, raw-key interception outside custom views, providers/other
host APIs and native Mac/desktop/Windows validation remain separate open requirements. Trusted extension execution is not OS isolation
or a tool-permission grant.

Example richer declaration:

```python
result = await api.custom("Choose", render, on_input, message="Read the introduction first",
                          fullscreen=True, key_events=("press", "repeat", "release"))
```

Render callbacks must honor zero height during host metadata/hidden presentation
and omit an out-of-viewport cursor. Enhanced event delivery is still data, never
a second model loop or a permission decision.


Example nested edit (state is retained; the initiating key is never replayed):

```python
state = {"name": "seed"}
async def on_input(event):
    if event["kind"] == "key" and event["key"] == "n":
        value = await api.input("Name", default=state["name"])
        if value is not None:
            state["name"] = value
    elif event["kind"] == "key" and event["key"] == "enter":
        return {"done": True, "value": dict(state)}
```

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
Invalid snapshots cannot publish a partial catalog, even from a custom-view reply.
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

#### Independent resource-watch interoperability checks

Optional tests in `tests/test_mcp_official_sdk.py` exercise the current subscription
wire against the independently published MCP TypeScript serving SDK 2.3.1 over
stdio and HTTP, rather than only XDOG-authored peers. Setup is documented in
`tests/fixtures/mcp_official_sdk/README.md`; no production dependency/download is
introduced. An absent SDK causes explicit skips, not a conformance success claim.

Watch acknowledgements may include omitted/default-false list-change filters;
those do not subscribe the host to new notification types. Unrequested true or
malformed filters remain errors. Schema-empty completion payloads use their RPC
identity and do not require resource contents. A completion before acknowledgement
refuses that watch without disconnecting a valid sibling or retrying the operation.
Bare completion compatibility is not proof that every malformed server conforms.
The verified serving-SDK path includes notices, exact unwatch ownership and
metadata-bearing graceful teardown; broader MCP/auth/content conformance remains
separate work.

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
command artifacts reject it so failed callbacks cannot release queued host actions.
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

### Reviewed typed multi-selection form fields

`api.form()` accepts `kind: "multi_choice"` with the same typed scalar `choices`
used by single-choice fields, plus optional `min_items`/`max_items`. Values are
arrays of distinct offered scalars: `False`, `0`, `None`, strings and floats are
not coerced into one another. Count bounds and defaults are validated on both
worker and host boundaries. Explicit `[]` differs from omitting an optional field;
a required field must be present and meet its declared count limits.

In the field editor, Up/Down selects an offered item and Space toggles it. Delete
sets an explicit empty array; Backspace unsets the field. UI output uses declaration
order, while programmatic valid answers retain their exact supplied order. Paste
cannot select choices or confirm. Tab changes fields and Enter opens the existing
whole-form review; every value/constraint still requires visible review before
submission. Blur revokes review proof and hidden rendering cannot restore it.
These answers remain extension data, never tool approval or model/session mutation.

Claw additionally negotiates `extension_multi_forms:1`; ordinary form support alone
is insufficient. Missing/zero/boolean or withdrawn capability cancels unsupported
parent/child forms rather than flattening arrays or rendering unknown field kinds.
The original command, connection/group/session, child and nested-dialog leases apply.
Real printable Space is matched as the named descriptor without changing editor key
data or modifier checks, fixing existing boolean/single-choice controls too.

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

### Explicit status/widget removal and UI owner fencing

`api.status(key, None)` / `api.clear_status(key)` and `api.widget(key, None)` /
`api.clear_widget(key)` explicitly remove the named contribution. Empty widget
lines remain an ordinary empty widget, not an implicit delete. Supplied line lists
are copied; later caller mutation cannot alter the pending contribution.

The host validates/prepares each whole UI batch before committing or publishing it.
An invalid later row cannot partially clear earlier UI, and clear operations remove
only that extension's matching kind/key. A status clear cannot delete a widget that
replaced its legacy shared slot. Repeating a clear is harmless and frees UI capacity
rather than accumulating invisible placeholder rows.

Setup contributions are buffered until the actual worker is validated/admitted.
Normal and nested/custom result paths verify current process ownership before UI
updates; retired, failed/unloaded or closed workers cannot rewrite a replacement's
snapshot. Host viewers receive the same complete snapshot format as before, so
removal does not require a new TUI socket capability or run a model turn. The same
session/child ownership, policy gates and native terminal focus remain authoritative.

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
worker-reader-thread Agent mutation is introduced. Parent/child permissions, planning,
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

### Explicit MCP catalog-change watches

`manager.watch_catalog(server, kind, lease=..., on_change=..., alive=...)` and
`unwatch_catalog` support `tools`, `prompts`, and `resources`. Current stdio/HTTP
transport opens an owned `subscriptions/listen` with only the selected opt-in
list-change flag; legacy peers must declare `listChanged` and use their existing
notification channel. Every acknowledgement/filter/identity is checked, unrelated
notifications are refused, and retired IDs cannot target a successor.

Both Coding and Claw expose these from the focused MCP server list:
**Alt+T** watches tools, **Alt+P** prompts, **Alt+U** resources/catalog entries;
press the same key to unwatch. Ordinary t/p/u browsing and w URI-resource watches
remain separate. The UI shows state/change count with a catalog label, not a
fabricated resource URI. Catalog/resource ownership keys cannot collide.

Watches are notices only: no automatic list/read/completion, tool registration,
connection/model input or permission change. Operators explicitly refresh or browse
through the normal controls. Original view/session/connection leases, close cleanup,
cancellation/timeouts and no uncertain request replay remain authoritative. Claw
separately negotiates `mcp_catalog_watch:1`; unsupported viewers cannot create these
watches, and capability withdrawal settles catalog jobs/watches without removing
unrelated resource watches.

Independent pinned serving-SDK tests exercise all three filters over both transports
and verify one explicit notification cause, no automatic reads/list calls and owned
cleanup. This verifies the singleton catalog-filter UI path; broader client capabilities,
external server/provider and native-platform interoperability remain separate checks.


### Combined MCP notification filters

`manager.watch_notifications(server, filters, lease=..., on_change=..., alive=...)`
opens **one** current-protocol subscription for an exact combination of catalog flags
and explicitly supplied resource URIs. `unwatch_notifications` stops that exact set;
`detach_notification_watches` stops only combined watches for the lease, while
`detach_resource_watches` retains its existing all-watch view cleanup semantics.

```python
filters = {
    "toolsListChanged": True,
    "promptsListChanged": True,
    "resourceSubscriptions": ["test://one", "test://two"],
}
await manager.watch_notifications("peer", filters, lease="view",
                                  on_change=show_notice, alive=view_is_alive)
```

Accepted catalog flags are `toolsListChanged`, `promptsListChanged`, and
`resourcesListChanged` with exact boolean values. URI arrays have at most 16 distinct
explicit entries, 8 KiB per URI and 64 KiB total. Unknown fields, invalid/empty sets
and duplicates are refused before subscription admission. False flags/empty arrays
are omitted; URI order is canonicalized so reordered operator input identifies the
same owned set. Filters and returned snapshots are copied. No wildcard expansion,
URI inference, resource read or tool registration is performed.

In both clients' focused MCP server list, **Alt+W** opens the editable JSON filter
form. **Enter** explicitly starts that exact set, or stops it when already active.
The draft remains editable after submission/errors. Different sets retain separate
owned identities; Escape returns to the server list and closing the MCP view stops
all its watches. Existing Alt+T/P/U catalog and w resource controls remain separate.
Combined snapshots carry `notifications`, not a fabricated resource URI/catalog.

ACKs must match the entire selected set, including exact flags, URI membership and
request order. Partial acknowledgement settles the watch as unsupported rather than
silently weakening it; extra flags/URIs, malformed types, duplicates, reordering and
unrelated notices are refused. Counters cover only requested change notices. Original
negative IDs, owning HTTP POST/SSE or stdio lifetime, cancellation and no-replay
semantics remain in force. Legacy combined requests are explicitly unsupported;
existing legacy singleton catalog/resource watches remain available without silently
emulating a combined stream or upgrading a session.

Claw negotiates `mcp_combined_watch:1` independently. Missing/withdrawn support refuses
combined requests and settles only combined jobs/watches, preserving unrelated
singleton resource/catalog watches. All operations retain the original connection,
group/session/view lease. Independent pinned serving-SDK 2.3.1 tests exercise three
catalog flags plus multiple URIs on one stream over both stdio and HTTP, including
unrequested URI suppression and owned unwatch; these are not full MCP conformance.


### Live and non-capturing custom overlays

Custom overlays can set `overlay={..., "non_capturing": True}` to render a live
background panel while the parent retains ordinary keyboard/paste editing and its
cursor. This automatically opts into live-overlay semantics; a capturing overlay
can opt in explicitly with `live_overlay=True`. Both clients and independent child
workers support the same owned path. No cursor marker is emitted by an unfocused
custom view, and visible background frames are independent of input capture.

From the owning invocation/input callback or its asyncio background task,
`api.request_overlay("hide" | "show" | "focus" | "unfocus")` queues a live
presentation request. **True means admitted, not a focus/visibility receipt.**
Unavailable, closed, quota-exhausted or non-live views return False; invalid actions
and calls from render callbacks are rejected. At most 32 controls queue per view
before host admission. Early requests wait for the first actual owned host frame
request, rather than disappearing before the host dialog exists. Cancel/reap
background tasks in the invocation's `finally` block.

Controls have monotonic sequence IDs and full host-owned hidden/capture snapshots.
Hide/show retains capture preference; focus requests showing/capturing; unfocus
releases capture without hiding. Requests affect only the current owned overlay,
not another view, command, worker or session. The host defers presentation beneath
approvals, output reviews, nested questions and other protected owners; a focus
request cannot preempt them or reorder unrelated dialogs. Cached parent controls
survive a nested question and restore the correct base editor afterward.

Every control invalidates queued/in-flight completion proof. Completion must use a
new painted viewport generation; hiding/unfocusing never proposes a custom answer.
Escape/Ctrl+C from the base editor cancels its owned background view without
canceling unrelated work or submitting the draft. Existing host suspend/exit and
terminal restoration behavior remain. Hidden/non-capturing presentation does not
finish the command, authorize tools, or bypass existing busy/submission policy.

Repaint/control task identities are weakly bound on first request. A surviving old
background task cannot adopt a sequential replacement view; nested callbacks use
the nearest actual custom owner rather than borrowing the root. Host SDK wiring
adds `runtime.custom_control_handler = broker.control_custom` alongside the
existing custom/refresh handlers. Claw independently negotiates
`extension_custom_live_overlay:1`; older viewers with only capturing-overlay support
cannot silently downgrade this mode, and disconnect/capability withdrawal cancels
its original owned future. Basic capturing overlays remain supported separately.

Both actual clients/child workers, nested restoration, approval/host-overlay
preemption, stale/reloaded identities, old viewers and Linux/private-tmux ordinary
keyboard editing/control/resize/recovery are tested. This does not establish native
Windows/macOS verification or parity with every extension/host rendering API.

### Read-only invocation tool inventory

`await api.get_tool_inventory(query="", offset=0, revision="")` returns a paged
snapshot of the actual owning Agent's configured model-facing tool definitions.
This works in commands, tools, hooks, context/custom/provider callbacks and headless
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
local definitions with this flag False. Permission/planning/child access gates are
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
existing 8-MiB RPC frame budget. That invocation reports inventory unavailable, not
empty/approved, and a smaller later invocation can inspect it normally. The generic
transport still rejects genuinely oversized requests; no frame limit, tool argument,
registration proof or approval gate is relaxed. Caller-supplied inline/ref fields
remain overwritten by the host's actual projection.

The native `/tools` viewer also distinguishes valid inventory readiness from ordinary
source-index readiness. Failed/loading/withdrawn/disconnected inventories cannot be
exported under another scope or replaced with canonical conversation records.
Reconnect clears old view admission; after handshake, explicit refresh reads the
actual current owner. A valid zero-tool snapshot remains distinguishable from an
unavailable snapshot. This does not automatically replay tool calls or agent input.

### Explicit pre-registered private-key JWT token authentication

MCP OAuth profiles can choose `token_auth_method: "private_key_jwt"` with an explicit
`client_private_key_env` containing an unencrypted RSA PEM. This is pre-registered
client authentication only—not dynamic registration, a provider login shortcut,
MCP tool approval or OS sandboxing. The issuer must advertise both private_key_jwt
and **RS256**; there is no method, algorithm or shared-secret fallback.

The current signer requires **POSIX, OpenSSL and /dev/stdin + /dev/fd**. RSA keys
must be 2,048–8,192 bits and PEM is bounded to 64 KiB. Unsupported platforms/missing
signers, encrypted/malformed/weak keys or absent sources fail before discovery/start.
The private PEM is passed on the owned helper's stdin, never argv, a key file, review,
transcript, credential record or public error. Helper environment is minimal, so the
key environment variable is not inherited. Only unsigned public assertion claims
use a private unnamed temporary descriptor; it is closed on success/failure/cancel.
Owned signer processes are timed out, killed/reaped and cannot silently continue
into a retired token operation.

Assertions use issuer/subject client ID, the exact pinned HTTPS token endpoint as
audience, a fresh random ID, issued-at and at most 60-second expiry. Code exchange
and explicitly reviewed refresh each create a new assertion. Only the token POST
receives it; existing origin/resource/TLS/cookie/redirect/no-replay and scope-ceiling
guards remain. A retired consent owner is rechecked after signing and cannot POST.
No plaintext shared secret is included alongside the assertion.

Both clients' existing whole-view OAuth review shows private_key_jwt, RS256, assertion
lifetime/audience and the key-source variable NAME. Internally the existing review
`client_secret_env` field carries this normalized authentication-source name (never
its value), preserving stored-review/profile binding. Configuration must use the
separate `client_private_key_env`, not `client_secret_env`, for this method.
Claw separately negotiates `mcp_private_key_jwt:1`; older viewers cannot silently
reinterpret it, and withdrawal revokes/detaches the private authentication view.

Actual both-client consent/exchange/refresh, real RSA signatures independently
verified by the offline peer, weak/invalid key rejection, method/algorithm negotiation,
retired-owner/owned-process cancellation and Linux/private-tmux paths are tested.
Windows signing/storage, native macOS validation, hardware/external key providers,
additional signing algorithms and broader OAuth/MCP interoperability remain open.
No extra Python dependency or live login is required for these offline checks.

Signer cleanup also retains ownership through repeated cancellation during process
startup or unsigned-claim descriptor creation. A later close request cannot interrupt
cleanup and orphan the original helper/handle; the original operation still returns
canceled and no callback/token POST is replayed. Reaping/draining occurs only for
that owned helper, never a user daemon or unrelated process.

Private-key signing capability withdrawal is also reflected in the existing consent
UI: the affected view clears its pending URL/receipts, stops polling and signing
controls, and cannot be revived by a late status/launch reply. Completed resource
credentials are not remotely revoked, and unrelated public-client consent remains
available. A new enabled capability requires freshly opened consent. Liveness is
rechecked after async key validation before a callback listener can be allocated.

Whole-page OAuth review proof is now tied to the actual focused MCP consent surface,
not merely background painting. Blur/preemption clears seen rows and confirmability;
returning focus requires a fresh complete endpoint/source/algorithm/scope review.
Both client hosts propagate their existing TUI focus to the nested OAuth component.
Painting a hidden/background view never counts as consent and does not grant tools.

Nested MCP argument-completion insertion also follows the actual parent view's focus.
Blur invalidates painted insertion proof without discarding valid inert suggestions
or rewriting the original argument draft. Blurred completion cannot request, edit,
capture paste or insert; after focus returns it must paint the current viewport anew.
Insertion still only edits a draft—the operator separately retrieves prompt/resource
data, with existing scope/draft/cancellation and no model/tool admission semantics.

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
planning/extension state recovery still uses normal host navigation and its idle,
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

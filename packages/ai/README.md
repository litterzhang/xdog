# xdog-ai

**Unified LLM provider API.**

A single interface over LLM providers — chat, embeddings, web search, and local
Anthropic Messages, OpenAI Responses, and OpenAI Chat Completions facades. Ships
the `xdog-ai` CLI for logging in, listing models, and talking to one from a terminal.

```bash
uv run xdog-ai login copilot
uv run xdog-ai chat copilot gpt-5.6-sol "Explain the CAP theorem in three sentences."
```

## Local API proxy

```bash
uv run xdog-ai proxy --port 8082
```

The proxy exposes these routes through the same provider runtime:

- `POST /v1/messages` — native Anthropic Messages when available, otherwise a
  best-effort projection.
- `POST /v1/messages/count_tokens` — native Anthropic token counting when available,
  otherwise a marked local best-effort estimate.
- `POST /v1/responses` — native OpenAI Responses when available, otherwise the
  strict stateless compatibility path documented below.
- `POST /v1/chat/completions` — native OpenAI Chat Completions only. A model
  without that exact generation capability receives an OpenAI HTTP 400 error
  with `param: "model"`; the proxy does not translate the request.
- `GET /v1/models` — the runtime's model catalog.

Use a `provider/model` ID for deterministic routing. A bare model ID is accepted
only when exactly one provider is active. For example, after logging into
Copilot:

```bash
curl http://127.0.0.1:8082/v1/responses \
  -H 'Content-Type: application/json' \
  -d '{"model":"copilot/gpt-5.6-sol","input":"Hello!","store":false,"stream":true}'
```

An OpenAI SDK client can use `base_url="http://127.0.0.1:8082/v1"` and call
`client.responses.create(...)` or `client.chat.completions.create(...)`. Set
`--api-key <key>` to authenticate the local listener through
`Authorization: Bearer <key>` or `x-api-key`; these values are never forwarded upstream.
Provider-resolved credentials and base URLs are authoritative. Without local
proxy authentication, an SDK may use any dummy API key.

Generation requests must carry `Content-Length`. The raw local server rejects
chunked request bodies with an HTTP 400 error before provider activity.

Native responses retain `content-type`, `request-id`/`x-request-id`,
`retry-after`, `openai-organization`, `openai-project`, `openai-version`,
`openai-processing-ms`, and `x-ratelimit-*`/`ratelimit-*` headers. Cookies,
credentials, and transport headers are not forwarded.

### Anthropic Messages compatibility

For Copilot models that advertise the native `anthropic-messages` protocol,
`POST /v1/messages` preserves the Anthropic JSON and SSE contract:

- All current request fields and nested content/tool unions are retained,
  including unknown future fields and opaque encrypted values. `max_tokens: 0`
  is preserved for prompt-cache warming.
- Both JSON responses and SSE events are forwarded in their native form.
  Unknown event and content-block types are retained, and an SSE `error` event
  is terminal rather than followed by a synthesized `message_stop`.
- `anthropic-version`, `anthropic-beta`, `anthropic-workspace-id`, and
  `anthropic-user-profile-id` are forwarded. Repeated/provider beta tokens are
  combined without duplicates. Safe request ID, retry, workspace/organization,
  and rate-limit response headers are returned to the client.
- Native non-streaming requests remain non-streaming upstream, so response-only
  fields, errors, status codes, and opaque blocks are not reconstructed from an
  internal event stream.

If the selected model exposes only an OpenAI protocol, the request uses an
explicit best-effort projection instead of rejecting the model. Common text,
base64 images, custom tools and tool results, sampling controls, tool choice,
structured JSON output, metadata, service tier, and reasoning controls are
mapped where the selected OpenAI endpoint has an equivalent. The response
includes `x-xdog-upstream-protocol: best-effort`; any supplied controls or nested
semantics that cannot be represented are listed as safe field paths in
`x-xdog-ignored-parameters`. For example, Responses has no stop-sequence
control, and Chat Completions does not receive Anthropic server-side web search.

The proxy validates protocol-independent request structure locally but leaves
model-, beta-, and feature-specific semantic validation to the native upstream.
Upstream Anthropic error envelopes and safe diagnostic headers are preserved.

### Count Message Tokens

`POST /v1/messages/count_tokens` is a non-streaming, non-generation endpoint. For
models with native Anthropic Messages support, the proxy forwards the complete stable,
beta, future, and opaque request body to `/v1/messages/count_tokens`, changing only the
wire model alias. Semantic Anthropic headers are retained; the obsolete
`token-counting-2024-11-01` beta is not injected. Native success bodies—including beta
`context_management` details—and upstream failures, statuses, and safe headers remain
intact. The operation never creates a message, executes tools, writes a prompt cache, or
retries through generation.

A known model without native Anthropic support receives a deterministic local estimate
with `x-xdog-upstream-protocol: best-effort`. This fallback is selected only before
authentication or upstream I/O; unknown models and every failure after native admission
remain errors. The estimate walks the raw request JSON, but it is not Anthropic's
model-specific tokenizer and may differ from message usage, especially for Anthropic
prompt framing, images/PDFs, signed or redacted thinking, server tools, and context
management. Native upstream validation still governs unsupported server tools, MCP
connectors, and URL or Files API media.

### Native OpenAI Responses

A model advertising `openai-responses` uses the native transport. The proxy
preserves current, deprecated, vendor-specific, unknown future fields, and
nested union members. A request with `stream: false` remains genuinely
non-streaming upstream, and native response bodies, error bodies, statuses, and
safe headers are retained.

Streaming keeps Responses' named SSE framing (`event: <type>` plus `data:
<json>`) and has no `[DONE]` marker. Unknown events are preserved. When the
provider uses a wire model alias, the proxy restores the client model only at
the top-level JSON response `model` and an SSE payload's `response.model`; it
does not rewrite unrelated nested `model` fields.

### Strict stateless Responses fallback

When a model does not advertise native `openai-responses` generation, the proxy
uses its strict compatibility adapter and adds `x-xdog-upstream-protocol:
best-effort`. The adapter rejects unsupported or unknown semantics with HTTP
400 rather than silently dropping them. Its JSON and SSE output is synthesized
from provider-neutral events, so the following capabilities and limits apply
only to this fallback:

- JSON responses and SSE lifecycle, text, function-argument, and reasoning-summary
  events; completion, token-limit, and failure terminal events.
- String input or full message/item history, `instructions`, system/developer
  messages, base64 image data URLs, function tools and results, parallel calls,
  `max_output_tokens`, `temperature`, and reasoning effort. Reasoning summaries
  and encrypted replay content depend on the upstream model/protocol. Encrypted
  reasoning retains its signed upstream identity across SSE and history replay.
  Reasoning events are buffered until block completion because Copilot may replace
  provisional IDs and ciphertext. Other output continues streaming afterward.
  Opaque, unprefixed upstream IDs are wrapped as `rs_xdog_v1_...` IDs for Codex.
  Short wrappers are reversible. If that wrapper would exceed the Responses API's
  64-character limit, replayable upstream IDs use a deterministic `rs_xdog_v2_...`
  alias and a canonical digest-bound envelope in `encrypted_content`; upstream IDs
  that themselves exceed the limit remain summary-only and are not replayed. Native
  Responses input also migrates canonical legacy v1 reasoning history: replayable
  IDs are restored, while over-limit reasoning items are omitted before forwarding.
- Codex Responses Lite `additional_tools` input items are normalized into the
  tool set, alongside top-level `tools`. Tool definitions use the same
  validation in either form; identical duplicates are deduplicated and
  conflicting definitions are rejected. These items are not chat messages.
- Namespace groups containing function or custom tools are supported. Distinct,
  deterministic internal names avoid cross-namespace collisions; JSON responses,
  SSE items, and history replay preserve each original `namespace` and tool `name`.
- Custom/freeform tools (including `apply_patch`) are adapted to functions with
  one string `input` argument and returned as `custom_tool_call` items. Custom
  input deltas are emitted after the complete JSON wrapper has been decoded, so
  escapes never leak into the raw input. Text and Lark/regex grammar formats are
  accepted; grammars are passed as model guidance, **not enforced by a parser**.
- Client-executed `tool_search` is adapted to a function call and returned as
  `tool_search_call`. Replayed `tool_search_output` items expose the discovered
  tool definitions to subsequent model calls. The proxy never executes searches.
- `parallel_tool_calls` accepts both `true` and `false`. The setting is forwarded
  to OpenAI Responses/Chat Completions upstreams and mapped to Anthropic
  `tool_choice.disable_parallel_tool_use` when using an Anthropic upstream.
- `text.verbosity` accepts `low`, `medium`, and `high`. It is forwarded as
  `text.verbosity` to OpenAI Responses upstreams and as `verbosity` to Chat
  Completions upstreams. Anthropic has no native equivalent and ignores this
  hint. When omitted, the upstream default is unchanged.
- Client-only `client_metadata` objects are accepted and ignored; they are not
  forwarded to models, echoed in responses, or persisted.
- `prompt_cache_key` is accepted and forwarded to upstream Responses APIs as a
  cache-routing hint. Other upstream protocols ignore it; caching is controlled
  by the provider, not guaranteed or stored locally by this proxy.
- **Stateless:** storage defaults to off. Send prior output items and tool results
  in `input` for subsequent turns. `store=true`, `previous_response_id`,
  conversations, item references, background jobs, and retrieval/deletion are
  not supported.
- Hosted tools, nested namespace groups, strict schemas, structured output,
  remote image URLs, files/audio, forced tool choice, `top_p`, and automatic truncation are not supported. Unsupported controls are rejected
  with HTTP 400 rather than silently changing generation behavior.
- Token usage includes cached input tokens in `input_tokens`; cached reads are
  also reported in `input_tokens_details`. The runtime does not separately
  track reasoning-token counts, so `reasoning_tokens` is reported as zero.

### Native OpenAI Chat Completions

`POST /v1/chat/completions` requires an exact native `openai-completions`
generation capability; embedding support through the same protocol adapter does
not imply Chat support. Unsupported models receive an OpenAI
`invalid_request_error` with HTTP 400 and `param: "model"`, without a fallback
request.

The proxy preserves current, deprecated, vendor-specific, unknown future
fields and chunks. Non-streaming requests remain non-streaming upstream. Chat
streams use data-only `data:` frames, never an `event:` field, and terminate
with the exact `data: [DONE]\n\n` frame. A provider wire-model alias is restored
only at the root `model` field in complete responses and stream chunks;
unrelated nested fields remain unchanged.

If a session created by an older proxy reports `Encrypted content item_id did
not match the target item id`, start a new conversation. Restarting the proxy
cannot repair a mismatched ID/ciphertext pair already stored in client history;
the encrypted payload is opaque and must not be reassigned to a generated ID.

### Optional Codex client integration tests

With Codex CLI 0.154 installed, run:

```bash
XDOG_TEST_CODEX_CLI=1 uv run pytest packages/ai/tests/test_proxy_codex_cli.py -q
```

These use an isolated read-only workspace and a loopback mock provider—no paid
upstream calls. They cover standard requests, Responses Lite namespaces with
custom tools, and a real namespace tool-call/result round trip using Codex's
read-only `collaboration.list_agents` tool, including encrypted reasoning ID
preservation during replay, including provisional and opaque Copilot-style IDs.
Hosted web search is disabled.

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

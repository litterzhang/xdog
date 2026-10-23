# xdog.ai SDK and protocol reference

Linux-only Python 3.12+ SDK. Stream chat, generate embeddings/images and perform
web searches through provider-neutral types where the selected model supports
them. See the [package README](../../../README.md) for installation, complete CLI
examples and proxy compatibility limits.

## Architecture

The request path has separate provider, vendor, routing and wire responsibilities:

```
Provider (user-facing, thin)
    |
    +-- Vendor (internal: auth, model sync)
    |       +-- resolve_auth(model) -> AuthResult
    |       +-- sync_models() -> tuple[Model, ...]
    |
    +-- Protocol (internal: wire format)
    |       +-- openai-completions    /v1/chat/completions
    |       +-- anthropic-messages    /v1/messages
    |       +-- openai-responses      /v1/responses
    |       +-- google-generative-ai Gemini generateContent / streamGenerateContent
    |
    +-- normalized path: Context + StreamOptions -> EventStream[AssistantMessage]
    |
    +-- native path: ProtocolRequest -> NativeResponse | NativeEventStream
```

- **Provider** -- user-facing: `stream`, `complete`, `embed`, `web_search`, `image_generation`, `login`, `models`, `sync_models`
- **Registered providers** -- `copilot` and `antigravity`, not Anthropic/OpenAI wire
  adapters or model brands. A provider owns its vendor credentials and dispatch.
- **Runtime** -- aggregates active providers, routing execution by `provider/model`;
  bare IDs require exactly one active provider. Catalogue `model()` lookup is not
  proof that a bare execution ID is unambiguous. No cross-provider auth fallback.
- **Vendor** -- internal: authentication (returns `AuthResult`), model sync (returns `Model` objects)
- **Protocol** -- internal: normalized wire adaptation plus optional protocol-native request hooks
- **ProtocolRequest** -- immutable canonical JSON bytes, an allowlisted header tuple, and operation metadata
- **NativeResponse / NativeEventStream** -- complete native JSON or endpoint-specific SSE without neutral reconstruction
- **AuthResult** -- frozen dataclass with `api_key`, `headers`, `base_url` (Model is never mutated for auth)

The native and normalized paths are intentionally separate. Agent consumers continue to
receive provider-neutral `AssistantMessageEvent` values. The proxy instead uses native
transport for models advertising the requested Anthropic Messages, OpenAI Responses, or
OpenAI Chat Completions generation protocol. Responses has a strict stateless normalized
fallback when that exact capability is absent; Chat Completions is native-only.

## Installation

```bash
# From the repository root:
uv sync
uv run xdog-ai --help
# Standalone installation:
pip install xdog-ai
```

The distribution registers `xdog-ai`; optional `xdog-ai[openai]` installs the OpenAI
SDK dependency, not an OpenAI provider. Model generation needs provider login and
network. Construction/cache inspection is local; explicit login or
`await provider.sync_models(force=True)` performs network IO.

## Quick Start

### Python API

```python
import asyncio
import xdog.ai as ai

async def main():
    copilot = ai.provider("copilot")
    ctx = ai.Context(messages=(ai.UserMessage(content="Hello!"),))
    opts = ai.StreamOptions(thinking="high")
    stream = copilot.stream("claude-sonnet-4.5", ctx, opts)
    async for event in stream:
        if event.type == "thinking_delta":
            print(f"[thinking] {event.delta}")
        elif event.type == "text_delta":
            print(event.delta, end="", flush=True)
    msg = await stream.result()
    print(msg.stop_reason)

    # Independent calls; the SDK does not store conversation history for you.
    msg = await copilot.complete("gpt-4o", ctx)
    print(msg.content)
    embedding = await copilot.embed(
        "text-embedding-3-small",
        ai.EmbeddingRequest(input="Hello world", dimensions=256),
    )
    print(len(embedding.data[0].embedding))
    searched = await copilot.web_search("goldeneye", "Python release documentation")
    print(searched.content)

asyncio.run(main())
```

Model IDs, reasoning levels and operations depend on the provider catalogue and
account; select suitable IDs with `xdog-ai models <provider> --details`. The calls
above illustrate separate APIs, not a guarantee that every listed model remains
available. Check `supports_web_search`/other tri-state metadata; unknown is not a
positive capability assertion. Tool definitions alone do not prove hosted search.

### Runtime (multi-provider)

Inside an async caller:

```python
import xdog.ai as ai

runtime = ai.load()  # Reads XDOG auth storage; does not log in or sync models.
ctx = ai.Context(messages=(ai.UserMessage(content="Hello!"),))
stream = runtime.stream("copilot/claude-sonnet-4.5", ctx)
async for event in stream:
    if event.type == "text_delta":
        print(event.delta, end="")
```

`provider()` constructs one registered provider independently of active auth
storage. `load()` creates a `Runtime` containing recognized providers with auth
entries; it does not prove tokens or upstream connectivity are currently valid.

### Native Anthropic Messages transport

`Runtime.request_complete()` and `Runtime.request_stream()` accept an immutable
`ProtocolRequest` for clients, such as the local proxy, that need wire fidelity rather
than provider-neutral events. For models advertising `anthropic-messages`, the native
path:

- preserves every current or future JSON field and nested union member, including
  `max_tokens: 0`, unknown discriminators, signatures, and opaque encrypted data;
- makes a genuinely non-streaming upstream call for non-streaming input and forwards
  native named SSE events for streaming input;
- replaces only the routing-owned wire model and operation-owned `stream` value, then
  restores the client-facing model in known response positions;
- forwards `anthropic-version`, `anthropic-beta`, `anthropic-workspace-id`, and
  `anthropic-user-profile-id`, with stable beta-token deduplication;
- returns safe request ID, retry, organization/workspace, and rate-limit headers; and
- excludes client `Authorization` and `x-api-key` values. Vendor-resolved credentials
  remain authoritative over client or cached model metadata.

The `/v1/messages` proxy selects this path whenever the model advertises it. Models
with only an OpenAI protocol remain usable through a best-effort neutral projection.
Equivalent sampling, stop, tool-choice, structured-output, metadata, service-tier,
parallel-tool, reasoning, custom-tool, and web-search controls are mapped where the
selected OpenAI endpoint supports them. The proxy marks that path with
`x-xdog-upstream-protocol: best-effort` and lists unrepresentable supplied semantics as
bounded, percent-encoded field paths in `x-xdog-ignored-parameters`.

Local validation covers protocol-independent outer structure. Native upstream errors,
status codes, safe headers, and error envelopes are otherwise retained. A native SSE
`error` event is terminal, including a payload whose `type` is `error` under a generic
event name.

### Count Message Tokens transport

`POST /v1/messages/count_tokens` is always ordinary JSON and never enters message
generation or streaming. Native Anthropic-capable models receive a lossless request at
the sibling count endpoint: stable, beta, future, opaque, signed, and encrypted prompt
fields are retained, only the routing model is replaced, and semantic Anthropic headers
are forwarded without injecting the historical `token-counting-2024-11-01` beta. Native
success bodies, beta `context_management` fields, status codes, error envelopes, request
IDs, retry metadata, and safe rate-limit headers are preserved.

Known models without native Anthropic support receive `{"input_tokens": n}` plus
`x-xdog-upstream-protocol: best-effort`. That deterministic raw-JSON estimate is selected
only before authentication or network I/O; an unknown model, authentication failure,
transport failure, or any upstream response remains an error. Counting never creates a
message, executes a tool, writes prompt-cache state, or retries through generation. The
fallback is not a model-specific tokenizer and can differ from eventual usage for prompt
framing, images/PDFs, signed or redacted thinking, server tools, and context management.
Native validation continues to enforce restrictions on unsupported server tools, MCP
connectors, and URL or Files API media.

### Native OpenAI transport

`POST /v1/responses` and `POST /v1/chat/completions` use the same immutable
`ProtocolRequest` path for models advertising the exact requested generation protocol.
Both retain current, deprecated, vendor-specific, unknown future fields, and nested union
members; `stream: false` makes a genuinely non-streaming upstream request. Local proxy
`Authorization` and `x-api-key` values are excluded, while vendor-resolved credentials
and base URLs remain authoritative.

Responses streams preserve named `event:` plus `data:` frames and do not use `[DONE]`.
Chat streams preserve data-only frames and terminate with exact `data: [DONE]\n\n`.
Responses restores a client-facing model alias only at the complete response's root
`model` and an SSE payload's `response.model`; Chat restores it only at root `model`.
Unrelated nested model values are not rewritten. Safe content type, request ID, retry,
OpenAI organization/project/version/processing, and rate-limit headers are retained.

When native Responses is unavailable, `/v1/responses` uses the strict stateless adapter,
marks output with `x-xdog-upstream-protocol: best-effort`, and rejects fields it cannot
represent instead of dropping them. `/v1/chat/completions` has no such fallback: a model
without native Chat generation receives an OpenAI HTTP 400 error with `param: "model"`.

### Vision

Within an async caller (local image read shown for simplicity; async hosts should
prepare file IO off-loop):

```python
import xdog.ai as ai
import base64

image_data = base64.b64encode(open("photo.jpg", "rb").read()).decode()
msg = ai.UserMessage(content=(
    ai.TextContent(text="What's in this image?"),
    ai.ImageContent(data=image_data, mime_type="image/jpeg"),
))
ctx = ai.Context(messages=(msg,))

async for event in ai.provider("copilot").stream("gpt-4o", ctx):
    if event.type == "text_delta":
        print(event.delta, end="")
```

### Tool Calling

Within an async caller:

```python
import xdog.ai as ai

weather_tool = ai.Tool(
    name="get_weather",
    description="Get the current weather for a location",
    parameters={
        "type": "object",
        "properties": {"location": {"type": "string"}},
        "required": ["location"],
    },
)
ctx = ai.Context(
    messages=(ai.UserMessage(content="What's the weather in Tokyo?"),),
    tools=(weather_tool,),
)

async for event in ai.provider("copilot").stream("gpt-4o", ctx):
    if event.type == "tool_call_done":
        print(f"Tool: {event.name}({event.arguments})")
```

Tool events describe requests; this SDK does not execute them. The caller must
validate/authorize execution, append paired `ToolResultMessage` history and make
the next request, or use `xdog-agent` for that loop. Signed/opaque thinking content
must retain its original identity during history replay.

### Image generation

Inside an async caller:

```python
import xdog.ai as ai

response = await ai.provider("antigravity").image_generation(
    "gemini-3.1-flash-image",
    ai.ImageGenerationRequest(prompt="A small robot reading a book", aspect_ratio="1:1"),
)
for image in response.images:
    print(image.mime_type, len(image.data))  # Bytes; the SDK writes no files.
```

Unsupported implementations raise `NotImplementedError`, not a text-chat fallback.
The CLI `image`/`image_generation` writes uniquely named files; see the package
README for reference-image/size limits and model capability handling.

## CLI

```bash
# Login (GitHub Copilot OAuth device flow)
xdog-ai login copilot

# List active providers
xdog-ai providers

# List models (from cache)
xdog-ai models copilot

# Sync models from provider API
xdog-ai models copilot --sync

# Chat (one-shot)
xdog-ai chat copilot claude-sonnet-4.5 "Explain quicksort"

# Chat (interactive)
xdog-ai chat copilot claude-sonnet-4.5

# Chat with options
xdog-ai chat copilot claude-sonnet-4.6 "Prove sqrt(2) is irrational" --thinking high --verbose
xdog-ai chat copilot gpt-4o "Describe this" -i photo.jpg
echo "Summarize this" | xdog-ai chat copilot gpt-4o

# Embeddings
xdog-ai embed copilot text-embedding-3-small "Hello world"
xdog-ai embed copilot text-embedding-3-small "Hello" -d 256 --json
echo "text" | xdog-ai embed copilot text-embedding-3-small

# Web search
xdog-ai search copilot goldeneye "latest Python release"

# Local Messages, Responses, and Chat Completions proxy
xdog-ai proxy --host 127.0.0.1 --port 8082
xdog-ai proxy --host 127.0.0.1 --port 8082 --api-key local-secret
```

The proxy's generation requests require `Content-Length`; its raw HTTP server rejects
chunked request bodies before provider activity.

## Module Reference

```
src/xdog/ai/
  __init__.py              Public API: provider(), load(), login() + type re-exports
  api.py                   Public API functions
  core.py                  ABCs: BaseProvider, BaseProtocol, BaseVendor, AuthResult
  types.py                 Frozen dataclasses (Model, Context, Message, StreamOptions, events)
  native.py                Immutable native requests, responses, SSE events, and HTTP errors
  paths.py                 Fixed home-relative config/data paths
  cli.py                   xdog-ai CLI entry point
  providers/
    __init__.py            Provider factory
    copilot.py             CopilotProvider (dispatches by model protocol)
    antigravity.py         AntigravityProvider (Gemini chat/search/images)
    runtime.py             Runtime (aggregates multiple providers)
    testing.py             TestProvider + TestProtocol for unit tests
  protocols/
    openai_completions.py  Chat Completions normalization, embeddings, and native delegation
    anthropic_messages.py  Anthropic Messages normalization + native delegation
    anthropic_native.py    Lossless Anthropic JSON and SSE transport
    openai_responses.py    Responses normalization, web search, and native delegation
    openai_native.py       Lossless Responses and Chat JSON/SSE transport
    google_generative.py   Gemini context encoding and response decoding
    google_images.py       Image request/response adaptation
    native_auth.py         Native request projection used only for vendor auth headers
    _message_builder.py    Shared mutable message builder for streaming
    _transform_messages.py Message format transformation for OpenAI
  vendors/
    antigravity/           Google OAuth, tokens, model sync and transport
    copilot/
      __init__.py          CopilotVendor (auth + token management)
      _model_sync.py       Model sync with API fallback models
  utils/
    event_stream.py        Async EventStream iterator with result
    cost.py                Cost calculation (premium multiplier or per-token)
    json_parse.py          Streaming JSON parser for partial tool args
    overflow.py            Context overflow detection
    hash.py                SHA-256 hashing utilities
    sanitize_unicode.py    Unicode sanitization
    validation.py          Tool argument validation
    auth.py                Reusable OAuth device code flow
```

## Key Types

Core request/message/model types are **frozen dataclasses**. State updates use `dataclasses.replace()`.

| Type | Purpose |
|------|---------|
| `Model` | Full model spec: ID, provider, protocol, limits, cost, capabilities |
| `ModelCost` | Pricing: premium request multiplier (Copilot) or per-token rates |
| `Context` | Conversation state: messages, system prompt, tools |
| `UserMessage` | User turn (text, images, or mixed content) |
| `AssistantMessage` | Assistant turn (text, thinking, tool calls) |
| `ToolResultMessage` | Tool execution result |
| `StreamOptions` | Neutral controls: thinking, sampling, stops, tools, structured output, metadata, service tier |
| `ProtocolRequest` | Immutable native protocol ID, operation, JSON bytes, and accepted headers |
| `NativeResponse` | Native status, complete response bytes, and safe headers |
| `NativeEventStream` | Native response start plus endpoint-specific SSE events |
| `AuthResult` | Resolved credentials: api_key, headers, base_url |
| `Usage` | Token counts and cost breakdown |
| `EventStream` | Async iterator over streaming events with final result |
| `EmbeddingRequest` | Input text(s) with optional dimensions |
| `EmbeddingResponse` | Embedding vectors with usage |
| `ImageGenerationRequest` / `ImageGenerationResponse` | Image prompt/options and decoded output images |
| `GeneratedImage` | Image bytes and MIME type |
| `BaseProvider` | ABC for provider implementations |
| `BaseProtocol` | ABC for wire-format protocols |
| `BaseVendor` | ABC for vendor auth + model sync |

## Provider-neutral streaming events

These events belong to the normalized `EventStream` contract and are discriminated by
`event.type`. Native proxy SSE bypasses them and retains each endpoint's wire framing:

| Event | Description |
|-------|-------------|
| `start` | Stream begins |
| `text_delta` | Incremental text token |
| `text_start` / `text_done` | Text block boundaries |
| `thinking_delta` | Reasoning token (when thinking enabled) |
| `thinking_start` / `thinking_done` | Thinking block boundaries |
| `tool_call_start` / `tool_call_delta` / `tool_call_done` | Tool invocation lifecycle |
| `usage` | Token usage update |
| `status` | Status update (e.g. web search progress) |
| `done` | Stream complete, final `AssistantMessage` available |
| `error` | Error during streaming |

## Storage

AI storage uses the fixed home-relative `~/.local/xdog/` directory (the current
`paths.py` does **not** read `XDG_DATA_HOME` or `XDG_CONFIG_HOME`). Do not infer the
AI credential location from other XDOG applications’ path rules:

| File | Purpose |
|------|---------|
| `auth.json` | OAuth tokens (keyed by provider ID) |
| `models_cache.json` | Synced model catalog |

## Testing

```bash
uv run pytest packages/ai/tests -q
```

## Model Limits

Each `Model` carries three token limits from the catalogue (`0` means unreported):

| Field | Meaning |
|-------|---------|
| `context_window` | Total token budget (input + output) |
| `max_prompt_tokens` | Maximum input tokens (the real usable limit) |
| `max_tokens` | Maximum output tokens |

## Cost

`Model.cost` holds pricing via `ModelCost`:

- **Copilot** — `cost.input` = premium request multiplier (0 = free, 0.33 = lightweight, 1 = standard, 3 = premium)
- **Per-token providers** — `cost.input`/`output`/`cache_read`/`cache_write` = per-million-token dollar rates

Copilot's normalized adapters use `usage_with_cost(model, usage)` to populate
`Usage.cost`. Other paths may retain default/unknown pricing; a zero total is not
proof of free access or a billing guarantee. Native responses preserve upstream
usage rather than reconstructing the normalized cost object.

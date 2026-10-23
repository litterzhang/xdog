---
title: Reference
---

Linux-only Python 3.12+ API and CLI. Import public types with
`import xdog.ai as ai`; see the
[SDK source reference](https://github.com/litterzhang/xdog/blob/main/packages/ai/src/xdog/ai/README.md)
for additional examples.

## Top-level API

| Name | Kind | Purpose |
|---|---|---|
| `provider(id)` | function | Construct `copilot` or `antigravity`; does not log in. |
| `load()` | function | Build a Runtime from recognized provider auth entries, without login/model sync or a live health check. |
| `await login(provider_id="copilot")` | function | Authenticate the selected provider. |
| `Context` | type | Messages, system prompt and tool definitions. |
| `StreamOptions` | type | Model-dependent reasoning, sampling, output limits, cancel event, tools, search and other controls. |
| `Model` | type | Routing identity, limits, pricing, endpoints and capability metadata. |
| `EventStream` | type | Async event iterator; `await stream.result()` returns the assembled assistant message. |
| `EmbeddingRequest / EmbeddingResponse` | types | Text/dimension input and embedding vectors with usage. |
| `ImageGenerationRequest / ImageGenerationResponse` | types | Prompt/reference options and decoded image bytes; SDK calls do not write files. |
| `ProtocolRequest / NativeResponse / NativeEventStream` | types | Separate immutable native-request and endpoint-specific response/SSE path. |

A complete normalized stream example (requires a valid login/network):

```python
import asyncio
import xdog.ai as ai

async def main():
    runtime = ai.load()
    context = ai.Context(messages=(ai.UserMessage(content="Hello!"),))
    stream = runtime.stream("copilot/claude-sonnet-4.5", context)
    async for event in stream:
        if event.type == "text_delta":
            print(event.delta, end="", flush=True)
    message = await stream.result()
    print(f"\nStop reason: {message.stop_reason}")

asyncio.run(main())
```

Select an available model from the catalog. With multiple active providers,
execution requires a qualified ID; there is no automatic credential fallback.

## Normalized stream events

| Event | Carries |
|---|---|
| `start` | Stream start. |
| `text_start / text_delta / text_done` | Text-block boundaries and incremental text. |
| `thinking_start / thinking_delta / thinking_done` | Reasoning-block boundaries and incremental content when supplied. |
| `tool_call_start / tool_call_delta / tool_call_done` | Tool-call identity, incremental arguments and completed request. |
| `usage` | Token/cache/cost data where supplied. |
| `status` | Provider-side status, such as search progress. |
| `done / error` | Final message or failure; inspect the terminal outcome. |

These events are not native proxy SSE frames. Tool requests do not execute
functions, persist results or grant permissions.

## Registered providers and capabilities

| Provider | Authentication | Wire implementation |
|---|---|---|
| `copilot` | GitHub OAuth device flow and provider-managed Copilot tokens | Model-selected `openai-completions`, `anthropic-messages`, `openai-responses`. |
| `antigravity` | Google OAuth through XDOG's own login/token storage | `google-generative-ai`, including Gemini generation/search/image paths where supported. |

Anthropic/OpenAI model brands and wire protocols are not additional provider
registrations. Availability of a provider or protocol does not imply every model
supports tools, vision, search, embeddings or image generation. Inspect
`--details`/`--json`: unknown capability is not support; image input/output differ.
AI auth/catalog paths currently use fixed `~/.local/xdog/` storage, not
`XDG_DATA_HOME` overrides.

## CLI — `xdog-ai`

| Command | Does |
|---|---|
| `login [provider]` | Authenticate a provider; default is Copilot. |
| `providers` | List active providers from auth storage, not all factories or live login health. |
| `models <provider> [--sync] [--details] [--json]` | Inspect cached metadata; `--sync` performs an explicit refresh. |
| `chat <provider> <model> [message]` | One-shot/TTY interactive chat; supports system, sampling, thinking, image and search options. |
| `embed <provider> <model> [text]` | Generate embeddings; `-d` selects dimensions, `--json` emits structured output. |
| `search <provider> <model> <query>` | Explicit provider/model web search. |
| `image <provider> <model> <prompt>` | Write uniquely named image files; supports reference images, aspect ratio, size and JSON metadata. `image_generation` is an alias. |
| `proxy [--host HOST] [--port PORT] [--api-key KEY]` | Serve local Messages, token-count, Responses and native-only Chat Completions routes. |
| `switch-cli <client> <provider-or-list> [--model MODEL]` | Set Coding/Claw defaults or configure external clients against the proxy; does not start the proxy. |

Use `uv run xdog-ai <command> --help` from the workspace root. The proxy's native
and compatibility paths have different guarantees; see the
[proxy and switch-cli reference](https://github.com/litterzhang/xdog/blob/main/packages/ai/README.md).
The optional `xdog-ai[openai]` extra adds an SDK dependency, not a provider.

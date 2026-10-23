---
title: Design
---

The AI layer separates authentication, model routing and wire adaptation. It is
not an Agent loop or a conversation store. Supported host scope is Linux with
Python 3.12+.

## Provider, vendor, protocol and runtime

A **Provider** exposes `stream`, `complete`, `embed`, `web_search`,
`image_generation`, login and catalog operations. Its **Vendor** owns credentials,
token refresh and model synchronization. **Wire adapters** encode/decode the
selected model's protocol. **Runtime** aggregates active providers and routes calls.

The shipped providers are Copilot and Antigravity. Copilot dispatches according
to advertised Anthropic Messages, OpenAI Chat Completions or OpenAI Responses
metadata; Antigravity uses Gemini generation transports. These protocol names
and model brands are not separately registered providers or interchangeable
credentials. Optional integrations do not silently log into another backend.

`ai.provider("copilot")` constructs one registered provider.
`ai.load()` builds a Runtime from recognized entries in XDOG's auth storage;
it does not log in, sync models or prove that stored tokens remain usable.
Generation uses `provider/model`, or a bare ID only with exactly one active
provider. Catalog lookup is not proof that a bare execution ID is unambiguous.

## Normalized types and native transport are separate

The normalized SDK uses frozen dataclasses: `Context`, `StreamOptions`, `Model`,
messages and content parts. `StreamOptions` carries controls such as thinking,
sampling, tool choice, structured output, cancellation and search. Whether a
control is representable depends on the executing model/protocol; the common
interface is not a universal feature guarantee.

Build replacement request values rather than mutating shared request state.
The frozen dataclasses are not a deep-freeze guarantee for nested JSON/tool
arguments; callers must still avoid concurrent mutation of their own data.

An `EventStream` is an async iterator plus `await stream.result()` for the final
`AssistantMessage`. Consumers inspect event types rather than parsing SSE.
Tool-call events carry requests, not permission or execution; a caller must
supply tool results and paired history, or use the Agent runtime.

`ProtocolRequest`, `NativeResponse` and `NativeEventStream` instead serve clients
that need wire fidelity, including the proxy. Native transport preserves the
requested endpoint's JSON/SSE framing rather than reconstructing neutral events.
Native and normalized API guarantees must not be conflated.

## Capability metadata is evidence, not an assumption

`Model` records context/input/output limits, accepted endpoints and capabilities.
Unreported limits are zero. Capability fields such as `supports_web_search` can
be `True`, `False` or `None`; absent metadata is not proof of support or its denial.
Ordinary tool calling and an advertised Responses endpoint alone do not prove
hosted web search. Image input and image output are separate capabilities.

Use `xdog-ai models <provider> --details` or `--json` for the full metadata;
`--sync` explicitly refreshes it. Cached catalogs and offline fallback metadata
do not prove upstream availability. Unsupported operations fail visibly instead
of silently becoming text chat; generation is not automatically retried merely
to obtain an image.

## Caching, usage and cancellation

`SystemPromptBlock(cache=True)` and `prompt_cache_key` express cache intent;
upstream support and cache hits are not guaranteed. Usage retains reported
input/output/cache counters. Copilot pricing uses premium-request multipliers;
other paths may have default or unknown pricing, so zero cost is not a billing
promise. Native responses retain upstream usage.

`StreamOptions.cancel` supplies cooperative cancellation. It does not undo
upstream generation or authorize replay of uncertain work. Tool execution and
its shutdown policy remain with the host/Agent.

## Local proxy compatibility

The proxy uses the same provider runtime. Native Messages, Responses and Chat
paths depend on exact advertised generation capabilities. Messages may use a
marked best-effort projection; Responses has a strict stateless fallback that
rejects unrepresentable controls. Chat Completions is native-only. Native token
counting has a separately marked local estimate for known non-Anthropic models;
that estimate is not a tokenizer guarantee.

See [reference](/packages/ai/reference) and the
[full proxy contract](https://github.com/litterzhang/xdog/blob/main/packages/ai/README.md)
for request limits, native/fallback differences and credential handling.

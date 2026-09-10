---
title: Reference
---

The top-level API, the streaming event union, the shipped providers, and the
`xdog-ai` CLI.

## Top-level API

The functions and types most application code touches, from `ai/__init__.py`.

| Name | Kind | Purpose |
|---|---|---|
| `provider(id)` | function | Return a Provider by id (today: `"copilot"`). |
| `load()` | function | Build a Runtime over all active, authenticated providers. |
| `login()` | function | Run a vendor's auth flow (Copilot: GitHub device code). |
| `Context` | type | Immutable messages + system prompt + tools. |
| `StreamOptions` | type | thinking / temperature / max_tokens / cancel / web_search. |
| `Model` | type | Routing id, pricing, capability flags, protocol preference. |
| `EventStream` | type | Async iterator over stream events + `.result()` future. |

## Stream event union

Each event yielded by a stream is one of these discriminated members (`types.py`).

| Event | Carries |
|---|---|
| `text_delta` | An incremental chunk of assistant text. |
| `thinking_delta` | An incremental chunk of reasoning text. |
| `tool_call_*` | The tool-call lifecycle (start / delta / end). |
| `usage` | Token counts for the turn. |
| `status` | Provider-side status transitions. |
| `done` | Terminal event; the final message is on `.result()`. |
| `error` | A provider or transport error. |

## Providers today

The shipped vendor set. The architecture is multi-vendor; the catalog is
currently one vendor reached over three protocols.

| Provider | Auth | Protocols |
|---|---|---|
| `copilot` | GitHub OAuth device code → Copilot JWT | openai-completions · anthropic-messages · openai-responses |

## CLI — `xdog-ai`

Subcommands of the ai console script.

| Command | Does |
|---|---|
| `login [provider]` | Authenticate a provider (default copilot). |
| `providers` | List active, authenticated providers. |
| `models <provider> [--sync]` | List a provider's models; `--sync` refreshes the catalog. |
| `chat <provider> <model> [msg]` | One-shot or interactive chat (`-s`, `-t`, `--max-tokens`, `--thinking`, `-i`, `--web-search`…). |
| `embed` | Embed text to a vector. |
| `search` | Run a web search through the provider. |
| `proxy [--host --port --api-key]` | Serve `/v1/messages`, `/v1/messages/count_tokens`, `/v1/responses`, and native-only `/v1/chat/completions`. |

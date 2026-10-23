---
title: Overview
---

*Typed model APIs with separate provider, routing and wire responsibilities.*

`xdog-ai` is the Linux-only Python 3.12+ foundation for normalized chat streams,
embeddings, web search and image generation where the selected model supports
them. The production factory registers **`copilot`** (GitHub Copilot) and
**`antigravity`** (Google Antigravity).

Anthropic Messages, OpenAI Responses/Chat Completions and Gemini are wire formats,
not additional configured providers. An OpenAI- or Anthropic-branded model reached
through Copilot still uses the `copilot` provider and its authentication.

## Highlights

- Frozen request/message/model types and a discriminated streaming event union
- Copilot and native Antigravity authentication and model catalog synchronization
- Explicit `provider/model` routing without cross-provider credential fallback
- Model-dependent tools, reasoning, vision, search, embeddings and image output
- Local Messages, token-count, Responses and supported Chat Completions facades

Model metadata can be unknown. A wire endpoint or ordinary tool calling does not
prove hosted search or image support. The SDK describes tool requests; execution,
permissions and conversation persistence belong to the caller or
[Agent](/packages/agent), not to the AI provider layer.

## Try it

From the workspace root after `uv sync`:

```bash
uv run xdog-ai --help
uv run xdog-ai login copilot
uv run xdog-ai models copilot --details
uv run xdog-ai chat copilot claude-sonnet-4.5 "Explain provider routing"
# Independent Google login/catalog:
uv run xdog-ai login antigravity
uv run xdog-ai models antigravity --details
```

Standalone installation: `pip install xdog-ai`. Python imports use
`import xdog.ai as ai`, not `import ai`. Generation/login require network and
account eligibility; choose actual model IDs from the catalog rather than
assuming an example ID is available to every account. The optional `openai`
extra installs an SDK dependency, not a new provider.

Continue with [design](/packages/ai/design), [API and CLI reference](/packages/ai/reference)
or the [package README](https://github.com/litterzhang/xdog/blob/main/packages/ai/README.md).

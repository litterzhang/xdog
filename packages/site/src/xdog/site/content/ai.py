"""Dynamic (Features + Roadmap) content for the ``ai`` package.

ai's static pages (Overview / Design / Reference) are markdown under
``content/pages/ai/``; its Features and Roadmap stay in Python here. Accurate
against packages/ai/src/ai. Today the only shipped vendor is GitHub Copilot; the
roadmap is explicit about what is shipped vs. planned.
"""

from __future__ import annotations

from xdog.site.content.docs import Feature, PackageDocs, Phase

_FEATURES = (
    Feature("Streaming completions", "Async EventStream with a typed event union and a .result() "
            "future for the final message.", "Model calls"),
    Feature("Tool / function calling", "Tool definitions plus per-model supports_tool_calls and "
            "supports_parallel_tool_calls capability flags.", "Model calls"),
    Feature("Web search", "A built-in provider.web_search() and a StreamOptions.web_search flag that "
            "enables in-turn browsing where the model supports it.", "Model calls"),
    Feature("Thinking levels", "StreamOptions carries a ThinkingLevel so reasoning depth is a "
            "first-class request parameter.", "Model calls"),
    Feature("Cancellation", "A StreamOptions.cancel asyncio.Event aborts an in-flight stream "
            "cooperatively.", "Model calls"),
    Feature("Token & cost accounting", "Usage, CostBreakdown, and ModelCost accompany every response; "
            "Copilot premium-request multipliers are modelled.", "Accounting"),
    Feature("Model catalog sync", "sync_models fetches the live /models list, caches it (24h TTL) with "
            "a bundled fallback set so the picker still works offline.", "Accounting"),
    Feature("Embeddings", "provider.embed() with EmbeddingRequest / EmbeddingResponse types for "
            "vector workloads.", "Model calls"),
    Feature("Prompt caching", "SystemPromptBlock(cache=True) marks the static prompt base cacheable "
            "across turns.", "Accounting"),
    Feature("GitHub OAuth device flow", "Copilot auth uses the GitHub device-code flow, exchanging for "
            "a Copilot JWT — no API key to paste.", "Providers"),
    Feature("Three wire protocols", "Copilot models are reached over openai-completions, "
            "anthropic-messages, or openai-responses, chosen per model.", "Providers"),
    Feature("Local API proxy", "proxy.py serves /v1/messages, /v1/messages/count_tokens, "
            "/v1/responses, and /v1/chat/completions through the same provider runtime.", "Interop"),
)

_FEATURE_CATEGORIES = ("Model calls", "Accounting", "Providers", "Interop")

_ROADMAP = (
    Phase("Shipped", "Typed multi-provider core", (
        "Provider / Protocol / Vendor three-layer split",
        "Frozen-dataclass Context / StreamOptions / Model type model",
        "Streaming event union with .result() future",
        "Tool calls, web search, thinking levels, cancellation",
    ), done=True),
    Phase("Shipped", "Copilot vendor + accounting", (
        "GitHub device-code auth → Copilot JWT",
        "Three wire protocols (openai-completions / anthropic-messages / openai-responses)",
        "Model catalog sync with 24h cache + offline fallback",
        "Usage / cost accounting with premium-request multipliers",
        "Messages, token count, Responses, and Chat Completions API proxy",
    ), done=True),
    Phase("2026", "Beyond one vendor", (
        "Additional first-party vendors behind the same Provider surface",
        "Provider-agnostic model-capability discovery",
        "Cross-provider routing / fallback policies",
    )),
    Phase("2026", "Richer accounting & caching", (
        "Unified per-run cost budgets surfaced to callers",
        "Automatic prompt-cache block placement",
        "Structured-output / JSON-schema responses across protocols",
    )),
)

DOCS = PackageDocs(
    name="ai",
    features_intro="What ai does today. Every capability below is backed by the shipped Copilot "
                   "provider and the typed core.",
    feature_categories=_FEATURE_CATEGORIES,
    features=_FEATURES,
    roadmap_intro="Shipped foundations plus where ai is heading in 2026. Planned items are "
                  "aspirational, not yet implemented.",
    roadmap=_ROADMAP,
)

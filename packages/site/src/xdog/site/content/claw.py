"""Dynamic (Features + Roadmap) content for the ``claw`` package.

claw's static pages (Overview / Design / Reference) are markdown under
``content/pages/claw/``; its Features and Roadmap stay in Python here. Accurate
against packages/claw/src/xdog/claw: the gateway daemon, the orchestrator + per-group
runtimes, the concurrency model, the workspace-file system prompt, long-term
memory with optional semantic search, goals, channels (tui + weixin), and the
xdog CLI.
"""

from __future__ import annotations

from xdog.site.content.docs import Feature, PackageDocs, Phase

_FEATURES = (
    Feature("Gateway daemon", "Unix-socket JSON-line server with streaming, PID/signal handling, and "
            "optional managed Eyes observation.", "Runtime"),
    Feature("Groups", "Isolated per-group runtimes with their own workspace, memory, goals, and "
            "tools.", "Runtime"),
    Feature("Per-group config", "model_id, thinking_level, temperature, max_tokens per group.",
            "Runtime"),
    Feature("Concurrency control", "Global semaphore (default 3) + per-group locks; user messages "
            "bypass the cap.", "Runtime"),
    Feature("Durable intake", "Shared group Inbox, commit before ACK; explicit steer/follow-up belongs to Agent.",
            "Runtime"),
    Feature("Workspace system prompt", "IDENTITY / AGENTS / SOUL / USER .md + MEMORY snapshot + "
            "one-time BOOTSTRAP.", "Memory & prompt"),
    Feature("Long-term memory", "Daily logs + MEMORY.md persisted across runs.", "Memory & prompt"),
    Feature("Semantic search", "Optional vector + BM25 hybrid (RRF), with a keyword fallback when the "
            "search extra is absent.", "Memory & prompt"),
    Feature("Prompt caching", "Static base cached; dynamic workspace overrides applied per turn.",
            "Memory & prompt"),
    Feature("Optional Goals", "Shared Goal package: explicit worker assignment, report acceptance and "
            "independent completion review.", "Autonomy"),
    Feature("Scheduling", "Opt-in Eyes sources: cron, interval and one-off events; separate notification cancellation.",
            "Autonomy"),
    Feature("Scoped tools", "Messaging, memory, todo and skills; optional Goals bind to their original group.",
            "Autonomy"),
    Feature("tui channel", "Local terminal client to a running gateway.", "Channels"),
    Feature("weixin channel", "WeChat bot: long-poll monitor, QR auth, typing indicators, context "
            "persistence.", "Channels"),
    Feature("Chunked delivery", "Paragraph-aware chunking streams long replies naturally.", "Channels"),
)

_FEATURE_CATEGORIES = ("Runtime", "Memory & prompt", "Autonomy", "Channels")

_ROADMAP = (
    Phase("Shipped", "Gateway & orchestration", (
        "Unix-socket gateway daemon with JSON-line protocol",
        "Orchestrator + isolated per-group runtimes",
        "Durable Inbox + single execution owner per group, explicit Agent input controls",
        "Cron / interval / one-off scheduling, session resets",
    ), done=True),
    Phase("Shipped", "Memory, prompt & channels", (
        "Workspace-file system prompt (IDENTITY / AGENTS / SOUL / USER)",
        "Long-term memory with optional vector+BM25 hybrid search",
        "Optional reusable Goals: explicit workers, acceptance and independent review",
        "Channels: local tui and weixin (WeChat) bot",
    ), done=True),
    Phase("2026", "More reach", (
        "Additional channels (e.g. Slack / Telegram / HTTP webhook)",
        "Multi-user groups with per-user context",
        "Richer memory consolidation and forgetting policies",
    )),
    Phase("2026", "Operability", (
        "Metrics / observability for a running gateway",
        "Hot-reload of workspace and config without restart",
        "Backup / sync of workspaces and transcripts",
    )),
)

DOCS = PackageDocs(
    name="claw",
    features_intro="What claw does today. Where agent is one loop, claw is the runtime that keeps "
                   "many agents and their state alive between conversations.",
    feature_categories=_FEATURE_CATEGORIES,
    features=_FEATURES,
    roadmap_intro="Shipped foundations plus where claw is heading in 2026. Planned items are "
                  "aspirational, not yet implemented.",
    roadmap=_ROADMAP,
)

"""Current user-facing answers; historical articles are not API references."""
from __future__ import annotations

FAQS: list[dict[str, str]] = [
    {
        "q": "What is XDOG?",
        "a": (
            "Nine local-first Python workspace packages: model APIs (ai), the Agent SDK (agent), terminal "
            "components (tui), coding and persistent assistant applications (coding/claw), typed workflows "
            "(flow), JSON observations (eyes), Goal/plan skills (skills), and this documentation/demo app (site)."
        ),
    },
    {
        "q": "Which package do I need?",
        "a": (
            "Use coding for repository work, claw for a persistent TUI/Weixin assistant, flow for a repeatable "
            "typed process and eyes for file/time observations. Build an application with ai/agent/tui. "
            "Coding and Claw include the skills hub; installed Flow/Eyes capabilities are composed through the SDK."
        ),
    },
    {
        "q": "Which model providers are supported?",
        "a": (
            "The registered providers are GitHub Copilot and Antigravity. OpenAI/Anthropic/Google wire "
            "formats and model families are not additional independently configured providers. Actual tool, "
            "search, embedding, image and streaming support depends on the selected provider/model metadata."
        ),
    },
    {
        "q": "Does local-first mean no database or account?",
        "a": (
            "No hosted control plane or external database is required. Durable applications use local JSON, "
            "JSONL, Markdown and SQLite. Real model calls still need the provider's authentication/account; "
            "workflow validation and stubbed tests can run offline. Eyes has an independent user-local daemon."
        ),
    },
    {
        "q": "Do all generated workflows run without Flow?",
        "a": (
            "No. Generated Python preserves the interpreter's typed frontier semantics, but runtime "
            "dependencies depend on node types and subflows. Some generated paths still import xdog.flow; "
            "SDK Agent nodes need their SDK dependencies. Use the documented portable build/install path "
            "rather than assuming every generated file is dependency-free."
        ),
    },
    {
        "q": "How do tools, MCP, capabilities and extensions differ?",
        "a": (
            "Each host owns a ToolRegistry for ordinary/capability tools and a separate McpRegistry for MCP "
            "connections. Agent merges the visible inventories in one permission-checked loop. Installed "
            "capabilities may contribute skills, tools, runtime resources and extension hooks. Extension "
            "workers retain hooks, commands, UI and providers; they no longer register model tools."
        ),
    },
    {
        "q": "Does discovering a skill activate it or grant tools?",
        "a": (
            "No. Packaged goal, plan, flow and eyes skills are discoverable, but their full bodies are not "
            "injected by default. Tool/command registration, skill activation and tool authorization are "
            "independent. Opening Eyes' installed application binding ensures daemon availability, not an observation."
        ),
    },
    {
        "q": "Who plans and completes a Goal?",
        "a": (
            "The current main Agent plans from its conversation, works or delegates through existing host "
            "tools, reports actual progress before continuation and judges completion. Goal owns records, "
            "task files and signal-driven ACP reminders, not another Agent runner. Pause stops proactive "
            "reminders and its owned observation; completed Goals remain visible until explicit clear."
        ),
    },
    {
        "q": "Does an Eyes enqueue receipt mean the Agent finished?",
        "a": (
            "No. It acknowledges durable receiver intake, not execution or user-channel delivery. A live "
            "owner consumes through its existing controller and permissions. JSON selects the destination; "
            "owner is a management namespace, not a group restriction. Unknown/interrupted effects are not "
            "blindly replayed, and stopping a source does not retract accepted downstream work."
        ),
    },
    {
        "q": "Why does an automatic Claw input not reply to Weixin?",
        "a": (
            "Automatic ACP input has no implicit chat recipient. The main Agent may explicitly use the "
            "message tool to select one currently bound channel for each message. Normal human replies "
            "keep their original route; execution success and delivered/pending/unknown output facts are separate."
        ),
    },
    {
        "q": "Are permissions a sandbox, and do children get more authority?",
        "a": (
            "No OS sandbox is implied. Coding's ask/ask-all/deny/allow-all policy and Claw's configured "
            "policy remain host-owned. Child tools, skills and filesystem ceilings cannot exceed the actual "
            "parent; stored constraints are data ceilings, never restored authority. A running child is "
            "managed by its parent, not a fixed SDK lifetime quota."
        ),
    },
    {
        "q": "Is Flow a hosted durable-execution service?",
        "a": (
            "No. Flow provides typed ports, explicit mappings, retry policies, bounded loops, fan-out, subflows, "
            "checkpoints, human signals, code generation and local scheduling. It is a local workflow engine, "
            "not a distributed multi-tenant control plane. Flow has no daemon; "
            "Eyes' daemon observes and delivers evidence."
        ),
    },
    {
        "q": "Can I edit workflows visually or run them here?",
        "a": (
            "xdog-flow build opens the native terminal builder. HaveFun is a trusted/local demonstration "
            "using workspace Flow/Agent/AI integration; uploaded scripts/tools run with host authority, not "
            "in a sandbox. A Flask secret does not make arbitrary submissions safe. Planned web authoring "
            "is not presented as an already shipped workflow editor."
        ),
    },
    {
        "q": "How do I install and run it?",
        "a": (
            "Linux and Python 3.12+ are supported. Install the required xdog-* applications in one Python "
            "environment, or clone the repository and run uv sync --locked. Eight distributions publish to "
            "PyPI; xdog-site is run from the workspace with uv run xdog-site. The development server uses "
            "XDOG_SITE_HOST/PORT; set XDOG_SITE_SECRET for deployment and restrict runner access."
        ),
    },
    {
        "q": "What is the license?",
        "a": (
            "Source is AGPL-3.0-or-later, not MIT. LICENSE-EXCEPTION.md grants additional permission for "
            "generated workflow modules, portable bundles, scheduling units and definitions; it does not "
            "relicense the XDOG libraries themselves."
        ),
    },
]

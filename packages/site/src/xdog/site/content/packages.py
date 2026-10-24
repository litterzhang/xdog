"""Authored product catalog; rendering metadata imports no business runtime.

Home, package routes, navigation and sitemap share the eight public packages.
The workspace-only site application is documented in its repository README.
"""

from __future__ import annotations

from dataclasses import dataclass

_GITHUB = "https://github.com/litterzhang/xdog"


@dataclass(frozen=True)
class Package:
    """One published distribution in the public product catalog."""

    name: str
    cli: str
    tagline: str
    summary: tuple[str, ...]
    highlights: tuple[str, ...]
    install: str = ""

    @property
    def readme_url(self) -> str:
        return f"{_GITHUB}/tree/main/packages/{self.name}"

    @property
    def dist(self) -> str:
        return f"xdog-{self.name}"

    @property
    def pypi_url(self) -> str:
        return f"https://pypi.org/project/{self.dist}/"


PACKAGES: tuple[Package, ...] = (
    Package(
        name="ai",
        cli="xdog-ai",
        tagline="Typed model APIs, providers and wire protocols",
        summary=(
            "Provider-neutral Context, model metadata and streaming APIs with separate authentication, "
            "routing and protocol adaptation. Copilot and Antigravity are the registered providers.",
            "Model capabilities determine streaming, tools, search, embedding and image support; "
            "wire protocols do not imply separately configured model providers.",
        ),
        highlights=(
            "Copilot and Antigravity provider implementations",
            "Typed streams, usage and cancellation",
            "Model-dependent tools, search, embeddings and image generation",
            "Local protocol proxy",
        ),
        install="pip install xdog-ai",
    ),
    Package(
        name="agent",
        cli="xdog-agent",
        tagline="Reusable Agent and host-owned composition SDKs",
        summary=(
            "One tool-calling loop with structured history, steering, follow-ups, cancellation and "
            "versioned snapshots.",
            "Hosts own ToolRepo, separate McpRegistry, capability bindings, permissions and child runtimes. "
            "The SDK supplies reusable contracts rather than a global execution service.",
        ),
        highlights=(
            "Parallel tools and owned cancellation",
            "Structured dump/restore with runtime reinjection",
            "Host-owned tool/MCP registries and installed capabilities",
            "Opt-in built-in compaction",
        ),
        install="pip install xdog-agent",
    ),
    Package(
        name="tui",
        cli="",
        tagline="Shared terminal rendering and input components",
        summary=(
            "Differential main-buffer rendering, Unicode-aware text, input parsing and reusable "
            "message/editor components.",
            "A rendering library shared by Coding and Claw, not an owner of approvals, sessions or model execution. "
            "Linux is the supported host platform; terminal image protocols are capability-dependent.",
        ),
        highlights=(
            "Scrollback-preserving differential rendering",
            "Unicode clipping, cursor and key handling",
            "Editors, messages, pickers and transcript views",
            "Terminal-capability-aware images",
        ),
        install="pip install xdog-tui",
    ),
    Package(
        name="coding",
        cli="xdog-coding",
        tagline="Interactive coding assistant with explicit permissions",
        summary=(
            "A repository assistant with native TUI and print modes, persistent sessions/branches and "
            "an independent ACP ingress.",
            "The session controller owns execution, approvals and isolated child Agents. Installed capabilities "
            "supply tools and discoverable skills without replacing that owner.",
        ),
        highlights=(
            "TUI/print over one session controller",
            "ask, ask-all, deny and explicit allow-all modes",
            "Persistent history, compaction and explicit steering",
            "Child tool/skill subsets and filesystem ceilings",
        ),
        install="pip install xdog-coding",
    ),
    Package(
        name="claw",
        cli="xdog-claw",
        tagline="Persistent multi-channel assistant application",
        summary=(
            "A gateway with one logical main Agent per group, durable input/output, workspace memory, "
            "TUI and Weixin channels.",
            "Claw owns application execution and delivery, not a public Python embedding SDK. Context rotation "
            "does not change the group's Goal owner or create another execution route.",
        ),
        highlights=(
            "One main Agent and execution route per group",
            "Durable intake and independent delivery receipts",
            "Workspace memory and authorized child delegation",
            "Private ACP intake; explicit single-channel sends",
        ),
        install="pip install xdog-claw",
    ),
    Package(
        name="flow",
        cli="xdog-flow",
        tagline="Typed workflows for humans and Coding Agents",
        summary=(
            "Git-friendly workflow JSON with node-private ports, explicit edge mappings, whole-graph validation "
            "and matching interpreter/code-generation semantics.",
            "Run repeatable processes, test them with stubs, generate Python or deploy local schedules. "
            "Generated artifacts retain the dependencies required by their node and subflow paths; Flow has no daemon.",
        ),
        highlights=(
            "Typed ports, mappings, loops and fan-out",
            "Script, SDK/CLI Agent, human and subflow nodes",
            "Workflow tests, checkpoints and code generation",
            "CLI-backed application tool and discoverable flow skill",
        ),
        install="pip install xdog-flow",
    ),
    Package(
        name="eyes",
        cli="xdog-eyes",
        tagline="JSON observations and an independent shared daemon",
        summary=(
            "File and scheduler sources collect evidence, detectors emit events and explicit JSON "
            "routes select handlers and destinations.",
            "The user-local daemon owns registrations, cursors and delivery recovery. A caller-selected owner "
            "is a management namespace, not authentication or a restriction to the current session/group.",
        ),
        highlights=(
            "File, at, interval and zoned cron observations",
            "One shared daemon, CLI and CLI-backed tool",
            "Explicit JSON destinations and owner-scoped management",
            "Durable intake is not Agent completion or chat delivery",
        ),
        install="pip install xdog-eyes",
    ),
    Package(
        name="skills",
        cli="",
        tagline="Goal and plan skills with isolated domain support",
        summary=(
            "The common skills hub ships goal and plan plus Goal records, task files, progress reports "
            "and signal-driven reminders.",
            "The current main Agent plans, works/delegates and judges completion. Goal never starts a second "
            "Agent loop; Flow and Eyes keep their independent packages and skills.",
        ),
        highlights=(
            "Discoverable skill bodies, not default prompt injection",
            "Native /goal commands and scoped model tools",
            "Main-Agent progress reports and evidence-based completion",
            "ACP reminders and optional Eyes task observation",
        ),
        install="pip install xdog-skills",
    ),

)

PACKAGES_BY_NAME: dict[str, Package] = {package.name: package for package in PACKAGES}

LAYERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Foundations", ("ai", "tui")),
    ("Agent SDK", ("agent",)),
    ("Workflows and capabilities", ("flow", "eyes", "skills")),
    ("Applications", ("coding", "claw")),
)

"""Current authored coding documentation; no business runtime is imported."""

from __future__ import annotations

from xdog.site.content.docs import Feature, PackageDocs, Phase

DOCS = PackageDocs(
    name="coding",
    features_intro="A native coding application over the shared SDK, with one execution controller per active session.",
    feature_categories=("Application", "Sessions", "Authority", "Integration"),
    features=(
        Feature(
            "TUI and print",
            "Interactive terminal chat and non-interactive text/JSON/Markdown output use the same session core.",
            "Application",
        ),
        Feature(
            "Independent ACP intake",
            "A private receiver can commit exact-session inputs while the TUI owner is offline.",
            "Application",
        ),
        Feature(
            "Durable sessions", "JSON history, explicit session/branch navigation and pre-turn compaction.", "Sessions"
        ),
        Feature(
            "Permissions",
            "ask, ask-all, deny and explicit allow-all; classified reads and mutating/execute calls "
            "follow host policy.",
            "Authority",
        ),
        Feature(
            "Child delegation",
            "Independent knowledge/system prompts, selected models and tools/skills within actual parent grants.",
            "Authority",
        ),
        Feature(
            "Filesystem ceilings",
            "Delegated file constraints are revalidated and saved as data ceilings, never authority.",
            "Authority",
        ),
        Feature(
            "Capability composition",
            "Installed application/session capabilities provide tools and discoverable skills without "
            "hardcoded Goal/Eyes implementations.",
            "Integration",
        ),
        Feature(
            "Extensions and MCP",
            "Separate MCP registry; extension hooks, commands, UI and providers do not register model tools.",
            "Integration",
        ),
    ),
    roadmap_intro="Shipped behavior is separate from possible future work; future items are not release promises.",
    roadmap=(
        Phase(
            "Shipped",
            "Native coding and integration",
            (
                "TUI/print and independent ACP ingress",
                "Sessions, branches, explicit input control and permissions",
                "Host-owned child runtime and durable safe continuation",
                "Default skills hub plus installed Flow/Eyes discovery",
            ),
            done=True,
        ),
        Phase(
            "Future",
            "Usability and recovery",
            (
                "Improve large-repository navigation and actionable diagnostics",
                "Extend restart and real-terminal coverage without restoring removed frontend RPC",
            ),
        ),
    ),
)

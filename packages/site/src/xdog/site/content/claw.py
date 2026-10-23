"""Current authored claw documentation; no business runtime is imported."""

from __future__ import annotations

from xdog.site.content.docs import Feature, PackageDocs, Phase

DOCS = PackageDocs(
    name="claw",
    features_intro="A persistent assistant application, not a public gateway/session embedding SDK.",
    feature_categories=("Application", "Channels", "Context", "Integration"),
    features=(
        Feature(
            "One main Agent per group",
            "A stable group owns workspace, memory and one execution route; history is not an event destination.",
            "Application",
        ),
        Feature(
            "Durable ordinary input",
            "Native, Weixin and private ACP use the Core Inbox and the same group controller.",
            "Application",
        ),
        Feature(
            "Execution settlement",
            "Failures settle the original producer and persistence before releasing group ownership.",
            "Application",
        ),
        Feature(
            "Delivery receipts",
            "Execution and delivered/pending/unknown output facts are separate; unknown sends are not replayed.",
            "Channels",
        ),
        Feature(
            "Explicit outbound messages",
            "The main Agent selects one currently bound channel per message; automatic ACP input has no "
            "implicit chat recipient.",
            "Channels",
        ),
        Feature(
            "TUI and Weixin",
            "A native terminal client and Weixin chat connection share the group main Agent.",
            "Channels",
        ),
        Feature(
            "Workspace memory",
            "Workspace Markdown, JSONL history and optional vector/BM25 search with explicit fallback behavior.",
            "Context",
        ),
        Feature(
            "Context rotation",
            "Idle reset rotates context, not Goal ownership; saved branches remain read-only history.",
            "Context",
        ),
        Feature(
            "Installed capabilities",
            "Skills/Goal by default; optional installed Flow/Eyes tools use their own packages and CLI boundaries.",
            "Integration",
        ),
        Feature(
            "Host-owned children",
            "Parent-managed child lifecycle, independent prompts/models and delegated authority; no "
            "fixed child lifetime quota.",
            "Integration",
        ),
    ),
    roadmap_intro="Shipped behavior is separate from possible future work; future items are not release promises.",
    roadmap=(
        Phase(
            "Shipped",
            "Persistent assistant application",
            (
                "Gateway, TUI/Weixin and default private ACP",
                "Core Inbox, single group owner and durable delivery receipts",
                "Memory, context rotation and host-owned children",
                "Native Goal integration and independent installed capabilities",
            ),
            done=True,
        ),
        Phase(
            "Future",
            "Operability",
            (
                "Broaden channel recovery and long-running reliability evidence",
                "Improve diagnostics without adding another scheduler or execution route",
            ),
        ),
    ),
)

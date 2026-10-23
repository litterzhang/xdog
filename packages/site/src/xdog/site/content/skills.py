"""Current authored skills documentation; no business runtime is imported."""

from __future__ import annotations

from xdog.site.content.docs import Feature, PackageDocs, Phase

DOCS = PackageDocs(
    name="skills",
    features_intro="Common goal/plan instructions and isolated Goal domain support, not an Agent harness.",
    feature_categories=("Instructions", "Goal", "Integration"),
    features=(
        Feature(
            "Discoverable skills",
            "goal and plan are listed without injecting full bodies by default; Flow/Eyes ship their own skills.",
            "Instructions",
        ),
        Feature(
            "Native Goal commands",
            "Installed Coding/Claw capability contributes /goal commands and model tools independently of activation.",
            "Goal",
        ),
        Feature(
            "Agent-owned workflow",
            "The current main Agent plans, delegates optionally, evaluates progress and judges completion.",
            "Goal",
        ),
        Feature(
            "Authoritative files",
            "One current Goal per owner; task JSON, plan.md, progress.md and versioned registry history.",
            "Goal",
        ),
        Feature(
            "Progress reports",
            "Before each continuation the main Agent records actual progress; repeated no progress can "
            "lead it to pause.",
            "Goal",
        ),
        Feature(
            "Signal-driven reminders",
            "Generic host events and optional Eyes task observation coalesce into ACP reminders to the "
            "original main Agent.",
            "Integration",
        ),
        Feature(
            "Explicit lifecycle",
            "Pause stops proactive reminders/owned observation; completed remains visible until clear; "
            "no host wait or timer auto-authorization.",
            "Integration",
        ),
    ),
    roadmap_intro="Shipped behavior is separate from possible future work; future items are not release promises.",
    roadmap=(
        Phase(
            "Shipped",
            "Goal and plan capability",
            (
                "Packaged discoverable skills",
                "Owner-bound commands, tools and data",
                "Progress reports and optional task observation",
                "Main-Agent acceptance without worker/reviewer runners",
            ),
            done=True,
        ),
        Phase(
            "Future",
            "Transparency",
            (
                "Improve bounded views and actionable questions",
                "Broaden recovery evidence without adding a second planner or Agent loop",
            ),
        ),
    ),
)

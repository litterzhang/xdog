"""Current authored eyes documentation; no business runtime is imported."""

from __future__ import annotations

from xdog.site.content.docs import Feature, PackageDocs, Phase

DOCS = PackageDocs(
    name="eyes",
    features_intro=(
        "Independent JSON-defined observation. The daemon delivers evidence, not Agent execution or "
        "user-channel verdicts."
    ),
    feature_categories=("Observation", "Lifecycle", "Integration"),
    features=(
        Feature(
            "File and time sources",
            "File polling, one-off at, monotonic intervals and five-field cron with explicit IANA timezone.",
            "Observation",
        ),
        Feature(
            "Explicit routes",
            "Sources/detectors/handlers/routes belong to the saved versioned JSON definition.",
            "Observation",
        ),
        Feature(
            "One shared daemon",
            "User-local durable registry, cursors and delivery recovery; --directory isolates the same implementation.",
            "Lifecycle",
        ),
        Feature(
            "Owner namespace",
            "Caller-chosen stable management identity; not authentication and not a destination restriction.",
            "Lifecycle",
        ),
        Feature(
            "CLI and model tool",
            "The application capability ensures daemon availability and calls the public CLI; no "
            "embedded host service.",
            "Integration",
        ),
        Feature(
            "ACP delivery boundary",
            "Enqueue acknowledges durable intake; prompt has its explicit live-turn boundary. Neither "
            "implies user chat delivery.",
            "Integration",
        ),
        Feature(
            "No blind replay",
            "Unknown/interrupted effects stay fenced; source cancellation does not undo accepted receiver work.",
            "Lifecycle",
        ),
    ),
    roadmap_intro="Shipped behavior is separate from possible future work; future items are not release promises.",
    roadmap=(
        Phase(
            "Shipped",
            "Independent observations",
            (
                "Standalone daemon, CLI and packaged skill",
                "Application-scoped CLI-backed tool",
                "File and scheduler sources with explicit JSON destinations",
                "Durable readiness checkpoints and bounded recovery",
            ),
            done=True,
        ),
        Phase(
            "Future",
            "Reliability evidence",
            (
                "Broaden offline receiver and long-running delivery coverage",
                "Add sources/handlers only behind the existing explicit routing contract",
            ),
        ),
    ),
)

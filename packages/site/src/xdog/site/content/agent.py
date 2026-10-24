"""Current authored agent documentation; no business runtime is imported."""

from __future__ import annotations

from xdog.site.content.docs import Feature, PackageDocs, Phase

DOCS = PackageDocs(
    name="agent",
    features_intro=(
        "Reusable SDK facilities for one Agent loop. Applications retain resource ownership, "
        "permissions and child scheduling."
    ),
    feature_categories=("Loop", "Lifecycle", "Composition"),
    features=(
        Feature(
            "One tool loop",
            "Structured messages and linked tool results; parallel tool calls by default with serial hooks.",
            "Loop",
        ),
        Feature(
            "Input control", "Identified steering/follow-up queues and lifecycle events on the existing Agent.", "Loop"
        ),
        Feature(
            "Owned shutdown",
            "Cancellation and aclose settle the owned producer; effects are not rolled back or replayed.",
            "Lifecycle",
        ),
        Feature(
            "Versioned snapshots",
            "dump/restore retain conversation, skill and compaction data; hosts reinject live resources.",
            "Lifecycle",
        ),
        Feature(
            "Opt-in compaction",
            "One built-in policy; generic Agent compaction is off unless explicitly configured.",
            "Lifecycle",
        ),
        Feature(
            "Host-owned tools",
            "ToolRepo supplies actual default bash/filesystem/current_time tools; submit_result is opt-in.",
            "Composition",
        ),
        Feature(
            "Separate MCP registry",
            "McpRegistry owns MCP connections and proxies; Agent consumes it separately from ordinary tools.",
            "Composition",
        ),
        Feature(
            "Installed capabilities",
            "CapabilityAssembly combines explicitly opened host bindings and selected skills, without another runtime.",
            "Composition",
        ),
        Feature(
            "Extension boundary",
            "Workers contribute hooks, commands, UI and providers, not model tools.",
            "Composition",
        ),
        Feature(
            "Child SDK",
            "Handles and delegation contracts; Coding/Claw own child execution runtimes and authority.",
            "Composition",
        ),
    ),
    roadmap_intro="Shipped behavior is separate from possible future work; future items are not release promises.",
    roadmap=(
        Phase(
            "Shipped",
            "Loop and host composition",
            (
                "Tool calling, steering and follow-ups",
                "Versioned snapshots and opt-in compaction",
                "Host-local registries and installed capabilities",
                "Inert child contracts; no SDK child supervisor service",
            ),
            done=True,
        ),
        Phase(
            "Future",
            "Reliability improvements",
            (
                "Broaden real-owner cancellation and recovery regression coverage",
                "Keep future adapters schema-neutral and owner-bound",
            ),
        ),
    ),
)

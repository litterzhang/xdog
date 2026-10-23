"""Current authored tui documentation; no business runtime is imported."""

from __future__ import annotations

from xdog.site.content.docs import Feature, PackageDocs, Phase

DOCS = PackageDocs(
    name="tui",
    features_intro=(
        "Dependency-light rendering and input primitives shared by applications, not their "
        "permission or runtime policy."
    ),
    feature_categories=("Rendering", "Components", "Input"),
    features=(
        Feature(
            "Differential main-buffer rendering",
            "Components return ANSI line arrays; changed lines repaint while scrollback is preserved.",
            "Rendering",
        ),
        Feature(
            "Unicode and cursor handling",
            "Width-aware clipping, wrapping, CJK/emoji support and real-cursor placement.",
            "Rendering",
        ),
        Feature(
            "Messages and transcripts",
            "Reusable user/assistant/tool presentation and bounded transcript views.",
            "Components",
        ),
        Feature(
            "Editors and navigation",
            "Multiline editing, undo/kill ring, pickers and completion components.",
            "Components",
        ),
        Feature(
            "Terminal input",
            "xterm/Kitty key parsing and scoped keybindings; host admission and approvals stay outside TUI.",
            "Input",
        ),
        Feature(
            "Images and attachments",
            "Terminal-capability-aware image protocols and Linux attachment/clipboard helpers.",
            "Input",
        ),
    ),
    roadmap_intro="Shipped behavior is separate from possible future work; future items are not release promises.",
    roadmap=(
        Phase(
            "Shipped",
            "Shared native UI",
            (
                "Main-buffer differential rendering",
                "Unicode, clipping and cursor fixes",
                "Message/editor/transcript components",
                "Input and terminal-capability-aware images",
            ),
            done=True,
        ),
        Phase(
            "Future",
            "Usability",
            (
                "Improve real-terminal, accessibility and rendering regressions",
                "Keep application policy outside shared components",
            ),
        ),
    ),
)

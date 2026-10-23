"""Current authored ai documentation; no business runtime is imported."""

from __future__ import annotations

from xdog.site.content.docs import Feature, PackageDocs, Phase

DOCS = PackageDocs(
    name="ai",
    features_intro=(
        "Typed model APIs with provider-specific capabilities; authentication, routing and wire "
        "adaptation stay separate."
    ),
    feature_categories=("Providers", "Model APIs", "Interop"),
    features=(
        Feature(
            "Registered providers",
            "Copilot and native Antigravity implementations; a protocol is not another provider.",
            "Providers",
        ),
        Feature(
            "Typed streams",
            "Frozen Context, Model and StreamOptions data with typed streaming events and final results.",
            "Model APIs",
        ),
        Feature(
            "Model capabilities",
            "Tool calling, search, embeddings and image output depend on the selected provider/model.",
            "Model APIs",
        ),
        Feature(
            "Cancellation and usage",
            "Cooperative stream cancellation plus token/cache/cost metadata where supplied.",
            "Model APIs",
        ),
        Feature(
            "Catalog and authentication",
            "Provider-specific login, model synchronization and local credential management.",
            "Providers",
        ),
        Feature(
            "Local protocol proxy",
            "Messages, token count, Responses and supported Chat Completions paths use the same provider runtime.",
            "Interop",
        ),
    ),
    roadmap_intro="Shipped behavior is separate from possible future work; future items are not release promises.",
    roadmap=(
        Phase(
            "Shipped",
            "Provider and protocol foundations",
            (
                "Copilot and Antigravity providers",
                "Typed streaming, cancellation and usage",
                "Explicit model/protocol compatibility checks",
                "Local API proxy and CLI model selection",
            ),
            done=True,
        ),
        Phase(
            "Future",
            "Further interoperability",
            (
                "Extend verified model/protocol coverage without inventing unsupported capabilities",
                "Improve diagnostics for provider-specific limitations",
            ),
        ),
    ),
)

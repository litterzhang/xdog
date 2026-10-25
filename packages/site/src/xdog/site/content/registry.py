"""Inert documentation registry shared by HTML and native JSON rendering."""
from __future__ import annotations

from xdog.site.content.docs import PackageDocs


def _load_package_docs() -> dict[str, PackageDocs]:
    """Registry of packages whose Features + Roadmap are Python-authored (all of them)."""
    from xdog.site.content.agent import DOCS as AGENT_DOCS
    from xdog.site.content.ai import DOCS as AI_DOCS
    from xdog.site.content.claw import DOCS as CLAW_DOCS
    from xdog.site.content.coding import DOCS as CODING_DOCS
    from xdog.site.content.eyes import DOCS as EYES_DOCS
    from xdog.site.content.flow_docs import DOCS as FLOW_DOCS
    from xdog.site.content.skills import DOCS as SKILLS_DOCS
    from xdog.site.content.tui import DOCS as TUI_DOCS

    return {
        d.name: d
        for d in (AI_DOCS, AGENT_DOCS, TUI_DOCS, CODING_DOCS, CLAW_DOCS, FLOW_DOCS, EYES_DOCS, SKILLS_DOCS)
    }


PACKAGE_DOCS: dict[str, PackageDocs] = _load_package_docs()



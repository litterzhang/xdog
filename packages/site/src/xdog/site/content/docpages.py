"""Shared markdown loader for per-package static pages.

Overview / Design / Reference / Examples are authored as markdown files under
``content/pages/<package>/<page>.md`` and rendered to HTML by one loader + one
route + one template. This mirrors the reference project ``huge-blog``: a fresh
``markdown.Markdown`` per render (the converter accumulates state), the
tables/fenced_code/codehilite/toc extensions, optional YAML frontmatter, and an
mtime cache so edits take effect without a restart.

Named ``docpages`` — not ``markdown`` — so it never shadows the ``markdown``
package it imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import frontmatter
import markdown
from markupsafe import Markup

# The four page kinds that exist as markdown. Any name/page outside these
# allow-lists is rejected before a path is built (path-traversal defense).
STATIC_PAGES: tuple[str, ...] = ("overview", "design", "reference", "examples")

_PAGES_DIR = Path(__file__).resolve().parent / "pages"


@dataclass(frozen=True)
class RenderedPage:
    """A rendered markdown page: safe HTML plus a display title."""

    html: Markup
    title: str


# (name, page) -> (source mtime, rendered). Re-rendered when the file changes.
_cache: dict[tuple[str, str], tuple[float, RenderedPage]] = {}


def _page_path(name: str, page: str) -> Path | None:
    """Resolve the markdown file for (name, page), or None if not allow-listed/missing.

    ``name`` must be a known package and ``page`` one of :data:`STATIC_PAGES`, so
    the path is assembled only from validated literals — never raw user input.
    """
    # Local import avoids a circular import at module load (packages imports nothing here).
    from xdog.site.content.packages import PACKAGES_BY_NAME

    if name not in PACKAGES_BY_NAME or page not in STATIC_PAGES:
        return None
    path = _PAGES_DIR / name / f"{page}.md"
    return path if path.is_file() else None


def render_markdown(text: str) -> Markup:
    """Render a markdown string to safe HTML with the site's extension set.

    A fresh ``markdown.Markdown`` per call — the converter accumulates state
    across uses. Shared by the package pages and the blog. ``Markup`` is safe
    because all rendered content is first-party authored files.
    """
    md = markdown.Markdown(extensions=["tables", "fenced_code", "codehilite", "toc"])
    return Markup(md.convert(text))


@dataclass(frozen=True)
class SourcePage:
    """Authored source, with no inferred deployment/publication timestamp."""

    title: str
    content: str
    updated_at: str | None


def _read(path: Path, page: str) -> SourcePage:
    post = frontmatter.loads(path.read_text(encoding="utf-8"))
    updated = post.get("updated_at")
    if isinstance(updated, (date, datetime)):
        updated = updated.isoformat()
    if isinstance(updated, str):
        try:
            datetime.fromisoformat(updated.replace("Z", "+00:00"))
        except ValueError:
            updated = None
    else:
        updated = None
    return SourcePage(str(post.get("title") or page.capitalize()), post.content, updated)


def read_page(name: str, page: str) -> SourcePage | None:
    """Read original Markdown only after the existing resource allowlist."""
    path = _page_path(name, page)
    return _read(path, page) if path is not None else None


def _render(path: Path, page: str) -> RenderedPage:
    source = _read(path, page)
    return RenderedPage(html=render_markdown(source.content), title=source.title)


def render_page(name: str, page: str) -> RenderedPage | None:
    """Return the rendered page for (name, page), or None when it does not exist.

    Cached by source mtime so authoring edits are picked up without a restart.
    """
    path = _page_path(name, page)
    if path is None:
        return None
    mtime = path.stat().st_mtime
    cached = _cache.get((name, page))
    if cached is not None and cached[0] == mtime:
        return cached[1]
    rendered = _render(path, page)
    _cache[(name, page)] = (mtime, rendered)
    return rendered

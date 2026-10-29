"""CSS-selected static HTML facts. No scripts, browser, follow-up fetches or pagination."""

from __future__ import annotations

from typing import Any

from xdog.eyes.errors import SourceError
from xdog.eyes.models import HttpSourceDef
from xdog.eyes.sources.documents import MAX_DEPTH, MAX_NODES, link
from xdog.eyes.sources.records import Record, record, unique


def records(body: bytes, source: HttpSourceDef, base_url: str, secret: str | None,
            encoding: str | None = None) -> tuple[Record, ...]:
    from bs4 import BeautifulSoup, Tag
    from bs4.element import PageElement

    markup: str | bytes = body
    if encoding:
        try:
            markup = body.decode(encoding)
        except (LookupError, UnicodeError) as exc:
            raise SourceError('HTML declared charset is invalid; baseline retained') from exc
    soup = BeautifulSoup(markup, "html.parser")
    if soup.contains_replacement_characters:
        raise SourceError('HTML decoding lost characters; baseline retained')
    # Body size is already bounded. Traverse once, not repeated whole-tree searches.
    todo: list[tuple[PageElement, int]] = [(soup, 0)]
    nodes = 0
    while todo:
        node, depth = todo.pop()
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise SourceError("HTML tree exceeds node/depth budget")
        if isinstance(node, Tag):
            todo.extend((child, depth + 1) for child in node.children)
    bases = soup.select('base[href]', limit=2)
    if len(bases) > 1:
        raise SourceError('Ambiguous HTML base URL')
    if bases:
        href = bases[0].get('href')
        if not isinstance(href, str):
            raise SourceError('HTML base URL is not scalar')
        base_url = link(href, base_url)
    for node in soup.select("script,style,noscript,template"):
        node.decompose()
    items = soup.select(source.items_selector or "", limit=source.max_records + 1)
    if not items and not source.allow_empty:
        raise SourceError("HTML items_selector matched nothing; baseline retained (allow_empty defaults false)")
    if len(items) > source.max_records:
        raise SourceError("HTML max_records exceeded; no partial snapshot")
    result = []
    for item in items:
        data: dict[str, Any] = {}
        for field in source.fields:
            selected = item.select(field.selector, limit=2) if field.selector else [item]
            if len(selected) != 1:
                raise SourceError("HTML field must match exactly one element; baseline retained")
            node = selected[0]
            if field.attribute is None:
                text = " ".join(node.get_text(" ", strip=True).split())
            else:
                value = node.get(field.attribute)
                if not isinstance(value, str) or not value.strip():
                    raise SourceError("HTML field attribute is missing or not scalar")
                text = value.strip()
                if field.attribute in {"href", "src"}:
                    text = link(text, base_url)
            if not text:
                raise SourceError("HTML required field is empty; baseline retained")
            data[field.name] = link(text, base_url) if field.name == 'url' else text
        key = data.get(source.key_field or "")
        if not isinstance(key, str):
            raise SourceError("HTML key_field is missing or not text")
        url = link(data["url"], base_url) if "url" in data else base_url
        result.append(record(key, data, url, secret=secret))
    return unique(tuple(result), source.max_records)

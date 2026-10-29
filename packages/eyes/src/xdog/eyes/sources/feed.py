"""RSS 2.0 / Atom 1.0 entry windows, parsed without DTD/entities or external resources."""

from __future__ import annotations

import io
from xml.etree.ElementTree import Element

from xdog.eyes.errors import SourceError
from xdog.eyes.models import FeedSourceDef
from xdog.eyes.sources.documents import MAX_DEPTH, MAX_NODES, link
from xdog.eyes.sources.records import Record, record, unique

ATOM = "{http://www.w3.org/2005/Atom}"
XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"


def text(node: Element | None) -> str:
    return "".join(node.itertext()).strip() if node is not None else ""


def records(body: bytes, source: FeedSourceDef, base_url: str, secret: str | None) -> tuple[Record, ...]:
    from defusedxml.ElementTree import iterparse

    root = None
    bases: dict[int, str] = {}
    stack: list[str] = []
    count = 0
    for event, node in iterparse(
        io.BytesIO(body), events=("start", "end"), forbid_dtd=True, forbid_entities=True, forbid_external=True
    ):
        if event == "start":
            count += 1
            if count > MAX_NODES or len(stack) >= MAX_DEPTH:
                raise SourceError("Feed tree exceeds node/depth budget")
            if root is None:
                root = node
            base = stack[-1] if stack else base_url
            base = link(node.attrib[XML_BASE], base) if node.attrib.get(XML_BASE) else base
            bases[id(node)] = base
            stack.append(base)
        else:
            stack.pop()
    if root is None:
        raise SourceError("Empty feed document")
    if root.tag == "rss" and root.get("version") == "2.0":
        channels = root.findall("channel")
        if len(channels) != 1:
            raise SourceError("RSS 2.0 requires one channel")
        entries = channels[0].findall("item")
        atom = False
    elif root.tag == ATOM + "feed":
        entries = root.findall(ATOM + "entry")
        atom = True
    else:
        raise SourceError("Expected RSS 2.0 or Atom 1.0 feed")
    if len(entries) > source.max_records:
        raise SourceError("Feed max_records exceeded; no partial window")
    result = []
    for entry in entries:
        base = bases[id(entry)]
        if atom:
            key = text(entry.find(ATOM + "id"))
            links = [node for node in entry.findall(ATOM + "link") if node.get("rel", "alternate") == "alternate"]
            if len(links) > 1:
                raise SourceError("Ambiguous Atom alternate links")
            url = link(links[0].get("href", ""), bases[id(links[0])]) if links else base_url
            if not key:
                raise SourceError("Atom entry requires a stable id")
            content = entry.find(ATOM + "content")
            data = {
                "id": key,
                "title": text(entry.find(ATOM + "title")),
                "url": url,
                "summary": text(entry.find(ATOM + "summary")),
                "content": text(content),
                "published": text(entry.find(ATOM + "published")),
                "updated": text(entry.find(ATOM + "updated")),
            }
            if content is not None and content.get("src"):
                # Preserve a reference as data; never fetch external Atom content.
                data["content_url"] = link(content.attrib["src"], bases[id(content)])
        else:
            href = text(entry.find("link"))
            url = link(href, base) if href else base_url
            guid = entry.find("guid")
            key = text(guid) or (url if href else "")
            if not href and guid is not None and guid.get('isPermaLink', 'true').lower() == 'true':
                if key.startswith(('https://', 'http://')):
                    url = link(key, base)
            if not key:
                raise SourceError("RSS item needs guid or a stable link; title is not an identity")
            data = {
                "id": key,
                "title": text(entry.find("title")),
                "url": url,
                "summary": text(entry.find("description")),
                "content": text(entry.find(CONTENT)),
                "published": text(entry.find("pubDate")),
                "updated": "",
            }
        result.append(record(key, data, url, secret=secret))
    return unique(tuple(result), source.max_records)

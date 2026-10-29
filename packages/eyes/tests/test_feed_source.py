import json
from dataclasses import replace

import httpx
import pytest
from xdog.eyes.detectors.records import RecordDetector
from xdog.eyes.models import FeedSourceDef, RecordDetectorDef
from xdog.eyes.sources.remote import RemoteSampler

from packages.eyes.tests.test_http_source import Transport


def rss(items):
    return (
        '<rss version="2.0"><channel>'
        + "".join(f"<item><guid>{key}</guid><title>{title}</title><link>/{key}</link></item>" for key, title in items)
        + "</channel></rss>"
    )


async def test_rss_updates_window_rollover_and_conditional_requests():
    seen = []
    responses = [
        httpx.Response(200, text=rss([("a", "Original")]), headers={"etag": "a"}),
        httpx.Response(200, text=rss([("a", "Edited"), ("b", "New")])),
        httpx.Response(200, text=rss([("b", "New")])),
        httpx.Response(304),
    ]

    def handle(request):
        seen.append(request)
        return responses.pop(0)

    sampler = RemoteSampler(Transport(handle))
    source = FeedSourceDef(id="feed", url="https://example.test/rss", max_records=2)
    detector = RecordDetector(RecordDetectorDef(id="changes", source="feed", removals=True), max_records=2)
    state = {}
    try:
        for index in range(4):
            value = await sampler.poll(source, state, now=index)
            assert value.snapshot.valid
            detector.observe(value.snapshot, now=index)
            events = detector.flush(now=index)
            if index == 1:
                assert {event.type for event in events} == {"record.updated", "record.created"}
            else:
                assert not events  # Leaving the feed window is NOT a removal.
            state = value.state
        assert seen[1].headers["if-none-match"] == "a"
        assert len(detector.checkpoint()["records"]) == 1
    finally:
        await sampler.close()


async def test_atom_id_xml_base_and_external_content_are_data_not_fetches():
    atom = """<feed xmlns="http://www.w3.org/2005/Atom" xml:base="https://example.test/blog/">
      <entry xml:base="posts/"><id>tag:example.test,2026:item</id><title>News</title>
        <link href="article"/><content src="body.xml"/><updated>2026-10-29</updated></entry></feed>"""
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, text=atom)

    sampler = RemoteSampler(Transport(handle))
    try:
        value = await sampler.poll(FeedSourceDef(id="f", url="https://example.test/feed"), {}, now=1)
        assert value.snapshot.valid and len(seen) == 1
        item = value.snapshot.records[0]
        assert item.key == "tag:example.test,2026:item" and item.url == "https://example.test/blog/posts/article"
        assert json.loads(item.value)["content_url"] == "https://example.test/blog/posts/body.xml"
    finally:
        await sampler.close()


@pytest.mark.parametrize(
    "body",
    [
        '<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss version="2.0"><channel>&x;</channel></rss>',
        '<!DOCTYPE rss [<!ENTITY x "bomb">]><rss version="2.0"><channel>&x;</channel></rss>',
        '<rss version="2.0"><channel>',
        "<html><body>login page</body></html>",
        '<rss version="2.0"><channel><item><title>No ID</title></item></channel></rss>',
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>No ID</title></entry></feed>',
        rss([("same", "A"), ("same", "B")]),
        '<rss version="2.0"><channel><item><link>javascript:alert(1)</link></item></channel></rss>',
        '<rss version="2.0"><channel>' + ("<nested>" * 140) + ("</nested>" * 140) + "</channel></rss>",
    ],
)
async def test_unsafe_or_invalid_xml_cannot_replace_baseline(body):
    sampler = RemoteSampler(Transport(lambda request: httpx.Response(200, text=body)))
    try:
        value = await sampler.poll(
            FeedSourceDef(id="f", url="https://example.test/feed"), {"last_success": 1, "etag": "old"}, now=2
        )
        assert not value.snapshot.valid and value.state["last_success"] == 1 and value.state["etag"] == "old"
        assert "root:" not in repr(value)
    finally:
        await sampler.close()


async def test_rss_link_fallback_and_record_budget():
    body = '<rss version="2.0"><channel><item><link>/article</link></item></channel></rss>'
    source = FeedSourceDef(id="f", url="https://example.test/feed")
    responses = [httpx.Response(200, text=body), httpx.Response(200, text=rss([("a", "A"), ("b", "B")]))]
    sampler = RemoteSampler(Transport(lambda request: responses.pop(0)))
    try:
        value = await sampler.poll(source, {}, now=1)
        assert value.snapshot.records[0].key == "https://example.test/article"
        failed = await sampler.poll(replace(source, max_records=1), value.state, now=2)
        assert not failed.snapshot.valid and failed.state["last_success"] == 1
    finally:
        await sampler.close()

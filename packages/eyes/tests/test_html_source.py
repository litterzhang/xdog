import json
from dataclasses import replace

import httpx
import pytest
from xdog.eyes.detectors.records import RecordDetector
from xdog.eyes.errors import ObserverValidationError
from xdog.eyes.loader import definition_data, parse
from xdog.eyes.models import RecordDetectorDef
from xdog.eyes.sources.remote import RemoteSampler

from packages.eyes.tests.test_http_source import Transport
from packages.eyes.tests.test_remote_config import external


def html_source():
    return {
        "id": "outside",
        "type": "http",
        "mode": "html",
        "url": "https://example.test/announcements",
        "items_selector": ".list li",
        "fields": {"title": {"selector": "a", "text": True}, "url": {"selector": "a", "attribute": "href"}},
        "key_field": "url",
    }


def definition(tmp_path, observer_data, source=None):
    return parse(external(observer_data, source or html_source()), base_dir=tmp_path)


async def test_html_extracts_only_selected_content_and_resolves_links(tmp_path, observer_data):
    source = definition(tmp_path, observer_data).sources[0]
    bodies = [
        '<meta charset=utf-8><header>clock 1</header><ul class=list><li><a href="/a">  招聘  公告 </a></li></ul>',
        '<meta charset=utf-8><header>clock 2</header><ul class=list><li><a href="/a">招聘 公告</a></li></ul>',
        '<meta charset=utf-8><ul class=list><li><a href="/a">修订公告<script>do_not_run()</script></a></li></ul>',
    ]
    sampler = RemoteSampler(Transport(lambda request: httpx.Response(200, content=bodies.pop(0).encode())))
    detector = RecordDetector(RecordDetectorDef(id="d", source="outside"), max_records=256)
    state = {}
    try:
        for index in range(3):
            value = await sampler.poll(source, state, now=index)
            assert value.snapshot.valid
            state = value.state
            detector.observe(value.snapshot, now=index)
            events = detector.flush(now=index)
            assert len(events) == (1 if index == 2 else 0)
        assert value.snapshot.records[0].key == "https://example.test/a"
        assert json.loads(value.snapshot.records[0].value)["title"] == "修订公告"
    finally:
        await sampler.close()


@pytest.mark.parametrize(
    "body",
    [
        "<div>redesigned page</div>",
        "<ul class=list><li><a>missing attribute</a></li></ul>",
        "<ul class=list><li><a href=/a>A</a><a href=/b>B</a></li></ul>",
        '<ul class=list><li><a href="javascript:alert(1)">A</a></li></ul>',
        "<ul class=list><li><a href=/a>A</a></li><li><a href=/a>B</a></li></ul>",
    ],
)
async def test_html_shape_failure_never_commits_empty_or_partial_baseline(tmp_path, observer_data, body):
    source = definition(tmp_path, observer_data).sources[0]
    sampler = RemoteSampler(Transport(lambda request: httpx.Response(200, text=body, headers={"etag": "bad"})))
    try:
        result = await sampler.poll(source, {"last_success": 1, "etag": "old"}, now=2)
        assert not result.snapshot.valid and result.state["last_success"] == 1 and result.state["etag"] == "old"
    finally:
        await sampler.close()


async def test_empty_opt_in_and_explicit_charset(tmp_path, observer_data):
    source = definition(tmp_path, observer_data).sources[0]
    replies = [
        httpx.Response(200, text="<div></div>"),
        httpx.Response(
            200,
            content="<ul class=list><li><a href=/a>招聘</a></li></ul>".encode("gbk"),
            headers={"content-type": "text/html; charset=gbk"},
        ),
    ]
    sampler = RemoteSampler(Transport(lambda request: replies.pop(0)))
    try:
        empty = await sampler.poll(replace(source, allow_empty=True), {}, now=1)
        assert empty.snapshot.valid and not empty.snapshot.records
        chinese = await sampler.poll(source, {}, now=2)
        assert json.loads(chinese.snapshot.records[0].value)["title"] == "招聘"
    finally:
        await sampler.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("items_selector", "["),
        ("items_selector", ""),
        ("fields", {}),
        ("fields", {"title": {"selector": "a", "text": True, "attribute": "href"}}),
        ("fields", {"title": {"selector": "a", "text": False}}),
        ("key_field", "unknown"),
        ("allow_empty", "yes"),
        ("pointer", "/items"),
        ("script", "do_anything()"),
        ("compare_fields", ["/x", "/x"]),
    ],
)
def test_html_configuration_is_finite_and_validated(tmp_path, observer_data, field, value):
    source = html_source()
    source[field] = value
    with pytest.raises(ObserverValidationError):
        definition(tmp_path, observer_data, source)


def test_normalized_html_and_legacy_definitions_are_stable(tmp_path, observer_data):
    current = definition_data(definition(tmp_path, observer_data))
    assert definition_data(parse(current, base_dir=tmp_path)) == current
    for source in [None, {"id": "outside", "type": "github", "repository": "o/r", "resource": "issues"}]:
        legacy = definition_data(parse(external(observer_data, source), base_dir=tmp_path))
        assert "compare_fields" not in legacy["sources"][0]
        assert "items_selector" not in legacy["sources"][0] and "include_prerelease" not in legacy["sources"][0]
        assert definition_data(parse(legacy, base_dir=tmp_path)) == legacy


async def test_redirected_html_uses_final_response_url_as_link_base(tmp_path, observer_data):
    seen = []

    def handle(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(302, headers={"location": "/new/list/"})
        return httpx.Response(200, text='<ul class=list><li><a href="item">News</a></li></ul>')

    sampler = RemoteSampler(Transport(handle))
    try:
        value = await sampler.poll(definition(tmp_path, observer_data).sources[0], {}, now=1)
        assert value.snapshot.records[0].url == "https://example.test/new/list/item"
    finally:
        await sampler.close()


async def test_parser_cancel_settles_worker_without_late_result():
    import asyncio
    import threading

    from xdog.eyes.sources.documents import parse_owned

    entered, release, exited = threading.Event(), threading.Event(), threading.Event()

    def parser():
        entered.set()
        assert release.wait(5)
        exited.set()
        return "must not be published"

    task = asyncio.create_task(parse_owned(parser))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert exited.is_set()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_html_base_element_is_resolved_without_fetching_it(tmp_path, observer_data):
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(200, text='<base href="https://content.test/posts/">'
                              '<ul class=list><li><a href="item">News</a></li></ul>')
    sampler = RemoteSampler(Transport(handle))
    try:
        value = await sampler.poll(definition(tmp_path, observer_data).sources[0], {}, now=1)
        assert value.snapshot.records[0].url == 'https://content.test/posts/item' and len(seen) == 1
    finally:
        await sampler.close()


@pytest.mark.parametrize('encoding', ['not-a-real-charset', 'utf-8'])
async def test_bad_declared_charset_is_failure_not_garbled_title(tmp_path, observer_data, encoding):
    body = b'<ul class=list><li><a href=/a>\xff\xfe</a></li></ul>'
    sampler = RemoteSampler(Transport(lambda request: httpx.Response(200, content=body,
        headers={'content-type': 'text/html; charset='+encoding})))
    try:
        value = await sampler.poll(definition(tmp_path, observer_data).sources[0], {'last_success': 1}, now=2)
        assert not value.snapshot.valid and value.state['last_success'] == 1
    finally:
        await sampler.close()


async def test_redirected_page_does_not_reuse_another_resource_etag(tmp_path, observer_data):
    seen = []
    def handle(request):
        seen.append(request)
        if request.url.path == '/announcements':
            target = '/one/' if len(seen) == 1 else '/two/'
            return httpx.Response(302, headers={'location': target})
        assert 'if-none-match' not in request.headers
        return httpx.Response(200, text='<ul class=list><li><a href="item">News</a></li></ul>',
                              headers={'etag': 'same-etag'})
    sampler = RemoteSampler(Transport(handle))
    try:
        source = definition(tmp_path, observer_data).sources[0]
        one = await sampler.poll(source, {}, now=1)
        two = await sampler.poll(source, one.state, now=2)
        assert one.snapshot.records[0].url == 'https://example.test/one/item'
        assert two.snapshot.records[0].url == 'https://example.test/two/item'
        assert 'etag' not in two.state
    finally:
        await sampler.close()

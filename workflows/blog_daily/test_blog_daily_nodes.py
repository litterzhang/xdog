"""Offline regression tests; no network, provider calls, or real digest writes."""

import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("daily_nodes_under_test", Path(__file__).with_name("blog_daily_nodes.py"))
nodes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nodes)

URL = nodes.ORIGIN + "/2026/01/27/example/"
HTML = """<title>A &amp; B</title><nav>Ignore menu</nav>
<span itemprop="datePublished" datetime="2026-01-27"></span>
<div class="post-content"><p>Useful <strong>content</strong>.</p><br>
<script>Ignore script</script><p>Second paragraph.</p></div><footer>Ignore footer</footer>"""


def setup_site(monkeypatch, html=HTML):
    def fetch(url):
        if url.endswith("robots.txt"):
            return "User-agent: *\nAllow: /"
        if url.endswith("sitemap.xml"):
            return f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{URL}</loc></url></urlset>'
        return html

    monkeypatch.setattr(nodes, "fetch", fetch)
    monkeypatch.setattr(nodes.time, "sleep", lambda _: None)


def test_article_extraction():
    parser = nodes.ArticleParser()
    parser.feed(HTML)
    title, text, published = parser.result()
    assert title == "A & B"
    assert "Useful content." in text and "Second paragraph." in text
    assert "Ignore" not in text
    assert published == "2026-01-27"


def test_initial_then_unchanged_then_updated(tmp_path, monkeypatch):
    setup_site(monkeypatch)
    crawl = nodes.crawl(None, str(tmp_path), 100, 6000)
    assert crawl["first_run"] and len(crawl["articles"]) == 1
    assert not (tmp_path / "seen.json").exists()  # Failed agent must not lose posts.
    result = nodes.publish(None, crawl, "Initial archive summary.", str(tmp_path))
    assert result["changed_posts"] == 1
    assert Path(result["summary_path"]).exists()
    unchanged = nodes.crawl(None, str(tmp_path), 100, 6000)
    assert unchanged["articles"] == [] and not unchanged["first_run"]
    repeated = nodes.publish(None, unchanged, "No updates.", str(tmp_path))
    assert repeated["daily_summary"] == result["daily_summary"]
    setup_site(monkeypatch, HTML.replace("Useful", "Updated"))
    updated = nodes.crawl(None, str(tmp_path), 100, 6000)
    assert updated["articles"][0]["change"] == "updated"


def test_all_failures_do_not_report_no_updates(tmp_path, monkeypatch):
    setup_site(monkeypatch, "<html>No body</html>")
    with pytest.raises(ValueError, match="All article fetches failed"):
        nodes.crawl(None, str(tmp_path), 100, 6000)
    assert not (tmp_path / "seen.json").exists()


def test_robots_disallow(tmp_path, monkeypatch):
    monkeypatch.setattr(nodes, "fetch", lambda _: "User-agent: *\nDisallow: /")
    with pytest.raises(ValueError, match="robots.txt disallows"):
        nodes.crawl(None, str(tmp_path), 100, 6000)


def test_truncated_input_hashes_full_body(tmp_path, monkeypatch):
    setup_site(monkeypatch)
    crawl = nodes.crawl(None, str(tmp_path), 100, 4)
    assert crawl["articles"][0]["truncated"]
    nodes.publish(None, crawl, "Summary.", str(tmp_path))
    setup_site(monkeypatch, HTML.replace("Second paragraph", "Changed ending"))
    assert len(nodes.crawl(None, str(tmp_path), 100, 4)["articles"]) == 1


def test_empty_summary_does_not_commit(tmp_path):
    with pytest.raises(ValueError, match="empty daily summary"):
        nodes.publish(None, {}, " ", str(tmp_path))
    assert not (tmp_path / "seen.json").exists()


def test_new_day_no_updates_writes_daily_file(tmp_path):
    crawl = {"date": "2026-10-10", "articles": [], "warnings": [], "fingerprints": {}}
    result = nodes.publish(None, crawl, "No new or updated posts detected.", str(tmp_path))
    assert "No new or updated posts" in Path(result["summary_path"]).read_text()
    assert json.loads((tmp_path / "seen.json").read_text()) == {}

"""Standard-library crawler and publication nodes for the daily blog digest."""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

ORIGIN = "https://blog.942295.xyz"
USER_AGENT = "XdogDailyDigest/1.0"
MAX_BYTES = 2_000_000


def fetch(url):
    # Encode Unicode paths without double-encoding existing escapes.
    parts = urllib.parse.urlsplit(url)
    url = urllib.parse.urlunsplit(parts._replace(path=urllib.parse.quote(parts.path, safe="/%")))
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=20) as response:
                if urllib.parse.urlsplit(response.url).netloc != urllib.parse.urlsplit(ORIGIN).netloc:
                    raise ValueError("Refusing off-site redirect")
                data = response.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise ValueError(f"Response exceeds {MAX_BYTES} bytes: {url}")
                return data.decode(response.headers.get_content_charset() or "utf-8")
        except (urllib.error.URLError, TimeoutError):
            if attempt:
                raise
            time.sleep(1)


class ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = []
        self.content = []
        self.in_title = False
        self.body_depth = 0
        self.skip_depth = 0
        self.published = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self.in_title = True
        if attrs.get("itemprop") == "datePublished":
            self.published = attrs.get("datetime", "")
        if self.body_depth:
            if tag not in {"br", "hr", "img", "input", "meta", "link", "source", "wbr"}:
                self.body_depth += 1
            if tag in {"script", "style"}:
                self.skip_depth += 1
            if tag in {"p", "li", "h1", "h2", "h3", "h4", "pre", "br", "tr"}:
                self.content.append("\n")
        elif attrs.get("itemprop") == "articleBody" or "post-content" in attrs.get("class", "").split():
            self.body_depth = 1

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if self.body_depth:
            if tag in {"script", "style"} and self.skip_depth:
                self.skip_depth -= 1
            self.body_depth -= 1
            if tag in {"p", "li", "pre", "tr"}:
                self.content.append("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        if self.body_depth and not self.skip_depth:
            self.content.append(data)

    def result(self):
        text = re.sub(r"[ \t]+", " ", "".join(self.content))
        text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
        if not text:
            raise ValueError("No article body found; refusing to summarize navigation HTML")
        return "".join(self.title).strip(), text, self.published


def atomic_write(path, text):
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def crawl(ctx, output_dir, max_posts, max_chars):
    root = Path(output_dir).expanduser().resolve()
    state_path = root / "seen.json"
    seen = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    first_run = not state_path.exists()
    robots = urllib.robotparser.RobotFileParser()
    try:
        robots.parse(fetch(ORIGIN + "/robots.txt").splitlines())
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        robots.parse(["User-agent: *", "Allow: /"])
    delay = max(0.3, robots.crawl_delay(USER_AGENT) or 0)

    def allowed_fetch(url):
        if not robots.can_fetch(USER_AGENT, url):
            raise ValueError(f"robots.txt disallows {url}")
        time.sleep(delay)
        return fetch(url)

    sitemap = ET.fromstring(allowed_fetch(ORIGIN + "/sitemap.xml"))
    urls = sorted(
        {
            loc.text.strip()
            for loc in sitemap.findall(".//{*}loc")
            if loc.text
            and urllib.parse.urlsplit(loc.text.strip()).netloc == "blog.942295.xyz"
            and urllib.parse.urlsplit(loc.text.strip()).scheme == "https"
            and re.match(r"^/\d{4}/\d{2}/\d{2}/[^/]+/?$", urllib.parse.urlsplit(loc.text.strip()).path)
        },
        reverse=True,
    )
    if not urls:
        raise ValueError("Sitemap contains no blog posts")
    warnings = []
    if len(urls) > max_posts:
        warnings.append(f"Only the newest {max_posts} of {len(urls)} discovered posts were checked.")
    articles, fingerprints = [], {}
    checked = 0
    for url in urls[:max_posts]:
        try:
            parser = ArticleParser()
            parser.feed(allowed_fetch(url))
            title, text, published = parser.result()
            fingerprint = hashlib.sha256((title + "\n" + published + "\n" + text).encode()).hexdigest()
            fingerprints[url] = fingerprint
            checked += 1
            if seen.get(url) != fingerprint:
                articles.append(
                    {
                        "title": title,
                        "url": url,
                        "published": published,
                        "change": "updated" if url in seen else "newly discovered",
                        "text": text[:max_chars],
                        "truncated": len(text) > max_chars,
                    }
                )
        except (ValueError, urllib.error.URLError, TimeoutError) as exc:
            warnings.append(f"{url}: {exc}")
    if not checked:
        raise ValueError("All article fetches failed: " + "; ".join(warnings))
    return {
        "date": datetime.now(timezone(timedelta(hours=8))).date().isoformat(),
        "source": ORIGIN,
        "first_run": first_run,
        "discovered": len(urls),
        "checked": checked,
        "articles": articles,
        "warnings": warnings,
        "fingerprints": fingerprints,
    }


def publish(ctx, crawl, daily_summary, output_dir):
    if not daily_summary.strip():
        raise ValueError("Agent returned an empty daily summary")
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    date = crawl["date"]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError("Invalid digest date")
    report = f"# Daily Blog Summary — {date}\n\nSource: {ORIGIN}\n\n" + daily_summary.strip() + "\n"
    if crawl["warnings"]:
        report += "\n## Crawl warnings\n" + "\n".join("- " + w for w in crawl["warnings"]) + "\n"
    # Preserve the day's earlier digest on a repeated no-change run.
    dated = root / f"{date}.md"
    if not crawl["articles"] and dated.exists():
        report = dated.read_text(encoding="utf-8")
    else:
        atomic_write(dated, report)
        atomic_write(root / f"{date}.json", json.dumps(crawl, ensure_ascii=False, indent=2) + "\n")
    atomic_write(root / "latest.md", report)
    state_path = root / "seen.json"
    seen = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    seen.update(crawl["fingerprints"])
    # Commit fingerprints only AFTER successful summarization and publication.
    atomic_write(state_path, json.dumps(seen, ensure_ascii=False, indent=2) + "\n")
    return {"daily_summary": report, "summary_path": str(dated), "changed_posts": len(crawl["articles"])}

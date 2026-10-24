"""The landing page explains value before the catalog, without changing docs UI."""
from __future__ import annotations

import ast
import html
import re
from html.parser import HTMLParser

from xdog.site import create_app
from xdog.site.content.packages import PACKAGES


class Page(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.links: list[str] = []
        self.styles: list[str] = []
        self.headings = 0
        self.feed(source)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if identity := values.get('id'):
            self.ids.add(identity)
        if tag == 'a' and (href := values.get('href')):
            self.links.append(href)
        if tag == 'link' and values.get('rel') == 'stylesheet' and (href := values.get('href')):
            self.styles.append(href)
        self.headings += tag == 'h1'


def test_homepage_leads_with_outcomes_and_clear_next_steps():
    client = create_app().test_client()
    source = client.get('/').get_data(as_text=True)
    page = Page(source)
    assert page.headings == 1
    assert 'Stop repeating' in source and 'Build the process.' in source
    assert source.index('id="home-start"') < source.index('id="use-cases"') < source.index('id="packages-title"')
    required = {'#get-started', '/packages/coding/overview', '/packages/flow/examples', '/packages/agent/reference'}
    assert required <= set(page.links)
    assert 'Illustrative workflow' in source and 'not a live run' in source
    assert 'not a live run or an included review recipe' in source
    assert 'Source SDK excerpt' in source and 'matching your installed release' in source
    assert 'no hosted control plane or external database' not in source  # Lead with value, not a dependency list.


def test_homepage_links_and_in_page_navigation_resolve():
    client = create_app().test_client()
    page = Page(client.get('/').get_data(as_text=True))
    for href in page.links:
        if href.startswith('#'):
            assert href[1:] in page.ids, href
        elif href.startswith('/') and not href.startswith('//'):
            assert client.get(href).status_code == 200, href
    for package in PACKAGES:
        assert any(path in page.links for path in (f'/packages/{package.name}',
                                                   f'/packages/{package.name}/overview'))
        assert package.pypi_url in page.links
    assert not any('/packages/site' in href for href in page.links)


def test_homepage_reuses_the_existing_hack_theme_and_site_layout():
    client = create_app().test_client()
    home = client.get('/').get_data(as_text=True)
    docs = client.get('/packages/agent/reference').get_data(as_text=True)
    home_styles = Page(home).styles
    assert home_styles == Page(docs).styles
    assert len(home_styles) == 2
    assert any('/css/hack.css?' in href for href in home_styles)
    assert any('/css/site.css?' in href for href in home_styles)
    for source in (home, docs):
        assert 'class="hack"' in source
        assert 'class="header grid"' in source
        assert 'class="left"' in source and 'class="right"' in source
    assert '<style' not in home[home.index('id="content-body"'):]
    assert 'style=' not in home[home.index('id="content-body"'):]
    assert 'class="card -5of12 cell"' in home
    assert 'class="btn btn-ghost btn-primary"' in home
    for path in home_styles:
        response = client.get(path)
        assert response.status_code == 200
        assert response.mimetype == 'text/css'


def test_homepage_sdk_excerpt_is_valid_python_and_uses_the_current_repo_api():
    source = create_app().test_client().get('/').get_data(as_text=True)
    code = re.search(r'<div id="sdk-example">.*?<pre><code>(.*?)</code></pre>', source, re.S)
    assert code is not None
    snippet = html.unescape(re.sub(r'<[^>]+>', '', code[1]))
    ast.parse(snippet)
    assert 'ToolRepo' in snippet and 'SkillRepo' in snippet
    assert '.select(["flow"]).open()' in snippet
    assert 'before_tool_call=permission_hook' in snippet
    assert 'async for event in await agent.prompt(task)' in snippet
    assert 'enabled_tools' not in snippet and 'enabled_skills' not in snippet

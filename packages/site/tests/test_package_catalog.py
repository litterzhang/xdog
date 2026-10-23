"""The website must describe the workspace, not a separately drifting package list."""
from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path
from xml.etree import ElementTree

import pytest
from xdog.site import create_app
from xdog.site.blueprints.main import PACKAGE_DOCS
from xdog.site.content.docpages import render_page
from xdog.site.content.packages import LAYERS, PACKAGES

ROOT = Path(__file__).resolve().parents[3]


def test_catalog_covers_public_workspace_distributions_and_real_cli():
    workspace = tomllib.loads((ROOT / 'pyproject.toml').read_text())['tool']['uv']['workspace']['members']
    projects = [tomllib.loads((ROOT / member / 'pyproject.toml').read_text())['project'] for member in workspace]
    public_projects = {project['name'] for project in projects if project['name'] != 'xdog-site'}
    assert {package.dist for package in PACKAGES} == public_projects
    assert len({package.name for package in PACKAGES}) == len(PACKAGES) == 8
    for package in PACKAGES:
        project, = (project for project in projects if project['name'] == package.dist)
        commands = project.get('scripts', {})
        assert package.cli in commands if commands else package.cli == ''
        assert (ROOT / 'packages' / package.name / 'README.md').is_file()
    assert 'site' not in {package.name for package in PACKAGES}
    assert set(PACKAGE_DOCS) == {package.name for package in PACKAGES}
    layered = [name for _, names in LAYERS for name in names]
    assert len(layered) == len(set(layered)) == 8
    assert set(layered) == {package.name for package in PACKAGES}


@pytest.mark.parametrize('name', [package.name for package in PACKAGES])
def test_all_catalog_pages_navigation_and_sitemap_are_complete(name):
    client = create_app().test_client()
    pages = ('overview', 'design', 'features', 'reference', 'roadmap')
    for page in pages:
        response = client.get(f'/packages/{name}/{page}')
        assert response.status_code == 200, (name, page)
        body = response.get_data(as_text=True)
        for package in PACKAGES:
            assert f'href="/packages/{package.name}/reference"' in body
        assert 'href="None"' not in body
        assert 'https://pypi.org/project/xdog-site/' not in body
    for page in ('overview', 'design', 'reference'):
        assert render_page(name, page) is not None
    paths = {element.text for element in ElementTree.fromstring(client.get('/sitemap.xml').data).iter()
             if element.tag.endswith('}loc')}
    for page in ('design', 'features', 'reference', 'roadmap'):
        assert f'https://xdog.942295.xyz/packages/{name}/{page}' in paths


def test_home_faq_and_footer_describe_current_boundaries():
    client = create_app().test_client()
    home = client.get('/').get_data(as_text=True)
    faq = client.get('/faq').get_data(as_text=True)
    assert 'Python 3.12+' in home and 'Linux' in home
    assert 'SQLite' in home and 'authenticated' in home
    assert 'no account' not in home.lower() and 'no database' not in home.lower()
    assert 'MIT licensed' not in home and 'AGPL-3.0-or-later' in home
    assert 'Antigravity' in faq and 'Copilot' in faq
    assert 'Python 3.11' not in faq and 'Bedrock' not in faq
    assert 'is MIT-licensed' not in faq
    assert 'result_schema' in client.get('/packages/agent/reference').get_data(as_text=True)
    assert 'McpRegistry' in client.get('/packages/agent/reference').get_data(as_text=True)
    assert 'McpManager' not in client.get('/packages/claw/reference').get_data(as_text=True)
    assert 'activated by default' in client.get('/packages/skills').get_data(as_text=True)
    assert 'not a sandbox' in client.get('/havefun/flow').get_data(as_text=True)


def test_rendered_authored_local_links_resolve_without_missing_package_pages():
    client = create_app().test_client()
    visited = ['/', '/packages/all', '/faq']
    visited += [f'/packages/{package.name}/{page}' for package in PACKAGES
                for page in ('overview', 'design', 'features', 'reference', 'roadmap')]
    for path in visited:
        body = client.get(path).get_data(as_text=True)
        for href in re.findall(r'href="([^"#?]+)', body):
            if href.startswith('/') and not href.startswith('//'):
                assert client.get(href).status_code == 200, (path, href)


def test_static_catalog_renders_without_importing_documented_business_packages():
    script = '''
import importlib.abc
import sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'xdog.ai', 'xdog.agent', 'xdog.flow', 'xdog.eyes',
                        'xdog.skills', 'xdog.coding', 'xdog.claw', 'xdog.tui'}:
            raise AssertionError('Static site must not import business runtime: ' + fullname)
sys.meta_path.insert(0, Block())
from xdog.site import create_app
from xdog.site.content.packages import PACKAGES
client = create_app().test_client()
for path in ['/', '/packages/all', '/faq', '/sitemap.xml'] + [
    f'/packages/{package.name}/{page}' for package in PACKAGES
    for page in ('overview', 'design', 'features', 'reference', 'roadmap')]:
    assert client.get(path).status_code == 200, path
'''
    result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_provider_docs_follow_registered_provider_ids():
    from xdog.ai.types import ProviderType

    client = create_app().test_client()
    body = client.get('/packages/ai/reference').get_data(as_text=True).lower()
    faq = client.get('/faq').get_data(as_text=True).lower()
    for provider in ProviderType:
        assert provider.value in body and provider.value in faq, provider.value


def test_site_itself_has_no_public_package_pages_or_discovery_links():
    client = create_app().test_client()
    for suffix in ('', '/overview', '/design', '/features', '/reference', '/roadmap', '/examples'):
        assert client.get('/packages/site' + suffix).status_code == 404, suffix
    for path in ('/', '/packages/all', '/faq', '/sitemap.xml'):
        assert '/packages/site' not in client.get(path).get_data(as_text=True), path

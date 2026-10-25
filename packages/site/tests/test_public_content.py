"""Public reading exports must remain inert and separate from trusted execution."""
from __future__ import annotations

from xdog.site import create_app
from xdog.site.content.packages import PACKAGES


def test_native_catalog_home_and_conditional_reads():
    client = create_app().test_client()
    home = client.get('/api/public/v1/home')
    assert home.status_code == 200
    data = home.json['data']
    assert data['schema_version'] == 1
    assert data['featured'] == ['coding', 'flow', 'claw']
    assert 'pip install xdog-coding' in data['quickstart']['content']
    assert data['updated_at'] is None, 'Do not invent publication dates from file mtime'
    assert len(data['source_revision']) == 64
    assert client.get('/api/public/v1/home', headers={'If-None-Match': home.headers['ETag']}).status_code == 304
    catalog = client.get('/api/public/v1/packages').json['data']['items']
    assert {row['name'] for row in catalog} == {pkg.name for pkg in PACKAGES}
    assert len(catalog) == 8
    assert all('overview' in row['sections'] for row in catalog)
    assert 'examples' not in next(row for row in catalog if row['name'] == 'coding')['sections']


def test_original_markdown_structured_sections_and_faq():
    client = create_app().test_client()
    overview = client.get('/api/public/v1/packages/flow/overview').json['data']
    assert overview['content_type'] == 'markdown'
    assert '```' in overview['content']
    assert overview['canonical_path'] == '/packages/flow/overview'
    features = client.get('/api/public/v1/packages/flow/features').json['data']
    assert features['content_type'] == 'features' and features['items']
    roadmap = client.get('/api/public/v1/packages/flow/roadmap').json['data']
    assert roadmap['content_type'] == 'roadmap'
    assert all(isinstance(phase['done'], bool) for phase in roadmap['items'])
    faq = client.get('/api/public/v1/faq').json['data']
    assert faq['content_type'] == 'faq' and faq['items'][0]['question']


def test_no_execution_blog_or_unbounded_file_exports():
    client = create_app().test_client()
    for path in ('packages/site/overview', 'packages/flow/missing', 'packages/ai/examples', 'packages/flow/examples',
                 'packages/../AGENTS.md', 'posts', 'havefun/flow/run'):
        assert client.get('/api/public/v1/' + path).status_code == 404, path
    assert client.post('/api/public/v1/home', json={'run': True}).status_code == 405

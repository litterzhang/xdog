
from xdog.eyes.loader import parse

from packages.eyes.tests.remote_config_helpers import external


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

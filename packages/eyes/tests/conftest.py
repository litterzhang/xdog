import pytest


@pytest.fixture
def observer_data():
    return {
        "version": 1,
        "name": "review",
        "sources": [{"id": "files", "type": "file", "root": "."}],
        "detectors": [{"id": "changes", "type": "file.change", "source": "files"}],
        "handlers": [{"id": "notify", "type": "session.notify", "target": "current",
                      "instruction": "Review tests"}],
        "routes": [{"id": "review", "detector": "changes", "handler": "notify",
                    "event_types": ["file.created", "file.modified", "file.deleted"]}],
    }

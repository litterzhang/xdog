"""Keep provider-sync catalog side effects out of the user's Codex setup."""
from pathlib import Path

import pytest
from xdog.ai import codex_catalog


@pytest.fixture(autouse=True)
def isolated_codex_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Provider sync refreshes an opted-in catalog even when vendor I/O is mocked.
    # Isolate the destination for every AI test, including real-client tests.
    monkeypatch.setattr(codex_catalog, "catalog_path", lambda: tmp_path / "codex_models.json")

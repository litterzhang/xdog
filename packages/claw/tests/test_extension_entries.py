from __future__ import annotations

from pathlib import Path

import pytest
from xdog.agent.extensions.state import validate_entries
from xdog.claw.core.persistence.transcript_store import TranscriptStore

FIXTURE = Path(__file__).parents[2] / "agent" / "tests" / "fixtures" / "extensions"


def test_extension_entry_sidecar_corruption_does_not_silently_reset_plugin_state(tmp_path):
    store = TranscriptStore(tmp_path, owner_group="main")
    session = store.create_session("main")
    for value in ("null", "[]", "{broken"):
        (tmp_path / f"{session.session_id}.extensions.json").write_text(value)
        recovered = TranscriptStore(tmp_path, owner_group="main")
        with pytest.raises(ValueError):
            validate_entries(recovered.get_extension_entries(session.session_id))

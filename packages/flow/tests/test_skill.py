"""The flow skill pack (skill/) must stay self-contained and in sync.

The skill is installable into a coding-agent CLI's skill dir; it bundles the
example JSON files it references so it works standalone.  This test guards against
drift: every skill/examples/*.json must byte-match the canonical examples/*.json,
and every example the SKILL.md names must exist in the pack.
"""

from __future__ import annotations

import pathlib
import re

_ROOT = pathlib.Path(__file__).parent.parent
_SKILL = _ROOT / "src" / "xdog" / "flow" / "skills" / "flow"
_EXAMPLES = _ROOT / "examples"


def test_skill_examples_match_canonical() -> None:
    """Each skill/examples/*.json is a verbatim copy of the canonical example."""
    skill_examples = sorted((_SKILL / "examples").glob("*.json"))
    assert skill_examples, "skill/examples/ should not be empty"
    for p in skill_examples:
        canonical = _EXAMPLES / p.name
        assert canonical.exists(), f"skill bundles {p.name} but examples/ has no such file"
        assert p.read_text(encoding="utf-8") == canonical.read_text(encoding="utf-8"), (
            f"skill/examples/{p.name} drifted from examples/{p.name}"
        )


def test_skill_referenced_examples_are_bundled() -> None:
    """Every examples/<x>.json named in SKILL.md is present in the skill pack."""
    text = (_SKILL / "SKILL.md").read_text(encoding="utf-8")
    referenced = set(re.findall(r"examples/([a-z_]+\.json)", text))
    bundled = {p.name for p in (_SKILL / "examples").glob("*.json")}
    missing = referenced - bundled
    assert not missing, f"SKILL.md references {missing} but they are not bundled under skill/examples/"

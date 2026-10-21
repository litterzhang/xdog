"""Exercise the documented explicit-loading example without providers or Claw."""
from __future__ import annotations

from pathlib import Path

import pytest
from xdog.agent.skills import SkillManager

from packages.agent.examples.load_skills import demonstrate, demonstrate_loading


def write_skill(root: Path, slug: str, *, turn: bool = False) -> Path:
    directory = root / slug
    directory.mkdir(parents=True)
    scope = "metadata:\n  scope: turn\n" if turn else ""
    (directory / "SKILL.md").write_text(
        f"---\nname: {slug}\ndescription: Example procedure\n{scope}---\n"
        "Use this distinct example instruction: ${SKILL_DIR}/examples/input.json\n",
        encoding="utf-8",
    )
    return directory


@pytest.mark.parametrize("packaged", [False, True])
@pytest.mark.parametrize("turn", [False, True])
async def test_example_activates_and_unloads_at_actual_model_boundary(
    tmp_path: Path, packaged: bool, turn: bool,
) -> None:
    root = tmp_path / "source"
    directory = write_skill(root, "procedure", turn=turn)
    shared = tmp_path / "unused" if packaged else root
    manager = SkillManager(shared_dir=shared, packaged={"procedure": directory} if packaged else {})
    result = await demonstrate(manager, "procedure")
    assert result == {
        "slug": "procedure", "scope": "turn" if turn else "session", "packaged": packaged,
        "skill_directory": str(directory),
        "available_skills": ["procedure"], "model_requests": 2, "tool_names": [],
        "loaded_at_model_entry": True, "unloaded_at_model_entry": True, "base_prompt_preserved": True,
    }
    if packaged:
        assert not shared.exists()


async def test_example_validates_missing_skill_instead_of_silent_activation(tmp_path: Path) -> None:
    manager = SkillManager(shared_dir=tmp_path / "missing", packaged={})
    with pytest.raises(ValueError, match="not available"):
        await demonstrate(manager, "xdog-flow")
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize("turn", [False, True])
async def test_model_loading_is_not_host_activation(tmp_path: Path, turn: bool) -> None:
    write_skill(tmp_path, "procedure", turn=turn)
    write_skill(tmp_path, "hidden")
    result = await demonstrate_loading(SkillManager(tmp_path, packaged={}), "procedure")
    assert result == {
        "slug": "procedure", "packaged": False, "model_requests": 3,
        "tool_names": ["skill"], "permission_checks": ["skill", "skill"],
        "listed_without_body": True, "loaded_at_model_entry": True,
        "active_skills": [], "base_prompt_preserved": True,
    }


async def test_real_packaged_flow_skill_activation_and_model_loading(tmp_path: Path) -> None:
    from xdog.agent.skills import packaged_skills

    directory = packaged_skills().get("flow")
    if directory is None:
        pytest.skip("Optional xdog-flow package is not installed")
    # Select the exact packaged copy with no ambient local overrides.
    manager = SkillManager(tmp_path / "unused", packaged={"flow": directory})
    assert (directory / "SKILL.md").is_file()
    assert len(list((directory / "examples").glob("*.json"))) == 12
    activated = await demonstrate(manager, "flow")
    loaded = await demonstrate_loading(manager, "flow")
    assert activated["packaged"] and activated["loaded_at_model_entry"]
    assert loaded["packaged"] and loaded["loaded_at_model_entry"]
    assert loaded["active_skills"] == [] and not (tmp_path / "unused").exists()

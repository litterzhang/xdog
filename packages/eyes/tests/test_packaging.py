from xdog.agent.skills import load_packaged_skill, packaged_skills


def test_installed_package_skill_is_discovered_and_loaded():
    paths = packaged_skills()
    assert "eyes-observers" in paths
    skill = load_packaged_skill("eyes-observers")
    assert skill is not None and skill.packaged
    assert skill.name == "eyes-observers"
    assert skill.directory == paths["eyes-observers"]

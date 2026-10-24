from xdog.agent.skills import load_packaged_skill, packaged_skills


def test_installed_package_skill_is_discovered_and_loaded():
    paths = packaged_skills()
    assert "eyes" in paths
    assert "eyes-observers" not in paths
    skill = load_packaged_skill("eyes")
    assert skill is not None and skill.packaged
    assert skill.name == "eyes"
    assert skill.directory == paths["eyes"]


def test_eyes_and_flow_use_default_builtin_discovery_without_copying_or_starting_work(tmp_path):
    from xdog.agent.skills import SkillRepo

    shared, project = tmp_path / 'user-skills', tmp_path / 'project-skills'
    manager = SkillRepo().register(directory=shared, name="shared").register(directory=project, name="group")
    listed = {skill.slug: skill for skill in manager.list_skills()}
    for slug in ('eyes', 'flow'):
        assert listed[slug].packaged and not listed[slug].content  # Progressive disclosure, not eager activation.
        loaded = manager.load_skill(slug)
        assert loaded is not None and loaded.packaged and loaded.content
        assert loaded.directory == packaged_skills()[slug]
    assert not shared.exists() and not project.exists()
    assert not list(tmp_path.iterdir())  # No copied skills, journal, receiver or observer state.

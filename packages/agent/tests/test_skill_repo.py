"""Skill source registration, packaged defaults, stable subsets and additive unions."""
from __future__ import annotations

import threading

import pytest
from xdog.agent import SkillRepo
from xdog.agent.skills.tool import _Reader


def write(root, slug, body):
    directory = root / slug
    directory.mkdir(parents=True)
    (directory / 'SKILL.md').write_text(f'---\nname: {slug}\ndescription: index only\n---\n{body}')
    return directory


def test_packaged_default_can_be_disabled_without_loading_ambient_host_directories(tmp_path, monkeypatch):
    packaged = write(tmp_path / 'installed', 'procedure', 'PACKAGED_BODY')
    write(tmp_path / 'skills', 'unrequested', 'HOST_BODY')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('xdog.agent.skills.repo.packaged_skills', lambda: {'procedure': packaged})
    default, empty = SkillRepo(), SkillRepo(builtin=False)
    assert [row.slug for row in default.list_skills()] == ['procedure']
    assert 'PACKAGED_BODY' not in default.skills_summary()
    assert default.load_skill('procedure').content == 'PACKAGED_BODY'
    assert not empty.list_skills() and default.load_skill('unrequested') is None


def test_register_directories_is_explicit_additive_and_conflicts_are_atomic(tmp_path):
    a, b, conflicting = tmp_path / 'a', tmp_path / 'b', tmp_path / 'conflict'
    write(a, 'alpha', 'A')
    write(b, 'beta', 'B')
    write(conflicting, 'alpha', 'DIFFERENT')
    repo = SkillRepo(builtin=False).register(directory=a).register(directory=b)
    assert {row.slug for row in repo.list_skills()} == {'alpha', 'beta'}
    with pytest.raises(ValueError, match='Duplicate'):
        repo.register(directory=conflicting)
    assert repo.load_skill('alpha').content == 'A'
    repo.unregister(str(a))
    repo.register(directory=conflicting)
    assert repo.load_skill('alpha').content == 'DIFFERENT'
    assert (a / 'alpha' / 'SKILL.md').is_file()


def test_selected_directory_repo_and_reader_keep_same_finite_subset(tmp_path):
    write(tmp_path, 'alpha', 'A')
    write(tmp_path, 'beta', 'B')
    repo = SkillRepo(builtin=False).register(directory=tmp_path)
    selected = repo.select(['alpha'])
    write(tmp_path, 'later', 'LATER')
    assert [row.slug for row in selected.list_skills()] == ['alpha']
    assert {row.slug for row in repo.list_skills()} == {'alpha', 'beta', 'later'}
    reader = _Reader(selected, None, 262144, 128)
    assert reader.load('alpha', threading.Event()).content == 'A'
    for hidden in ['beta', 'later']:
        assert selected.load_skill(hidden) is None
        with pytest.raises(ValueError):
            reader.load(hidden, threading.Event())
    with pytest.raises(ValueError, match='subset'):
        selected.register(directory=tmp_path / 'other')
    assert not selected.select([]).list_skills()
    with pytest.raises(ValueError):
        selected.select(['beta'])


def test_merge_deduplicates_sources_but_not_different_same_name_bodies(tmp_path):
    a = write(tmp_path / 'a', 'procedure', 'A')
    b = write(tmp_path / 'b', 'procedure', 'B')
    first = SkillRepo(builtin=False, packaged={'procedure': a})
    same = SkillRepo(builtin=False, packaged={'procedure': a})
    union = first.merge(same.select(['procedure']))
    assert union.load_skill('procedure') == first.load_skill('procedure')
    assert not first.merge(SkillRepo(builtin=False)).load_skill('missing')
    with pytest.raises(ValueError, match='Duplicate'):
        first.merge(SkillRepo(builtin=False, packaged={'procedure': b}))
    # Merging retains live file identity, not a stale in-memory instruction body.
    (a / 'SKILL.md').unlink()
    assert union.load_skill('procedure') is None


@pytest.mark.parametrize('value', [None, 0, 1, 'yes'])
def test_builtin_flag_is_not_truthiness(value):
    with pytest.raises(TypeError):
        SkillRepo(builtin=value)


def test_public_sdk_has_no_retired_registry_or_manager_names():
    import importlib.util

    import xdog.agent as sdk

    assert hasattr(sdk, 'ToolRepo') and hasattr(sdk, 'SkillRepo')
    assert not hasattr(sdk, 'ToolRegistry') and not hasattr(sdk, 'SkillManager')
    assert importlib.util.find_spec('xdog.agent.tools.registry') is None
    assert importlib.util.find_spec('xdog.agent.skills.manager') is None

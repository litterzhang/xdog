"""Common hub discovery is inert; Goal remains an isolated domain with stable data."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tomllib
from importlib.resources import files
from pathlib import Path


def hub():
    spec = importlib.util.find_spec('xdog.skills')
    assert spec is not None, 'Common medium-level skills must ship as xdog.skills, not xdog.goal'
    return Path(str(files('xdog.skills')))


def test_hub_import_does_not_import_goal_or_start_business_runtime(tmp_path):
    hub()
    result = subprocess.run(
        [sys.executable, '-c',
         "import sys,xdog.skills; "
         "assert not any(n.startswith(('xdog.skills.goal','xdog.agent','xdog.ai','xdog.tui')) for n in sys.modules)"],
        cwd=tmp_path, text=True, capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert not list(tmp_path.iterdir())


def test_generic_discovery_finds_hub_skill_and_existing_flow_eyes_without_copying(tmp_path):
    from xdog.agent.skills import SkillManager, packaged_skills

    root = hub()
    assert not (root / 'skills' / 'eyes').exists()
    assert not (root / 'skills' / 'flow').exists()
    paths = packaged_skills()
    assert paths['goal'] == root / 'skills' / 'goal'
    assert paths['plan'] == root / 'skills' / 'plan'
    assert not {'goals', 'eyes-observers', 'flow-workflows', 'xdog-planning'}.intersection(paths)
    manager = SkillManager(tmp_path / 'not-created')
    available = {item.slug: item for item in manager.list_skills()}
    for slug in ('goal', 'plan', 'eyes', 'flow'):
        assert available[slug].packaged and not available[slug].content
        loaded = manager.load_skill(slug)
        assert loaded is not None and loaded.content and loaded.packaged
        assert loaded.name == slug
    assert manager.load_skill('flow-workflows') is None
    assert manager.load_skill('xdog-planning') is None
    assert not list(tmp_path.iterdir())


def test_goal_skill_and_readonly_shared_display_have_no_worker_roles(tmp_path):
    from xdog.agent.extensions.discovery import extension_directory

    root = hub()
    assert not (root / 'goal' / 'roles').exists()
    assert not (root / 'goal' / 'review.py').exists()
    assert not (root / 'goal' / 'worker.py').exists()
    assert extension_directory('goal_ui', tmp_path / 'absent') == root / 'extensions' / 'goal_ui'
    assert 'display_only: true' in (root / 'extensions/goal_ui/extension.yaml').read_text()
    body = (root / 'skills/goal/SKILL.md').read_text()
    assert 'main Agent' in body and 'expected_version' in body and 'subagent' in body


def test_goal_registry_identity_is_not_a_python_namespace():
    from xdog.skills.goal.store import _FORMAT

    assert _FORMAT == 'xdog.goal.registry.v2'
    assert importlib.util.find_spec('xdog.goal') is None


def test_hub_distribution_and_optional_host_extras_are_common_not_goal_specific():
    repository = Path(__file__).resolve().parents[3]
    config_path = repository / 'packages' / 'skills' / 'pyproject.toml'
    assert config_path.is_file(), 'The common skills hub must be its own workspace distribution'
    config = tomllib.loads(config_path.read_text())
    assert config['project']['name'] == 'xdog-skills'
    assert config['project']['dependencies'] == ['xdog-agent>=2.4.7']
    for host in ('claw', 'coding'):
        config = tomllib.loads((repository / 'packages' / host / 'pyproject.toml').read_text())
        assert 'xdog-skills>=2.4.7' in config['project']['dependencies']
        extras = config['project']['optional-dependencies']
        assert extras['skills'] == ['xdog-skills>=2.4.7'] and 'goals' not in extras


def test_private_goal_workspace_cannot_enter_umbrella_or_skills_artifacts():
    repository = Path(__file__).resolve().parents[3]
    assert '.xdog/' in (repository / '.gitignore').read_text().splitlines()
    for path in (repository / 'pyproject.toml', repository / 'packages/skills/pyproject.toml'):
        config = tomllib.loads(path.read_text())
        excludes = config['tool']['hatch']['build']['exclude']
        assert {'**/.xdog', '**/.xdog/**'} <= set(excludes)

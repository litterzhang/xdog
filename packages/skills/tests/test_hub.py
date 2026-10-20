"""Common hub discovery is inert; Goal remains an isolated domain with stable data."""
from __future__ import annotations

import hashlib
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
    assert not (root / 'skills' / 'flow-workflows').exists()
    paths = packaged_skills()
    assert paths['goal'] == root / 'skills' / 'goal'
    assert paths['xdog-planning'] == root / 'skills' / 'xdog-planning'
    assert 'goals' not in paths and 'eyes-observers' not in paths
    manager = SkillManager(tmp_path / 'not-created')
    available = {item.slug: item for item in manager.list_skills()}
    for slug in ('goal', 'xdog-planning', 'eyes', 'flow-workflows'):
        assert available[slug].packaged and not available[slug].content
        loaded = manager.load_skill(slug)
        assert loaded is not None and loaded.content and loaded.packaged
    assert not list(tmp_path.iterdir())


def test_goal_roles_and_shared_extension_resolve_from_correct_domain_and_hub_roots(tmp_path):
    from xdog.agent.extensions.discovery import extension_directory

    root = hub()
    from xdog.skills.goal.resources import skill_directory

    assert skill_directory() == root / 'skills' / 'goal'
    assert (root / 'goal' / 'roles' / 'worker.md').is_file()
    assert (root / 'goal' / 'roles' / 'reviewer.md').is_file()
    assert extension_directory('goal_ui', tmp_path / 'absent') == root / 'extensions' / 'goal_ui'
    assert hashlib.sha256((root / 'extensions' / 'goal_ui' / 'main.py').read_bytes()).hexdigest() == (
        '8eef6b34bd726682d30b3a6c65dd6ca6327cd37d201df248f67199ec015724ce'
    )


def test_goal_journal_and_snapshot_identities_are_not_python_namespace_names():
    hub()
    from xdog.skills.goal.codec import FORMAT
    from xdog.skills.goal.store import _FORMAT

    assert FORMAT == 'xdog.goal.snapshot.v1'
    assert _FORMAT == 'xdog.goal.journal.v1'
    assert importlib.util.find_spec('xdog.goal') is None  # No old public import facade.


def test_hub_distribution_and_optional_host_extras_are_common_not_goal_specific():
    repository = Path(__file__).resolve().parents[3]
    config_path = repository / 'packages' / 'skills' / 'pyproject.toml'
    assert config_path.is_file(), 'The common skills hub must be its own workspace distribution'
    config = tomllib.loads(config_path.read_text())
    assert config['project']['name'] == 'xdog-skills'
    assert config['project']['dependencies'] == ['xdog-agent>=2.4.4']
    for host in ('claw', 'coding'):
        config = tomllib.loads((repository / 'packages' / host / 'pyproject.toml').read_text())
        extras = config['project']['optional-dependencies']
        assert extras['skills'] == ['xdog-skills>=2.4.4'] and 'goals' not in extras

"""Skills uses the existing per-package OIDC publisher, not a token workflow."""
from __future__ import annotations

import json
from pathlib import Path

import yaml


def workflow():
    path = Path(__file__).resolve().parents[3] / '.github/workflows/publish.yml'
    return yaml.load(path.read_text(), Loader=yaml.BaseLoader)


def test_skills_is_in_the_same_build_and_publish_matrix_as_existing_packages():
    value = workflow()
    packages = json.loads(value['on']['workflow_dispatch']['inputs']['packages']['default'])
    assert 'xdog-skills' in packages and 'xdog-goal' not in packages
    assert len(packages) == len(set(packages))
    for job in ('build', 'publish'):
        matrix = value['jobs'][job]['strategy']['matrix']['package']
        assert json.dumps(packages, separators=(',', ':')) in matrix
    build_commands = [step.get('run', '') for step in value['jobs']['build']['steps']]
    assert any('uv build --package ${{ matrix.package }}' in command for command in build_commands)


def test_skills_publisher_uses_matching_environment_oidc_and_never_publishes_on_main_push():
    value = workflow()
    assert value['on']['push'] == {'tags': ['v*']}  # A normal main push cannot upload to PyPI.
    publish = value['jobs']['publish']
    assert publish['environment'] == 'pypi-${{ matrix.package }}'
    assert publish['permissions']['id-token'] == 'write'
    assert publish['needs'] == ['build', 'test']
    action = next(step for step in publish['steps'] if step.get('uses', '').startswith('pypa/gh-action-pypi-publish@'))
    assert not {'password', 'username'} & action.get('with', {}).keys()


def test_publishing_type_gate_includes_the_new_skills_namespace():
    commands = '\n'.join(step.get('run', '') for step in workflow()['jobs']['test']['steps'])
    assert 'mypy --strict' in commands and '-p xdog.skills' in commands

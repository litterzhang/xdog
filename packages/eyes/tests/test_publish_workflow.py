"""Eyes uses the shared OIDC release path and validates the uploaded wheel."""
from __future__ import annotations

import json
import runpy
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def workflow():
    return yaml.load((ROOT / '.github/workflows/publish.yml').read_text(), Loader=yaml.BaseLoader)


def test_eyes_uses_shared_tag_and_manual_package_publisher():
    value = workflow()
    packages = json.loads(value['on']['workflow_dispatch']['inputs']['packages']['default'])
    assert packages.count('xdog-eyes') == 1
    for job in ('build', 'publish'):
        assert json.dumps(packages, separators=(',', ':')) in value['jobs'][job]['strategy']['matrix']['package']
    assert value['on']['push'] == {'tags': ['v*']}
    publish = value['jobs']['publish']
    assert publish['environment'] == 'pypi-${{ matrix.package }}'
    assert publish['permissions']['id-token'] == 'write'
    assert publish['needs'] == ['build', 'test']
    action = next(step for step in publish['steps'] if step.get('uses', '').startswith('pypa/gh-action-pypi-publish@'))
    assert not {'password', 'username'} & action.get('with', {}).keys()


def test_publishing_strict_type_gate_includes_eyes():
    commands = '\n'.join(step.get('run', '') for step in workflow()['jobs']['test']['steps'])
    assert 'mypy --strict' in commands and '-p xdog.eyes' in commands


@pytest.mark.parametrize('damage', [None, 'skill', 'dependency', 'version', 'tag'])
def test_eyes_wheel_release_gate_rejects_incomplete_or_mismatched_artifacts(tmp_path, monkeypatch, damage):
    step = next(
        step for step in workflow()['jobs']['build']['steps']
        if step.get('name') == 'Verify Eyes release artifact'
    )
    assert step['if'] == "matrix.package == 'xdog-eyes'"
    assert 'github.ref_name' in step['env']['RELEASE_TAG']
    config = ROOT / 'packages/eyes/pyproject.toml'
    target = tmp_path / 'packages/eyes/pyproject.toml'
    target.parent.mkdir(parents=True)
    target.write_bytes(config.read_bytes())
    version = tomllib.loads(config.read_text())['project']['version']
    verifier = ROOT / 'scripts/verify_eyes_artifact.py'
    policy = runpy.run_path(str(verifier))
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'scripts/verify_eyes_artifact.py').write_bytes(verifier.read_bytes())
    (tmp_path / 'dist').mkdir()
    with ZipFile(tmp_path / 'dist/xdog_eyes-test-py3-none-any.whl', 'w') as wheel:
        wheel.writestr('xdog_eyes-test.dist-info/METADATA', '\n'.join([
            'Metadata-Version: 2.4', 'Name: xdog-eyes',
            f'Version: {version if damage != "version" else "0.0.0"}',
            *['Requires-Dist: ' + value for value in sorted(policy['APPROVED_REQUIREMENTS'])],
            *(['Requires-Dist: xdog-agent'] if damage == 'dependency' else []), '',
        ]))
        for name in policy['REQUIRED_RESOURCES']:
            if damage != 'skill' or not name.endswith('SKILL.md'):
                wheel.writestr(name, '')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('RELEASE_TAG', 'v0.0.0' if damage == 'tag' else f'v{version}')
    command = shlex.split(step['run'])
    assert command[0] == 'python'
    result = subprocess.run([sys.executable, *command[1:]], capture_output=True, text=True, timeout=10)
    if damage is None:
        assert result.returncode == 0, result.stderr
    else:
        expected = {'skill': 'missing resources', 'dependency': 'dependency policy mismatch',
                    'version': 'version does not match', 'tag': 'release tag'}
        assert result.returncode == 1 and expected[damage] in result.stderr, result.stderr

"""Actual Eyes wheel/sdist contain v2 skills and no retired host implementation."""

import email
import json
import runpy
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest
from packaging.requirements import Requirement


@pytest.fixture(scope='module')
def artifacts(tmp_path_factory):
    repo=Path(__file__).resolve().parents[3]
    directory=tmp_path_factory.mktemp('eyes-acp-artifacts')
    result=subprocess.run(['uv','build','--package','xdog-eyes','--out-dir',str(directory)],
                           cwd=repo,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stderr
    return directory


def test_eyes_wheel_has_only_approved_dependencies_and_no_harness_host(artifacts,tmp_path):
    from xdog.eyes.loader import parse

    with zipfile.ZipFile(next(artifacts.glob('*.whl'))) as wheel:
        names=wheel.namelist()
        metadata=email.message_from_bytes(wheel.read(next(n for n in names if n.endswith('.dist-info/METADATA'))))
        requirements=[Requirement(line) for line in metadata.get_all('Requires-Dist',[])]
        # Exercise the exact artifact policy used by CI and release, not a
        # second allowlist that can drift while the unit suite stays green.
        repo = Path(__file__).resolve().parents[3]
        verify = runpy.run_path(str(repo / 'scripts/verify_eyes_artifact.py'))['verify']
        assert verify(artifacts) == next(artifacts.glob('*.whl'))
        sdk=next(r for r in requirements if r.name=='agent-client-protocol')
        assert sdk.marker.evaluate({'python_version':'3.14'})
        assert not sdk.marker.evaluate({'python_version':'3.15'})
        for name in ('local_control.py','acp_client.py','handlers/acp.py','py.typed',
                     'skills/eyes/SKILL.md','examples/source-test-review.json',
                     'sources/scheduler.py','detectors/scheduler_due.py','examples/scheduled-review.json'):
            assert 'xdog/eyes/'+name in names
        assert not any('/transport/' in n or n.endswith('/session_notify.py') for n in names)
        assert not any(n.endswith('.pyc') or '__pycache__' in n for n in names)
        assert any(n.endswith('/licenses/LICENSE') for n in names)
        data=json.loads(wheel.read('xdog/eyes/examples/source-test-review.json'))
        definition=parse(data,base_dir=tmp_path)
        assert definition.version==2 and definition.handlers[0].delivery=='enqueue'
        scheduled=parse(json.loads(wheel.read('xdog/eyes/examples/scheduled-review.json')),base_dir=tmp_path)
        assert scheduled.sources[0].type=='scheduler'


def test_eyes_sdist_retains_skill_example_and_clean_runtime_sources(artifacts):
    with tarfile.open(next(artifacts.glob('*.tar.gz'))) as archive:
        names=archive.getnames()
        assert any(n.endswith('/src/xdog/eyes/skills/eyes/SKILL.md') for n in names)
        assert any(n.endswith('/src/xdog/eyes/local_control.py') for n in names)
        assert not any('/xdog/eyes/transport/' in n or n.endswith('/session_notify.py') for n in names)


@pytest.fixture(scope='module')
def policy():
    repo = Path(__file__).resolve().parents[3]
    return runpy.run_path(str(repo / 'scripts/verify_eyes_artifact.py'))


def changed_wheel(artifacts, target, *, requirements=None, drop='', extra='', version=None):
    original = next(artifacts.glob('*.whl'))
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(target / original.name, 'w') as destination:
        for name in source.namelist():
            if name == drop:
                continue
            content = source.read(name)
            if name.endswith('.dist-info/METADATA'):
                metadata = email.message_from_bytes(content)
                if requirements is not None:
                    del metadata['Requires-Dist']
                    for requirement in requirements:
                        metadata['Requires-Dist'] = requirement
                if version is not None:
                    metadata.replace_header('Version', version)
                content = metadata.as_bytes()
            destination.writestr(name, content)
        if extra:
            destination.writestr(extra, '# Unwanted package\n')


@pytest.mark.parametrize('change', ['extra', 'missing', 'unbounded', 'marker', 'duplicate', 'legacy'])
def test_shared_artifact_policy_rejects_dependency_drift(artifacts, tmp_path, policy, change):
    requirements = sorted(policy['APPROVED_REQUIREMENTS'])
    if change == 'extra':
        requirements.append('xdog-agent>=2.5.0')
    elif change == 'missing':
        requirements.remove('httpx<1,>=0.27')
    elif change == 'unbounded':
        requirements[requirements.index('httpx<1,>=0.27')] = 'httpx>=0.27'
    elif change == 'marker':
        requirements[0] = 'agent-client-protocol<0.13,>=0.12.1'
    elif change == 'duplicate':
        requirements.append(requirements[0])
    else:
        requirements = [value for value in requirements if value.startswith(('agent-client-protocol', 'croniter'))]
    changed_wheel(artifacts, tmp_path, requirements=requirements)
    with pytest.raises(ValueError, match='dependency policy mismatch'):
        policy['verify'](tmp_path)


@pytest.mark.parametrize('drop', ['skills/eyes/SKILL.md', 'examples/job-events.json',
                                 'sources/event.py', 'sources/feed.py', 'sources/html.py', 'contracts.py'])
def test_shared_artifact_policy_checks_new_and_existing_resources(artifacts, tmp_path, policy, drop):
    changed_wheel(artifacts, tmp_path, drop='xdog/eyes/' + drop)
    with pytest.raises(ValueError, match='missing resources'):
        policy['verify'](tmp_path)


def test_shared_artifact_policy_rejects_bundled_harness_and_wrong_version(artifacts, tmp_path, policy):
    changed_wheel(artifacts, tmp_path, extra='xdog/agent/__init__.py')
    with pytest.raises(ValueError, match='another XDOG package'):
        policy['verify'](tmp_path)
    changed_wheel(artifacts, tmp_path, version='0.0.0')
    with pytest.raises(ValueError, match='version does not match'):
        policy['verify'](tmp_path)


def test_shared_artifact_policy_preserves_tag_guard_and_requires_one_wheel(artifacts, tmp_path, policy):
    import tomllib

    repo = Path(__file__).resolve().parents[3]
    version = tomllib.loads((repo / 'packages/eyes/pyproject.toml').read_text())['project']['version']
    assert policy['verify'](artifacts, release_tag='v' + version)
    with pytest.raises(ValueError, match='release tag'):
        policy['verify'](artifacts, release_tag='v0.0.0')
    with pytest.raises(ValueError, match='exactly one Eyes wheel'):
        policy['verify'](tmp_path)


def test_ci_and_publish_use_the_same_standalone_verifier():
    import yaml

    repo = Path(__file__).resolve().parents[3]
    ci = yaml.safe_load((repo / '.github/workflows/ci.yml').read_text())
    publish = yaml.safe_load((repo / '.github/workflows/publish.yml').read_text())
    step = next(step for step in ci['jobs']['test']['steps']
                if step.get('name') == 'Eyes example and independently packaged skill')
    assert 'python scripts/verify_eyes_artifact.py /tmp/eyes-wheelcheck' in step['run']
    step = next(step for step in publish['jobs']['build']['steps']
                if step.get('name') == 'Verify Eyes release artifact')
    assert step['run'] == 'python scripts/verify_eyes_artifact.py dist'
    assert step['if'] == "matrix.package == 'xdog-eyes'"
    assert 'RELEASE_TAG' in step['env']


def test_artifact_verifier_cli_is_stdlib_only_and_fails_bad_tag(artifacts):
    import os
    import sys

    repo = Path(__file__).resolve().parents[3]
    command = [sys.executable, '-I', str(repo / 'scripts/verify_eyes_artifact.py'), str(artifacts)]
    environment = {key: value for key, value in os.environ.items() if key != 'RELEASE_TAG'}
    result = subprocess.run(command, cwd=artifacts, env=environment, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    result = subprocess.run(command, cwd=artifacts, env={**environment, 'RELEASE_TAG': 'v0.0.0'},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 1 and 'release tag' in result.stderr

"""Actual Eyes wheel/sdist contain v2 skills and no retired host implementation."""

import email
import json
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
        assert {r.name for r in requirements}=={'agent-client-protocol', 'croniter'}
        sdk=next(r for r in requirements if r.name=='agent-client-protocol')
        assert sdk.marker.evaluate({'python_version':'3.14'})
        assert not sdk.marker.evaluate({'python_version':'3.15'})
        for name in ('local_control.py','acp_client.py','handlers/acp.py','py.typed',
                     'skills/eyes-observers/SKILL.md','examples/source-test-review.json',
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
        assert any(n.endswith('/src/xdog/eyes/skills/eyes-observers/SKILL.md') for n in names)
        assert any(n.endswith('/src/xdog/eyes/local_control.py') for n in names)
        assert not any('/xdog/eyes/transport/' in n or n.endswith('/session_notify.py') for n in names)

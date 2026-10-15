"""Inspect actual built artifacts, not only source manifests or editable imports."""

import email
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest
from packaging.requirements import Requirement


@pytest.fixture(scope='module')
def artifacts(tmp_path_factory):
    repo=Path(__file__).resolve().parents[3]
    directory=tmp_path_factory.mktemp('agent-acp-artifacts')
    result=subprocess.run(['uv','build','--package','xdog-agent','--out-dir',str(directory)],
                           cwd=repo,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stderr
    return directory


def test_agent_wheel_declares_bounded_optional_sdk_and_ships_protocol(artifacts):
    with zipfile.ZipFile(next(artifacts.glob('*.whl'))) as wheel:
        names=wheel.namelist()
        for suffix in ('adapter.py','backend.py','contracts.py','events.py','transport.py'):
            assert 'xdog/agent/acp/'+suffix in names
        assert 'xdog/agent/py.typed' in names
        metadata=email.message_from_bytes(wheel.read(next(n for n in names if n.endswith('.dist-info/METADATA'))))
        requirements=[Requirement(line) for line in metadata.get_all('Requires-Dist',[])]
        sdk=next(r for r in requirements if r.name=='agent-client-protocol')
        assert '0.12.1' in sdk.specifier and '0.13.0' not in sdk.specifier
        assert sdk.marker.evaluate({'python_version':'3.14'})
        assert not sdk.marker.evaluate({'python_version':'3.15'})
        assert any(n.endswith('/licenses/LICENSE') for n in names)


def test_agent_sdist_contains_the_protocol_source_and_typing_marker(artifacts):
    with tarfile.open(next(artifacts.glob('*.tar.gz'))) as archive:
        names=archive.getnames()
        assert any(n.endswith('/src/xdog/agent/acp/adapter.py') for n in names)
        assert any(n.endswith('/src/xdog/agent/py.typed') for n in names)

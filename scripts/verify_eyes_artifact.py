"""One strict Eyes wheel policy shared by CI, release builds and regression tests.

Uses only stdlib so release verification needs neither workspace installation nor
an Eyes import (which would inspect editable sources instead of the artifact).
"""
from __future__ import annotations

import argparse
import os
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPROVED_REQUIREMENTS = frozenset({
    "agent-client-protocol<0.13,>=0.12.1; python_version < '3.15'",
    "beautifulsoup4<5,>=4.12",
    "croniter<7,>=2.0",
    "defusedxml<1,>=0.7",
    "httpx<1,>=0.27",
})
REQUIRED_RESOURCES = frozenset('xdog/eyes/' + name for name in (
    'py.typed', 'skills/eyes/SKILL.md',
    'examples/source-test-review.json', 'examples/scheduled-review.json',
    'examples/http-announcements.json', 'examples/http-service-health.json',
    'examples/github-feedback.json', 'examples/github-actions.json',
    'examples/html-announcements.json', 'examples/feed-updates.json',
    'examples/github-releases.json', 'examples/job-events.json',
    'sources/file.py', 'sources/scheduler.py', 'sources/http.py', 'sources/github.py',
    'sources/html.py', 'sources/feed.py', 'sources/event.py', 'sources/records.py',
    'sources/documents.py', 'contracts.py', 'config_values.py', 'detector_config.py',
    'event_intake.py', 'detectors/file_change.py', 'detectors/scheduler_due.py', 'detectors/records.py',
))


def verify(directory: Path, *, release_tag: str = '') -> Path:
    wheels = list(directory.glob('xdog_eyes-*.whl'))
    if len(wheels) != 1:
        raise ValueError(f'Expected exactly one Eyes wheel in {directory}, found {len(wheels)}')
    project = tomllib.loads((ROOT / 'packages/eyes/pyproject.toml').read_text())['project']
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = set(wheel.namelist())
        missing = REQUIRED_RESOURCES - names
        if missing:
            raise ValueError(f'Eyes wheel missing resources: {sorted(missing)}')
        foreign = {name for name in names if name.startswith('xdog/') and not name.startswith('xdog/eyes/')}
        if foreign:
            raise ValueError(f'Eyes wheel contains another XDOG package: {sorted(foreign)}')
        metadata_names = [name for name in names if name.endswith('.dist-info/METADATA')]
        if len(metadata_names) != 1:
            raise ValueError('Eyes wheel must contain exactly one distribution metadata record')
        metadata = BytesParser().parsebytes(wheel.read(metadata_names[0]))
    if metadata['Name'] != project['name'] or metadata['Name'] != 'xdog-eyes':
        raise ValueError('Eyes wheel distribution name does not match project')
    if metadata['Version'] != project['version']:
        raise ValueError('Eyes wheel version does not match project')
    if release_tag and release_tag != 'v' + project['version']:
        raise ValueError(f'Eyes release tag {release_tag!r} does not match version {project["version"]}')
    actual = metadata.get_all('Requires-Dist', [])
    if len(actual) != len(APPROVED_REQUIREMENTS) or set(actual) != APPROVED_REQUIREMENTS:
        raise ValueError(f'Eyes wheel dependency policy mismatch: {actual}')
    return wheels[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    try:
        wheel = verify(args.directory, release_tag=os.environ.get('RELEASE_TAG', ''))
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        parser.exit(1, str(exc) + '\n')
    print(f'Verified {wheel.name}: version/tag, bounded standalone dependencies and packaged resources')


if __name__ == '__main__':
    main()

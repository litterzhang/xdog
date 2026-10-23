"""Offline CS-06/08 packaging and documented interface checks, not host runtime tests."""

import email
import json
import re
import subprocess
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.version import Version

REPO = Path(__file__).resolve().parents[3]
SKILL = Path("packages/eyes/src/xdog/eyes/skills/eyes/SKILL.md")
PUBLISHED = {
    "xdog-ai", "xdog-tui", "xdog-agent", "xdog-flow", "xdog-claw", "xdog-coding", "xdog-eyes", "xdog-skills",
}


def _toml(path):
    return tomllib.loads((REPO / path).read_text())


def test_claw_optional_eyes_and_eyes_owns_bounded_croniter():
    claw = _toml("packages/claw/pyproject.toml")["project"]
    eyes = _toml("packages/eyes/pyproject.toml")["project"]
    requirements = [Requirement(value) for value in claw["dependencies"]]
    direct = {r.name for r in requirements}
    assert "xdog-eyes" not in direct
    assert {"xdog-skills", "agent-client-protocol"} <= direct
    requirement = next(Requirement(value) for value in claw["optional-dependencies"]["eyes"]
                       if Requirement(value).name == "xdog-eyes")
    assert str(requirement.specifier) == ">=2.4.9"
    assert Version(eyes["version"]) in requirement.specifier
    assert requirement.marker is None and not requirement.extras
    all_requirements = requirements + [
        Requirement(value) for extra in claw["optional-dependencies"].values() for value in extra
    ]
    assert not any(value.name == "croniter" for value in all_requirements)
    croniter = next(Requirement(value) for value in eyes["dependencies"] if value.startswith("croniter"))
    assert str(croniter.specifier) == "<7,>=2.0"
    assert _toml("pyproject.toml")["tool"]["uv"]["sources"]["xdog-eyes"] == {"workspace": True}


def test_editable_lock_records_eyes_edge_and_no_direct_claw_croniter():
    packages = {value["name"]: value for value in _toml("uv.lock")["package"]}
    claw = packages["xdog-claw"]
    assert {"name": "xdog-eyes"} not in claw["dependencies"]
    assert {"name": "xdog-eyes"} in claw["optional-dependencies"]["eyes"]
    for extra in ("eyes", "all"):
        assert {"name": "xdog-eyes", "marker": f"extra == '{extra}'",
                "editable": "packages/eyes"} in claw["metadata"]["requires-dist"]
    edges = (
        claw["dependencies"] + claw["metadata"]["requires-dist"]
        + [edge for extra in claw["optional-dependencies"].values() for edge in extra]
    )
    assert not any(edge["name"] == "croniter" for edge in edges)
    assert packages["xdog-eyes"]["source"] == {"editable": "packages/eyes"}
    assert {"name": "croniter"} in packages["xdog-eyes"]["dependencies"]
    assert Version(packages["croniter"]["version"]) in Requirement("croniter>=2,<7").specifier


def test_publish_default_and_both_matrices_include_eyes_without_site_or_umbrella():
    # BaseLoader keeps the GitHub Actions `on` key a string (not YAML 1.1 bool).
    workflow = yaml.load((REPO / ".github/workflows/publish.yml").read_text(), Loader=yaml.BaseLoader)
    default = json.loads(workflow["on"]["workflow_dispatch"]["inputs"]["packages"]["default"])
    assert len(default) == len(set(default)) and set(default) == PUBLISHED
    for job in ("build", "publish"):
        expression = workflow["jobs"][job]["strategy"]["matrix"]["package"]
        match = re.fullmatch(r"\$\{\{ fromJSON\(inputs\.packages \|\| '(\[.*\])'\) \}\}", expression)
        assert match is not None
        assert json.loads(match[1]) == default
    assert workflow["jobs"]["publish"]["environment"] == "pypi-${{ matrix.package }}"


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory):
    directory = tmp_path_factory.mktemp("claw-eyes-packaging")
    for package in ("xdog-claw", "xdog-eyes"):
        # Cached build tooling only: never resolve/install from the network.
        result = subprocess.run(
            ["uv", "build", "--offline", "--package", package, "--out-dir", str(directory)],
            cwd=REPO, capture_output=True, text=True, timeout=120,
        )
        (directory / f"{package}-build.log").write_text(result.stdout + result.stderr)
        assert result.returncode == 0, result.stderr
    return directory


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
def test_built_claw_metadata_optional_eyes_without_direct_croniter(artifacts, kind):
    if kind == "wheel":
        with zipfile.ZipFile(next(artifacts.glob("xdog_claw-*.whl"))) as archive:
            name = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
            content = archive.read(name)
    else:
        with tarfile.open(next(artifacts.glob("xdog_claw-*.tar.gz"))) as archive:
            name = next(name for name in archive.getnames() if name.count("/") == 1 and name.endswith("/PKG-INFO"))
            stream = archive.extractfile(name)
            assert stream is not None
            content = stream.read()
    metadata = email.message_from_bytes(content)
    requirements = [Requirement(value) for value in metadata.get_all("Requires-Dist", [])]
    eyes = [value for value in requirements if value.name == "xdog-eyes"]
    assert eyes and all(str(value.specifier) == ">=2.4.9" and value.marker is not None for value in eyes)
    for extra, expected in (("", False), ("search", False), ("eyes", True), ("all", True)):
        assert any(value.marker.evaluate({"extra": extra}) for value in eyes) is expected
    assert not any(value.name == "croniter" for value in requirements)
    assert set(metadata.get_all("Provides-Extra", [])) == {"search", "skills", "eyes", "acp", "all"}
    skills = [value for value in requirements if value.name == "xdog-skills"]
    assert skills and all(str(value.specifier) == ">=2.4.9" for value in skills)
    assert any(value.marker is None for value in skills)  # Default capability, no extra required.
    for extra in ("", "search", "eyes", "skills", "all"):
        assert any(value.marker is None or value.marker.evaluate({"extra": extra}) for value in skills)
    acp = [value for value in requirements if value.name == "agent-client-protocol"]
    assert any(value.marker is not None and 'extra' not in str(value.marker) for value in acp)
    assert "eyes" in metadata.get_payload()


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
def test_built_eyes_includes_updated_host_skill(artifacts, kind):
    if kind == "wheel":
        with zipfile.ZipFile(next(artifacts.glob("xdog_eyes-*.whl"))) as archive:
            content = archive.read("xdog/eyes/skills/eyes/SKILL.md")
    else:
        with tarfile.open(next(artifacts.glob("xdog_eyes-*.tar.gz"))) as archive:
            name = next(
                name for name in archive.getnames()
                if name.endswith("/src/xdog/eyes/skills/eyes/SKILL.md")
            )
            stream = archive.extractfile(name)
            assert stream is not None
            content = stream.read()
    assert content == (REPO / SKILL).read_bytes()


def test_docs_describe_thin_tool_and_default_acp():
    text = (REPO / "packages/claw/SCHEDULER.md").read_text()
    examples = [json.loads(value) for value in re.findall(r"```json\n(.*?)\n```", text, re.DOTALL)]
    assert examples == [{"action": "create", "owner": "my-watches", "workspace": "/project",
                         "definition_path": "eyes.json"}]
    assert "normal ACP input" in text and "enabled by default" in text
    assert "any valid explicit" in text and "not just the current group" in text
    assert "cancel_notifications" in text and "removed" in text
    assert "not Agent completion" not in text or "queued receipt" in text


def test_standalone_skill_definitions_still_validate_without_starting_observers(tmp_path):
    from xdog.eyes.loader import parse

    text = (REPO / SKILL).read_text()
    examples = re.findall(r"```json\n(.*?)\n```", text, re.DOTALL)
    assert len(examples) == 2
    definitions = [parse(json.loads(value), base_dir=tmp_path) for value in examples]
    assert [definition.sources[0].type for definition in definitions] == ["file", "scheduler"]
    for definition in definitions:
        assert definition.handlers[0].session == "EXPLICIT_SESSION_ID"
        assert definition.handlers[0].delivery == "enqueue"

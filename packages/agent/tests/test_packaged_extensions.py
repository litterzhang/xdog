"""Selected installed extension resources, never implicit code installation/startup."""
from __future__ import annotations

import sys

import pytest
from xdog.agent.extensions.runtime import ExtensionRuntime


def test_actual_installed_goal_resource_is_discovered_without_starting_a_worker(tmp_path):
    from xdog.agent.extensions.discovery import extension_directory

    directory = extension_directory("goal_ui", tmp_path / "no-user-copy")
    assert directory is not None and directory.name == "goal_ui"
    assert (directory / "main.py").is_file()
    runtime = ExtensionRuntime(("goal_ui",), tmp_path / "no-user-copy", tmp_path)
    try:
        assert runtime._manager._loop is None and not runtime._manager._clients
        assert not (tmp_path / "no-user-copy").exists()
    finally:
        runtime.close()


async def test_corrupt_explicit_local_selection_never_falls_back_to_an_installed_implementation(tmp_path):
    path = tmp_path / "goal_ui"
    path.mkdir()
    (path / "extension.yaml").write_text("name: different\nentry_point: main\n")
    (path / "main.py").write_text("raise AssertionError('must not run')\n")
    runtime = ExtensionRuntime(("goal_ui",), tmp_path, tmp_path)
    try:
        await runtime.initialize()
        assert runtime.snapshot()["extensions"] == [{"name": "goal_ui", "state": "failed"}]
        assert not runtime.commands() and not runtime._manager._clients
    finally:
        await runtime.aclose()


def test_explicit_local_directory_has_priority_and_dangling_links_do_not_select_packages(tmp_path):
    from xdog.agent.extensions.discovery import extension_directory

    path = tmp_path / "goal_ui"
    path.symlink_to(tmp_path / "missing", target_is_directory=True)
    assert extension_directory("goal_ui", tmp_path) == path


def test_two_real_namespace_packages_cannot_ambiguously_claim_one_selected_id(tmp_path, monkeypatch):
    import xdog
    from xdog.agent.extensions.discovery import extension_directory

    roots = []
    names = ("view_fixture_one", "view_fixture_two")
    for name in names:
        root = tmp_path / name / "xdog"
        path = root / name / "extensions" / "ambiguous_ui"
        path.mkdir(parents=True)
        (root / name / "__init__.py").write_text("")
        (path / "extension.yaml").write_text("name: ambiguous_ui\n")
        roots.append(str(root))
    monkeypatch.setattr(xdog, "__path__", [*xdog.__path__, *roots])
    try:
        with pytest.raises(ValueError, match="[Aa]mbiguous|[Dd]uplicate"):
            extension_directory("ambiguous_ui", tmp_path / "explicit")
    finally:
        for name in names:
            sys.modules.pop("xdog." + name, None)
            if hasattr(xdog, name):
                delattr(xdog, name)


@pytest.mark.parametrize("name", ["../other", "", "bad-name", "x" * 33])
def test_packaged_selection_retains_existing_identifier_bounds(tmp_path, name):
    from xdog.agent.extensions.discovery import extension_directory

    with pytest.raises(ValueError):
        extension_directory(name, tmp_path)


async def test_packaged_link_does_not_change_the_configured_manifest_identity(tmp_path, monkeypatch):
    import xdog

    root = tmp_path / "namespace" / "xdog"
    package = root / "view_fixture_alias"
    resources = package / "extensions"
    resources.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    target = tmp_path / "other_ui"
    target.mkdir()
    (target / "extension.yaml").write_text("name: other_ui\n")
    (target / "main.py").write_text('def setup(api):\n    api.command("ping", "Wrong identity", lambda *a: "wrong")\n')
    (resources / "alias_ui").symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(xdog, "__path__", [*xdog.__path__, str(root)])
    runtime = ExtensionRuntime(("alias_ui",), tmp_path / "explicit", tmp_path)
    try:
        await runtime.initialize()
        assert runtime.snapshot()["extensions"] == [{"name": "alias_ui", "state": "failed"}]
        assert not runtime.commands() and not runtime._manager._clients
    finally:
        await runtime.aclose()
        sys.modules.pop("xdog.view_fixture_alias", None)
        if hasattr(xdog, "view_fixture_alias"):
            delattr(xdog, "view_fixture_alias")

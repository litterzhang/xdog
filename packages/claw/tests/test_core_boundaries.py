"""Import direction and domain ownership are contracts, not only runtime smoke tests."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import xdog.claw
from xdog.agent.mcp import McpRegistry
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.tools import AVAILABLE_TOOL_NAMES
from xdog.claw.core.types import Group

ROOT = Path(xdog.claw.__file__).parent


def test_retired_packages_and_old_transport_paths_have_no_compatibility_shells():
    for module in ('xdog.claw.core.skills', 'xdog.claw.core.planning',
                   'xdog.claw.core.runtime.gateway', 'xdog.claw.core.runtime.bootstrap',
                   'xdog.claw.core.runtime.native_connection', 'xdog.claw.core.runtime.native_requests',
                   'xdog.claw.core.runtime.native_controls', 'xdog.claw.core.runtime.mcp_protocol',
                   'xdog.claw.core.runtime.xhs_posts', 'xdog.claw.core.tools.tool_xhs_post'):
        assert importlib.util.find_spec(module) is None, module


def test_core_has_no_application_or_concrete_channel_dependencies():
    for path in (ROOT / 'core').rglob('*.py'):
        source = path.read_text()
        for node in ast.walk(ast.parse(source)):
            names = ([node.module or ''] if isinstance(node, ast.ImportFrom)
                     else [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
            for name in names:
                assert not name.startswith(('xdog.claw.application', 'xdog.claw.channels.xhs',
                    'xdog.claw.channels.weixin', 'xdog.claw.channels.tui')), (path, name)
        # Do not replace an import with a same-named context/tool/channel special case.
        for business_name in ('xhs_post', '_xhs_post_reader', 'XhsChannel'):
            assert business_name not in source, (path, business_name)
    assert 'xhs_post' not in AVAILABLE_TOOL_NAMES


async def test_core_keeps_owner_local_agent_mcp_sdk_not_a_parallel_implementation(tmp_path):
    runtime = GroupRuntime(group=Group(id='main', enabled_tools=()), data_dir=tmp_path)
    try:
        assert isinstance(runtime.mcp, McpRegistry)
        other = GroupRuntime(group=Group(id='other', enabled_tools=()), data_dir=tmp_path)
        try:
            assert other.mcp is not runtime.mcp
        finally:
            await other.aclose()
    finally:
        await runtime.aclose()


async def test_real_bootstrap_composes_channel_tool_for_initial_and_new_groups(tmp_path, monkeypatch):
    from xdog.claw.application import bootstrap, gateway
    from xdog.claw.channels.xhs.tools import XhsPostTool
    from xdog.claw.config import ClawConfig, GroupDef

    monkeypatch.setattr(gateway, '_build_model_and_options', lambda _: ('test/dummy', None))
    config = ClawConfig(data_dir=str(tmp_path), enabled_tools=('xhs_post',),
                        groups=(GroupDef(id='main'),))
    built = bootstrap.build(config)
    app = built.application
    try:
        assert built.error is None
        app.auto_register_group('another')
        first = app._runtimes['main'].tools
        second = app._runtimes['another'].tools
        assert isinstance(first[0], XhsPostTool) and isinstance(second[0], XhsPostTool)
        assert first[0] is not second[0]
    finally:
        await app.stop()

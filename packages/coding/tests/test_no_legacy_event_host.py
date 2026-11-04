"""No event server alias remains; no historical event codec is retained."""

from pathlib import Path


def test_old_capability_host_is_not_shipped():
    repo = Path(__file__).resolve().parents[3]
    for name in ('packages/agent/src/xdog/agent/external_event.py',
                 'packages/coding/src/xdog/coding/core/external_events',
                 'packages/eyes/src/xdog/eyes/transport',
                 'packages/eyes/src/xdog/eyes/handlers/session_notify.py',
                 'docs/eyes-host-protocol.md','docs/fixtures/eyes-host-v1.json'):
        assert not (repo/name).exists(),name
    for package in ('coding','eyes'):
        for path in (repo/'packages'/package/'src').rglob('*.py'):
            body = path.read_text()
            for retired in ('enable_external_events','XDOG_EVENT_CONTEXT','HostContext','HostClient'):
                assert retired not in body,(path,retired)


def test_session_environment_is_generic_preserved_and_not_inherited_from_parent(coding_session):
    assert not hasattr(coding_session,'enable_external_events')
    assert not hasattr(coding_session,'external_events')
    context = coding_session.agent.tool_context
    assert context['subprocess_env']['XDOG_SESSION_ID']==coding_session.session_id
    assert 'XDOG_EVENT_CONTEXT' not in context['subprocess_env']


async def test_session_hint_refreshes_on_navigation_and_keeps_caller_environment(coding_session,monkeypatch):
    import asyncio

    from xdog.coding.core.sessions.runtime import SessionRuntime

    context = coding_session.agent.tool_context
    context['subprocess_env']={'OTHER':'kept','XDOG_SESSION_ID':'parent'}
    coding_session.agent.set_tool_context(context)
    runtime = SessionRuntime(coding_session,bound=True)
    runtime.start()
    old = coding_session.session_id
    monkeypatch.setattr(coding_session,'_resolve_selected_model',lambda _:None)

    async def navigate():
        return await asyncio.to_thread(coding_session.new_session)

    try:
        new = await asyncio.wrap_future(runtime.controller.control(navigate,name='new'))
        assert new!=old
        assert coding_session.agent.tool_context['subprocess_env']=={'OTHER':'kept','XDOG_SESSION_ID':new}
    finally:
        await asyncio.wrap_future(runtime.close())

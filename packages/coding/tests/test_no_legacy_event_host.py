"""No event server alias remains; only historical message decoding is compatible."""

from dataclasses import replace
from pathlib import Path


def test_old_capability_host_is_not_shipped():
    repo = Path(__file__).resolve().parents[3]
    for name in ('packages/coding/src/xdog/coding/core/external_events',
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


def test_historical_event_survives_persistence_and_read_only_tui_projection(coding_session):
    from xdog.agent import CustomAgentMessage
    from xdog.coding.modes.interactive.components.chat_log import ChatLog
    from xdog.coding.modes.interactive.session_presenter import SessionPresenter
    from xdog.coding.modes.interactive.theme import create_default_theme
    from xdog.tui.utils import strip_ansi

    raw={'id':'old','registration_id':'old-observer','route_id':'old-route','delivery_ids':['old-delivery'],
         'instruction':'Historical instruction','events':[{'id':'e','source_id':'files','detector_id':'changes',
         'type':'file.modified','observed_at':1,'evidence':[{'path':'old.py'}]}]}
    message = CustomAgentMessage(type='xdog.external_event.v1',data=raw)
    coding_session.session_manager.save_session(replace(coding_session.session_data,messages=[message]))
    saved = coding_session.session_manager.load_session(coding_session.session_id)
    log = ChatLog(create_default_theme())
    SessionPresenter(log).replay((),tuple(saved.messages))
    output='\n'.join(strip_ansi(line) for line in log.render(120))
    assert 'Historical instruction' in output and 'old.py' in output and 'not a human' in output
    assert coding_session.runtime is None and coding_session.messages==[]

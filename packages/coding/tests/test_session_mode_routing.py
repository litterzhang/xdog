import asyncio
import inspect


async def test_sdk_entry_uses_single_session_controller(coding_session, monkeypatch):
    from xdog.coding.core.sessions.controller import SessionController
    submitted = []
    original = SessionController.submit
    def submit(self, *args, **kwargs):
        submitted.append(id(self))
        return original(self, *args, **kwargs)
    monkeypatch.setattr(SessionController, 'submit', submit)
    await asyncio.gather(coding_session.send_message('one'), coding_session.send_message('two'))
    assert len(submitted) == 2
    assert len(set(submitted)) == 1


def test_tui_does_not_own_a_second_agent_prompt_loop():
    from xdog.coding.modes.interactive import interactive_mode
    source = inspect.getsource(interactive_mode)
    assert '.agent.prompt(' not in source
    assert 'NativeExternalEvents' not in source


async def test_new_sdk_input_does_not_reuse_a_controller_whose_last_user_is_closing(coding_session, monkeypatch):
    from concurrent.futures import Future

    from xdog.coding.core.sessions.controller import SessionController
    # An absent capability releases a transient controller after the last call.
    monkeypatch.setattr("xdog.agent.capabilities.runtime.discover", lambda scope: ())
    coding_session.capabilities.providers = ()
    original = SessionController.close
    closing = asyncio.Event()
    receipt = Future()
    underlying = []
    def delayed(self):
        closed = original(self)
        if not underlying:
            underlying.append(closed)
            closing.set()
            return receipt
        return closed
    monkeypatch.setattr(SessionController, 'close', delayed)
    first = asyncio.create_task(coding_session.send_message('first'))
    second = None
    try:
        await asyncio.wait_for(closing.wait(), 2)
        second = asyncio.create_task(coding_session.send_message('new input during previous cleanup'))
        await asyncio.sleep(0.02)
        assert not second.done()  # A new owner cannot write until the previous close receipt settles.
    finally:
        await asyncio.wrap_future(underlying[0])
        receipt.set_result(None)
        await first
        if second is not None:
            await asyncio.wait_for(second, 2)

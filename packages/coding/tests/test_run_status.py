from xdog.coding.modes.interactive.run_status import RunPhase, RunStatus


def test_permission_remembers_background_phase_and_cancel_cannot_be_revived():
    ready = RunStatus()
    waiting = ready.start(100.0)
    reasoning = waiting.advance(RunPhase.REASONING)
    permission = reasoning.advance(RunPhase.PERMISSION)
    running = permission.advance(RunPhase.RUNNING)
    assert ready.phase == RunPhase.READY
    assert running.phase == RunPhase.PERMISSION
    assert running.started == 100.0
    assert running.resume().phase == RunPhase.RUNNING
    canceled = running.advance(RunPhase.CANCELLING)
    assert canceled.advance(RunPhase.RESPONDING) == canceled
    assert canceled.resume() == canceled
    assert canceled.finish() == RunStatus()
    assert canceled.finish().advance(RunPhase.RUNNING) == RunStatus()
    assert canceled.start(200.0).started == 200.0


def test_terminal_status_cannot_have_active_timer():
    status = RunStatus().start(10.0).advance(RunPhase.PERMISSION).finish()
    assert not status.busy
    assert not status.awaiting_permission
    assert status.started is None

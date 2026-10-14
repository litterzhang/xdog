"""Painted review helpers shared by the real-terminal smoke tests."""
from __future__ import annotations

import time


def full_review(terminal, phase):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if 'Enter approves exact ' + phase in terminal.visible():
            return
        terminal.keys('NPage')
        time.sleep(.05)
    raise AssertionError('Sampling full review did not paint:\n' + terminal.visible())


def reviewed(terminal):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if ('Enter continues reviewed data' in terminal.visible()
                or 'o opens reviewed URL once' in terminal.visible()):
            return
        terminal.keys('NPage')
        time.sleep(.04)
    raise AssertionError('Elicitation full review not painted:\n' + terminal.visible())

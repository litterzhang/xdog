"""Deterministic packaged-skill consumers validate executable definitions and current delivery semantics."""
import json
import re
from importlib.resources import files

import pytest
from xdog.eyes.loader import parse


def skill():
    return (files('xdog.eyes')/'skills'/'eyes-observers'/'SKILL.md').read_text()


def examples():
    yield json.loads((files('xdog.eyes')/'examples'/'source-test-review.json').read_text())
    for block in re.findall(r'```json\s*(.*?)```',skill(),re.S):
        yield json.loads(block)


@pytest.mark.parametrize('index',[0,1])
def test_shipped_definition_does_not_request_message_approval(tmp_path,index):
    data = list(examples())[index]
    parse(data,base_dir=tmp_path)
    assert data['version']==2 and data['handlers'][0]['delivery']=='enqueue'
    instruction = data['handlers'][0]['instruction'].lower()
    assert 'after user approval' not in instruction and 'after explicit user approval' not in instruction
    assert 'tool permissions' in instruction


def test_packaged_skill_describes_router_handlers_and_automatic_bounded_batches():
    body = skill()
    assert 'EventRouter' in body and 'EventHandler' in body
    assert '/inbox approve' not in body and '/inbox cancel' in body and '/inbox retry' in body
    assert 'max_messages_per_turn' in body and '1..128' in body and '10' in body
    assert 'one-turn fallback' not in body and 'grants at most one automatic turn' not in body
    assert 'message delivery does not require approval' in body.lower()
    assert 'untrusted' in body and 'unavailable' in body and 'does not\nstart observation' in body

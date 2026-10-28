"""Deterministic packaged-skill consumers validate executable definitions and current delivery semantics."""
import json
import re
from importlib.resources import files

import pytest
from xdog.eyes.loader import parse


def skill():
    return (files('xdog.eyes')/'skills'/'eyes'/'SKILL.md').read_text()


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


def test_packaged_skill_teaches_generic_tool_and_delivery_contract():
    body = skill()
    assert 'EventRouter' in body and 'EventHandler' in body
    for instruction in ('definition_path="/project/eyes.json"',
                        'action="status"', 'action="cancel"', 'action="pause"', 'action="resume"'):
        assert instruction in body
    for field in ('owner', 'definition_path'):
        assert field in body
    for contract in ('relative', 'instruction', 'untrusted', 'unavailable',
                     'not task completion', 'before ACK', 'does not start observation'):
        assert contract in " ".join(body.split())
    assert 'Do not pass a tool `workspace` or CLI `--workspace`' in body
    assert 'one-turn fallback' not in body and 'grants at most one automatic turn' not in body


def test_packaged_skill_has_no_harness_specific_policy_or_setup():
    body = skill()
    assert not re.search(r'\b(claw|coding|goal|flow)\b', body, re.IGNORECASE)
    for host_detail in ('group:', 'max_messages_per_turn', 'allow-all', '/inbox',
                        'acp_socket_path', 'data_dir/acp/agent.sock', 'scheduled_tasks.json'):
        assert host_detail not in body


def test_tool_schema_itself_explains_the_workflow_and_exact_action_fields():
    from xdog.eyes.tool import EyesTool

    schema = EyesTool.parameters
    assert schema['required'] == ['action', 'owner'] and schema['additionalProperties'] is False
    assert 'workspace' not in schema['properties']
    action = schema['properties']['action']
    assert set(action['enum']) == {'create', 'list', 'status', 'pause', 'resume', 'cancel'}
    assert all(name in action['description'] for name in action['enum'])
    for detail in ('definition_path', 'observer_id', 'Omit', 'null', 'accepted'):
        assert detail in action['description']
    path = schema['properties']['definition_path']['description']
    for detail in ('ALREADY SAVED', 'sources', 'detectors', 'handlers', 'routes', 'absolute', 'relative', 'snapshot'):
        assert detail in path
    for detail in ('enqueue needs no cwd', 'receiver resolves', 'assertion', 'prompt requires', 'load_session'):
        assert detail in path
    for detail in ('eyes skill', 'owner', 'JSON', 'destinations', 'permissions'):
        assert detail in EyesTool.description
    assert not re.search(r'\b(claw|coding|goal|flow)\b', json.dumps(schema) + EyesTool.description, re.IGNORECASE)


@pytest.mark.parametrize('name', ['http-announcements', 'http-service-health', 'github-feedback', 'github-actions'])
def test_packaged_external_examples_validate_without_credentials_or_network(tmp_path, name):
    value = json.loads((files('xdog.eyes')/'examples'/f'{name}.json').read_text())
    definition = parse(value, base_dir=tmp_path)
    assert definition.sources and all(source.type in {'http', 'github'} for source in definition.sources)
    assert all(handler.delivery == 'enqueue' for handler in definition.handlers)
    assert all('EXPLICIT_SESSION_ID' == handler.session for handler in definition.handlers)


def test_external_skill_keeps_observation_separate_from_agent_execution():
    body = skill()
    for term in ('record.change', 'value.condition', 'availability', 'bearer_env', 'source_health',
                 'remote-sources-v1', 'untrusted evidence', 'not yet supported', 'does not choose the delivery'):
        assert term in body

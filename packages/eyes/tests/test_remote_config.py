"""Whole-definition external configuration validation remains inert and strict."""
import copy

import pytest
from xdog.eyes.errors import ObserverValidationError
from xdog.eyes.loader import definition_data, parse


def external(observer_data, source=None, detector=None):
    value = copy.deepcopy(observer_data)
    value['sources'] = [source or {'id': 'outside', 'type': 'http', 'url': 'https://example.test/data'}]
    value['detectors'] = [detector or {'id': 'change', 'type': 'record.change', 'source': 'outside'}]
    value['routes'] = [{'id': 'route', 'detector': value['detectors'][0]['id'], 'handler': 'notify',
                        'event_types': ['record.created', 'record.updated']}]
    return value


@pytest.mark.parametrize('kind', ['http', 'github'])
def test_normalized_round_trip(tmp_path, observer_data, kind):
    source = {'id': 'outside', 'type': 'http', 'url': 'https://example.test/data'} if kind == 'http' else {
        'id': 'outside', 'type': 'github', 'repository': 'owner/project', 'resource': 'issues'}
    value = parse(external(observer_data, source), base_dir=tmp_path)
    frozen = definition_data(value)
    assert definition_data(parse(frozen, base_dir=tmp_path)) == frozen


@pytest.mark.parametrize('field,value', [
    ('url', 'file:///etc/passwd'), ('url', 'https://user:password@example.test/'),
    ('url', 'https://example.test/?access_token=secret'), ('url', 'https://example.test/#secret'),
    ('url', 'https://example.test:wrong/'), ('pointer', 'not-a-pointer'), ('pointer', '/bad~3'),
    ('max_bytes', True), ('max_bytes', 0), ('max_bytes', 999999999), ('max_records', -1),
    ('poll_interval_seconds', 0), ('poll_interval_seconds', float('nan')), ('timeout_seconds', 121),
    ('bearer_env', '$(gh auth token)'), ('bearer_env', True), ('headers', {'Authorization': 'secret'}),
    ('mode', 'browser'), ('command', ['sh', '-c', 'anything']),
])
def test_http_rejects_unsafe_or_unbounded_fields(tmp_path, observer_data, field, value):
    data = external(observer_data)
    data['sources'][0][field] = value
    with pytest.raises(ObserverValidationError):
        parse(data, base_dir=tmp_path)


@pytest.mark.parametrize('field,value', [('repository', '../secret'), ('repository', 'a/b/c'),
    ('resource', 'everything'), ('max_pages', 0), ('lookback_seconds', 10), ('overlap_seconds', 999999999),
    ('api_url', 'https://api.test/?a=b')])
def test_github_rejects_ambiguous_scopes(tmp_path, observer_data, field, value):
    source = {'id': 'outside', 'type': 'github', 'repository': 'a/b', 'resource': 'issues', field: value}
    with pytest.raises(ObserverValidationError):
        parse(external(observer_data, source), base_dir=tmp_path)


def test_detector_source_and_route_types_are_checked(tmp_path, observer_data):
    data = external(observer_data)
    data['detectors'][0]['type'] = 'file.change'
    with pytest.raises(ObserverValidationError, match='requires the files observation contract'):
        parse(data, base_dir=tmp_path)
    data = external(observer_data)
    data['routes'][0]['event_types'] = ['scheduler.due']
    with pytest.raises(ObserverValidationError, match='do not match'):
        parse(data, base_dir=tmp_path)


@pytest.mark.parametrize('condition', [{'pointer': '/x', 'op': 'script', 'value': 1},
    {'pointer': '/x', 'op': 'eq'}, {'pointer': '/x', 'op': 'gt', 'value': True},
    {'pointer': '/x', 'op': 'eq', 'value': {}}, {'pointer': '/x', 'op': 'eq', 'value': float('nan')}])
def test_predicates_are_small_typed_comparisons(tmp_path, observer_data, condition):
    data = external(observer_data, detector={'id': 'change', 'source': 'outside',
                                             'type': 'value.condition', 'all': [condition]})
    data['routes'][0]['event_types'] = ['condition.met']
    with pytest.raises(ObserverValidationError):
        parse(data, base_dir=tmp_path)


@pytest.mark.parametrize('collection,field', [('sources', 'type'), ('sources', 'mode'),
                                            ('detectors', 'type')])
def test_unhashable_json_types_are_validation_errors(tmp_path, observer_data, collection, field):
    data = external(observer_data)
    data[collection][0][field] = {}
    with pytest.raises(ObserverValidationError):
        parse(data, base_dir=tmp_path)


async def test_old_daemon_rejects_external_registration_before_any_mutation(tmp_path, observer_data, monkeypatch):
    from xdog.eyes._daemon_control import DaemonControl
    from xdog.eyes.protocol import FEATURES, hello

    calls = []
    async def request(location, action, body, **kwargs):
        calls.append(action)
        if action == 'hello':
            return {'status': 'ok', 'generation': 'old', 'body': {**hello(), 'features': list(FEATURES)}}
        assert action == 'register'
        return {'status': 'accepted', 'generation': 'old',
                'body': {'registration_id': 'file-registration', 'duplicate': False}}
    monkeypatch.setattr('xdog.eyes._daemon_control.request', request)
    control = DaemonControl(owner='test', directory=tmp_path/'private')
    with pytest.raises(RuntimeError, match='restart it explicitly'):
        await control.start(parse(external(observer_data), base_dir=tmp_path), wait_ready=False)
    assert calls == ['hello']
    result = await control.start(parse(observer_data, base_dir=tmp_path), wait_ready=False)
    assert result['registration_id'] == 'file-registration' and calls == ['hello', 'register']
    await control.aclose()


@pytest.mark.parametrize('source', [
    {'id': 'outside', 'type': 'feed', 'url': 'https://example.test/rss'},
    {'id': 'outside', 'type': 'http', 'mode': 'html', 'url': 'https://example.test', 'items_selector': 'a',
     'fields': {'id': {'selector': '', 'attribute': 'href'}}, 'key_field': 'id'},
    {'id': 'outside', 'type': 'github', 'repository': 'o/r', 'resource': 'releases'},
])
async def test_content_features_fail_before_submission_to_first_batch_daemon(
    tmp_path, observer_data, monkeypatch, source,
):
    from xdog.eyes._daemon_control import DaemonControl
    from xdog.eyes.protocol import FEATURES, hello
    calls = []
    async def request(location, action, body, **kwargs):
        calls.append(action)
        assert action == 'hello'
        return {'status': 'ok', 'generation': 'first-batch',
                'body': {**hello(), 'features': [*FEATURES, 'remote-sources-v1']}}
    monkeypatch.setattr('xdog.eyes._daemon_control.request', request)
    control = DaemonControl(owner='test', directory=tmp_path/'private')
    with pytest.raises(RuntimeError, match='content-sources-v1'):
        await control.start(parse(external(observer_data, source), base_dir=tmp_path), wait_ready=False)
    assert calls == ['hello']
    await control.aclose()

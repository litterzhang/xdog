"""Whole-definition external configuration validation remains inert and strict."""
import copy


def external(observer_data, source=None, detector=None):
    value = copy.deepcopy(observer_data)
    value['sources'] = [source or {'id': 'outside', 'type': 'http', 'url': 'https://example.test/data'}]
    value['detectors'] = [detector or {'id': 'change', 'type': 'record.change', 'source': 'outside'}]
    value['routes'] = [{'id': 'route', 'detector': value['detectors'][0]['id'], 'handler': 'notify',
                        'event_types': ['record.created', 'record.updated']}]
    return value

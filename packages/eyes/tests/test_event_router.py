"""Routing is independent of handler transport and retains each event boundary."""
from dataclasses import replace


def test_router_preserves_event_order_and_matches_two_handlers(tmp_path, observer_data):
    from xdog.eyes.loader import parse
    from xdog.eyes.models import Event
    from xdog.eyes.router import EventRouter

    observer_data['handlers'].append({**observer_data['handlers'][0], 'id': 'audit-handler'})
    observer_data['routes'].append({'id': 'audit', 'detector': 'changes', 'handler': 'audit-handler',
                                    'event_types': ['file.modified']})
    events = tuple(Event(str(i), 'files', 'changes', 'file.modified', float(i), ()) for i in range(2))
    routed = EventRouter(parse(observer_data, base_dir=tmp_path)).route(events)
    assert [(r.route_id, r.handler_id) for r in routed] == [('review', 'notify'), ('audit', 'audit-handler')]
    assert [r.events for r in routed] == [events, events]


def test_router_checks_detector_identity_and_event_type_without_dropping_order(tmp_path, observer_data):
    from xdog.eyes.loader import parse
    from xdog.eyes.models import Event
    from xdog.eyes.router import EventRouter

    observer_data['routes'][0]['event_types'] = ['file.modified']
    event = Event('one', 'files', 'changes', 'file.modified', 1.0, ())
    mixed = (replace(event, id='not-matching', type='file.deleted'), event,
             replace(event, id='wrong-detector', detector_id='other'), replace(event, id='two'))
    routed = EventRouter(parse(observer_data, base_dir=tmp_path)).route(mixed)
    assert len(routed) == 1
    assert [item.id for item in routed[0].events] == ['one', 'two']

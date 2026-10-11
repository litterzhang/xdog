import json


def setup(api):
    state = {'show': 0, 'shows': 0}
    def show(args, context):
        state['show'] += 1
        return 'EXACT-SHOW-ONLY'
    def shows(args, context):
        state['shows'] += 1
        return 'EXPLICIT-SHOWS-ONLY'
    api.command('show', 'Exact command', show)
    api.command('shows', 'Neighbor command', shows)
    api.command('counts', 'Inspect actual callback counts', lambda args, context: json.dumps(state))

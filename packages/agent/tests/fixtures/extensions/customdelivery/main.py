import json


def setup(api):
    async def command(args, context):
        options = json.loads(args or '{}')
        if options.get('paths_file'):
            from pathlib import Path

            import xdog.agent.agent as agent_module
            import xdog.agent.extensions.api as api_module
            import xdog.agent.extensions.runtime as runtime_module
            Path(options['paths_file']).write_text(json.dumps(
                [api_module.__file__, runtime_module.__file__, agent_module.__file__]))
        approved = await api.request_context_message('delivery-note', 'CANONICAL-CUSTOM-DELIVERY',
            display=options.get('display', True), details={'opaque': 'NEVER-MODEL-METADATA'},
            deliver_as=options.get('delivery', 'steer'), trigger_turn=options.get('trigger', False))
        if options.get('also_message'):
            await api.request_message('Separate reviewed message', timeout=10)
        if options.get('fail'):
            raise ValueError('Callback failed after review')
        return json.dumps({'approved': approved})
    api.command('request', 'Review canonical custom delivery', command)
    async def tool(params, context):
        return await command(json.dumps(params), context)

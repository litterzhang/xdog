from pathlib import Path


def setup(api):
    async def stream(model, context, options):
        Path(api.settings['called']).write_text('unexpected-model-call')
        yield {'type': 'text', 'text': 'SHOULD-NOT-BE-CALLED'}
        yield {'type': 'done', 'stop_reason': 'stop'}
    def profile(image):
        return {'id': 'vision', 'name': 'Vision admission test', 'context_window': 32000, 'max_tokens': 2048,
                'input': ['text', 'image'] if image else ['text'], 'reasoning': False}
    api.register_provider('local', [profile(True)], stream)
    async def changed(context):
        api.register_provider('local', [profile(False)], stream, replace=True)
    api.on('turn_start', changed)

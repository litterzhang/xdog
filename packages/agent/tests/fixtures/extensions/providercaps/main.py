def setup(api):
    calls = 0
    async def stream(model, context, options):
        nonlocal calls
        calls += 1
        assert model['id'] == 'gpt-5.1/code:preview'
        assert model['max_prompt_tokens'] == 30000 and model['supported_efforts'] == ['low', 'high']
        yield {'type': 'text', 'text': 'CAPABILITY-PROVIDER-ANSWER'}
        yield {'type': 'done'}
    api.register_provider('local', [{
        'id': 'gpt-5.1/code:preview', 'context_window': 48000, 'max_prompt_tokens': 30000, 'max_tokens': 2048,
        'reasoning': True, 'supported_efforts': ['low', 'high'], 'input': ['text', 'image'],
        'vision_limits': {'max_prompt_images': 1, 'max_prompt_image_size': 1024,
                          'supported_media_types': ['image/png']},
    }], stream)
    async def count(args, context):
        return 'CALLS=' + str(calls)
    api.command('count', 'Inspect exact provider invocation count', count)

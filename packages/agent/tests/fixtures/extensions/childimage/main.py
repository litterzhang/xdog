"""Owned context worker: inject an image only into the child-rendered request."""
def setup(api):
    async def context(messages, metadata):
        if metadata.get('child_id'):
            messages[-1]['content'].append({'type': 'image', 'data': api.settings['image'], 'mime_type': 'image/png'})
        return messages
    api.context(context)

def setup(api):
    api.status('setup', 'BOOTSTRAP-LIVE')

    async def show(args, context):
        api.clear_status('setup')
        api.status('state', 'STATUS-LIVE')
        api.widget('details', ['WIDGET-LIVE'])
        return 'UI-SHOWN'

    async def hide(args, context):
        api.status('state', None)
        api.widget('details', None)
        api.clear_status('state')
        api.clear_widget('details')
        return 'UI-CLEARED'

    async def collide(args, context):
        api.status('same', 'status')
        api.widget('same', ['widget'])
        api.clear_status('same')
        return 'UI-WIDGET-RETAINS-OWN-KIND'

    api.command('show', 'Show owned status and widget', show)
    api.command('hide', 'Explicitly remove owned status and widget', hide)
    api.command('collide', 'A status clear cannot remove a replacing widget', collide)

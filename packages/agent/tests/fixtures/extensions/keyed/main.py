def setup(api):
    async def compose(args, context):
        snapshot = await api.get_editor()
        if snapshot is None:
            return 'SHORTCUT-EDITOR-UNAVAILABLE'
        applied = await api.set_editor(snapshot['text'] + ' SHORTCUT-DRAFT', snapshot['revision'])
        return 'SHORTCUT-COMMAND-' + str(applied)

    async def blocked(args, context):
        return 'RESERVED-KEY-MUST-NOT-INVOKE'

    api.shortcut('compose', 'alt+k', 'Compose draft through the actual command owner', compose)
    api.shortcut('blocked', 'ctrl+e', 'Conflicts with effective editor end key', blocked)

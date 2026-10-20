def setup(api):
    async def inspect(args, context):
        return 'INSPECT:' + args
    async def complete(prefix, context):
        return [{'value': prefix + '-ready'}]
    async def rename(args, context):
        api.command('inspect', 'Inspect state', inspect, public_name=args, replace=True, complete=complete)
        return 'renamed'
    api.command('inspect', 'Inspect state', inspect,
                public_name=api.settings.get('public_name', 'inspect-state'), complete=complete)
    api.command('rename', 'Rename public entry', rename)
    api.shortcut('quick', 'f6', 'Quick state', inspect,
                 public_name=api.settings.get('quick_public_name', 'quick-state'))

def setup(api):
    async def compose(args, context):
        snapshot = await api.get_editor()
        if snapshot is None:
            return "EDITOR-UNAVAILABLE"
        if args == "pause":
            proceed = await api.confirm(
                "Apply captured editor transformation", "Typing in the editor invalidates the snapshot"
            )
            if not proceed:
                return "EDITOR-CANCELED"
        applied = await api.set_editor(snapshot["text"] + " EXTENSION-COMPOSED", snapshot["revision"])
        return "EDITOR-APPLIED-" + str(applied)

    async def illegal(params, context):
        snapshot = await api.get_editor()
        applied = await api.set_editor("tool must not set editor", "0" * 64)
        return f"TOOL-EDITOR-{snapshot}-{applied}"

    api.command("compose", "CAS transform command editor without sending a turn", compose)

def setup(api):
    async def remember(args, context):
        api.append_entry("note", {"text": args})
        return "ENTRY-SAVED-" + args

    async def restore(context):
        api.status("entries", "RESTORED-ENTRIES-" + str(len(context.get("entries", []))))

    async def transform(messages, context):
        notes = [row["data"]["text"] for row in context.get("entries", []) if row["kind"] == "note"]
        if notes:
            messages.append({"role": "user", "content": [{"type": "text", "text": "EXTENSION-CONTEXT-" + notes[-1]}]})
        return messages

    api.command("remember", "Remember a bounded session entry", remember)
    api.on("session_start", restore)
    api.context(transform)

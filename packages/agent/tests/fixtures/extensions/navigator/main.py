def setup(api):
    async def browse(args, context):
        snapshot = await api.get_session()
        if snapshot is None:
            return "NAVIGATION-UNAVAILABLE"
        queued = await api.open_sessions(tree=args == "tree")
        if args == "fail":
            raise ValueError("Queue then fail: browser must not open")
        return "NAVIGATION-QUEUED-" + str(queued)

    async def illegal(params, context):
        snapshot = await api.get_session()
        queued = await api.open_sessions()
        assert not await api.navigate_session("new")
        assert await api.list_sessions() is None
        assert await api.get_session_tree() is None
        inspected = await api.get_model()
        usage = await api.get_context_usage()
        if inspected is not None:
            assert inspected["id"] == context["model"]
            assert usage["session_id"] == inspected["session_id"]
        return f"TOOL-NAVIGATION-{snapshot}-{queued}"

    api.command("browse", "Open native session/tree browser after successful callback", browse)
    api.tool("illegal", "Tools cannot request command session actions", {"type": "object"}, illegal)

    async def navigate(args, context):
        snapshot = await api.get_session()
        if snapshot is None:
            return "NAVIGATION-UNAVAILABLE"
        import json

        values = json.loads(args) if args.startswith("{") else {"action": args.split()[0] if args else "new"}
        fail = values.pop("fail", False)
        hold = values.pop("hold", False)
        action = values.pop("action")
        queued = await api.navigate_session(action, **values)
        if hold and queued:
            import asyncio
            from pathlib import Path
            Path(api.settings['marker']).write_text('review-queued')
            async with asyncio.timeout(10):
                while not Path(api.settings['release']).exists():
                    await asyncio.sleep(0.01)
        if fail:
            raise ValueError("Navigation callback failed after review")
        return "NAVIGATION-REVIEWED-" + str(queued)

    api.command("navigate", "Request reviewed deferred native session navigation", navigate)

    async def catalogs(args, context):
        import json
        options = json.loads(args) if args.startswith("{") else {"scope": args or "sessions"}
        scope = options.pop("scope")
        result = await (api.list_sessions(**options) if scope == "sessions" else api.get_session_tree(**options))
        if result is None:
            return "CATALOG-UNAVAILABLE"
        api.append_entry("catalog", result)
        return "CATALOG-PAGE-" + str(result["offset"]) + "-" + str(result["total"])

    api.command("catalog", "Read paginated host session/tree metadata without activation", catalogs)

    async def inspect(args, context):
        import json
        model = await api.get_model()
        usage = await api.get_context_usage()
        if model is None or usage is None:
            return "INSPECTION-UNAVAILABLE"
        value = {"model": model, "usage": usage}
        api.append_entry("inspection", value)
        return "SESSION-INSPECTION " + json.dumps(value, sort_keys=True)

    api.command("inspect", "Inspect actual selected model and measured context without a model call", inspect)

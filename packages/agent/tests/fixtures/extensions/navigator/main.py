def setup(api):
    async def illegal(params, context):
        snapshot = await api.get_session()
        queued = False
        assert not hasattr(api, "open_sessions")
        assert not hasattr(api, "navigate_session")
        assert await api.list_sessions() is None
        assert await api.get_session_tree() is None
        inspected = await api.get_model()
        usage = await api.get_context_usage()
        if inspected is not None:
            assert inspected["id"] == context["model"]
            assert usage["session_id"] == inspected["session_id"]
        return f"TOOL-NAVIGATION-{snapshot}-{queued}"


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

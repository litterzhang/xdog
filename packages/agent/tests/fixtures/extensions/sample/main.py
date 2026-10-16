import asyncio
from pathlib import Path


def setup(api):
    marker = api.settings.get("marker", "")
    print("EXTENSION-IMPORT-PRINT\x1b]52;invalid\a")
    if marker:
        Path(marker).write_text("started\n")
    api.status("mode", "EXTENSION-READY")
    api.status("checks", "Safe declarative status")

    async def tool(params, context):
        if params.get("value") == "hang":
            await asyncio.sleep(60)
        print("EXTENSION-TOOL-PRINT")
        return "EXTENSION-RESULT-" + params.get("value", "default")

    async def command(args, context):
        api.status("mode", "EXTENSION-COMMAND-STATUS")
        api.notify("EXTENSION-COMMAND-NOTICE")
        return "EXTENSION-COMMAND-" + args

    async def pre(name, params, context):
        if name == "filesystem" and params.get("path", "").endswith("rewrite"):
            return {"action": "write", "path": params["path"], "content": "rewritten"}
        if params.get("value") == "veto":
            return None
        return params

    async def post(name, params, result, context):
        if name.startswith("ext_"):
            return result + "-POST"
        return result

    async def turn(context):
        api.notify("EXTENSION-TURN-START")

    async def end(context):
        if marker:
            with Path(marker).open("a") as file:
                file.write("stopped\n")
        print("EXTENSION-STOP-PRINT")

    api.tool("echo", "A controlled test extension tool", {"type": "object", "properties": {
        "value": {"type": "string"}}}, tool)
    api.command("hello", "Test extension command", command)
    api.before_tool(pre)
    api.after_tool(post)
    api.on("turn_start", turn)
    api.on("session_end", end)

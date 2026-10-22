PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII="


def setup(api):
    async def result(params, context):
        value = params.get("value", "mixed")
        if value == "malformed":
            return {"content": [{"type": "image", "data": "not-valid-base64", "mimeType": "image/png"}]}
        if value == "unsupported":
            return {"content": [{"type": "audio", "data": "AA==", "mimeType": "audio/wav"}]}
        if value == "typed":
            from xdog.agent.core import AgentToolResult
            from xdog.ai.types import ImageContent, TextContent

            return AgentToolResult(
                content=(TextContent(text="RICH-TYPED-RESULT"), ImageContent(data=PNG, mime_type="image/png")),
                details={"typed": True},
            )
        if value == "error":
            return api.result([{"type": "text", "text": "RICH-EXTENSION-ERROR"},
                {"type": "image", "data": PNG, "mimeType": "image/png"}],
                structured={"diagnostic": "ERROR-STRUCTURED-TAIL", "retry": False},
                details={"failed": True}, is_error=True)
        if value == "structured":
            return api.result(structured=None, details=False)
        return api.result(
            [
                {"type": "text", "text": "RICH-EXTENSION-RESULT"},
                {"type": "image", "data": PNG, "mimeType": "image/png"},
            ],
            structured=[False, 0, None],
            details={"count": 0, "enabled": False, "value": None},
        )

    async def post(name, params, text, context):
        return text + "-POST-MUST-NOT-DROP-IMAGE"

    api.after_tool(post)

    async def command(args, context):
        if args == 'legacy':
            return 'RICH-LEGACY-COMMAND'
        if args == 'malformed':
            return {'content': [{'type': 'image', 'data': 'bad', 'mimeType': 'image/png'}]}
        if args == 'hang':
            import asyncio
            await asyncio.sleep(60)
        return api.result([{'type': 'text', 'text': 'RICH-COMMAND-OUTPUT'},
                           {'type': 'image', 'data': PNG, 'mimeType': 'image/png'}],
                          structured=[False, 0, None], details={'operator': 'command', 'enabled': False})

    api.command('show', 'Return inspectable command data, not a model turn', command)

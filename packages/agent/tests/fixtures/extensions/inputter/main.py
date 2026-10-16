def setup(api):
    async def input_hook(text, metadata, context):
        if "hook-hang" in text:
            import asyncio

            await asyncio.sleep(60)
        if "hook-handled" in text:
            return {"action": "handled", "feedback": "INPUT-HANDLER-FEEDBACK (not a model answer)"}
        if "hook-fail" in text:
            raise ValueError("Fixture failure; raw input must not pass through")
        if "hook-reject" in text:
            return {"action": "reject", "reason": "User input intentionally rejected by fixture"}
        if "hook-bad" in text:
            return {"action": "transform", "text": 42}
        assert not hasattr(api, 'get_editor')
        assert not hasattr(api, 'open_sessions')
        api.append_entry(
            "input_seen",
            {
                "source": metadata["source"],
                "channel": metadata["channel"],
                "image_count": len(metadata["images"]),
                "has_raw_images": any("data" in image for image in metadata["images"]),
            },
        )
        return {"action": "transform", "text": "INPUT-HOOK-" + text}

    api.on_input(input_hook)

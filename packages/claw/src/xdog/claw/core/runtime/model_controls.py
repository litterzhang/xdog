"""Model metadata and selection on an admitted actual group runtime."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, cast

from xdog.agent.async_work import run_owned
from xdog.agent.core import CatalogStreamFn, ModelStreamFn
from xdog.ai.types import ThinkingLevel
from xdog.claw.core.runtime.group import GroupRuntime


async def reasoning(runtime: GroupRuntime, expected_session: str, level: str | None = None) -> dict[str, Any]:
    runtime.require_current(expected_session)
    session = await runtime.prepare_session(allow_reset=False)
    # An initially empty owner may explicitly bootstrap its first session.
    owner_session = session.meta.session_id
    await session.initialize_extensions()
    if session.agent.state.is_streaming:
        raise ValueError("Reasoning changes require an idle agent")
    if level is not None:
        session._children_idle()
    model_id = session.agent.state.model
    stream = session.agent.stream_fn
    model = await run_owned(stream.resolve_model, model_id) if isinstance(stream, ModelStreamFn) else None
    if (runtime._active_session is not session or runtime.get_new_session_id() != owner_session
            or session.agent.state.model != model_id or session.agent.stream_fn is not stream
            or session.agent.state.is_streaming):
        raise ValueError("Reasoning owner changed during metadata read")
    levels = ("minimal", "low", "medium", "high", "xhigh")
    available = (model.supported_efforts if model is not None and model.supported_efforts is not None
                 else levels if model is not None and model.reasoning else ())
    available = tuple(item for item in available if item in levels) if model is not None and model.reasoning else ()
    if level is not None:
        if level != "off" and level not in available:
            raise ValueError("Thinking level not advertised; choose a supported level or off")
        task = asyncio.current_task()
        def owner_alive() -> bool:
            return (runtime._active_session is session and runtime.get_new_session_id() == owner_session
                    and session.agent.state.model == model_id and not session.agent.state.is_streaming)
        def alive() -> bool:
            return task is not None and not task.cancelling() and owner_alive()
        committed = [False]
        def persist() -> None:
            runtime.transcript_store.save_reasoning(owner_session, model_id, level, alive)
            committed[0] = True
        def apply() -> None:
            session.agent.set_options(replace(session.agent.options,
                thinking=None if level == "off" else cast(ThinkingLevel, level)))
            session._model_metadata = replace(session._model_metadata, metadata=model)
            runtime._source_revision += 1
        try:
            await run_owned(persist)
        except asyncio.CancelledError:
            # A lost acknowledgement does not roll back an already admitted
            # atomic commit. Keep live/persisted options aligned on this owner;
            # do not replay the write or apply it to another session/model.
            if committed[0] and owner_alive():
                apply()
            raise
        if not owner_alive():
            raise ValueError("Reasoning owner changed before applying settings")
        apply()
    return {"session_id": owner_session, "model": model_id,
            "thinking": session.agent.options.thinking or "off",
            "levels": ["off", *available],
            "efforts_reported": model is not None and model.supported_efforts is not None}


async def extension_models(runtime: GroupRuntime, expected_session: str, selected: str = "") -> dict[str, Any]:
    runtime.require_current(expected_session)
    session = await runtime.prepare_session(allow_reset=False)
    # An initially empty owner may explicitly bootstrap its first session.
    owner_session = session.meta.session_id
    await session.initialize_extensions()
    stream = session.agent.stream_fn
    original_model = session.agent.state.model

    def owner_current() -> bool:
        return (runtime._active_session is session and runtime.get_new_session_id() == owner_session
                and session.agent.state.model == original_model and session.agent.stream_fn is stream)

    models = (await run_owned(stream.models) if isinstance(stream, CatalogStreamFn)
              else session.extensions.provider_models())
    if not owner_current():
        raise ValueError("Model catalog owner changed during metadata read")
    if len(models) > 576 or len({model.id for model in models}) != len(models):
        raise ValueError("Invalid/duplicate/oversized configured model catalog")
    for model in models:
        if (not model.id or len(model.id.encode()) > 256 or not isinstance(model.name, str)
                or len(model.name.encode()) > 512 or any(ord(char) < 33 or ord(char) == 127 for char in model.id)):
            raise ValueError("Invalid configured model catalog identity")
    if selected:
        session._children_idle()
        if session.agent.state.is_streaming:
            raise ValueError("Model selection requires an idle session")
        resolved = await run_owned(stream.resolve_model, selected) if isinstance(stream, ModelStreamFn) else None
        if not owner_current() or session.agent.state.is_streaming:
            raise ValueError("Model selection owner changed during metadata read")
        if (resolved is None or resolved.model_type != "chat" or not resolved.supports_streaming
                or resolved.context_window <= 0):
            raise ValueError("Model is not an available streaming chat model on configured adapter; no fallback")
        model = resolved
        selected = model.id  # Persist and route the canonical identity, not a short lookup alias.
        target_thinking = session.agent.options.thinking
        if selected != session.agent.state.model:
            saved = await run_owned(runtime.transcript_store.get_reasoning,
                                            session.meta.session_id, selected)
            if not owner_current() or session.agent.state.is_streaming:
                raise ValueError("Model selection owner changed during saved reasoning read")
            target_thinking = (None if saved == "off" else cast(ThinkingLevel, saved) if saved is not None
                               else runtime.agent_config.options.thinking)
        if not model.reasoning:
            target_thinking = None
        if (target_thinking is not None and model.supported_efforts is not None
                and target_thinking not in model.supported_efforts):
            raise ValueError("Model does not advertise current thinking level; choose off before switching")
        previous_model, owner_task = session.agent.state.model, asyncio.current_task()
        def owner_alive() -> bool:
            return (runtime._active_session is session and runtime.get_new_session_id() == owner_session
                    and session.agent.state.model == previous_model and not session.agent.state.is_streaming)
        def admit() -> bool:
            return owner_task is not None and not owner_task.cancelling() and owner_alive()
        committed = [False]
        def persist() -> None:
            runtime.transcript_store.save_model(session.meta.session_id, selected, admit)
            committed[0] = True
        def apply() -> None:
            session.agent.set_model(selected)
            session.agent.set_options(replace(session.agent.options, thinking=target_thinking))
            session.agent.set_model_capabilities(context_window=model.context_window,
                                                 max_prompt_tokens=model.max_prompt_tokens,
                                                 supports_tool_calls=model.supports_tool_calls)
            session._model_metadata = replace(session._model_metadata, model_id=selected,
                metadata=model, supports_tool_calls=model.supports_tool_calls)
            runtime.model, runtime.context_window = selected, model.context_window
            runtime.max_prompt_tokens = model.max_prompt_tokens
            runtime._source_revision += 1
        try:
            await run_owned(persist)
        except asyncio.CancelledError:
            if committed[0] and owner_alive():
                apply()  # Lost acknowledgement is not a rollback/replay of the admitted atomic setting.
            raise
        if not owner_alive():
            raise ValueError("Model session changed before applying stored selection")
        apply()
    active_id = session.agent.state.model
    active_model = await run_owned(stream.resolve_model, active_id) if isinstance(stream, ModelStreamFn) else None
    if (runtime._active_session is not session or runtime.get_new_session_id() != owner_session
            or session.agent.state.model != active_id or session.agent.stream_fn is not stream):
        raise ValueError("Active model owner changed during metadata read")
    context_window = (active_model.context_window if active_model is not None else
                      0 if session.agent.state.model.startswith("ext-") else session.agent.context_window)
    return {"session_id": runtime.get_new_session_id() or "", "model": session.agent.state.model,
            "model_context_window": context_window, "thinking": session.agent.options.thinking or "off",
            "models": [{"id": model.id, "name": model.name, "context_window": model.context_window,
                        "max_prompt_tokens": model.max_prompt_tokens, "max_tokens": model.max_tokens,
                        "reasoning": model.reasoning, "supports_tool_calls": model.supports_tool_calls}
                       for model in models]}

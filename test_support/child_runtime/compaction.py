"""Supervised long tool runs use the native Agent policy and durable original-history exports."""

from __future__ import annotations

import asyncio
import threading

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult, CompactionConfig
from xdog.agent.child_config import ChildConfig
from xdog.agent.child_model import resolve_child_model
from xdog.agent.compaction import estimate_input_tokens, safe_cut_indices
from xdog.agent.core import ModelStreamFn
from xdog.agent.events import CompactionEndEvent, CompactionFailedEvent
from xdog.agent.messages import dicts_to_messages, messages_to_dicts
from xdog.agent.snapshots import decode_snapshot_messages, encode_snapshot_messages
from xdog.ai.types import AssistantMessage, DoneEvent, Model, StreamOptions, TextContent, ToolCall, Usage
from xdog.ai.utils.event_stream import EventStream


class LongToolAdapter:
    def __init__(self, *, failure=False, block=False, advance_clock=False):
        self.metadata = Model(id="offline/child", context_window=7000, max_prompt_tokens=5000, max_tokens=128)
        self.requests = []
        self.summaries = []
        self.snapshots = []
        self.agent = None
        self.failure = failure
        self.block = block
        self.advance_clock = advance_clock
        self.entered = threading.Event()
        self.closed = threading.Event()

    def resolve_model(self, name):
        return self.metadata if name == self.metadata.id else None

    def __call__(self, model, context, options):
        assert model == self.metadata.id
        limit = min(self.metadata.max_prompt_tokens, self.metadata.context_window - options.max_tokens)
        assert estimate_input_tokens(context, options) <= limit
        safe_cut_indices(context.messages)
        if not context.tools:
            self.summaries.append(context)
            self.snapshots.append(self.agent.messages)
            self.entered.set()
            if self.block:

                async def generate():
                    try:
                        await asyncio.Event().wait()  # Ignore abort; owner must close this request.
                        yield DoneEvent(message=AssistantMessage())
                    finally:
                        self.closed.set()

                return EventStream.from_async_generator(generate())
            return EventStream.empty(
                AssistantMessage(
                    content=() if self.failure else (TextContent(text="Goal and prior findings retained"),),
                    usage=Usage(input=3000, output=10),
                )
            )
        self.requests.append(context)
        assert "CURRENT_GOAL" in str(context.messages)
        number = len(self.requests)
        response = AssistantMessage(
            content=(
                (ToolCall(id=f"call-{number}", name="read", arguments={}),)
                if number <= 30
                else (TextContent(text="finished"),)
            ),
            usage=Usage(input=2000, output=10),
            stop_reason="toolUse" if number <= 30 else "stop",
        )
        return EventStream.empty(response)


def make_owner(tmp_path, adapter, *, archive=None, events=None, explicit=False, runtime_factory, child_store_type):
    store = child_store_type(tmp_path)

    def factory(spec, publish):
        selected = resolve_child_model(
            adapter, "offline/parent", StreamOptions(max_tokens=128), ChildConfig(model="offline/child"), 12000, True
        )
        stream = selected.stream(adapter)
        assert isinstance(stream, ModelStreamFn) and stream.resolve_model(selected.id) == adapter.metadata

        async def execute(*args, **kwargs):
            if adapter.advance_clock and len(adapter.requests) == 18:
                loop = asyncio.get_running_loop()
                original = loop.time
                loop.time = lambda: original() + 601
            text = f"ROUND-{len(adapter.requests)} " + "tool material " * 130
            return AgentToolResult(content=(TextContent(text=text),))

        compaction = CompactionConfig(auto=True) if explicit else selected.compaction(stream)
        assert compaction is not None and compaction.auto
        adapter.agent = Agent(
            stream,
            config=AgentConfig(
                model=selected.id,
                options=selected.options,
                system_prompt=selected.prompt("SYSTEM_MUST_STAY"),
                compaction=compaction,
            ),
            tools=[AgentTool(name="read", parameters={"type": "object"}, execute=execute)],
        )
        return adapter.agent

    async def on_event(spec, event):
        if events is not None:
            events.append(event)

    owner = runtime_factory(
        factory,
        store.save,
        begin=store.begin,
        persist_admission=store.save_admission,
        archive_compaction=archive or store.archive_compaction,
        on_event=on_event,
        forget=store.forget,
    )
    return owner, store


class TestChildCompaction:
    @pytest.mark.parametrize("explicit", [False, True])
    async def test_long_tool_loop_auto_compacts_after_old_calls_tokens_and_time_boundaries(
        self, tmp_path, explicit, *, runtime_factory, child_store_type
    ):
        adapter = LongToolAdapter(advance_clock=True)
        events = []
        owner, store = make_owner(
            tmp_path,
            adapter,
            events=events,
            explicit=explicit,
            runtime_factory=runtime_factory,
            child_store_type=child_store_type,
        )
        try:
            identifier = await owner.start("CURRENT_GOAL", inherit_context=False)
            result = await owner.control("wait", identifier)
            assert result["status"] == "completed", result["error"]
            assert len(adapter.requests) == 31 and len(adapter.summaries) >= 2
            assert result["calls"] == 31 + len(adapter.summaries)
            assert result["tokens"] == 31 * 2010 + len(adapter.summaries) * 3010
            assert result["tokens"] > 32000 and result["calls"] > 16
            assert isinstance(adapter.agent.stream_fn, ModelStreamFn)
            assert adapter.agent.stream_fn.resolve_model("offline/child") == adapter.metadata
            archives = store.compaction_archives(identifier)
            assert len(archives) == len(adapter.summaries)
            assert len([event for event in events if isinstance(event, CompactionEndEvent)]) == len(archives)
            for archive, snapshot in zip(archives, adapter.snapshots, strict=True):
                assert archive["messages"] == encode_snapshot_messages(snapshot)
                assert decode_snapshot_messages(archive["messages"]) == snapshot
                safe_cut_indices(decode_snapshot_messages(archive["messages"]))
            safe_cut_indices(dicts_to_messages(result["messages"]))
            assert store.load()[0]["messages"] == result["messages"]
            # At least one full raw tool result is no longer in the live context,
            # but remains exactly recoverable from the pre-commit archive.
            assert "ROUND-1 " not in str(result["messages"]) and "ROUND-1 " in str(archives)
            await owner.control("close", identifier)
            assert store.load() == [] and store.compaction_archives(identifier) == archives
        finally:
            await owner.aclose()

    async def test_native_summary_failure_preserves_original_history_and_durable_archive(
        self, tmp_path, *, runtime_factory, child_store_type
    ):
        adapter = LongToolAdapter(failure=True)
        events = []
        owner, store = make_owner(
            tmp_path, adapter, events=events, runtime_factory=runtime_factory, child_store_type=child_store_type
        )
        try:
            identifier = await owner.start("CURRENT_GOAL")
            result = await owner.control("wait", identifier)
            assert result["status"] == "failed" and "Compaction" in result["error"]
            source = messages_to_dicts(list(adapter.snapshots[0]))
            assert result["messages"][: len(source)] == source
            assert store.compaction_archives(identifier)[0]["messages"] == encode_snapshot_messages(
                adapter.snapshots[0])
            assert not any(isinstance(event, CompactionEndEvent) for event in events)
            assert any(isinstance(event, CompactionFailedEvent) for event in events)
        finally:
            await owner.aclose()

    async def test_archive_failure_prevents_summary_request_and_does_not_discard_original_pairs(
        self, tmp_path, *, runtime_factory, child_store_type
    ):
        adapter = LongToolAdapter()
        originals = []

        def failed_archive(spec, generation, messages):
            originals.append(messages)
            raise OSError("archive disk full")

        owner, store = make_owner(
            tmp_path,
            adapter,
            archive=failed_archive,
            runtime_factory=runtime_factory,
            child_store_type=child_store_type,
        )
        try:
            identifier = await owner.start("CURRENT_GOAL")
            result = await owner.control("wait", identifier)
            assert result["status"] == "failed" and len(originals) == 1 and not adapter.summaries
            source = messages_to_dicts(list(originals[0]))
            assert result["messages"][: len(source)] == source
            assert store.load()[0]["messages"] == result["messages"]
        finally:
            await owner.aclose()

    async def test_cancel_during_native_summary_closes_request_and_keeps_original_history(
        self, tmp_path, *, runtime_factory, child_store_type
    ):
        adapter = LongToolAdapter(block=True)
        owner, store = make_owner(tmp_path, adapter, runtime_factory=runtime_factory, child_store_type=child_store_type)
        try:
            identifier = await owner.start("CURRENT_GOAL")
            assert await asyncio.to_thread(adapter.entered.wait, 5)
            result = await owner.control("cancel", identifier)
            assert result["status"] == "canceled" and adapter.closed.is_set()
            source = messages_to_dicts(list(adapter.snapshots[0]))
            assert result["messages"][: len(source)] == source
            assert store.compaction_archives(identifier)[0]["messages"] == encode_snapshot_messages(
                adapter.snapshots[0])
            assert not owner.active
        finally:
            await owner.aclose()

    async def test_repeated_cancel_waits_for_archive_writer_before_returning_or_starting_summary(
        self, tmp_path, *, runtime_factory, child_store_type
    ):
        adapter = LongToolAdapter()
        entered, release = threading.Event(), threading.Event()
        store = child_store_type(tmp_path)

        def blocked_archive(spec, generation, messages):
            entered.set()
            assert release.wait(5)
            store.archive_compaction(spec, generation, messages)

        owner, _ = make_owner(
            tmp_path,
            adapter,
            archive=blocked_archive,
            runtime_factory=runtime_factory,
            child_store_type=child_store_type,
        )
        try:
            identifier = await owner.start("CURRENT_GOAL")
            assert await asyncio.to_thread(entered.wait, 5)
            operation = asyncio.create_task(owner.control("cancel", identifier))
            await asyncio.sleep(0.02)
            operation.cancel()
            await asyncio.sleep(0.02)
            operation.cancel()
            assert not operation.done() and owner.active and not adapter.summaries
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await operation
            result = await owner.control("wait", identifier)
            assert result["status"] == "canceled" and not adapter.summaries
            assert store.compaction_archives(identifier)[0]["messages"] == encode_snapshot_messages(
                owner._children[identifier].transcript)
        finally:
            release.set()
            await owner.aclose()

    async def test_auto_compaction_without_archive_contract_fails_explicitly_before_any_history_loss(
        self, tmp_path, *, runtime_factory, child_store_type
    ):
        adapter = LongToolAdapter()
        owner, store = make_owner(tmp_path, adapter, runtime_factory=runtime_factory, child_store_type=child_store_type)
        owner.archive_compaction = None
        try:
            identifier = await owner.start("CURRENT_GOAL")
            result = await owner.control("wait", identifier)
            assert result["status"] == "failed" and "archive_compaction" in result["error"]
            assert not adapter.summaries and not store.compaction_archives(identifier)
            assert "ROUND-1 " in str(result["messages"])
            safe_cut_indices(dicts_to_messages(result["messages"]))
        finally:
            await owner.aclose()

    async def test_native_summary_cleanup_failure_never_commits_and_original_history_remains_archived(
        self, tmp_path, *, runtime_factory, child_store_type
    ):
        class Adapter(LongToolAdapter):
            def __call__(self, model, context, options):
                stream = super().__call__(model, context, options)
                if not context.tools:

                    async def failed_close():
                        await stream.__class__.aclose(stream)
                        raise OSError("summary cleanup failed")

                    stream.aclose = failed_close
                return stream

        adapter = Adapter()
        events = []
        owner, store = make_owner(
            tmp_path, adapter, events=events, runtime_factory=runtime_factory, child_store_type=child_store_type
        )
        try:
            identifier = await owner.start("CURRENT_GOAL")
            result = await owner.control("wait", identifier)
            assert result["status"] == "failed"
            assert not any(isinstance(event, CompactionEndEvent) for event in events)
            source = messages_to_dicts(list(adapter.snapshots[0]))
            assert result["messages"][: len(source)] == source
            assert store.compaction_archives(identifier)[0]["messages"] == encode_snapshot_messages(
                adapter.snapshots[0])
        finally:
            await owner.aclose()

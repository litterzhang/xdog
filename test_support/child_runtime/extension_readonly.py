"""Child UI ownership and pending-query reads do not retain extension write adapters."""

from __future__ import annotations

import asyncio
import shutil

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.children import ChildSpec
from xdog.agent.extensions.runtime import ExtensionRuntime

from test_support.child_runtime.processes import AGENT_TEST_FIXTURES

REMOVED = (
    "editor_write_enabled",
    "editor_write_generation",
    "enable_editor_write",
    "enable_queued_input",
    "set_queued_input_sender",
    "queued_input_generation",
    "bind_queued_input",
)


class TestChildExtensionReadonly:
    @pytest.mark.parametrize("name", REMOVED)
    def test_child_extension_mutation_adapters_are_absent(
        self, name, tmp_path, *, child_store_type, child_extensions_type
    ):
        parent = ExtensionRuntime([], tmp_path, tmp_path)
        children = child_extensions_type(parent, child_store_type(tmp_path / "records"))
        assert not hasattr(children, name)
        assert not hasattr(children, "_queued_inputs") and not hasattr(children, "_editor_write_tokens")

    async def test_pending_boolean_uses_original_agent_loop_without_transport_or_queue_write_grant(
        self, tmp_path, *, child_store_type, child_extensions_type
    ):
        scope = {"session_id": "original"}
        parent = ExtensionRuntime([], tmp_path, tmp_path, context=lambda: scope)
        children = child_extensions_type(parent, child_store_type(tmp_path / "records"))
        spec = ChildSpec(id="a" * 12, task="Read original pending boolean")
        runtime = await children.create(spec, lambda event: None, initialize=False)
        agent = Agent(
            lambda *args: pytest.fail("Metadata read cannot run a model"), config=AgentConfig(model="offline")
        )
        children.bind_pending_messages(spec.id, agent)
        reader = runtime.pending_messages
        try:
            assert reader is not None

            # Worker callbacks normally run on another loop; use an owned test thread.
            async def external_read():
                return await asyncio.to_thread(lambda: asyncio.run(reader()))

            assert await external_read() is False
            agent.follow_up("PRIVATE QUEUED BODY /quit")
            assert await external_read() is True
            assert agent.has_queued_messages() and not agent.messages
            assert all(
                not hasattr(runtime, name)
                for name in (
                    "editor_write_handler",
                    "queued_input_handler",
                    "name_request_handler",
                    "active_tools_request_handler",
                    "compaction_request_handler",
                    "context_message_handler",
                )
            )
            with pytest.raises(ValueError, match="already bound"):
                children.bind_pending_messages(spec.id, agent)
            scope["session_id"] = "replacement"
            assert await external_read() is None
            scope["session_id"] = "original"
            assert await external_read() is True
        finally:
            await children.close(spec)
            assert runtime.pending_messages is None and await reader() is None
            assert not children._pending_readers and not children.owners
            await parent.aclose()

    async def test_basic_child_dialogs_stay_scoped_cancelable_and_independent_of_pending_queries(
        self, tmp_path, *, child_store_type, child_extensions_type
    ):
        root = tmp_path / "extensions"
        shutil.copytree(AGENT_TEST_FIXTURES / "extensions/dialogue", root / "dialogue")
        parent = ExtensionRuntime(["dialogue"], root, tmp_path, context=lambda: {"session_id": "original"})
        children = child_extensions_type(parent, child_store_type(tmp_path / "records"))
        events = []
        children.default_ui = "transport", events.append
        spec = ChildSpec(id="b" * 12, task="Owned basic dialogs")
        children.reserve(spec.id)
        runtime = await children.create(spec, lambda event: None)
        agent = Agent(lambda *args: pytest.fail("Basic questions cannot call a model"))
        children.bind_pending_messages(spec.id, agent)
        work = asyncio.create_task(runtime.command("ext-dialogue-ask", ""))
        try:
            async with asyncio.timeout(5):
                while not any(event["type"] == "child_extension_dialog" for event in events):
                    await asyncio.sleep(0.003)
            event = next(event for event in events if event["type"] == "child_extension_dialog")
            identifier = event["dialog"]["id"]
            assert event["dialog"]["kind"] == "select"
            assert not children.answer(spec.id, identifier, "foreign", "Red")
            assert children.contains(spec.id, identifier, "transport")
            assert children.input_owner(spec.id, identifier, "transport")
            assert children.answer(spec.id, identifier, "transport", "Blue")
            async with asyncio.timeout(5):
                while sum(event["type"] == "child_extension_dialog" for event in events) < 2:
                    await asyncio.sleep(0.003)
            children.detach("transport")
            assert await asyncio.wait_for(work, 5) == "DIALOG-RESULT-Blue-None-False"
            assert not children.has_pending_dialogs and runtime.dialog_handler is None
            assert await runtime.pending_messages() is False  # Disconnect is not read-query revocation.
            assert not any("editor_write" in event["type"] or "input_queued" in event["type"] for event in events)
        finally:
            await asyncio.gather(work, return_exceptions=True)
            await children.close(spec)
            await parent.aclose()

    async def test_native_parent_contributions_are_not_instantiated_as_child_workers(
        self, tmp_path, *, child_store_type, child_extensions_type
    ):
        parent = ExtensionRuntime((), tmp_path / "extensions", tmp_path)

        async def native(args):
            return "only the actual parent"

        parent.bind_native_commands("native_owner", {"inspect-parent": ("Parent only", native)}, valid=lambda: True)
        assert parent.snapshot()["extensions"] == [{"name": "native_owner", "state": "active"}]
        children = child_extensions_type(parent, child_store_type(tmp_path / "records"))
        spec = ChildSpec(id="c" * 12, task="Ordinary child must remain usable")
        runtime = await children.create(spec, lambda event: None)
        try:
            assert runtime.enabled == () and not runtime.validation_failed
            assert "inspect-parent" not in runtime.commands()
            assert not runtime._manager._clients
            assert await parent.command("inspect-parent", "") == "only the actual parent"
        finally:
            await children.close(spec)
            await parent.aclose()

    def test_terminal_runtime_and_child_capture_apis_are_absent(self, child_extensions_type):
        for name in ("terminal_metadata", "capture_terminal", "_publish_terminal"):
            assert not hasattr(child_extensions_type, name)

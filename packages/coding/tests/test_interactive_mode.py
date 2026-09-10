"""Tests for interactive mode components."""


from xdog.coding.modes.interactive.theme import (
    create_default_theme,
)


class TestFooterComponent:
    """Tests for the footer component."""


class TestAssistantMessageComponent:
    def test_renders_reasoning_and_response(self):
        from xdog.coding.modes.interactive.components.assistant_message import (
            AssistantMessageComponent,
        )
        from xdog.tui.utils import strip_ansi

        component = AssistantMessageComponent(
            "final answer",
            create_default_theme(),
            thinking="reasoning details",
        )
        rendered = "\n".join(strip_ansi(line) for line in component.render(80))

        assert "Thinking" in rendered
        assert "reasoning details" in rendered
        assert "final answer" in rendered

    def test_reasoning_can_collapse_and_reexpand(self):
        from xdog.coding.modes.interactive.components.assistant_message import (
            AssistantMessageComponent,
        )
        from xdog.tui.utils import strip_ansi

        component = AssistantMessageComponent(
            "final answer",
            create_default_theme(),
            thinking="reasoning start\nREASONING-TAIL",
        )

        component.set_expanded(False)
        collapsed = "\n".join(strip_ansi(line) for line in component.render(80))
        assert "Thinking" in collapsed
        assert "REASONING-TAIL" not in collapsed
        assert "final answer" in collapsed

        component.set_expanded(True)
        expanded = "\n".join(strip_ansi(line) for line in component.render(80))
        assert "REASONING-TAIL" in expanded
        assert "final answer" in expanded

        component.set_expanded(False)
        recollapsed = "\n".join(strip_ansi(line) for line in component.render(80))
        assert "REASONING-TAIL" not in recollapsed
        assert "final answer" in recollapsed

    def test_reasoning_strips_terminal_control_sequences(self):
        from xdog.coding.modes.interactive.components.assistant_message import (
            AssistantMessageComponent,
        )
        from xdog.tui.utils import strip_ansi

        component = AssistantMessageComponent(
            "answer",
            create_default_theme(),
            thinking="safe\x1b[2J\x1b]52;c;clipboard\x07tail",
        )
        rendered = "\n".join(strip_ansi(line) for line in component.render(80))

        assert "safetail" in rendered
        assert "\x1b[2J" not in rendered
        assert "\x1b]52" not in rendered


class TestPermissionPromptComponent:
    def test_allow_once_and_escape_to_deny(self):
        from xdog.coding.core.permissions import PermissionRequest
        from xdog.coding.modes.interactive.components.permission_prompt import (
            PermissionPromptComponent,
        )
        from xdog.tui.keys import KeyEvent
        from xdog.tui.utils import strip_ansi

        decisions: list[str] = []
        request = PermissionRequest(
            id="req-1",
            tool_name="bash",
            arguments={"command": "pytest"},
            summary="Run shell command:\npytest",
        )
        prompt = PermissionPromptComponent(
            request,
            create_default_theme(),
            decisions.append,  # type: ignore[arg-type]
        )

        rendered = "\n".join(strip_ansi(line) for line in prompt.render(80))
        assert '"command"' not in rendered

        assert prompt.handle_input(KeyEvent(key="enter"))
        assert decisions == ["allow_once"]

        decisions.clear()
        prompt = PermissionPromptComponent(
            request,
            create_default_theme(),
            decisions.append,  # type: ignore[arg-type]
        )
        assert prompt.handle_input(KeyEvent(key="escape"))
        assert decisions == ["deny"]

    def test_focused_permission_escape_denies_without_canceling_turn(self):
        from unittest.mock import MagicMock

        from xdog.coding.core.permissions import PermissionRequest
        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
        from xdog.tui.keys import KeyEvent

        session = MagicMock()
        session.agent.subscribe.return_value = lambda: None
        session.permissions.mode = "ask"
        mode = InteractiveMode(session)
        mode._is_busy = True
        request = PermissionRequest(
            id="req-escape",
            tool_name="bash",
            arguments={"command": "pwd"},
            summary="Run pwd",
        )
        mode._show_permission_request(request)

        mode._tui._dispatch_input(KeyEvent(key="escape"))

        session.permissions.resolve.assert_called_once_with("req-escape", "deny")
        session.cancel.assert_not_called()

    def test_permission_panel_is_after_editor(self):
        from unittest.mock import MagicMock

        from xdog.coding.core.permissions import PermissionRequest
        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

        session = MagicMock()
        session.agent.subscribe.return_value = lambda: None
        session.permissions.mode = "ask"
        mode = InteractiveMode(session)
        root = mode._tui.children[0]

        assert root is mode._layout
        assert root.editor is mode._editor
        assert root.permission is None
        assert mode._permission_container.children == []

        mode._show_permission_request(PermissionRequest(
            id="req-inline",
            tool_name="bash",
            arguments={"command": "pwd"},
            summary="Run shell command:\npwd",
        ))

        assert mode._permission_container.children == [mode._permission_prompt]
        assert mode._tui._focused is mode._permission_prompt


class TestToolExecutionComponent:
    """Tests for the tool execution component."""

    def test_summarize_args_bash(self):
        from xdog.coding.modes.interactive.components.tool_execution import _summarize_args
        result = _summarize_args({"command": "echo hello"}, "bash")
        assert result == "echo hello"

    def test_summarize_args_filesystem(self):
        from xdog.coding.modes.interactive.components.tool_execution import _summarize_args
        result = _summarize_args({"action": "read", "path": "/test.py"}, "filesystem")
        assert result == "read /test.py"


    def test_summarize_args_grep(self):
        from xdog.coding.modes.interactive.components.tool_execution import _summarize_args
        result = _summarize_args({"pattern": "foo", "path": "."}, "grep")
        assert result == "/foo/ in ."


class TestChatLog:
    """Tests for the chat log component."""

    def test_chat_log_streaming(self):
        from xdog.coding.modes.interactive.components.chat_log import ChatLog
        theme = create_default_theme()
        log = ChatLog(theme)
        log.start_assistant("Hel", "stream1")
        log.update_assistant("Hello wo", "stream1")
        log.finalize_assistant("Hello world!", "stream1")
        lines = log.render(80)
        assert len(lines) > 0

    def test_completed_history_is_not_pruned_at_a_fixed_count(self):
        from xdog.coding.modes.interactive.components.chat_log import ChatLog
        from xdog.tui.utils import strip_ansi

        log = ChatLog(create_default_theme())
        for index in range(201):
            log.add_assistant(f"HISTORY-{index}")

        rendered = "\n".join(strip_ansi(line) for line in log.render(80))
        assert "HISTORY-0" in rendered
        assert "HISTORY-200" in rendered

    def test_detail_toggle_updates_retained_components(self):
        from xdog.coding.modes.interactive.components.chat_log import ChatLog
        from xdog.tui.utils import strip_ansi

        log = ChatLog(create_default_theme())
        log.add_assistant("answer", thinking="THINKING-TAIL")
        tool = log.add_tool("bash", {"command": "pytest"})
        tool.set_result(f"first\n{'x' * 600}\nTOOL-TAIL")

        log.set_details_expanded(False)
        collapsed = "\n".join(strip_ansi(line) for line in log.render(80))
        assert "THINKING-TAIL" not in collapsed
        assert "TOOL-TAIL" not in collapsed

        log.set_details_expanded(True)
        expanded = "\n".join(strip_ansi(line) for line in log.render(80))
        assert "THINKING-TAIL" in expanded
        assert "TOOL-TAIL" in expanded


class TestInteractiveMessageHandling:
    def test_submit_while_busy_queues_message_below_editor(self):
        from unittest.mock import MagicMock

        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
        from xdog.tui.utils import strip_ansi

        session = MagicMock()
        session.agent.subscribe.return_value = lambda: None
        session.permissions.mode = "ask"
        mode = InteractiveMode(session)
        mode._worker_active = True
        mode._is_busy = True

        mode._start_turn("run this after the current turn")
        mode._start_turn("and include this too")

        assert list(mode._pending_messages) == [
            ("run this after the current turn", True),
            ("and include this too", True),
        ]
        rendered = "\n".join(
            strip_ansi(line) for line in mode._message_queue_container.render(80)
        )
        assert "Queued messages (2)" in rendered
        assert "run this after the current turn" in rendered
        assert "and include this too" in rendered

    def test_queue_worker_drains_messages_in_order(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

        session = MagicMock()
        session.agent.subscribe.return_value = lambda: None
        session.permissions.mode = "ask"
        mode = InteractiveMode(session)
        mode._worker_active = True
        mode._pending_messages.append(("second", True))
        mode._pending_messages.append(("third", True))
        mode._async_agent_turn = AsyncMock()

        asyncio.run(mode._async_agent_queue("first"))

        assert [call.args[0] for call in mode._async_agent_turn.await_args_list] == [
            "first",
            "second",
            "third",
        ]
        assert not mode._worker_active
        assert list(mode._pending_messages) == []

    def test_escape_cancels_active_work_and_clears_queue(self):
        from unittest.mock import MagicMock

        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
        from xdog.tui.keys import KeyEvent
        from xdog.tui.utils import strip_ansi

        session = MagicMock()
        session.agent.subscribe.return_value = lambda: None
        session.permissions.mode = "ask"
        mode = InteractiveMode(session)
        mode._is_busy = True
        mode._worker_active = True
        mode._pending_messages.append(("queued", True))
        mode._update_message_queue()

        result = mode._handle_global_input(KeyEvent(key="escape"))

        assert result == {"consume": True}
        session.cancel.assert_called_once_with()
        assert mode._cancel_requested
        assert list(mode._pending_messages) == []
        assert mode._message_queue_container.render(80) == []
        assert mode._layout.queue is None
        assert mode._editor.get_text() == "queued"

        mode._handle_global_input(KeyEvent(key="escape"))
        session.cancel.assert_called_once_with()
        cancellation_notices = [
            child
            for child in mode._chat_log.children
            if "cancelling active turn" in "\n".join(strip_ansi(line) for line in child.render(80))
        ]
        assert len(cancellation_notices) == 1

    def test_ctrl_o_opens_details_without_rewriting_transcript(self):
        from unittest.mock import MagicMock

        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
        from xdog.tui.keys import KeyEvent
        from xdog.tui.utils import strip_ansi

        session = MagicMock()
        session.agent.subscribe.return_value = lambda: None
        session.permissions.mode = "ask"
        mode = InteractiveMode(session)
        mode._chat_log.add_assistant("answer", thinking="THINKING-DETAIL")

        transcript = "\n".join(strip_ansi(line) for line in mode._chat_log.render(80))
        assert "THINKING-DETAIL" not in transcript

        assert mode._handle_global_input(KeyEvent(key="o", ctrl=True)) == {"consume": True}
        assert "\n".join(strip_ansi(line) for line in mode._chat_log.render(80)) == transcript
        details = "\n".join(strip_ansi(line) for line in mode._details_panel.render(80))
        assert "THINKING-DETAIL" in details

    def test_message_end_displays_non_streamed_text(self):
        import queue

        from xdog.agent import MessageEndEvent
        from xdog.ai.types import AssistantMessage, TextContent
        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

        mode = object.__new__(InteractiveMode)
        mode._event_queue = queue.Queue()
        mode._dispatch_agent_event(MessageEndEvent(
            message=AssistantMessage(content=(TextContent(text="final text"),)),
        ))

        event = mode._event_queue.get_nowait()
        assert event["type"] == "text_update"
        assert event["text"] == "final text"

    def test_agent_end_surfaces_thrown_provider_error(self):
        import queue

        from xdog.agent import AgentEndEvent
        from xdog.ai.types import AssistantMessage, TextContent
        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

        mode = object.__new__(InteractiveMode)
        mode._event_queue = queue.Queue()
        mode._dispatch_agent_event(AgentEndEvent(messages=(
            AssistantMessage(
                content=(TextContent(text="Agent error: network failed"),),
                stop_reason="error",
                error_message="network failed",
            ),
        )))

        event = mode._event_queue.get_nowait()
        assert event["type"] == "error"
        assert event["message"] == "network failed"
        assert event["stamp"].generation == 0

    def test_message_end_surfaces_provider_error(self):
        import queue

        from xdog.agent import MessageEndEvent
        from xdog.ai.types import AssistantMessage
        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

        mode = object.__new__(InteractiveMode)
        mode._event_queue = queue.Queue()
        mode._dispatch_agent_event(MessageEndEvent(
            message=AssistantMessage(
                content=(),
                stop_reason="error",
                error_message="No tool output found for function call call-1",
            ),
        ))

        event = mode._event_queue.get_nowait()
        assert event["type"] == "error"
        assert event["message"] == "No tool output found for function call call-1"

    def test_each_agent_turn_queues_footer_refresh(self):
        import queue

        from xdog.agent import TurnEndEvent
        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

        mode = object.__new__(InteractiveMode)
        mode._event_queue = queue.Queue()
        mode._dispatch_agent_event(TurnEndEvent())

        event = mode._event_queue.get_nowait()
        assert event["type"] == "turn_footer_update"
        assert event["stamp"].generation == 0

    def test_stale_turn_end_does_not_mark_new_worker_idle(self):
        from unittest.mock import MagicMock

        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

        session = MagicMock()
        session.agent.subscribe.return_value = lambda: None
        session.permissions.mode = "ask"
        mode = InteractiveMode(session)
        mode._worker_generation = 2
        mode._worker_active = True
        mode._is_busy = True

        mode._handle_ui_event({"type": "turn_end", "generation": 1})

        assert mode._is_busy is True
        assert mode._worker_active is True

    def test_stale_worker_error_does_not_cancel_new_turn(self):
        from unittest.mock import MagicMock

        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

        session = MagicMock()
        session.agent.subscribe.return_value = lambda: None
        session.permissions.mode = "ask"
        mode = InteractiveMode(session)
        mode._worker_generation = 2
        mode._worker_active = True
        mode._is_busy = True
        mode._streaming_text = "new response"

        mode._handle_ui_event({
            "type": "error",
            "message": "old worker failed",
            "generation": 1,
        })

        assert mode._is_busy is True
        assert mode._worker_active is True
        assert mode._streaming_text == "new response"

    def test_turn_footer_event_updates_footer_immediately(self):
        from unittest.mock import Mock

        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

        mode = object.__new__(InteractiveMode)
        mode._update_footer = Mock()
        mode._update_header = Mock()
        mode._handle_ui_event({"type": "turn_footer_update"})

        mode._update_footer.assert_called_once_with()
        mode._update_header.assert_called_once_with()

    def test_busy_poll_does_not_request_redundant_render(self):
        import queue
        from unittest.mock import Mock

        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

        mode = object.__new__(InteractiveMode)
        mode._event_queue = queue.Queue()
        mode._is_busy = True
        mode._awaiting_permission = False
        mode._cancel_requested = False
        mode._format_elapsed = Mock(return_value="0s")
        mode._status_loader = Mock()
        mode._last_busy_label = "thinking... • 0s"
        mode._tui = Mock()

        mode._poll()

        mode._status_loader.set_message.assert_not_called()
        mode._tui.request_render.assert_not_called()

    def test_post_tool_text_is_rendered_after_tool_result(self):
        from unittest.mock import MagicMock

        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
        from xdog.tui.utils import strip_ansi

        session = MagicMock()
        session.agent.subscribe.return_value = lambda: None
        session.permissions.mode = "ask"
        mode = InteractiveMode(session)

        mode._handle_ui_event({"type": "assistant_start"})
        mode._handle_ui_event({
            "type": "text_update",
            "text": "",
            "thinking": "planning",
        })
        mode._handle_ui_event({"type": "assistant_end"})
        mode._handle_ui_event({
            "type": "tool_call",
            "id": "call-1",
            "name": "bash",
            "arguments": {"command": "pwd"},
        })
        mode._handle_ui_event({
            "type": "tool_result",
            "id": "call-1",
            "name": "bash",
            "result": "/workspace",
            "is_error": False,
        })
        mode._handle_ui_event({"type": "assistant_start"})
        mode._handle_ui_event({
            "type": "text_update",
            "text": "latest answer",
            "thinking": "",
        })
        mode._handle_ui_event({"type": "assistant_end"})

        rendered = "\n".join(
            strip_ansi(line) for line in mode._chat_log.render(80)
        )
        assert rendered.index("/workspace") < rendered.index("latest answer")

    def test_tool_result_preserves_image_content_for_rendering(self):
        import queue

        from xdog.agent import AgentToolResult, ToolExecutionEndEvent
        from xdog.ai.types import ImageContent
        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

        mode = object.__new__(InteractiveMode)
        mode._event_queue = queue.Queue()
        image = ImageContent(data="aGVsbG8=", mime_type="image/png")
        mode._dispatch_agent_event(ToolExecutionEndEvent(
            tool_call_id="call-image",
            tool_name="filesystem",
            result=AgentToolResult(content=(image,)),
        ))

        event = mode._event_queue.get_nowait()
        assert event["content"] == (image,)

    def test_completed_tool_event_retains_full_output_for_expansion(self):
        import queue

        from xdog.agent import AgentToolResult, ToolExecutionEndEvent
        from xdog.ai.types import TextContent
        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

        mode = object.__new__(InteractiveMode)
        mode._event_queue = queue.Queue()
        result = f"first\n{'x' * 700}\nEVENT-TAIL"
        mode._dispatch_agent_event(ToolExecutionEndEvent(
            tool_call_id="call-a",
            tool_name="bash",
            result=AgentToolResult(content=(TextContent(text=result),)),
        ))

        event = mode._event_queue.get_nowait()
        assert event["result"] == result

    def test_tool_events_keep_their_call_ids(self):
        import queue

        from xdog.agent import (
            AgentToolResult,
            ToolExecutionEndEvent,
            ToolExecutionStartEvent,
        )
        from xdog.ai.types import TextContent
        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

        mode = object.__new__(InteractiveMode)
        mode._event_queue = queue.Queue()
        mode._dispatch_agent_event(ToolExecutionStartEvent(
            tool_call_id="call-a",
            tool_name="bash",
            args={"command": "pwd"},
        ))
        mode._dispatch_agent_event(ToolExecutionEndEvent(
            tool_call_id="call-a",
            tool_name="bash",
            result=AgentToolResult(content=(TextContent(text="done"),)),
        ))

        started = mode._event_queue.get_nowait()
        finished = mode._event_queue.get_nowait()
        assert started["id"] == "call-a"
        assert finished["id"] == "call-a"

    def test_parallel_tools_update_their_own_status_components(self):
        from unittest.mock import MagicMock

        from xdog.coding.modes.interactive.components.tool_execution import (
            ToolExecutionComponent,
        )
        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
        from xdog.tui.utils import strip_ansi

        session = MagicMock()
        session.agent.subscribe.return_value = lambda: None
        session.permissions.mode = "ask"
        mode = InteractiveMode(session)

        for index in range(3):
            mode._handle_ui_event({
                "type": "tool_call",
                "id": f"call-{index}",
                "name": "bash",
                "arguments": {"command": f"echo {index}"},
            })

        # Parallel calls can finish in any order.
        for index in (1, 0, 2):
            mode._handle_ui_event({
                "type": "tool_result",
                "id": f"call-{index}",
                "name": "bash",
                "result": f"result-{index}",
                "is_error": False,
            })

        components = [
            child
            for child in mode._chat_log.children
            if isinstance(child, ToolExecutionComponent)
        ]
        assert len(components) == 3
        assert all(component._state == "success" for component in components)
        assert mode._tool_components == {}
        rendered = "\n".join(
            strip_ansi(line) for line in mode._chat_log.render(80)
        )
        assert all(f"result-{index}" in rendered for index in range(3))

    def test_replay_restores_tool_call_and_result(self):
        from types import SimpleNamespace

        from xdog.ai.types import (
            AssistantMessage,
            TextContent,
            ToolCall,
            ToolResultMessage,
        )
        from xdog.coding.modes.interactive.components.chat_log import ChatLog
        from xdog.coding.modes.interactive.components.custom_editor import CustomEditorComponent
        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
        from xdog.tui.utils import strip_ansi

        theme = create_default_theme()
        mode = object.__new__(InteractiveMode)
        mode._session = SimpleNamespace(messages=[
            AssistantMessage(content=(
                ToolCall(id="call-1", name="bash", arguments={"command": "pwd"}),
            )),
            ToolResultMessage(
                tool_call_id="call-1",
                tool_name="bash",
                content=(TextContent(text="/workspace"),),
            ),
        ])
        mode._chat_log = ChatLog(theme)
        mode._editor = CustomEditorComponent(theme)

        mode._replay_history()
        rendered = "\n".join(strip_ansi(line) for line in mode._chat_log.render(80))

        assert "bash" in rendered
        assert "pwd" in rendered
        assert "/workspace" in rendered


class TestCustomEditor:
    """Tests for the custom editor component."""

    def test_editor_text_operations(self):
        from xdog.coding.modes.interactive.components.custom_editor import CustomEditorComponent
        theme = create_default_theme()
        editor = CustomEditorComponent(theme)
        editor.set_text("hello")
        assert editor.get_text() == "hello"

    def test_editor_history(self):
        from xdog.coding.modes.interactive.components.custom_editor import CustomEditorComponent
        theme = create_default_theme()
        editor = CustomEditorComponent(theme)
        editor.add_to_history("first")
        editor.add_to_history("second")
        assert len(editor._history) == 2
        # Duplicate should not be added
        editor.add_to_history("second")
        assert len(editor._history) == 2

    def test_editor_wraps_long_input_to_terminal_width(self):
        from xdog.coding.modes.interactive.components.custom_editor import CustomEditorComponent
        from xdog.tui.utils import string_width, strip_ansi

        editor = CustomEditorComponent(create_default_theme())
        editor.set_text("abcdefghij")
        lines = editor.render(8)

        assert all(string_width(line) <= 8 for line in lines)
        visible = [strip_ansi(line) for line in lines]
        assert any("abcdef" in line for line in visible)
        assert any("ghij" in line for line in visible)

    def test_editor_moves_across_wrapped_visual_rows(self):
        from xdog.coding.modes.interactive.components.custom_editor import (
            CustomEditorComponent,
        )
        from xdog.tui.keys import KeyEvent

        editor = CustomEditorComponent(create_default_theme())
        editor.render(8)
        editor.set_text("abcdefghij")

        assert editor.handle_input(KeyEvent(key="up"))
        assert editor._cursor == 4
        assert editor.handle_input(KeyEvent(key="down"))
        assert editor._cursor == 10

    def test_editor_pastes_multiline_text_without_submitting(self):
        from xdog.coding.modes.interactive.components.custom_editor import (
            CustomEditorComponent,
        )

        editor = CustomEditorComponent(create_default_theme())
        submitted: list[str] = []
        editor.on_submit = submitted.append
        editor.set_text("before-after")
        editor._cursor = len("before-")

        assert editor.handle_paste("first\r\nsecond")
        assert editor.get_text() == "before-first\nsecondafter"
        assert submitted == []

    def test_editor_supports_multiline_input(self):
        from xdog.coding.modes.interactive.components.custom_editor import CustomEditorComponent
        from xdog.tui.keys import KeyEvent

        editor = CustomEditorComponent(create_default_theme())
        submitted: list[str] = []
        editor.on_submit = submitted.append
        editor.set_text("first")

        assert editor.handle_input(KeyEvent(key="enter", alt=True))
        for ch in "second":
            assert editor.handle_input(KeyEvent(key=ch))

        assert editor.get_text() == "first\nsecond"
        assert editor.handle_input(KeyEvent(key="enter"))
        assert submitted == ["first\nsecond"]

    def test_editor_multiline_vertical_movement(self):
        from xdog.coding.modes.interactive.components.custom_editor import CustomEditorComponent
        from xdog.tui.keys import KeyEvent

        editor = CustomEditorComponent(create_default_theme())
        editor.set_text("abc\n12345")
        assert editor.handle_input(KeyEvent(key="up"))
        assert editor._cursor == 3
        assert editor.handle_input(KeyEvent(key="down"))
        assert editor._cursor == 9

class TestSlashSelectList:
    """Tests for the slash command select list."""

    def test_select_list(self):
        from xdog.coding.modes.interactive.components.custom_editor import SlashSelectList
        items = [("/help", "Show help"), ("/model", "Show model")]
        sl = SlashSelectList(items)
        assert sl.selected_value == "/help"
        sl.move(1)
        assert sl.selected_value == "/model"
        sl.move(1)  # wraps around
        assert sl.selected_value == "/help"

class TestDiffComponent:
    """Tests for the Diff component with custom diff format."""

    def test_diff_context_lines(self):
        from xdog.tui.components.diff import Diff
        diff_text = " 1 context line"
        d = Diff(diff_text, padding_left=0)
        lines = d.render(80)
        assert len(lines) >= 1
        assert "context line" in lines[0]

    def test_diff_intra_line_highlight(self):
        """When 1 removed + 1 added, intra-line diff applies inverse."""
        from xdog.tui.components.diff import Diff
        diff_text = "-3 hello world\n+3 hello earth"
        d = Diff(diff_text, padding_left=0)
        lines = d.render(80)
        assert len(lines) == 2
        # Inverse ANSI should appear for the changed word
        assert "\x1b[7m" in lines[0] or "\x1b[7m" in lines[1]

class TestToolExecutionState:
    """Tests for tool execution state tracking."""

    def test_expand_collapse_large_output(self):
        from xdog.coding.modes.interactive.components.tool_execution import ToolExecutionComponent
        from xdog.tui.utils import strip_ansi

        comp = ToolExecutionComponent("bash", None, create_default_theme())
        big_output = f"first line\n{'x' * 600}\nEND-OF-TOOL-OUTPUT"
        comp.set_result(big_output)

        comp.set_expanded(False)
        collapsed = "\n".join(strip_ansi(line) for line in comp.render(80))
        assert "more chars" in collapsed
        assert "END-OF-TOOL-OUTPUT" not in collapsed

        comp.set_expanded(True)
        expanded = "\n".join(strip_ansi(line) for line in comp.render(80))
        assert "first line" in expanded
        assert "END-OF-TOOL-OUTPUT" in expanded

        comp.set_expanded(False)
        recollapsed = "\n".join(strip_ansi(line) for line in comp.render(80))
        assert "more chars" in recollapsed
        assert "END-OF-TOOL-OUTPUT" not in recollapsed

    def test_empty_final_result_clears_streaming_preview(self):
        from xdog.coding.modes.interactive.components.tool_execution import (
            ToolExecutionComponent,
        )
        from xdog.tui.utils import strip_ansi

        component = ToolExecutionComponent("bash", None, create_default_theme())
        component.set_streaming("still working")
        component.set_result("")
        rendered = "\n".join(strip_ansi(line) for line in component.render(80))

        assert "still working" not in rendered
        assert "(no output)" in rendered


class TestToolExecutionDiffDetection:
    """Tests for diff detection in tool_execution.py."""

    def test_extract_diff(self):
        from xdog.coding.modes.interactive.components.tool_execution import _extract_diff
        text = "Successfully replaced 1 occurrence\n\n-1 old\n+1 new"
        diff = _extract_diff(text)
        assert diff.startswith("-1 old")

class TestInitialMessage:
    """Tests for the initial message builder."""

    def test_build_with_prompt(self):
        from xdog.coding.cli.initial_message import build_initial_message
        msg = build_initial_message(prompt="hello")
        assert msg == "hello"

    def test_build_with_prompt_and_files(self, tmp_path):
        from xdog.coding.cli.initial_message import build_initial_message
        f = tmp_path / "test.txt"
        f.write_text("file content", encoding="utf-8")
        msg = build_initial_message(prompt="analyze this", files=(f,))
        assert msg is not None
        assert "file content" in msg
        assert "analyze this" in msg

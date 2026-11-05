"""Shared message contracts: identity, order, spacing, and bounded rendering."""
from xdog.tui.components.markdown import MarkdownTheme
from xdog.tui.components.messages import AssistantMessages, NormalMessage, ThinkingMessage, Transcript
from xdog.tui.components.tool_message import ToolMessage
from xdog.tui.utils import sanitize_terminal_text, string_width, strip_ansi


def test_transcript_keeps_thinking_and_prose_as_independent_stable_messages():
    transcript = Transcript()
    turn = AssistantMessages("", "first thought", MarkdownTheme(), lambda value: value)
    transcript.add_child(turn)
    thinking, prose = transcript.children
    assert isinstance(thinking, ThinkingMessage)
    assert isinstance(prose, NormalMessage)
    turn.set_content("answer", thinking="updated thought")
    assert transcript.children == [thinking, prose]
    assert thinking.detail_body == "updated thought"
    rows = transcript.render(60)
    assert "answer" in "\n".join(rows)
    assert not any(not rows[i].strip() and not rows[i + 1].strip() for i in range(len(rows) - 1))
    transcript.remove_child(turn)
    assert transcript.children == []


def test_parallel_message_updates_do_not_reorder_tools():
    transcript = Transcript()
    tools = [ToolMessage(), ToolMessage()]
    for tool in tools:
        transcript.add_child(tool)
    for index in (1, 0):
        tools[index].update(header=f"tool-{index}", summary="", output=f"result-{index}",
                            running=False, style=lambda value: value)
    assert transcript.children == tools
    text = "\n".join(transcript.render(40))
    assert text.index("result-0") < text.index("result-1")


def test_transcript_preserves_image_height_reservations():
    from xdog.tui.tui import Component

    class Graphics(Component):
        def render(self, width: int) -> list[str]:
            return ["", "", "\x1b[2A\x1b_Ga=T;payload\x1b\\"]

    transcript = Transcript()
    graphic = Graphics()
    transcript.add_child(graphic)
    assert transcript.render(80) == graphic.render(80)


def test_user_background_covers_gutter_padding_and_inline_resets_without_leaking():
    from xdog.tui.components.markdown import DefaultTextStyle
    from xdog.tui.components.messages import UserMessage

    background = "\x1b[44m"
    style = DefaultTextStyle(bg_color=lambda text: f"{background}{text}\x1b[49m")
    theme = MarkdownTheme(bold=lambda text: f"\x1b[1m{text}\x1b[0m")
    user = UserMessage("hello **bold** tail", theme, style)
    rows = user.render(32)
    assert len(rows) == 3
    assert all(row.startswith(background) and row.endswith("\x1b[49m") for row in rows)
    assert all(string_width(row) == 32 for row in rows)
    assert strip_ansi(rows[0]) == strip_ansi(rows[-1]) == " " * 32
    assert rows[1].startswith(background + " › ")
    assert "\x1b[0m" + background in rows[1]

    transcript = Transcript()
    transcript.add_child(NormalMessage("before", MarkdownTheme()))
    transcript.add_child(user)
    transcript.add_child(UserMessage("second", theme, style))
    transcript.add_child(NormalMessage("after", MarkdownTheme()))
    rendered = transcript.render(32)
    assert [strip_ansi(row).rstrip() for row in rendered] == [
        "", " • before", "", "", " › hello bold tail", "", " › second", "", "", " • after", "",
    ]
    assert all(background in row for row in rendered[3:-3])
    assert background not in rendered[1] + rendered[-2]
    assert [record.body for record in transcript.transcript_records()] == [
        "before", "hello **bold** tail", "second", "after",
    ]


def test_user_assistant_and_tool_share_marker_and_body_columns():
    from xdog.tui.components.markdown import DefaultTextStyle
    from xdog.tui.components.messages import UserMessage

    transcript = Transcript()
    transcript.add_child(UserMessage("question", MarkdownTheme(), DefaultTextStyle()))
    tool = ToolMessage()
    tool.update(header=ToolMessage.make_header("bash", "pwd", "success"), summary="",
                output="/workspace", running=False, style=lambda text: text)
    transcript.add_child(tool)
    transcript.add_child(NormalMessage("answer", MarkdownTheme()))
    rows = [strip_ansi(row).rstrip() for row in transcript.render(60)]
    assert rows == ["", " › question", "", "", " • Ran pwd", "   └ /workspace", "", " • answer", ""]
    for row in (rows[1], rows[4], rows[7]):
        assert row[0] == " " and row[2] == " "
        assert row[1] in "›•*"
        assert row[3] != " "


def test_one_assistant_message_has_no_extra_blank_between_prose_paragraphs():
    source = 'I tried again.\n\nSo I cannot continue.\n\n\nAnother paragraph.'
    turn = AssistantMessages(source, "", MarkdownTheme(), lambda text: text)
    assert [strip_ansi(row).rstrip() for row in turn.render(80)] == [
        "", " • I tried again.", "   So I cannot continue.", "   Another paragraph.", "",
    ]
    transcript = Transcript()
    transcript.add_child(turn)
    assert [record.body for record in transcript.transcript_records()] == [source]
    turn.set_text("Changed.\n\nNext.")
    assert [strip_ansi(row).rstrip() for row in turn.render(80)] == ["", " • Changed.", "   Next.", ""]


def test_thinking_wraps_unicode_and_ansi_safely_at_tiny_widths():
    source = "中文 👩‍💻 e\u0301 " * 12 + "\nTAIL"
    message = ThinkingMessage(source, lambda text: f"\x1b[90m{text}\x1b[39m")
    for expanded in (False, True):
        message.set_expanded(expanded)
        for width in range(65):
            rows = message.render(width)
            assert all(string_width(row) <= width for row in rows)
            assert all("\n" not in row and "\r" not in row for row in rows)
            assert all("\x1b" not in strip_ansi(row) for row in rows)
            if width >= 4:
                assert sum(strip_ansi(row).startswith(" • ") for row in rows) == 1
                assert all(strip_ansi(row).startswith("   ") for row in rows[1:])
        assert message.render(0) == []
        assert message.render(-1) == []
        assert message.detail_body == sanitize_terminal_text(source)

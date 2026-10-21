"""Shared message contracts: identity, order, spacing, and bounded rendering."""
from xdog.tui.components.markdown import MarkdownTheme
from xdog.tui.components.messages import AssistantMessages, NormalMessage, ThinkingMessage, Transcript
from xdog.tui.components.text import Text
from xdog.tui.components.tool_message import ToolMessage
from xdog.tui.utils import sanitize_terminal_text, strip_ansi, visible_width


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


def test_transcript_collapses_only_boundary_spacing_and_skips_empty_messages():
    transcript = Transcript()
    transcript.add_child(Text("user", 0, 2))
    transcript.add_child(Text("", 0, 2))
    transcript.add_child(Text("paragraph one\n\nparagraph two", 0, 2))
    assert [row.rstrip() for row in transcript.render(40)] == [
        "", "user", "", "paragraph one", "", "paragraph two", "",
    ]


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


def test_tool_component_owns_branches_and_never_emits_multiline_rows():
    tool = ToolMessage()
    tool.update(header="bash\ncommand", summary="one\ntwo", output="old\nlatest",
                running=True, style=lambda value: value)
    for width in (12, 40, 100):
        rows = tool.render(width)
        assert all("\n" not in row and "\r" not in row for row in rows)
        assert all(visible_width(row) <= width for row in rows)
    assert "└" in strip_ansi(tool.render(100)[1])
    assert tool.detail_body == "old\nlatest"


def test_transcript_preserves_image_height_reservations():
    from xdog.tui.tui import Component

    class Graphics(Component):
        def render(self, width: int) -> list[str]:
            return ["", "", "\x1b[2A\x1b_Ga=T;payload\x1b\\"]

    transcript = Transcript()
    graphic = Graphics()
    transcript.add_child(graphic)
    assert transcript.render(80) == graphic.render(80)


def test_user_messages_have_one_blank_row_above_and_below_even_at_transcript_edges():
    from xdog.tui.components.markdown import DefaultTextStyle
    from xdog.tui.components.messages import UserMessage

    transcript = Transcript()
    for text in ("first", "second"):
        user = UserMessage(text, MarkdownTheme(), DefaultTextStyle())
        assert [row.rstrip() for row in user.render(30)] == ["", f" › {text}", ""]
        transcript.add_child(user)
    assert [row.rstrip() for row in transcript.render(30)] == ["", " › first", "", " › second", ""]
    transcript.add_child(NormalMessage("answer", MarkdownTheme()))
    assert [row.rstrip() for row in transcript.render(30)] == [
        "", " › first", "", " › second", "", "", " • answer", "",
    ]


def test_role_gutter_wraps_and_aligns_without_changing_source_or_exports():
    from xdog.tui.components.markdown import DefaultTextStyle
    from xdog.tui.components.messages import UserMessage

    source = "abcdefghijklmnop"
    user = UserMessage(source, MarkdownTheme(), DefaultTextStyle())
    assistant = NormalMessage(source, MarkdownTheme())
    for message, marker in ((user, "›"), (assistant, "•")):
        rows = [strip_ansi(row).rstrip() for row in message.render(10) if row.strip()]
        assert rows == [f" {marker} abcdefg", "   hijklmn", "   op"]
        assert message.source_text == source
    assert assistant.preferred_height(10) == 3
    transcript = Transcript()
    transcript.add_child(user)
    transcript.add_child(assistant)
    assert [(record.role, record.body) for record in transcript.transcript_records()] == [
        ("user", source), ("assistant", source),
    ]


def test_message_gutters_stay_bounded_for_tiny_unicode_and_styled_content():
    from xdog.tui.components.markdown import DefaultTextStyle
    from xdog.tui.components.messages import UserMessage

    style = DefaultTextStyle(color=lambda text: f"\x1b[32m{text}\x1b[0m",
                             bg_color=lambda text: f"\x1b[44m{text}\x1b[0m")
    source = "中文 👩‍💻 **bold**\n\n```python\nprint('hello')\n```"
    for message in (UserMessage(source, MarkdownTheme(), style), NormalMessage(source, MarkdownTheme())):
        for width in range(17):
            rows = message.render(width)
            assert all(visible_width(row) <= width for row in rows)
            assert all("\n" not in row and "\r" not in row for row in rows)
        assert message.render(0) == []


def test_streaming_empty_update_transform_and_removal_keep_single_markers():
    from xdog.tui.components.markdown import DefaultTextStyle
    from xdog.tui.components.messages import UserMessage

    transcript = Transcript()
    user = UserMessage("original", MarkdownTheme(), DefaultTextStyle())
    transcript.add_child(user)
    turn = AssistantMessages("", "", MarkdownTheme(), lambda text: text)
    transcript.add_child(turn)
    assert [row.rstrip() for row in transcript.render(30)] == ["", " › original", ""]
    assert transcript.transform_user_input("original", "transformed")
    for content in ("a", "answer", "a much longer answer"):
        turn.set_content(content)
        rows = [strip_ansi(row).rstrip() for row in transcript.render(30)]
        assert rows.count(" › transformed") == 1
        assert sum(row.startswith(" • ") for row in rows) == 1
        assert rows[2:4] == ["", ""]
        assert len(rows) == 6  # each message retains its own boundary padding
    transcript.remove_child(turn)
    assert [row.rstrip() for row in transcript.render(30)] == ["", " › transformed", ""]
    user.set_text("")
    assert transcript.render(30) == []


def test_assistant_uses_the_tool_bullet_in_gray_without_coloring_body_or_slicing_ansi():
    def gray(text):
        return f"\x1b[90m{text}\x1b[39m"

    turn = AssistantMessages("plain answer", "", MarkdownTheme(), gray)
    rows = turn.normal_message.render(30)
    assert rows[0].startswith(" " + gray("•") + " plain answer")
    assert strip_ansi(rows[0]).startswith(" • plain answer")
    assert rows[0].count("\x1b[90m") == 1
    for width in range(16):
        lines = turn.normal_message.render(width)
        assert all(visible_width(row) <= width for row in lines)
        if width >= 4:
            assert sum(gray("•") in row for row in lines) == 1
        else:
            assert all("\x1b" not in row for row in lines)


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
    assert all(visible_width(row) == 32 for row in rows)
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


def test_user_background_identity_style_and_small_unicode_widths():
    from xdog.tui.components.markdown import DefaultTextStyle
    from xdog.tui.components.messages import UserMessage

    for bg in (lambda text: text, lambda text: f"\x1b[44m{text}\x1b[0m"):
        user = UserMessage("中文 **hello**", MarkdownTheme(), DefaultTextStyle(bg_color=bg))
        for width in range(1, 18):
            rows = user.render(width)
            assert all(visible_width(row) == width for row in rows)
            assert not strip_ansi(rows[0]).strip()
            assert not strip_ansi(rows[-1]).strip()
        assert user.render(0) == []
        user.set_text("")
        assert user.render(20) == []


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


def test_tool_and_agent_only_transcripts_have_one_outer_and_shared_blank_row():
    for first_is_tool in (True, False):
        transcript = Transcript()
        tool = ToolMessage()
        tool.update(header=ToolMessage.make_header("bash", "pwd", "success"), summary="",
                    output="/workspace", running=False, style=lambda text: text)
        assistant = NormalMessage("answer", MarkdownTheme())
        for child in ((tool, assistant) if first_is_tool else (assistant, tool)):
            transcript.add_child(child)
        rows = [strip_ansi(row).rstrip() for row in transcript.render(60)]
        assert rows[0] == rows[-1] == ""
        assert rows.count("") == 3
        assert all(row or rows[index + 1] for index, row in enumerate(rows[:-1]))
        assert " • Ran pwd" in rows
        assert " • answer" in rows
        assert transcript.render(0) == []


def test_message_spacing_does_not_consume_graphics_reserved_rows():
    from xdog.tui.tui import Component

    class Graphics(Component):
        def render(self, width: int) -> list[str]:
            return ["", "", "\x1b[2A\x1b_Ga=T;payload\x1b\\"]

    transcript = Transcript()
    before = NormalMessage("before", MarkdownTheme())
    transcript.add_child(before)
    graphics = Graphics()
    transcript.add_child(graphics)
    rows = transcript.render(40)
    assert rows[-3:] == graphics.render(40)
    assert rows[-4] == ""  # prose padding is not a graphics reservation


def test_user_agent_boundaries_keep_colored_and_plain_padding_even_across_empty_streams():
    from xdog.tui.components.markdown import DefaultTextStyle
    from xdog.tui.components.messages import UserMessage

    bg = "\x1b[44m"
    transcript = Transcript()
    transcript.add_child(NormalMessage("before", MarkdownTheme()))
    transcript.add_child(UserMessage("question", MarkdownTheme(), DefaultTextStyle(
        bg_color=lambda text: f"{bg}{text}\x1b[49m",
    )))
    turn = AssistantMessages("", "", MarkdownTheme(), lambda text: text)
    transcript.add_child(turn)
    rows = transcript.render(30)
    assert [strip_ansi(row).rstrip() for row in rows] == ["", " • before", "", "", " › question", ""]
    assert rows[2] == ""  # assistant's bottom padding
    assert bg in rows[3]  # user's top padding
    for content in ("reply", "longer reply"):
        turn.set_content(content)
        rows = transcript.render(30)
        assert [strip_ansi(row).rstrip() for row in rows] == [
            "", " • before", "", "", " › question", "", "", f" • {content}", "",
        ]
        assert bg in rows[5]  # user's bottom padding
        assert rows[6] == ""  # assistant's top padding stays outside the background
    turn.set_content("")
    assert len(transcript.render(30)) == 6  # no phantom padding for empty streaming replies


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


def test_retained_role_layout_is_reused_until_content_width_or_theme_invalidates(monkeypatch):
    from xdog.tui.components import messages
    from xdog.tui.components.markdown import DefaultTextStyle
    from xdog.tui.components.messages import UserMessage

    calls = []
    original = messages._message_rows

    def counted(render, width, marker):
        calls.append((width, strip_ansi(marker)))
        return original(render, width, marker)

    monkeypatch.setattr(messages, "_message_rows", counted)
    backgrounds = []

    def background(text):
        backgrounds.append(text)
        return f"\x1b[44m{text}\x1b[49m"

    for message in (NormalMessage("old", MarkdownTheme()),
                    UserMessage("old", MarkdownTheme(), DefaultTextStyle(bg_color=background))):
        first = message.render(40)
        before = len(calls), len(backgrounds)
        for _ in range(20):
            assert message.render(40) is first
        assert (len(calls), len(backgrounds)) == before
        message.set_text("new text")
        changed = message.render(40)
        assert "new text" in strip_ansi("\n".join(changed)) and changed is not first
        message.render(20)
        assert len(calls) == before[0] + 2
        message.invalidate()
        message.render(20)
        assert len(calls) == before[0] + 3
        message.set_text("")
        assert message.render(40) == []
        message.set_text("visible again")
        assert "visible again" in strip_ansi("\n".join(message.render(40)))
        assert message.render(0) == []


def test_direct_markdown_text_setter_also_invalidates_assistant_role_cache():
    message = NormalMessage("old", MarkdownTheme())
    message.render(40)
    message.text = "new"
    assert [strip_ansi(row).rstrip() for row in message.render(40)] == [" • new"]


def test_thinking_has_gray_marker_and_body_aligned_with_normal_message():
    def gray(text):
        return f"\x1b[90m{text}\x1b[39m"

    message = ThinkingMessage("first thought\nlast thought", gray)
    normal = NormalMessage("answer", MarkdownTheme(), marker_style=gray)
    for expanded in (False, True, False):
        message.set_expanded(expanded)
        rows = message.render(60)
        plain = [strip_ansi(row).rstrip() for row in rows]
        title = "Thinking" if expanded else "Thinking (Ctrl+O: full details)"
        assert plain == [f" • {title}", "   first thought", *(["   last thought"] if expanded else [])]
        assert rows[0].startswith(" " + gray("•") + " \x1b[90mThinking")
        assert all("\x1b[90m" in row for row in rows)
        assert sum(gray("•") in row for row in rows) == 1
        assert plain[0].index("•") == strip_ansi(normal.render(60)[0]).index("•") == 1
        assert all(row[3] != " " for row in plain)


def test_thinking_wraps_unicode_and_ansi_safely_at_tiny_widths():
    source = "中文 👩‍💻 e\u0301 " * 12 + "\nTAIL"
    message = ThinkingMessage(source, lambda text: f"\x1b[90m{text}\x1b[39m")
    for expanded in (False, True):
        message.set_expanded(expanded)
        for width in range(65):
            rows = message.render(width)
            assert all(visible_width(row) <= width for row in rows)
            assert all("\n" not in row and "\r" not in row for row in rows)
            assert all("\x1b" not in strip_ansi(row) for row in rows)
            if width >= 4:
                assert sum(strip_ansi(row).startswith(" • ") for row in rows) == 1
                assert all(strip_ansi(row).startswith("   ") for row in rows[1:])
        assert message.render(0) == []
        assert message.render(-1) == []
        assert message.detail_body == sanitize_terminal_text(source)


def test_thinking_streaming_empty_updates_have_no_marker_or_padding_and_export_full_text():
    source = "first thought\n" + "hidden thought " * 100 + "\nTAIL"
    turn = AssistantMessages("", "", MarkdownTheme(), lambda text: f"\x1b[90m{text}\x1b[39m")
    transcript = Transcript()
    transcript.add_child(turn)
    for content in ("", " \n\t", "first", source, "", source):
        turn.set_content("", thinking=content)
        for expanded in (False, True):
            turn.set_expanded(expanded)
            rows = transcript.render(60)
            if not content.strip():
                assert rows == []
                assert turn.thinking_message.render(60) == []
            else:
                assert sum(strip_ansi(row).startswith(" • ") for row in rows) == 1
                assert ("TAIL" in strip_ansi("\n".join(rows))) == (expanded and content == source)
                assert [(record.role, record.title, record.body) for record in transcript.transcript_records()] == [
                    ("reasoning", "reasoning", content),
                ]
                assert turn.detail_body == content


def test_thinking_role_layout_caches_only_latest_width_and_invalidates(monkeypatch):
    from xdog.tui.components import messages

    calls = []
    original = messages._message_rows

    def counted(render, width, marker):
        calls.append(width)
        return original(render, width, marker)

    monkeypatch.setattr(messages, "_message_rows", counted)
    message = ThinkingMessage("old thought", lambda text: text)
    first = message.render(60)
    for _ in range(20):
        assert message.render(60) is first
    assert calls == [60]
    message.render(40)
    assert message.render(60) is not first  # Previous widths are not retained.
    assert calls == [60, 40, 60]
    for update in (lambda: message.set_text("new thought"), lambda: message.set_expanded(True), message.invalidate):
        previous = message.render(60)
        before = len(calls)
        update()
        changed = message.render(60)
        assert changed is not previous
        assert message.render(60) is changed
        assert len(calls) == before + 1
        assert "new thought" in strip_ansi("\n".join(changed))
    message.set_text("")
    assert message.render(60) == []
    assert message.render(0) == []

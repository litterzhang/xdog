"""Shared message contracts: identity, order, spacing, and bounded rendering."""
from xdog.tui.components.markdown import MarkdownTheme
from xdog.tui.components.messages import AssistantMessages, NormalMessage, ThinkingMessage, Transcript
from xdog.tui.components.text import Text
from xdog.tui.components.tool_message import ToolMessage
from xdog.tui.utils import strip_ansi, visible_width


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
        "user", "", "paragraph one", "", "paragraph two",
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

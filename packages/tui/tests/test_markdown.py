from xdog.tui.components.markdown import Markdown


def test_markdown_caching():
    md = Markdown(padding_x=0, padding_y=0)
    md.text = "Hello world"

    lines1 = md.render(80)
    lines2 = md.render(80)
    assert lines1 is lines2  # Should be cached


def _plain_markdown(source, width=100):
    from xdog.tui.utils import strip_ansi

    return [strip_ansi(row).rstrip() for row in Markdown(source, 0, 0).render(width)]


def test_list_inline_styles_flow_as_one_paragraph():
    assert _plain_markdown("- Read `config.py`, **check** *settings* and ~~old~~ [docs](url).") == [
        "- Read config.py, check settings and old docs (url).",
    ]
    assert _plain_markdown("1. **One** `two` three\n2. four *five* six") == [
        "1. One two three", "2. four five six",
    ]


def test_nested_list_keeps_paragraph_and_code_block_boundaries():
    rows = _plain_markdown("- **parent** `code`\n  - nested *text* `inline`\n\n"
                           "  next **paragraph**\n  ```py\n  x = 1\n  ```")
    assert rows == ["- parent code", "  - nested text inline", "  next paragraph",
                    "  ```py", "  x = 1", "  ```"]


def test_inline_paragraphs_and_streaming_updates_keep_natural_layout():
    from xdog.tui.utils import strip_ansi

    md = Markdown("", 0, 0)
    for source in ("- before `co", "- before `code`", "- before `code` **after**"):
        md.set_text(source)
        rows = md.render(80)
        assert len(rows) == 1
        assert strip_ansi(rows[0]).rstrip().startswith("- before ")
    assert _plain_markdown("Body `code` and **bold**.\nContinues here.\n\nNext *paragraph*.") == [
        "Body code and bold. Continues here.", "", "Next paragraph.",
    ]


def test_inline_lists_wrap_with_unicode_width_and_preserve_styles():
    from xdog.tui.utils import strip_ansi, visible_width

    md = Markdown("- 中文 **强调** `code` tail", 0, 0)
    for width in (4, 10, 20, 80):
        rows = md.render(width)
        assert all(visible_width(row) <= width for row in rows)
        assert all("\n" not in row for row in rows)
        joined = "".join(strip_ansi(row).replace(" ", "") for row in rows)
        assert joined == "-中文强调codetail"
    assert "\x1b[1m" in "".join(md.render(80))
    assert "\x1b[33m" in "".join(md.render(80))


def test_compact_prose_keeps_code_blank_lines_and_other_block_boundaries():
    from xdog.tui.utils import strip_ansi

    source = "First.\n\nSecond.\n\n```py\na = 1\n\nb = 2\n```\n\n- one\n- two\n\nLast."
    rows = [strip_ansi(row).rstrip() for row in Markdown(source, 0, 0, compact_paragraphs=True).render(80)]
    assert rows == ["First.", "Second.", "", "```py", "  a = 1", "", "  b = 2", "```", "",
                    "- one", "- two", "", "Last."]

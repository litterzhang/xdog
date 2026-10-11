from xdog.tui.utils import (
    extract_segments,
    slice_by_column,
    string_width,
    wrap_text,
)


def test_string_width():
    assert string_width("hello") == 5
    assert string_width("\x1b[31mhello\x1b[0m") == 5
    # Wide char
    assert string_width("你好") == 4

def test_wrap_text():
    text = "hello world this is a test"
    wrapped = wrap_text(text, width=10)
    assert wrapped == ["hello", "world this", "is a test"]

    long_word = "abcdefghijklmnopqrstuvwxyz"
    wrapped = wrap_text(long_word, width=10)
    assert wrapped == ["abcdefghij", "klmnopqrst", "uvwxyz"]

# ---------- Column-based slicing ----------

def test_slice_by_column_with_ansi():
    """ANSI codes are preserved in sliced output."""
    text = "\x1b[31mhello\x1b[0m world"
    result = slice_by_column(text, 0, 5)
    assert "hello" in result
    assert "\x1b[31m" in result

def test_extract_segments_ansi():
    """ANSI codes produce segments with width=0."""
    segs = extract_segments("\x1b[31mA\x1b[0m")
    ansi_segs = [s for s in segs if s.type == "ansi"]
    text_segs = [s for s in segs if s.type == "text"]
    assert len(ansi_segs) >= 1
    assert len(text_segs) == 1
    assert text_segs[0].value == "A"
    assert all(s.width == 0 for s in ansi_segs)


def test_slice_by_column_preserves_whole_graphemes_and_active_style():
    from xdog.tui.utils import strip_ansi
    text = "\x1b[31m界👩‍💻éabcd\x1b[0m"
    assert strip_ansi(slice_by_column(text, 2, 5)) == "👩‍💻é"
    assert strip_ansi(slice_by_column(text, 3, 6)) == " éa"
    assert strip_ansi(slice_by_column(text, 0, 3)) == "界 "
    assert "\x1b[31m" in slice_by_column("\x1b[31mab\x1b[1mcd", 3, 4)
    assert "\x1b[1m" in slice_by_column("\x1b[31mab\x1b[1mcd", 3, 4)

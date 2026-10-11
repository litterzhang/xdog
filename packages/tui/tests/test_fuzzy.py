from xdog.tui.fuzzy import fuzzy_filter, fuzzy_match


def test_fuzzy_match_partial():
    m = fuzzy_match("tst", "test")
    assert m is not None
    assert m.indices == (0, 2, 3)

def test_fuzzy_filter():
    candidates = ["apple", "application", "banana", "snapple"]

    matches = fuzzy_filter("app", candidates)
    assert len(matches) == 3
    assert matches[0].text == "apple" or matches[0].text == "application"

    matches_limited = fuzzy_filter("app", candidates, limit=1)
    assert len(matches_limited) == 1



def test_unicode_lowercase_expansion_preserves_original_indices():
    for query, text, expected in [
        ("x", "İx", (1,)), ("py", "İfile.py", (6, 7)),
        ("İ", "İfile.py", (0,)), ("İx", "İx", (0, 1)),
        ("σ", "ΟΣ", ()),  # Contextual final sigma does not equal non-final sigma.
    ]:
        match = fuzzy_match(query, text)
        if not expected:
            assert match is None
        else:
            assert match is not None
            assert match.indices == expected

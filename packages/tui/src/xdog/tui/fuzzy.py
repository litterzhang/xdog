"""Fuzzy string matching utilities.

Provides a simple but effective fuzzy matcher that scores candidates based on
consecutive character matches, word-boundary bonuses, and match position.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True, slots=True)
class FuzzyMatch:
    """Result of a fuzzy match.

    Attributes:
        text: The original candidate string.
        score: Numeric score (higher is better).  Zero means no match.
        indices: Indices in *text* that matched the query characters.
    """

    text: str
    score: int
    indices: tuple[int, ...]


def fuzzy_match(query: str, text: str) -> FuzzyMatch | None:
    """Score *text* against *query* using fuzzy matching.

    Returns a :class:`FuzzyMatch` if every character in *query* appears (in
    order) in *text*, otherwise ``None``.

    Scoring heuristics:
    - Consecutive matches are heavily rewarded.
    - Matches at the start of *text* or right after a separator (``/``, ``_``,
      ``-``, ``.``, `` ``) get a word-boundary bonus.
    - Case-exact matches get a small bonus.
    """
    if not query:
        return FuzzyMatch(text=text, score=1, indices=())

    q_lower = query.lower()
    t_lower = text.lower()
    q_len = len(q_lower)
    t_len = len(t_lower)
    # Unicode lowercase can expand one code point (İ -> i + combining dot).
    # Keep highlights and case/boundary bonuses indexed into the original text.
    q_positions = _original_positions(query, q_lower)
    t_positions = _original_positions(text, t_lower)

    if q_len > t_len:
        return None

    # Fast check: every query char must exist in text
    qi = 0
    for ch in t_lower:
        if ch == q_lower[qi]:
            qi += 1
            if qi == q_len:
                break
    if qi < q_len:
        return None

    # Score the best match using a simple greedy algorithm
    score = 0
    indices: list[int] = []
    qi = 0
    prev_match_idx = -2  # track consecutive matches
    _separators = frozenset("/_-. ")

    for ti in range(t_len):
        if qi >= q_len:
            break
        if t_lower[ti] == q_lower[qi]:
            original_ti = t_positions[ti]
            if not indices or indices[-1] != original_ti:
                indices.append(original_ti)
            # Consecutive bonus
            if ti == prev_match_idx + 1:
                score += 8
            else:
                score += 1

            # Word-boundary bonus
            if original_ti == 0 or text[original_ti - 1] in _separators:
                score += 6

            # Case-exact bonus
            if text[original_ti] == query[q_positions[qi]]:
                score += 1

            prev_match_idx = ti
            qi += 1

    if qi < q_len:
        return None

    # Penalise late starts slightly
    if indices:
        score -= indices[0]

    return FuzzyMatch(text=text, score=max(score, 1), indices=tuple(indices))


def _original_positions(text: str, lowered: str) -> Sequence[int]:
    if len(text) == len(lowered):
        return range(len(text))
    return tuple(index for index, char in enumerate(text) for _ in char.lower())


def fuzzy_filter(
    query: str,
    candidates: Sequence[str],
    *,
    limit: int = 0,
) -> list[FuzzyMatch]:
    """Filter and rank *candidates* against *query*.

    Returns matches sorted by descending score.  If *limit* > 0 only the top
    *limit* results are returned.
    """
    matches: list[FuzzyMatch] = []
    for text in candidates:
        m = fuzzy_match(query, text)
        if m is not None:
            matches.append(m)

    matches.sort(key=lambda m: m.score, reverse=True)
    if limit > 0:
        matches = matches[:limit]
    return matches

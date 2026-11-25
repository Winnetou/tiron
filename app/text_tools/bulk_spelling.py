"""The spelling check of a whole edition at once, for the spellings that repeat.

Old print has spellings that the modern dictionary flags and that are right for the
edition ("Konwencya"). A word that is flagged once is probably an OCR mistake; one
that is flagged again and again is a spelling of the print, to be accepted as a legal
spelling of the edition. So the words that the spell check flags are counted over all
the pages, and only the ones that are there more than `MORE_THAN` times are offered.

The first and the last word of every line (so of every page too) are not looked at:
a word that is cut by the end of a line is half a word, and the spell check would flag
it for nothing. Those are the false positives that bury the real ones.

The spellings are then put into families, so that a person who accepts "Konwencya"
sees "Konwencyi" and "Konwencyami" next to it: words that begin with the same letters
and differ only by what is left of them are the same noun with other endings.
"""

from collections.abc import Collection
from dataclasses import dataclass

from app.text_tools.spelling import _WORD, check_words, tokenize
from app.text_tools.spelling_chunks import ChunkMatch
from app.text_tools.token_occurrences import context_after, context_before

# A spelling is offered if it is in the edition more than this many times.
MORE_THAN = 3
# Words are of the same family if they begin with the same letters, at least this many.
_LEAST_STEM = 4
# ... and what is left of each after the stem (the ending) is at most this long.
_MOST_ENDING = 4


def _on_edge_of_line(text: str, start: int, end: int) -> bool:
    """Is the word at `start` to `end` the first or the last of its line? (A line is
    what is between two line breaks; signs, such as a dash or a full stop, do not
    count: `— Ala ma kota.` has `Ala` first and `kota` last.)"""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    return not any(c.isalnum() for c in text[line_start:start]) or not any(
        c.isalnum() for c in text[end:line_end]
    )


def find_inner_flagged_words(
    text: str,
    legal: Collection[str] = (),
    ignore: Collection[tuple[int, int]] = (),
) -> list[ChunkMatch]:
    """The words of `text` that the spell check flags (see `check_words`: a word
    split over two lines is judged as one word, the legal spellings count as
    correct, the words in the stretches `ignore` are not checked), in order, except
    the first and the last word of each line.

    Raises SpellcheckUnavailable if the dictionary cannot be loaded.
    """
    words = check_words(text, legal, ignore)
    tokens = tokenize(text)  # (the same tokens, in the same order, as the words)
    found = []
    for index, word in enumerate(words):
        if word.correct or not _WORD.fullmatch(word.text):
            continue
        if _on_edge_of_line(text, word.start, word.end):
            continue
        found.append(
            ChunkMatch(
                start=word.start,
                end=word.end,
                text=word.text,
                before=context_before(text, tokens, index),
                after=context_after(text, tokens, index),
            )
        )
    return found


def _common_prefix(a: str, b: str) -> int:
    length = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        length += 1
    return length


def same_family(a: str, b: str) -> bool:
    """Are the two spellings one word with different endings? They are if they begin
    with the same letters (at least `_LEAST_STEM`, whatever the case) and each of
    them has at most `_MOST_ENDING` letters after those: "Konwencya", "konwencyi" and
    "konwencyami" are, "Konwencya" and "Konstytucya" are not."""
    a, b = a.lower(), b.lower()
    stem = _common_prefix(a, b)
    return (
        stem >= _LEAST_STEM
        and len(a) - stem <= _MOST_ENDING
        and len(b) - stem <= _MOST_ENDING
    )


@dataclass(frozen=True)
class Family:
    """The spellings of one family: `stem` is the letters that they all begin with,
    `count` is how many times they are in all, and `spellings` are the words, the
    most common first."""

    stem: str
    count: int
    spellings: list[str]


def group_into_families(counts: dict[str, int]) -> list[Family]:
    """Put the spellings (a word, and how many times it is in the edition) into
    families (`same_family`). A word joins the first family whose most common word it
    is of the same family as, so the families do not drift from one word to the next.
    The families are the most common first (by all their words together), and the words
    of a family likewise; a tie is left in the order of the words in `counts`."""
    order = {word: index for index, word in enumerate(counts)}
    families: list[list[str]] = []
    for word in sorted(counts, key=lambda w: (-counts[w], order[w])):
        for family in families:
            if same_family(family[0], word):
                family.append(word)
                break
        else:
            families.append([word])
    result = []
    for words in families:
        stem = words[0].lower()
        for word in words[1:]:
            stem = stem[: _common_prefix(stem, word.lower())]
        result.append(
            Family(stem, sum(counts[w] for w in words), words),
        )
    return sorted(result, key=lambda f: (-f.count, order[f.spellings[0]]))

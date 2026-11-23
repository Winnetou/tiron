"""Spellings of the 19th century that a person accepts by their substring.

Old print has spellings that repeat: "Konwencya" and "tendencyę" have a "y" where we
write "j". The dictionary is a modern one and flags them, and making every word a
legal spelling by hand is no work for a human. The person gives the substring that these
words have in common: its core (`cy`) and the endings that can follow it (`a`, `i`,
`ami`), and this finds the words of the edition that have `cya`, `cyi` or `cyami`
in them, anywhere in the word, that are flagged. Nothing is corrected or replaced:
the words are only to be accepted, as legal spellings of the edition, one by one or
all at once.

Letters are matched whatever their case. Only words are looked at, not signs, and
only the ones that the spell check flags (a word that is correct, or already legal in
the edition, needs no acceptance).
"""

import re
from collections.abc import Collection
from dataclasses import dataclass

from app.text_tools.spelling import _WORD, check_words

# The most endings of a substring, and the longest core or ending.
MOST_ENDINGS = 40
MOST_LENGTH = 30
_CHUNK = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")


class InvalidChunk(ValueError):
    """A substring that cannot be looked for; the message says why."""


def chunk_substrings(core: str, endings: Collection[str]) -> list[str]:
    """What is looked for: the core with each of the endings (`cy` and `a`, `i`
    gives `cya` and `cyi`), without repeats. An ending may be empty, and then it is
    the core alone. Spaces at the ends of the core and of an ending are not part of
    them.

    Raises InvalidChunk if the core is empty, there are no endings or too many, or
    a part is too long or is not letters (a hyphen or an apostrophe inside a word is
    fine).
    """
    core = core.strip()
    if not core:
        raise InvalidChunk("The core of the substring must not be empty")
    if not endings:
        raise InvalidChunk("There must be at least one ending")
    if len(endings) > MOST_ENDINGS:
        raise InvalidChunk(f"There are at most {MOST_ENDINGS} endings")
    parts = [core] + [ending.strip() for ending in endings]
    for part in parts:
        if len(part) > MOST_LENGTH:
            raise InvalidChunk(
                f"A part of the substring is at most {MOST_LENGTH} letters"
            )
        if part and not _CHUNK.fullmatch(part):
            raise InvalidChunk(f"{part!r} is not made of letters")
    return list(dict.fromkeys(core + part for part in parts[1:]))


@dataclass(frozen=True)
class ChunkMatch:
    """A word that has one of the substrings in it and is flagged: the characters
    `start` to `end` are `text`. `before` and `after` are the token before it and
    the one after it (empty at the ends of the text), to see it in its context."""

    start: int
    end: int
    text: str
    before: str
    after: str


def find_chunk_matches(
    text: str, substrings: Collection[str], legal: Collection[str] = ()
) -> list[ChunkMatch]:
    """The words of `text` that have one of `substrings` in them and that the spell
    check flags (see `check_words`: a word split over two lines is judged as one
    word, and the spellings that are legal in the edition count as correct), in
    order.

    Raises SpellcheckUnavailable if the dictionary cannot be loaded.
    """
    wanted = [substring.lower() for substring in substrings if substring]
    words = check_words(text, legal)
    found = []
    for index, word in enumerate(words):
        if word.correct or not _WORD.fullmatch(word.text):
            continue
        if not any(substring in word.text.lower() for substring in wanted):
            continue
        found.append(
            ChunkMatch(
                start=word.start,
                end=word.end,
                text=word.text,
                before=words[index - 1].text if index else "",
                after=words[index + 1].text if index + 1 < len(words) else "",
            )
        )
    return found

"""Finding the hyphens that the OCR dropped at the end of a line.

A word that is split over two lines has a hyphen at the end of the first
("przeczy-" / "tać"). The OCR often leaves it out ("przeczy" / "tać"), and then
the spellcheck sees two wrong words. This finds those line ends on a page, so
that the hyphens can be put back in bulk (only on that page, not the edition).

A line end is a candidate when the last word of the line and the first word of
the next one make a correct word together ("przeczytać"). (A word that has a
hyphen of its own, "biało-czerwony", is not found: any two correct words are
correct with a hyphen between them, so that would find every line end.) The OCR
may also have read the hyphen as another sign ("przeczy," or "przeczy'"): a short
run of signs glued to the word at the end of the line is taken as the hyphen, and
goes when it is put back.

When both parts are correct words on their own ("do" / "mu" makes "domu") it is
not clear that the word is split, so the candidate is marked `ambiguous`.
"""

from collections.abc import Collection
from dataclasses import dataclass

from app.text_tools.spelling import _WORD, tokenize, word_verdict

# Each part of a split word has at least this many letters (a hyphenated word
# is not split to leave one letter on a line).
_LEAST_LETTERS = 2
# The longest run of signs that is taken for a hyphen that was misread.
_LONGEST_STRAY = 2
_HYPHENS = "-–—‒―¬"


@dataclass(frozen=True)
class MissingHyphen:
    """A word at the end of a line that needs a hyphen: the characters `start` to
    `end` of the text are `text` (the word, with the sign that was taken for the
    hyphen, if any), which is to become `suggestion` (the word and a hyphen).
    `next_word` is the word that starts the next line, and `joined` the word that
    the two parts make together (what was found correct). `ambiguous` is True if
    both parts are correct words on their own."""

    start: int
    end: int
    text: str
    suggestion: str
    next_word: str
    joined: str
    ambiguous: bool


def _letters(word: str) -> int:
    return sum(character.isalpha() for character in word)


def _is_part(word: str) -> bool:
    return (
        _WORD.fullmatch(word) is not None
        and not any(character.isdigit() for character in word)
        and _letters(word) >= _LEAST_LETTERS
    )


def find_missing_hyphens(text: str, legal: Collection[str] = ()) -> list[MissingHyphen]:
    """The line ends of `text` where a hyphen is missing, in order.

    `legal` are the spellings that are legal in the edition (see `check_words`).
    A line end that has the hyphen already is not one. An empty line between two
    lines is a paragraph break, so the line before it does not go on.

    Raises SpellcheckUnavailable if the dictionary cannot be loaded.
    """
    verdict = word_verdict(legal)
    tokens = tokenize(text)
    found = []
    for index, word in enumerate(tokens):
        stray = None
        last = word
        if text[word.end() : word.end() + 1] != "\n":
            # a sign glued to the word, then the line break
            if index + 1 >= len(tokens):
                continue
            sign = tokens[index + 1]
            if (
                sign.start() == word.end()
                and not _WORD.fullmatch(sign.group())
                and len(sign.group()) <= _LONGEST_STRAY
                and not any(character in _HYPHENS for character in sign.group())
            ):
                stray, last = sign, sign
            else:
                continue
        next_index = index + (2 if stray else 1)
        if next_index >= len(tokens):
            continue
        following = tokens[next_index]
        if text[last.end() : following.start()] != "\n":
            continue
        first, second = word.group(), following.group()
        if not _is_part(first) or not _is_part(second):
            continue
        if not verdict(first + second):
            continue
        found.append(
            MissingHyphen(
                start=word.start(),
                end=last.end(),
                text=text[word.start() : last.end()],
                suggestion=first + "-",
                next_word=second,
                joined=first + second,
                ambiguous=verdict(first) and verdict(second),
            )
        )
    return found

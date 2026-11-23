"""Finding every place of a token, exactly, in the text of a page.

The OCR makes the same mistake again and again, and some are not flagged by the spell
check because the wrong word is a word too: "tem" is read as "tern". A person who has
seen it on the scan gives the token as it is wrong, and this finds each place of it,
each with the tokens around it, so that it can be judged in its context and corrected.

A token is what `tokenize` makes of a text: a word (with a hyphen or an apostrophe in
it) or a run of signs. The match is exact, whole tokens only and the case of the
letters counts: "tern" is not "Tern", and it is not in "terne" or "intern".
"""

import re
from dataclasses import dataclass

from app.text_tools.spelling import _WORD, tokenize


class InvalidToken(ValueError):
    """What was given is not a single token; the message says why."""


def check_token(token: str) -> str:
    """The token, if it is one: a word or a run of signs, with nothing else in it.

    Raises InvalidToken if it is not (empty, spaces in it, or more than one token)."""
    if not token or token != token.strip():
        raise InvalidToken("The token must not be empty or start or end with a space")
    found = tokenize(token)
    if len(found) != 1 or found[0].group() != token:
        raise InvalidToken(
            f"{token!r} is not a single token: it has spaces in it, or is a word "
            "and signs together"
        )
    return token


@dataclass(frozen=True)
class TokenOccurrence:
    """A place of the token: the characters `start` to `end` are `text`. `before`
    and `after` are its context: the nearest word before it and the nearest word
    after it, with the signs that are between them and the token ("dniu." / "raz"),
    so that a full stop or a comma next to it does not leave nothing to read (empty
    at the ends of the text, where there is no word)."""

    start: int
    end: int
    text: str
    before: str
    after: str


def _one_line(text: str) -> str:
    """The text with each run of white space (a line break too) as one space, and
    none at its ends."""
    return re.sub(r"\s+", " ", text).strip()


def context_before(text: str, tokens: list[re.Match[str]], index: int) -> str:
    """The context before the token `index`: the nearest word before it and the signs
    between that word and the token, as they are written. If no word is before it,
    just the signs; if nothing is, an empty string."""
    for at in range(index - 1, -1, -1):
        if _WORD.fullmatch(tokens[at].group()):
            return _one_line(text[tokens[at].start() : tokens[index].start()])
    return _one_line(text[: tokens[index].start()])


def context_after(text: str, tokens: list[re.Match[str]], index: int) -> str:
    """The context after the token `index`: the signs between it and the nearest word
    after it, and that word, as they are written. If no word is after it, just the
    signs; if nothing is, an empty string."""
    for at in range(index + 1, len(tokens)):
        if _WORD.fullmatch(tokens[at].group()):
            return _one_line(text[tokens[index].end() : tokens[at].end()])
    return _one_line(text[tokens[index].end() :])


def find_token(text: str, token: str) -> list[TokenOccurrence]:
    """Every place of `token` in `text`, in order: whole tokens that are the same,
    letter by letter, with the case."""
    tokens = tokenize(text)
    return [
        TokenOccurrence(
            start=match.start(),
            end=match.end(),
            text=match.group(),
            before=context_before(text, tokens, index),
            after=context_after(text, tokens, index),
        )
        for index, match in enumerate(tokens)
        if match.group() == token
    ]

"""Fetching and reading a text of wolnelektury.pl (a link to its `.txt`).

The text is laid out like this (lines end with CRLF, which is read as LF):

    Adam Mickiewicz                       <- the author, first name(s) and last name
                                          <- a blank line
    Księgi narodu polskiego               <- the title, one line or more
    i pielgrzymstwa polskiego             <-   until the next blank line
                                          <- blank lines
    ISBN 978-83-288-2494-2                <- not always there; it is skipped
                                          <- blank lines
    the body of the work ...
                                          <- blank lines
    -----                                 <- the footer: the body ends before it, and
    Ta lektura, podobnie jak ...             everything after it is ignored
"""

import re
import urllib.request
from dataclasses import dataclass

from app.text_tools.spelling import _WORD

LINK_START = "https://wolnelektury.pl/media/book/txt/"
LINK_END = "txt"

FETCH_TIMEOUT = 30  # seconds

# The footer that is under every text: five dashes, and the sentence.
_FOOTER = re.compile(
    r"\n-----[ \t]*\n"
    r"Ta lektura, podobnie jak tysiące innych, dostępna jest na stronie "
    r"wolnelektury\.pl\."
)
_MOST_LETTERS = 100  # the longest spelling that is kept (see `Spelling`)


class ParseError(ValueError):
    """The text is not laid out like a text of wolnelektury.pl."""


def is_valid_link(link: str) -> bool:
    """True for a link to a `.txt` of wolnelektury.pl."""
    return (
        link.startswith(LINK_START)
        and link.endswith(LINK_END)
        and len(link) > len(LINK_START) + len(LINK_END)
        and not any(character.isspace() for character in link)
    )


def fetch_text(link: str) -> str:
    """The text at the link, as the string it is (UTF-8). Raises OSError (URLError
    is one) if it cannot be fetched, and ValueError if the link is not valid."""
    if not is_valid_link(link):
        raise ValueError("Not a link to a .txt of wolnelektury.pl")
    with urllib.request.urlopen(link, timeout=FETCH_TIMEOUT) as response:  # noqa: S310
        return response.read().decode("utf-8")


@dataclass(frozen=True)
class ParsedText:
    author: str  # as written: first name(s), then the last name
    title: str  # the lines of the title in one, with single spaces
    body: str


def parse(text: str) -> ParsedText:
    """The author, title and body of a text. Raises ParseError if there is no
    author or title, or the footer is not there (the body would not be known to
    end)."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    footer = _FOOTER.search(text)
    if footer is None:
        raise ParseError("The footer (five dashes, 'Ta lektura, ...') is missing")
    lines = text[: footer.start()].split("\n")

    at = 0

    def skip_blank() -> None:
        nonlocal at
        while at < len(lines) and not lines[at].strip():
            at += 1

    skip_blank()
    if at >= len(lines):
        raise ParseError("The text is empty")
    author = " ".join(lines[at].split())
    at += 1
    skip_blank()
    title_lines = []
    while at < len(lines) and lines[at].strip():
        title_lines.append(" ".join(lines[at].split()))
        at += 1
    if not title_lines:
        raise ParseError("There is no title")
    skip_blank()
    if at < len(lines) and lines[at].lstrip().upper().startswith("ISBN"):
        at += 1
    body = "\n".join(lines[at:]).strip()
    return ParsedText(author=author, title=" ".join(title_lines), body=body)


def split_author(author: str) -> tuple[str, str]:
    """(first name(s), last name) of "Adam Mickiewicz": the last word is the last
    name. A single word is a last name, with no first name."""
    *first, last = author.split() or [""]
    return " ".join(first), last


def join_author(first: str, last: str) -> str:
    """The author as the catalogue writes it: "Mickiewicz, Adam" (last name, a comma,
    the first names), which is what the editions have."""
    first, last = first.strip(), last.strip()
    return f"{last}, {first}" if first and last else first or last


def words(body: str) -> list[str]:
    """The different words of a text, in the order they first come. Case matters
    ("Jey" and "jey" are two). Words with digits, and the ones that a spelling may
    not be (too long), are left out."""
    found = (match.group() for match in _WORD.finditer(body))
    return list(
        dict.fromkeys(
            word
            for word in found
            if len(word) <= _MOST_LETTERS and not any(c.isdigit() for c in word)
        )
    )

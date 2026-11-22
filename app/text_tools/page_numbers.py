"""Finding the printed page numbers of an edition: from one page number that a
human marked, the same pattern on the other pages.

Note: the same can be done for the other furniture of a printed page, the running
header and the footer (`<fw type="header">`, `<fw type="footer">`): a human marks
one, and the same line on the other pages is offered for approval. Only the page
number is done for now, on purpose; the headers and footers are left for later,
when this has proved itself. What would change is the pattern (a line of words,
not a number with dashes, so the match would be on the shape or on the text of the
line, not on digits) and the section that is marked."""

import re
from dataclasses import dataclass
from typing import Literal

from app.text_tools.tei_xml import tei_xml_sections, tei_xml_to_text

# What a page number may be surrounded by: single or double dashes ("- 12 -",
# "— 12 —", "-12-"), of any of the dashes that print and OCR use.
_DASHES = "–—‒―-"  # the hyphen last: in a class it is a range otherwise
_LINE = re.compile(
    rf"(?P<pre>[\s{_DASHES}]*)(?P<number>\d{{1,4}})(?P<post>[\s{_DASHES}]*)"
)
_CONTEXT = 60  # characters of the line next to the number, to show where it is

# How the marked page number is written in the XML, as tei_xml_sections gives it
PAGE_NUMBER_OPEN = '<fw type="pageNum">'


@dataclass(frozen=True)
class PageNumberPattern:
    """What a page number looks like, taken from one that was marked: it is the
    whole first line of the page (or the whole last line), and it is a number of
    one to four digits, with dashes before it and after it, or without."""

    where: Literal["first", "last"]
    dashes_before: bool
    dashes_after: bool
    # The marked part includes the dashes (else it is the digits only)
    marks_dashes_before: bool
    marks_dashes_after: bool


@dataclass(frozen=True)
class PageNumberFound:
    """A page number in the text of a page: the characters `start` to `end` are
    what to mark (`text`), `number` is its digits as a number, and `context` is
    the start of the line next to it."""

    start: int
    end: int
    text: str
    number: int
    context: str


def marked_page_number(xml: str) -> tuple[str, int, int] | None:
    """The text of a page, and the characters `start` to `end` of the first page
    number that is marked in its XML (<fw type="pageNum">), or None if there is
    none (or the page has no text). Raises InvalidTei for XML that is not TEI."""
    text = tei_xml_to_text(xml)
    for section in tei_xml_sections(xml):
        if section["open"] == PAGE_NUMBER_OPEN:
            return text, section["start"], section["end"]
    return None


def _line_around(text: str, start: int, end: int) -> tuple[int, int]:
    """The offsets of the line of the text that holds the characters."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    return line_start, len(text) if line_end == -1 else line_end


def pattern_of(text: str, start: int, end: int) -> PageNumberPattern:
    """The pattern of the page number in the characters `start` to `end` of `text`.

    Raises ValueError, with the reason, if it is not a page number that can be
    looked for on the other pages: the line must be the first or the last of the
    page and have nothing but the number (and dashes around it).
    """
    line_start, line_end = _line_around(text, start, end)
    if line_start == 0:
        where: Literal["first", "last"] = "first"
    elif line_end == len(text):
        where = "last"
    else:
        raise ValueError(
            "The page number must be on the first or the last line of the page"
        )
    line = text[line_start:line_end]
    match = _LINE.fullmatch(line)
    if match is None:
        raise ValueError(
            "The page number must be alone on its line: nothing but the number "
            "and dashes around it"
        )
    before = match.group("pre").strip()
    after = match.group("post").strip()
    first_dash = (
        line_start
        + match.start("pre")
        + len(match.group("pre"))
        - len(match.group("pre").lstrip())
    )
    last_dash = (
        line_start
        + match.end("post")
        - (len(match.group("post")) - len(match.group("post").rstrip()))
    )
    return PageNumberPattern(
        where=where,
        dashes_before=bool(before),
        dashes_after=bool(after),
        marks_dashes_before=bool(before) and start <= first_dash,
        marks_dashes_after=bool(after) and end >= last_dash,
    )


def find_page_number(text: str, pattern: PageNumberPattern) -> PageNumberFound | None:
    """The page number in the text of a page, if its first (or last) line is one
    like `pattern`: the whole line is a number, with dashes around it if the
    pattern has them (single or double, any of the dashes) and without them if it
    has none. What is to be marked is what the pattern marks: the number, with its
    dashes or without them."""
    lines = text.split("\n")
    if not text or (pattern.where == "first" and not lines[0]):
        return None
    index = 0 if pattern.where == "first" else len(lines) - 1
    line = lines[index]
    line_start = sum(len(other) + 1 for other in lines[:index])
    match = _LINE.fullmatch(line)
    if match is None:
        return None
    if bool(match.group("pre").strip()) != pattern.dashes_before:
        return None
    if bool(match.group("post").strip()) != pattern.dashes_after:
        return None
    pre, post = match.group("pre"), match.group("post")
    start = line_start + (
        match.start("pre") + len(pre) - len(pre.lstrip())
        if pattern.dashes_before and pattern.marks_dashes_before
        else match.start("number")
    )
    end = line_start + (
        match.end("post") - (len(post) - len(post.rstrip()))
        if pattern.dashes_after and pattern.marks_dashes_after
        else match.end("number")
    )
    # the nearest line with something in it, on the side where the page goes on
    others = lines[1:] if index == 0 else lines[index - 1 :: -1]
    context = next((other.strip() for other in others if other.strip()), "")
    return PageNumberFound(
        start=start,
        end=end,
        text=text[start:end],
        number=int(match.group("number")),
        context=context[:_CONTEXT],
    )

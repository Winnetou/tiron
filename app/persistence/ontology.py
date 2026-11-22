"""Domain models (ontology) for the application."""

from typing import Annotated
from uuid import UUID, uuid4

from pydantic import AfterValidator, BaseModel, Field, RootModel, field_validator


class Work(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    author: str | None = None
    title: str | None = None


class Edition(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    work_id: UUID
    place: str | None = None
    year: int | None = Field(default=None, ge=1500, le=2030)
    fetched_completed: bool = False
    page_count: int | None = Field(default=None, ge=1)


class Page(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    edition_id: UUID
    number: int = Field(ge=0)
    raw_ocr_content: str
    xml_content_corrected: str | None = None
    scan_uri: str | None = None
    ready: bool = False


class TokenMatch(BaseModel):
    """A token of a page's text: `start` to `end` are offsets in the text, counted
    in code points, so text[start:end] is `text`. `suggestion` is what the token
    would be corrected to (the same mistake corrected in it)."""

    text: str
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    suggestion: str = Field(min_length=1)


class PageMatches(BaseModel):
    """The tokens of one page that match something, in the order of the text."""

    page_number: int = Field(ge=0)
    tokens: list[TokenMatch]


class MissingHyphen(BaseModel):
    """A word at the end of a line of a page that needs the hyphen the OCR
    dropped: the characters `start` to `end` of the text (offsets, counted in
    code points) are `text`, the word with the sign that was taken for the hyphen,
    if any; it is to become `suggestion`, the word and a hyphen. `next_word` is
    the word that starts the next line, and `joined` the word that the two parts
    make together (the one that was found correct). `ambiguous` is true if both
    parts are correct words on their own, so that it is not sure that the word is
    split."""

    start: int = Field(ge=0)
    end: int = Field(ge=1)
    text: str
    suggestion: str
    next_word: str
    joined: str
    ambiguous: bool = False


class PageNumberMatch(BaseModel):
    """A printed page number found in the text of a page: the characters `start`
    to `end` (offsets in the text, counted in code points) are `text`, which is
    what to mark as the page number; `number` is its digits as a number. `context`
    is the start of the line next to it, to see where it is. `fits_sequence` says
    whether the number follows the numbering of the example: the printed number
    is the number of the page less the same offset."""

    page_number: int = Field(ge=0)
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    text: str
    number: int = Field(ge=0)
    context: str = ""
    fits_sequence: bool = True


class SimilarPageNumbers(BaseModel):
    """The page numbers of an edition that are like one that was marked: `example`
    is the marked one, `where` the line of the page it is on (the first or the
    last), and `offset` the number of its page less its printed number, which is
    the same on all the pages of a numbering. `candidates` are the pages that have
    a number like it and have none marked yet, in the order of the pages."""

    example: PageNumberMatch
    where: str
    offset: int
    candidates: list[PageNumberMatch]


class Correction(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    page_id: UUID
    before: str = Field(min_length=1)
    after: str = Field(min_length=1)


class Collection(RootModel[list[UUID]]):
    """A list of unique UUIDs."""

    @field_validator("root")
    @classmethod
    def no_duplicates(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("collection UUIDs must be unique")
        return value


def _no_spaces(value: str) -> str:
    if any(
        character.isspace() or character.isprintable() is False for character in value
    ):
        raise ValueError(
            "a spelling must not contain spaces, line breaks or control characters"
        )
    return value


# One spelling of a word: not empty, and in one piece. Case matters ("Jey" and
# "jey" are two spellings).
Spelling = Annotated[
    str, Field(min_length=1, max_length=100), AfterValidator(_no_spaces)
]


class LegalSpelling(BaseModel):
    """A spelling that is legal in an edition, although the dictionary does not
    know it (old print uses spellings like "jey" and "tey"). A spelling is
    legal for an edition, not everywhere, and each can be given once.

    We say "legal", not "correct": the dictionary is a modern one, and what was
    correct in the 19th century often is not anymore. We have no better word."""

    edition_id: UUID
    spelling: Spelling

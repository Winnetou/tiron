"""Request and response models of the API."""

from typing import Literal
from uuid import UUID

from fastapi import Query
from fastapi_pagination import Page as PaginatedPage
from fastapi_pagination.customization import CustomizedPage, UseParamsFields
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.persistence.ontology import Page, Spelling

COLLECTION_PAGE_SIZE = 100


class CollectionEdition(BaseModel):
    """An edition of the collection, as the main page lists it: its `id` and, if
    the metadata of the book is known, its `author` and `title` (null if they are
    not)."""

    id: UUID
    author: str | None = None
    title: str | None = None


# A page of the collection: its editions, 100 on a page (the default, and the
# most that can be asked for with `size`).
CollectionPage = CustomizedPage[
    PaginatedPage[CollectionEdition],
    UseParamsFields(
        size=Query(COLLECTION_PAGE_SIZE, ge=1, le=COLLECTION_PAGE_SIZE),
    ),
]


class PageView(BaseModel):
    """What the client shows of a page: its scan and a single text, `content`."""

    id: UUID
    edition_id: UUID
    number: int
    scan_uri: str | None
    ready: bool
    content: str

    @classmethod
    def from_page(cls, page: Page) -> PageView:
        """`content` is the page's TEI XML. The raw OCR text is never served."""
        return cls(
            id=page.id,
            edition_id=page.edition_id,
            number=page.number,
            scan_uri=page.scan_uri,
            ready=page.ready,
            content=page.xml_content_corrected,
        )


class PageUpdate(BaseModel):
    """The only things of a page that can be edited. Any other field in the
    request is rejected, so nothing else can be changed through the API."""

    model_config = ConfigDict(extra="forbid")

    xml_content_corrected: str | None = None
    ready: bool | None = None

    @model_validator(mode="after")
    def check_fields(self) -> PageUpdate:
        if not self.model_fields_set:
            raise ValueError("Give xml_content_corrected and/or ready")
        if "ready" in self.model_fields_set and self.ready is None:
            raise ValueError("ready cannot be null")
        if "xml_content_corrected" in self.model_fields_set and (
            not self.xml_content_corrected
        ):
            raise ValueError("xml_content_corrected cannot be null or empty")
        return self


class TeiXml(BaseModel):
    xml: str = Field(max_length=1_000_000)


class TeiText(BaseModel):
    text: str


class SectionSpan(BaseModel):
    """A section marked in the XML of a page: the characters `start` to `end` of
    its text, and the tags around them as written in the XML."""

    start: int
    end: int
    open: str
    close: str


class TeiSections(BaseModel):
    sections: list[SectionSpan]


class TeiEntities(BaseModel):
    entities: list[Entity]


class NerRequest(BaseModel):
    text: str = Field(max_length=100_000)


class Entity(BaseModel):
    """A named entity found in a text: `text[start:end] == entity.text`."""

    text: str
    label: str
    start: int
    end: int


class NerResult(BaseModel):
    entities: list[Entity]


# What a text can be marked as: the labels of the NER model, which are TEI
# elements, and the elements for what the NER model cannot find, a reference
# (<ref>), bibliographic information (<bibl>) and a cited quotation (<cit>).
EntityLabel = Literal[
    "date",
    "geogName",
    "orgName",
    "persName",
    "placeName",
    "time",
    "ref",
    "bibl",
    "cit",
]


# The languages that a quotation can be in, by their codes (xml:lang): the most
# used European ones, and Latin.
LANGUAGES: dict[str, str] = {
    "ru": "Russian",
    "en": "English",
    "de": "German",
    "es": "Spanish",
    "fr": "French",
    "it": "Italian",
    "la": "Latin",
}

# The sections of a page that can be marked, as the API calls them.
SectionKind = Literal[
    "header", "footer", "page_number", "toc", "note_group", "note", "quote"
]


class EntityTag(BaseModel):
    """An entity to mark in the XML of a page: `label` becomes the TEI element
    around the characters `start` to `end` of the page's text, which must be
    `text` (the entity as the client saw it)."""

    model_config = ConfigDict(extra="forbid")

    start: int = Field(ge=0)
    end: int = Field(ge=1)
    text: str = Field(min_length=1)
    label: EntityLabel

    @model_validator(mode="after")
    def check_range(self) -> EntityTag:
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        return self


class SpellcheckRequest(BaseModel):
    text: str = Field(max_length=100_000)
    # The edition the text is from: its legal spellings count as correct.
    edition_id: UUID | None = None
    # The TEI XML that `text` is the text of: what is in a quotation in another
    # language (<quote xml:lang="la">) is not checked, as it is not Polish.
    xml: str | None = Field(default=None, max_length=1_000_000)


class Word(BaseModel):
    """A word of a text and whether it is spelled correctly:
    `text[start:end] == word.text`."""

    text: str
    start: int
    end: int
    correct: bool


class SpellcheckResult(BaseModel):
    words: list[Word]


class WordCorrection(BaseModel):
    """A word of the text of a page that the user corrected: the characters
    `start` to `end`, which are `before`, become `after`."""

    model_config = ConfigDict(extra="forbid")

    start: int = Field(ge=0)
    end: int = Field(ge=1)
    before: str = Field(min_length=1)
    # An empty `after` removes the token (nothing is recorded for that).
    after: str

    @model_validator(mode="after")
    def check_range(self) -> WordCorrection:
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        return self


class NewToken(BaseModel):
    """A text token that the OCR missed, to be added at `position` of the text of
    a page (an offset between two characters). It may be several words."""

    model_config = ConfigDict(extra="forbid")

    position: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=200)


class HyphenFix(BaseModel):
    """A hyphen to put back at the end of a line: the characters `start` to `end`
    of the text of a page, which are `before` (a word, perhaps with the sign that
    the OCR made of the hyphen), become `after` (the word and a hyphen)."""

    model_config = ConfigDict(extra="forbid")

    start: int = Field(ge=0)
    end: int = Field(ge=1)
    before: str = Field(min_length=1, max_length=200)
    after: str = Field(min_length=2, max_length=200)

    @model_validator(mode="after")
    def check_fix(self) -> HyphenFix:
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        if not self.after.endswith("-") or not self.before.startswith(self.after[:-1]):
            raise ValueError(
                "after must be the word of before, without any sign after it, and "
                "a hyphen"
            )
        return self


class HyphenFixes(BaseModel):
    """The hyphens to put back on a page, all at once."""

    model_config = ConfigDict(extra="forbid")

    fixes: list[HyphenFix] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def check_apart(self) -> HyphenFixes:
        ordered = sorted(self.fixes, key=lambda fix: fix.start)
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            if later.start < earlier.end:
                raise ValueError("The fixes must not overlap")
        return self


class TokenJoin(BaseModel):
    """Tokens of the text of a page to join into one (the OCR split a word): the
    characters `start` to `end`, which are `text` (two or more tokens, separated
    by single spaces), lose their spaces."""

    model_config = ConfigDict(extra="forbid")

    start: int = Field(ge=0)
    end: int = Field(ge=3)
    text: str = Field(min_length=3, max_length=400)

    @model_validator(mode="after")
    def check_range(self) -> TokenJoin:
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        return self


class LineSplit(BaseModel):
    """Two tokens of the same line of a page's text, to be split into two lines:
    the characters `start` to `end`, which are `text` (the two tokens, separated
    by one space), and the space between them becomes a line break."""

    model_config = ConfigDict(extra="forbid")

    start: int = Field(ge=0)
    end: int = Field(ge=3)
    text: str = Field(min_length=3, max_length=400)

    @model_validator(mode="after")
    def check_range(self) -> LineSplit:
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        return self


class LineJoin(BaseModel):
    """Two lines of the text of a page to be joined: the characters `start` to
    `end`, which are `text` (the last token of a line and the first token of the
    next one, separated by one line break or an empty line), and the line break
    between them is removed (an empty line is reduced to a line break)."""

    model_config = ConfigDict(extra="forbid")

    start: int = Field(ge=0)
    end: int = Field(ge=3)
    text: str = Field(min_length=3, max_length=400)

    @model_validator(mode="after")
    def check_range(self) -> LineJoin:
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        return self


class LineRemoval(BaseModel):
    """A whole line of the text of a page to be removed (OCR rubbish): the
    characters `start` to `end`, which are `text`, and the line break that goes
    with it."""

    model_config = ConfigDict(extra="forbid")

    start: int = Field(ge=0)
    end: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def check_range(self) -> LineRemoval:
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        return self


class LineRemoved(BaseModel):
    """The page after a line was removed, and how many characters of its text
    went with it (the line and its line break or breaks), so that the client can
    move the offsets of what follows."""

    page: PageView
    removed: int = Field(ge=1)


class NewLegalSpelling(BaseModel):
    """A spelling to be made legal in an edition (the edition is in the path).
    "Legal", not "correct": see `LegalSpelling`."""

    model_config = ConfigDict(extra="forbid")

    spelling: Spelling


class SectionMark(BaseModel):
    """A section of a page to mark in the XML: the characters `start` to `end`
    of the page's text, which must be `text` (as the client saw it), are marked
    as the `section`."""

    model_config = ConfigDict(extra="forbid")

    start: int = Field(ge=0)
    end: int = Field(ge=1)
    text: str = Field(min_length=1)
    section: SectionKind
    # The number of a note (<note n="1">); only for a `note`, and optional.
    n: str | None = Field(default=None, max_length=20)
    # The language of a quotation (<quote xml:lang="la">): its code; only for a
    # `quote`, and needed for it.
    lang: str | None = Field(default=None, max_length=10)

    @model_validator(mode="after")
    def check_range(self) -> SectionMark:
        if self.section == "quote":
            if self.lang not in LANGUAGES:
                raise ValueError("a quote needs lang, one of: " + ", ".join(LANGUAGES))
        elif self.lang is not None:
            raise ValueError("lang is only for a quote")
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        if self.n is not None:
            self.n = self.n.strip()
            if not self.n:
                raise ValueError("n must not be empty")
            if self.section != "note":
                raise ValueError("n is only for a note")
        return self

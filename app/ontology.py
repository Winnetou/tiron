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


class Page(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    edition_id: UUID
    number: int = Field(ge=0)
    raw_content: str
    corrected_content: str | None = None
    scan_uri: str | None = None
    ready: bool = False


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
    legal for an edition, not everywhere, and each can be given once."""

    edition_id: UUID
    spelling: Spelling

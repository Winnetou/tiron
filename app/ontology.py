"""Domain models (ontology) for the application."""

from uuid import UUID, uuid4

from pydantic import BaseModel, Field, RootModel, field_validator


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

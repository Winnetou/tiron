"""Domain models (ontology) for the application."""

from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class Work(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    author: str


class Edition(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    work_id: UUID
    place: str
    year: int = Field(ge=1500, le=2030)


class Page(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    edition_id: UUID
    number: int = Field(ge=1)
    raw_content: str
    corrected_content: str | None = None


class Correction(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    page_id: UUID
    before: str = Field(min_length=1)
    after: str = Field(min_length=1)

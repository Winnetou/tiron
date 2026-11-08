"""Request and response models of the API."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ontology import Page


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
        """`content` is the corrected content if there is any, else the raw one."""
        return cls(
            id=page.id,
            edition_id=page.edition_id,
            number=page.number,
            scan_uri=page.scan_uri,
            ready=page.ready,
            content=page.corrected_content or page.raw_content,
        )


class PageUpdate(BaseModel):
    """The only things of a page that can be edited. Any other field in the
    request is rejected, so nothing else can be changed through the API."""

    model_config = ConfigDict(extra="forbid")

    corrected_content: str | None = None
    ready: bool | None = None

    @model_validator(mode="after")
    def check_fields(self) -> PageUpdate:
        if not self.model_fields_set:
            raise ValueError("Give corrected_content and/or ready")
        if "ready" in self.model_fields_set and self.ready is None:
            raise ValueError("ready cannot be null")
        return self


class TeiXml(BaseModel):
    xml: str = Field(max_length=1_000_000)


class TeiText(BaseModel):
    text: str

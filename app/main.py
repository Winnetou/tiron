from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, HTTPException, Path, status
from pydantic import BaseModel

from app.db import Database
from app.ontology import Correction, Edition, Page, Work
from app.polona_connector import PolonaError, fetch_edition_metadata, fetch_page

db = Database()

app = FastAPI(title="tiron")


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


@app.get("/editions/{edition_id}/pages/{number}", response_model=PageView)
def get_page(edition_id: UUID, number: Annotated[int, Path(ge=0)]) -> PageView:
    """Return a page of an edition, ready to display.

    1. The edition must be in the collection, otherwise 404. This check comes
       first, so it also guards pages that are already stored.
    2. If the page is already in the database, it is returned from there and
       Polona is not contacted.
    3. Otherwise the page is fetched from Polona. If the edition is not stored
       yet, its metadata is fetched too and a Work and an Edition are created
       (the page needs its edition). The page is then saved and returned, so
       the next request is served from the database.

    Polona failures are turned into HTTP errors: 404 if Polona has no such
    page, 502 if it could not be reached or answered unexpectedly. Nothing is
    saved in that case.

    The response is a PageView: its `content` is the corrected text if the
    page has one, otherwise the raw OCR text.
    """
    if not db.check_edition_in_collection(edition_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Edition not found")
    stored = db.get_page_by_number(edition_id, number)
    if stored is not None:
        return PageView.from_page(stored)
    try:
        polona_page = fetch_page(edition_id, number)
        if db.get_edition(edition_id) is None:
            metadata = fetch_edition_metadata(edition_id)
            work = Work(author=metadata["author"], title=metadata["title"])
            db.upsert_edition(
                work,
                Edition(
                    id=edition_id,
                    work_id=work.id,
                    place=metadata["place"],
                    year=metadata["year"],
                ),
            )
    except PolonaError as exc:
        if exc.not_found:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found") from exc
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    page = db.save_fetched_page(
        Page(
            edition_id=edition_id,
            number=number,
            raw_content=polona_page["raw_content"],
            scan_uri=polona_page["scan_uri"],
        )
    )
    return PageView.from_page(page)


@app.post(
    "/corrections", response_model=Correction, status_code=status.HTTP_201_CREATED
)
def create_correction(correction: Correction) -> Correction:
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Not implemented")

import urllib.error
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, HTTPException, Path, status

from app.db import Database
from app.ontology import Correction, Edition, Page, Work
from app.polona_connector import fetch_edition_metadata, fetch_page

db = Database()

app = FastAPI(title="tiron")


class PageView(Page):
    scan_page_url: str


@app.get("/editions/{edition_id}/pages/{number}", response_model=PageView)
def get_page(edition_id: UUID, number: Annotated[int, Path(ge=0)]) -> PageView:
    if not db.check_edition_in_collection(edition_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Edition not found")
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
    except (ValueError, LookupError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found") from exc
    except urllib.error.HTTPError as exc:
        if exc.code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found") from exc
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "polona.pl returned an error"
        ) from exc
    except urllib.error.URLError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Could not reach polona.pl"
        ) from exc
    page = Page(
        edition_id=edition_id,
        number=number,
        raw_content=polona_page["raw_content"],
    )
    return PageView(**page.model_dump(), scan_page_url=polona_page["scan_page_url"])


@app.post(
    "/corrections", response_model=Correction, status_code=status.HTTP_201_CREATED
)
def create_correction(correction: Correction) -> Correction:
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Not implemented")

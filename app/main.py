from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, HTTPException, Path, status

from app.db import Database
from app.models import PageUpdate, PageView, TeiText, TeiXml
from app.ontology import Correction, Edition, Page, Work
from app.polona_connector import PolonaError, fetch_edition_metadata, fetch_page
from app.tei_xml import InvalidTei, tei_xml_to_text, text_to_tei_xml

db = Database()

app = FastAPI(title="tiron")


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
       the next request is served from the database. It is saved with the
       Polona text as `raw_content`, and that text converted to TEI XML as
       `corrected_content`, which the user then edits.

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
    raw_content = polona_page["raw_content"]
    page = db.save_fetched_page(
        Page(
            edition_id=edition_id,
            number=number,
            raw_content=raw_content,
            corrected_content=str(text_to_tei_xml(raw_content)),
            scan_uri=polona_page["scan_uri"],
        )
    )
    return PageView.from_page(page)


@app.patch("/editions/{edition_id}/pages/{number}", response_model=PageView)
def update_page(
    edition_id: UUID, number: Annotated[int, Path(ge=0)], update: PageUpdate
) -> PageView:
    """Edit a page: only `corrected_content` and `ready` can be changed.

    Fields left out of the request are not touched, and
    `"corrected_content": null` (or an empty string) removes the correction, so
    `content` falls back to the raw text. Any other field in the request, such
    as `raw_content` or `scan_uri`, is rejected with 422. The page must already
    be stored (open it with GET first); it is never fetched from Polona here.

    404 if the page is not stored. Returns the updated page as a PageView.
    """
    page = db.update_page(edition_id, number, **update.model_dump(exclude_unset=True))
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found")
    return PageView.from_page(page)


@app.post("/tei/text", response_model=TeiText)
def tei_to_text(tei: TeiXml) -> TeiText:
    """Validate a TEI XML document and return just the text extracted from it.

    The body is `{"xml": "<TEI ...>...</TEI>"}`. The XML must be well-formed,
    have no DOCTYPE/ENTITY declarations, and be a TEI document: a `TEI` root in
    the TEI namespace with a `teiHeader` and a `text`. Otherwise the response
    is 422 and `detail` says what is wrong. This checks the TEI skeleton; it
    is not a validation against the full TEI schema.

    The response is `{"text": "..."}`: the content of the `text` element
    without any markup. Blocks (`p`, `head`, ...) are separated by a blank
    line and line breaks (`lb`, ...) become newlines, so a page converted with
    `text_to_tei_xml` comes back as the same text. Nothing is stored.
    """
    try:
        return TeiText(text=tei_xml_to_text(tei.xml))
    except InvalidTei as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@app.post(
    "/corrections", response_model=Correction, status_code=status.HTTP_201_CREATED
)
def create_correction(correction: Correction) -> Correction:
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Not implemented")

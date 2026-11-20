import logging
from uuid import UUID

from app.db import Database
from app.ontology import Edition, Page, Work
from app.polona_connector import fetch_edition_metadata, request_polona_edition_page
from app.tei_xml import text_to_tei_xml

logger = logging.getLogger(__name__)


def idempotent_fetch_page(edition_id: UUID, page_number: int, *, db: Database) -> Page:
    """Serve a page of an edition from the database if it is stored there, and
    otherwise fetch it from polona.pl.

    `edition_id` is the Polona object UUID and `page_number` is 0-based, as in
    request_polona_edition_page. `db` is the database to read and save in.

    A stored page is returned as it is and Polona is not contacted. For a page
    that is not stored, `fetch_unsaved_page_from_polona` is called, which saves
    the edition and the page, and the saved page is returned. Either way the
    result is the Page as it is stored.

    Raises PolonaError for a page that has to be fetched, like
    fetch_unsaved_page_from_polona.
    """
    stored = db.get_page_by_number(edition_id, page_number)
    if stored is not None:
        return stored
    return fetch_unsaved_page_from_polona(edition_id, page_number, db=db)


def fetch_unsaved_page_from_polona(
    edition_id: UUID, page_number: int, *, db: Database
) -> Page:
    """Fetch a page of an edition from polona.pl that is not saved in the
    database yet, and save it, and its edition if that is not saved either.

    `edition_id` is the Polona object UUID and `page_number` is 0-based, as in
    request_polona_edition_page. Returns the saved Page.

    The edition comes first, because the page needs it: if it is not known
    (stored) yet, its metadata is fetched and a Work and an Edition are created.
    Saving the edition is idempotent: `Database.upsert_edition` keeps a stored
    edition as it is, so two requests for the same new edition at the same time
    store it once. Then the page is fetched from the known edition and saved.
    Note that a page that does not exist still leaves its edition saved.

    Raises PolonaError, like request_polona_edition_page and
    fetch_edition_metadata.
    """
    edition_instance = db.get_edition(edition_id)
    if edition_instance is None:
        metadata = fetch_edition_metadata(edition_id)
        work = Work(author=metadata["author"], title=metadata["title"])
        edition_instance = db.upsert_edition(
            work,
            Edition(
                id=edition_id,
                work_id=work.id,
                place=metadata["place"],
                year=metadata["year"],
            ),
        )

    edition_uuid = edition_instance.id
    return _fetch_page_from_known_edition(edition_uuid, page_number, db=db)


def _fetch_page_from_known_edition(
    edition_uuid: UUID, page_number: int, *, db: Database
) -> Page:
    """Fetch a page from polona.pl for an edition that is already known
    (stored in the database), and save it.

    `edition_uuid` is the Polona object UUID and `page_number` is 0-based, as in
    request_polona_edition_page.

    The page is saved with the Polona text as `raw_ocr_content`, and that text
    converted to TEI XML as `xml_content_corrected`, which the user then edits.
    Saving is idempotent: if the page is stored meanwhile (by a concurrent
    request), the stored one is kept as it is (`Database.save_fetched_page`
    only refreshes its `scan_uri`) and returned.

    Raises PolonaError, like request_polona_edition_page. The page is not saved
    in that case.
    """
    logger.info("Fetching page %s of edition %s from Polona", page_number, edition_uuid)
    polona_page = request_polona_edition_page(edition_uuid, page_number)
    raw_ocr_content = polona_page["raw_ocr_content"]
    saved = db.save_fetched_page(
        Page(
            edition_id=edition_uuid,
            number=page_number,
            raw_ocr_content=raw_ocr_content,
            xml_content_corrected=str(text_to_tei_xml(raw_ocr_content)),
            scan_uri=polona_page["scan_uri"],
        )
    )
    logger.info("Page %s of edition %s is saved", page_number, edition_uuid)
    return saved

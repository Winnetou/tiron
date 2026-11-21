import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from app.async_tasking.domain_events import NewEditionCreatedEvent
from app.async_tasking.event_consumers import NewEditionCreatedEventConsumer
from app.persistence.db import Database
from app.persistence.ontology import Edition, Page, Work
from app.polona.polona_connector import (
    fetch_edition_metadata,
    request_polona_edition_page,
)
from app.text_tools.tei_xml import text_to_tei_xml

logger = logging.getLogger(__name__)

# One lock for each page that is being fetched for the first time, with the
# number of requests that hold or wait for it, so that the entry goes when the
# last one is done and the dictionary does not grow with every page ever served.
_fetching: dict[tuple[UUID, int], tuple[threading.Lock, int]] = {}
_fetching_guard = threading.Lock()


@contextmanager
def _first_fetch_of(edition_id: UUID, page_number: int) -> Iterator[None]:
    """Let one request at a time fetch and save the page (edition + number).

    The requests are served in threads of one process, which share one database
    connection, so a database lock (an advisory lock belongs to a connection)
    would not keep them apart: a lock in this process does.
    """
    key = (edition_id, page_number)
    with _fetching_guard:
        lock, users = _fetching.get(key, (threading.Lock(), 0))
        _fetching[key] = (lock, users + 1)
    try:
        with lock:
            yield
    finally:
        with _fetching_guard:
            lock, users = _fetching[key]
            if users == 1:
                del _fetching[key]
            else:
                _fetching[key] = (lock, users - 1)


def idempotent_fetch_page(edition_id: UUID, page_number: int, *, db: Database) -> Page:
    """Serve a page of an edition from the database if it is stored there, and
    otherwise fetch it from polona.pl and save it.

    `edition_id` is the Polona object UUID and `page_number` is 0-based, as in
    request_polona_edition_page. `db` is the database to read and save in.

    The edition comes first (`idempotent_fetch_edition`), because the page
    belongs to it: an edition that is not stored yet is fetched and saved. A
    stored page is then returned as it is and its text is not fetched again;
    otherwise `fetch_page_from_known_edition` fetches and saves it, and the saved
    page is returned. Either way the result is the Page as it is stored.

    The first fetch of a page is safe against concurrent requests for it: they
    take turns (a lock for each page, in this process), and the ones that wait
    look in the database again when their turn comes, find the page that the
    first one saved and return it, so Polona is asked once and the page is saved
    once. The lock is not shared between processes: with several backend
    processes the page may still be fetched more than once, but the database
    (unique edition and number) keeps it stored once, with the first text.

    Raises PolonaError for an edition or a page that has to be fetched, like
    fetch_edition_metadata and request_polona_edition_page. Note that a page
    that does not exist still leaves its new edition saved.
    """
    edition = idempotent_fetch_edition(edition_id, db=db)
    stored = db.get_page_by_number(edition.id, page_number)
    if stored is not None:
        return stored
    with _first_fetch_of(edition.id, page_number):
        # a request that held the lock before us may have saved the page
        stored = db.get_page_by_number(edition.id, page_number)
        if stored is not None:
            return stored
        return fetch_page_from_known_edition(edition, page_number, db=db)


def fire_event(edition_id: UUID) -> None:
    """Fire a `NewEditionCreatedEvent` for the edition, and let its consumer
    handle it."""
    event = NewEditionCreatedEvent(edition_id=edition_id)
    NewEditionCreatedEventConsumer().handle(event)


def idempotent_fetch_edition(edition_id: UUID, db: Database) -> Edition:
    """Return the edition from the database, saving it first if it is not
    stored there yet.

    `edition_id` is the Polona object UUID. An unknown edition is fetched from
    polona.pl (its metadata) and a Work and an Edition are created
    (`Database.create_work` and `Database.create_edition`).

    Raises PolonaError, like fetch_edition_metadata, if the metadata cannot be
    fetched. Nothing is saved in that case.
    """
    edition_instance = db.get_edition(edition_id)
    if edition_instance is None:
        metadata = fetch_edition_metadata(edition_id)
        work = Work(author=metadata["author"], title=metadata["title"])
        db.create_work(work)
        edition_instance = db.create_edition(
            Edition(
                id=edition_id,
                work_id=work.id,
                place=metadata["place"],
                year=metadata["year"],
            )
        )
        fire_event(edition_instance.id)
    return edition_instance


def fetch_page_from_known_edition(
    edition: Edition, page_number: int, *, db: Database
) -> Page:
    """Fetch a page from polona.pl for an edition that is already known
    (stored in the database), and save it.

    `edition` is the Edition instance and `page_number` is 0-based, as in
    request_polona_edition_page.

    The page is saved with the Polona text as `raw_ocr_content`, and that text
    converted to TEI XML as `xml_content_corrected`, which the user then edits.
    Saving is idempotent: if the page is stored meanwhile (by a concurrent
    request), the stored one is kept as it is (`Database.save_fetched_page`
    only refreshes its `scan_uri`) and returned.

    Raises PolonaError, like request_polona_edition_page. The page is not saved
    in that case.
    """
    logger.info("Fetching page %s of edition %s from Polona", page_number, edition.id)
    polona_page = request_polona_edition_page(edition.id, page_number)
    raw_ocr_content = polona_page["raw_ocr_content"]
    saved = db.save_fetched_page(
        Page(
            edition_id=edition.id,
            number=page_number,
            raw_ocr_content=raw_ocr_content,
            xml_content_corrected=str(text_to_tei_xml(raw_ocr_content)),
            scan_uri=polona_page["scan_uri"],
        )
    )
    logger.info("Page %s of edition %s is saved", page_number, edition.id)
    return saved

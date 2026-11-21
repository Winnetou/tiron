"""Event consumers: what runs when a domain event happens."""

import logging
from uuid import UUID

from app.async_tasking.domain_events import NewEditionCreatedEvent
from app.persistence.db import Database
from app.persistence.ontology import Page
from app.polona.polona_connector import (
    PolonaError,
    request_polona_edition_page,
)
from app.text_tools.tei_xml import text_to_tei_xml

logger = logging.getLogger(__name__)


class NewEditionCreatedEventConsumer:
    """Listens to `NewEditionCreatedEvent`."""

    def handle(self, event: NewEditionCreatedEvent) -> None:
        """React to a new edition: fetch all its pages."""
        edition_id = event.edition_id
        logger.info(f"***** {edition_id} *****")
        self.fetch_all_pages_from_edition(edition_id)

    def fetch_all_pages_from_edition(self, edition_id: UUID):
        """
        Bad design.
        I cannot resolve circular imports
        so i reduplicated the code from 'page_provider'
        Fix me when possible, no time for it right now.
        """
        db = Database()
        try:
            edition = db.get_edition(edition_id)
            if edition is None:
                logger.warning(
                    "Edition %s is not stored: no pages to fetch", edition_id
                )
                return
            page_number = 0
            MORE_PAGES = True
            while MORE_PAGES:
                try:
                    logger.info(
                        "Celery task fetching page %s of edition %s from Polona",
                        page_number,
                        edition.id,
                    )
                    polona_page = request_polona_edition_page(edition.id, page_number)
                    raw_ocr_content = polona_page["raw_ocr_content"]
                    db.save_fetched_page(
                        Page(
                            edition_id=edition.id,
                            number=page_number,
                            raw_ocr_content=raw_ocr_content,
                            xml_content_corrected=str(text_to_tei_xml(raw_ocr_content)),
                            scan_uri=polona_page["scan_uri"],
                        )
                    )
                    logger.info(
                        "Page %s of edition %s is saved", page_number, edition.id
                    )
                    page_number += 1
                except PolonaError as exc:
                    if not exc.not_found:
                        raise  # Polona is down or failed: not the end of the edition
                    logger.info(
                        "Edition %s has no page %s: %s pages in all",
                        edition.id,
                        page_number,
                        page_number,
                    )
                    MORE_PAGES = False
        finally:
            db.close()

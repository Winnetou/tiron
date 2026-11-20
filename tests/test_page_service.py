from unittest import mock
from uuid import uuid4

import pytest

from app import page_service
from app.ontology import Edition, Page
from app.polona_connector import PolonaError
from app.tei_xml import text_to_tei_xml

PAGE = {"scan_uri": "https://polona.pl/scan", "raw_ocr_content": "Ala ma kota"}
METADATA = {
    "author": "Jan Kowalski",
    "title": "Dzieje",
    "place": "Kraków",
    "year": 1800,
}


@pytest.fixture
def polona(monkeypatch):
    request_polona_edition_page = mock.Mock(return_value=PAGE)
    fetch_metadata = mock.Mock(return_value=METADATA)
    monkeypatch.setattr(
        page_service, "request_polona_edition_page", request_polona_edition_page
    )
    monkeypatch.setattr(page_service, "fetch_edition_metadata", fetch_metadata)
    return mock.Mock(page=request_polona_edition_page, metadata=fetch_metadata)


def database(known: Edition | None, stored: Edition | None = None):
    """A database in which the edition is `known`; upsert_edition returns `stored`."""
    db = mock.Mock()
    db.get_edition.return_value = known
    db.upsert_edition.return_value = stored
    db.save_fetched_page.side_effect = lambda page: page
    return db


class TestFetchUnsavedPageFromPolona:
    def test_a_known_edition_is_not_saved_again(self, polona):
        edition_id = uuid4()
        db = database(Edition(id=edition_id, work_id=uuid4()))

        page = page_service.fetch_unsaved_page_from_polona(edition_id, 3, db=db)

        assert page.raw_ocr_content == PAGE["raw_ocr_content"]
        polona.page.assert_called_once_with(edition_id, 3)
        polona.metadata.assert_not_called()
        db.upsert_edition.assert_not_called()

    def test_the_page_is_saved_with_its_text_and_the_xml_made_of_it(self, polona):
        edition_id = uuid4()
        db = database(Edition(id=edition_id, work_id=uuid4()))

        page = page_service.fetch_unsaved_page_from_polona(edition_id, 3, db=db)

        (saved,) = db.save_fetched_page.call_args.args
        assert page is saved
        assert saved.edition_id == edition_id
        assert saved.number == 3
        assert saved.raw_ocr_content == "Ala ma kota"
        assert saved.xml_content_corrected == str(text_to_tei_xml("Ala ma kota"))
        assert saved.scan_uri == PAGE["scan_uri"]

    def test_the_saved_page_that_comes_back_is_the_stored_one(self, polona):
        edition_id = uuid4()
        stored = Page(edition_id=edition_id, number=3, raw_ocr_content="earlier")
        db = database(Edition(id=edition_id, work_id=uuid4()))
        db.save_fetched_page.side_effect = None
        db.save_fetched_page.return_value = stored

        assert (
            page_service.fetch_unsaved_page_from_polona(edition_id, 3, db=db) is stored
        )

    def test_an_unknown_edition_is_saved_with_its_metadata(self, polona):
        edition_id = uuid4()
        db = database(None, Edition(id=edition_id, work_id=uuid4()))

        page_service.fetch_unsaved_page_from_polona(edition_id, 3, db=db)

        polona.metadata.assert_called_once_with(edition_id)
        work, edition = db.upsert_edition.call_args.args
        assert (work.author, work.title) == ("Jan Kowalski", "Dzieje")
        assert edition.id == edition_id
        assert edition.work_id == work.id
        assert (edition.place, edition.year) == ("Kraków", 1800)

    def test_the_page_is_fetched_after_the_edition_is_saved(self, polona):
        edition_id = uuid4()
        db = database(None, Edition(id=edition_id, work_id=uuid4()))
        order = mock.Mock()
        order.attach_mock(db.upsert_edition, "save")
        order.attach_mock(polona.page, "fetch")

        page_service.fetch_unsaved_page_from_polona(edition_id, 3, db=db)

        assert [call[0] for call in order.mock_calls] == ["save", "fetch"]

    def test_a_page_polona_does_not_have_is_a_polona_error(self, polona):
        edition_id = uuid4()
        polona.page.side_effect = PolonaError("no such page", not_found=True)
        db = database(Edition(id=edition_id, work_id=uuid4()))

        with pytest.raises(PolonaError) as raised:
            page_service.fetch_unsaved_page_from_polona(edition_id, 3, db=db)

        assert raised.value.not_found
        db.save_fetched_page.assert_not_called()

    def test_metadata_that_cannot_be_fetched_is_a_polona_error(self, polona):
        polona.metadata.side_effect = PolonaError("down")
        db = database(None)

        with pytest.raises(PolonaError):
            page_service.fetch_unsaved_page_from_polona(uuid4(), 3, db=db)

        db.upsert_edition.assert_not_called()
        polona.page.assert_not_called()


class TestIdempotentFetchPage:
    def test_a_stored_page_is_served_without_contacting_polona(self, polona):
        edition_id = uuid4()
        stored = Page(edition_id=edition_id, number=3, raw_ocr_content="Ala")
        db = database(None)
        db.get_page_by_number.return_value = stored

        page = page_service.idempotent_fetch_page(edition_id, 3, db=db)

        assert page is stored
        db.save_fetched_page.assert_not_called()
        db.get_page_by_number.assert_called_once_with(edition_id, 3)
        polona.page.assert_not_called()
        polona.metadata.assert_not_called()
        db.upsert_edition.assert_not_called()

    def test_a_page_that_is_not_stored_is_fetched_and_saved(self, polona):
        edition_id = uuid4()
        db = database(Edition(id=edition_id, work_id=uuid4()))
        db.get_page_by_number.return_value = None

        page = page_service.idempotent_fetch_page(edition_id, 3, db=db)

        assert page.raw_ocr_content == PAGE["raw_ocr_content"]
        db.save_fetched_page.assert_called_once()
        polona.page.assert_called_once_with(edition_id, 3)

    def test_the_edition_of_a_page_that_is_not_stored_is_saved(self, polona):
        edition_id = uuid4()
        db = database(None, Edition(id=edition_id, work_id=uuid4()))
        db.get_page_by_number.return_value = None

        page_service.idempotent_fetch_page(edition_id, 3, db=db)

        db.upsert_edition.assert_called_once()

    def test_a_polona_error_is_not_caught(self, polona):
        edition_id = uuid4()
        polona.page.side_effect = PolonaError("down")
        db = database(Edition(id=edition_id, work_id=uuid4()))
        db.get_page_by_number.return_value = None

        with pytest.raises(PolonaError):
            page_service.idempotent_fetch_page(edition_id, 3, db=db)

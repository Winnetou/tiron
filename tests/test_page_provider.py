import threading
import time
from unittest import mock
from uuid import uuid4

import pytest

from app import page_provider
from app.persistence.ontology import Edition, Page
from app.polona.polona_connector import PolonaError
from app.text_tools.tei_xml import text_to_tei_xml

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
        page_provider, "request_polona_edition_page", request_polona_edition_page
    )
    monkeypatch.setattr(page_provider, "fetch_edition_metadata", fetch_metadata)
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

        page = page_provider.fetch_unsaved_page_from_polona(edition_id, 3, db=db)

        assert page.raw_ocr_content == PAGE["raw_ocr_content"]
        polona.page.assert_called_once_with(edition_id, 3)
        polona.metadata.assert_not_called()
        db.upsert_edition.assert_not_called()

    def test_the_page_is_saved_with_its_text_and_the_xml_made_of_it(self, polona):
        edition_id = uuid4()
        db = database(Edition(id=edition_id, work_id=uuid4()))

        page = page_provider.fetch_unsaved_page_from_polona(edition_id, 3, db=db)

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
            page_provider.fetch_unsaved_page_from_polona(edition_id, 3, db=db) is stored
        )

    def test_an_unknown_edition_is_saved_with_its_metadata(self, polona):
        edition_id = uuid4()
        db = database(None, Edition(id=edition_id, work_id=uuid4()))

        page_provider.fetch_unsaved_page_from_polona(edition_id, 3, db=db)

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

        page_provider.fetch_unsaved_page_from_polona(edition_id, 3, db=db)

        assert [call[0] for call in order.mock_calls] == ["save", "fetch"]

    def test_a_page_polona_does_not_have_is_a_polona_error(self, polona):
        edition_id = uuid4()
        polona.page.side_effect = PolonaError("no such page", not_found=True)
        db = database(Edition(id=edition_id, work_id=uuid4()))

        with pytest.raises(PolonaError) as raised:
            page_provider.fetch_unsaved_page_from_polona(edition_id, 3, db=db)

        assert raised.value.not_found
        db.save_fetched_page.assert_not_called()

    def test_metadata_that_cannot_be_fetched_is_a_polona_error(self, polona):
        polona.metadata.side_effect = PolonaError("down")
        db = database(None)

        with pytest.raises(PolonaError):
            page_provider.fetch_unsaved_page_from_polona(uuid4(), 3, db=db)

        db.upsert_edition.assert_not_called()
        polona.page.assert_not_called()


class TestIdempotentFetchPage:
    def test_a_stored_page_is_served_without_contacting_polona(self, polona):
        edition_id = uuid4()
        stored = Page(edition_id=edition_id, number=3, raw_ocr_content="Ala")
        db = database(None)
        db.get_page_by_number.return_value = stored

        page = page_provider.idempotent_fetch_page(edition_id, 3, db=db)

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

        page = page_provider.idempotent_fetch_page(edition_id, 3, db=db)

        assert page.raw_ocr_content == PAGE["raw_ocr_content"]
        db.save_fetched_page.assert_called_once()
        polona.page.assert_called_once_with(edition_id, 3)

    def test_the_edition_of_a_page_that_is_not_stored_is_saved(self, polona):
        edition_id = uuid4()
        db = database(None, Edition(id=edition_id, work_id=uuid4()))
        db.get_page_by_number.return_value = None

        page_provider.idempotent_fetch_page(edition_id, 3, db=db)

        db.upsert_edition.assert_called_once()

    def test_a_polona_error_is_not_caught(self, polona):
        edition_id = uuid4()
        polona.page.side_effect = PolonaError("down")
        db = database(Edition(id=edition_id, work_id=uuid4()))
        db.get_page_by_number.return_value = None

        with pytest.raises(PolonaError):
            page_provider.idempotent_fetch_page(edition_id, 3, db=db)


class TestConcurrentFirstFetch:
    """Several requests for the same page that is not stored yet."""

    class FakeDatabase:
        """Stores what is saved, like the real one, and is safe to share."""

        def __init__(self, edition):
            self.edition = edition
            self.pages = {}
            self.saves = 0

        def get_page_by_number(self, edition_id, number):
            return self.pages.get((edition_id, number))

        def get_edition(self, edition_id):
            return self.edition

        def save_fetched_page(self, page):
            self.saves += 1
            return self.pages.setdefault((page.edition_id, page.number), page)

    def run_requests(self, db, edition_id, numbers):
        results = [None] * len(numbers)

        def request(index, number):
            results[index] = page_provider.idempotent_fetch_page(
                edition_id, number, db=db
            )

        threads = [
            threading.Thread(target=request, args=(i, n)) for i, n in enumerate(numbers)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        return results

    @pytest.fixture
    def slow_polona(self, polona):
        def slow(edition_id, number):
            time.sleep(0.1)  # long enough for the other requests to arrive
            return PAGE

        polona.page.side_effect = slow
        return polona

    def test_polona_is_asked_once_and_the_page_is_saved_once(self, slow_polona):
        edition_id = uuid4()
        db = self.FakeDatabase(Edition(id=edition_id, work_id=uuid4()))

        results = self.run_requests(db, edition_id, [3] * 5)

        assert slow_polona.page.call_count == 1
        assert db.saves == 1
        assert all(page is results[0] for page in results)

    def test_a_new_edition_is_saved_once(self, slow_polona):
        edition_id = uuid4()
        db = self.FakeDatabase(None)
        db.upsert_edition = mock.Mock(
            side_effect=lambda work, edition: setattr(db, "edition", edition) or edition
        )
        db.get_edition = lambda edition_id: db.edition

        self.run_requests(db, edition_id, [3] * 5)

        db.upsert_edition.assert_called_once()
        slow_polona.metadata.assert_called_once()

    def test_different_pages_do_not_wait_for_each_other(self, slow_polona):
        edition_id = uuid4()
        db = self.FakeDatabase(Edition(id=edition_id, work_id=uuid4()))
        started = time.perf_counter()

        self.run_requests(db, edition_id, [1, 2, 3, 4])

        assert slow_polona.page.call_count == 4
        assert time.perf_counter() - started < 0.35  # not 4 * 0.1 one after another

    def test_a_failed_fetch_lets_the_next_request_try_again(self, polona):
        edition_id = uuid4()
        db = self.FakeDatabase(Edition(id=edition_id, work_id=uuid4()))
        polona.page.side_effect = [PolonaError("down"), PAGE]

        with pytest.raises(PolonaError):
            page_provider.idempotent_fetch_page(edition_id, 3, db=db)
        page = page_provider.idempotent_fetch_page(edition_id, 3, db=db)

        assert page.raw_ocr_content == "Ala ma kota"

    def test_no_lock_is_left_behind(self, slow_polona):
        edition_id = uuid4()
        db = self.FakeDatabase(Edition(id=edition_id, work_id=uuid4()))

        self.run_requests(db, edition_id, [3] * 3 + [4])

        assert page_provider._fetching == {}

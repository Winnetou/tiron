"""CRUD tests against a real PostgreSQL.

Skipped unless INTEGRATION_DB=1 and the POSTGRES_* variables point to a
disposable database (tables are dropped and recreated).
"""

import os
from uuid import uuid4

import psycopg
import pytest

from app.db import Database
from app.ontology import Correction, Edition, Page, Work
from tests.conftest import real_connect

pytestmark = pytest.mark.skipif(
    os.environ.get("INTEGRATION_DB") != "1", reason="set INTEGRATION_DB=1"
)


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr("app.db.psycopg.connect", real_connect)
    database = Database()
    database.conn.execute(
        "TRUNCATE collection, corrections, pages, editions, works"
        " RESTART IDENTITY CASCADE"
    )
    yield database
    database.close()


@pytest.fixture
def edition(db):
    work = db.create_work(Work(author="Bolesław Prus"))
    return db.create_edition(Edition(work_id=work.id, place="Warszawa", year=1890))


def test_work_roundtrip(db):
    work = db.create_work(Work(author="Prus"))

    assert db.get_work(work.id) == work
    assert db.get_work(uuid4()) is None


def test_edition_roundtrip(db, edition):
    assert db.get_edition(edition.id) == edition
    assert db.get_edition(uuid4()) is None


def test_edition_requires_existing_work(db):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.create_edition(Edition(work_id=uuid4(), place="X", year=1900))


def test_page_roundtrip(db, edition):
    page = db.create_page(Page(edition_id=edition.id, number=1, raw_content="Aia"))

    assert db.get_page(page.id) == page
    assert db.get_page(uuid4()) is None


def test_correction_roundtrip(db, edition):
    page = db.create_page(Page(edition_id=edition.id, number=1, raw_content="Aia"))

    correction = db.create_correction(
        Correction(page_id=page.id, before="Aia", after="Ala")
    )

    assert db.get_correction(correction.id) == correction
    assert db.get_correction(uuid4()) is None


def test_collection_table_enforces_unique_ids(db):
    uid = uuid4()
    db.conn.execute("INSERT INTO collection (id) VALUES (%s)", (uid,))

    with pytest.raises(psycopg.errors.UniqueViolation):
        db.conn.execute("INSERT INTO collection (id) VALUES (%s)", (uid,))


def test_check_edition_in_collection(db):
    present, absent = uuid4(), uuid4()
    db.conn.execute("INSERT INTO collection (id) VALUES (%s)", (present,))

    assert db.check_edition_in_collection(present) is True
    assert db.check_edition_in_collection(absent) is False


def test_upsert_edition_creates_work_and_edition(db):
    work = Work(author=None)
    edition = Edition(work_id=work.id, place=None, year=None)

    stored = db.upsert_edition(work, edition)

    assert stored == edition
    assert db.get_work(work.id) == work
    assert db.get_edition(edition.id) == edition


def test_upsert_edition_is_noop_when_edition_exists(db):
    work = Work(author="Prus")
    edition = Edition(work_id=work.id, place="Warszawa", year=1890)
    db.upsert_edition(work, edition)
    other_work = Work(author="Someone else")

    stored = db.upsert_edition(
        other_work, Edition(id=edition.id, work_id=other_work.id, year=1900)
    )

    assert stored == edition
    assert db.get_work(other_work.id) is None  # no orphan work left behind


def test_editions_of_same_author_and_title_share_a_work(db):
    first_work = Work(author="Prus, Bolesław", title="Lalka")
    first = db.upsert_edition(
        first_work, Edition(work_id=first_work.id, place="Warszawa", year=1890)
    )
    other_work = Work(author="PRUS, BOLESŁAW", title="lalka")  # case-insensitive
    second = db.upsert_edition(
        other_work, Edition(work_id=other_work.id, place="Kraków", year=1910)
    )

    assert first.work_id == first_work.id
    assert second.work_id == first_work.id
    assert db.get_edition(second.id) == second
    assert db.get_work(other_work.id) is None
    count = db.conn.execute("SELECT count(*) AS n FROM works").fetchone()["n"]
    assert count == 1


def test_different_title_or_author_gets_own_work(db):
    works = [
        Work(author="Prus", title="Lalka"),
        Work(author="Prus", title="Faraon"),
        Work(author="Sienkiewicz", title="Lalka"),
    ]
    editions = [db.upsert_edition(w, Edition(work_id=w.id, year=1900)) for w in works]

    assert len({e.work_id for e in editions}) == 3


def test_unknown_author_or_title_never_matches(db):
    works = [Work(author=None, title="Lalka"), Work(author="Prus", title=None)]
    works += [Work(author=None, title="Lalka"), Work(author="Prus", title=None)]
    editions = [db.upsert_edition(w, Edition(work_id=w.id)) for w in works]

    assert len({e.work_id for e in editions}) == 4


def test_page_edition_and_number_are_unique_together(db, edition):
    db.create_page(Page(edition_id=edition.id, number=1, raw_content="a"))

    with pytest.raises(psycopg.errors.UniqueViolation):
        db.create_page(Page(edition_id=edition.id, number=1, raw_content="b"))


def test_same_page_number_allowed_in_different_editions(db, edition):
    work = Work(author="Other")
    other = db.upsert_edition(work, Edition(work_id=work.id, year=1900))

    db.create_page(Page(edition_id=edition.id, number=1, raw_content="a"))
    db.create_page(Page(edition_id=other.id, number=1, raw_content="b"))


def test_page_ready_defaults_to_false_and_roundtrips(db, edition):
    page = db.create_page(Page(edition_id=edition.id, number=1, raw_content="a"))
    ready = db.create_page(
        Page(edition_id=edition.id, number=2, raw_content="b", ready=True)
    )

    assert db.get_page(page.id).ready is False
    assert db.get_page(ready.id).ready is True


def test_page_scan_uri_roundtrips(db, edition):
    uri = "https://polona.pl/iiif/3/x/full/max/0/default.jpg"
    page = db.create_page(
        Page(edition_id=edition.id, number=1, raw_content="a", scan_uri=uri)
    )
    without = db.create_page(Page(edition_id=edition.id, number=2, raw_content="b"))

    assert db.get_page(page.id).scan_uri == uri
    assert db.get_page(without.id).scan_uri is None


def test_save_fetched_page_inserts(db, edition):
    page = Page(edition_id=edition.id, number=3, raw_content="raw", scan_uri="u")

    stored = db.save_fetched_page(page)

    assert stored == page
    assert db.get_page(page.id) == page


def test_save_fetched_page_refreshes_fetched_data_but_keeps_work(db, edition):
    first = db.save_fetched_page(
        Page(edition_id=edition.id, number=3, raw_content="raw", scan_uri="u1")
    )
    # a user's work on the page
    db.conn.execute(
        "UPDATE pages SET corrected_content = 'fixed', ready = true WHERE id = %s",
        (first.id,),
    )

    stored = db.save_fetched_page(
        Page(edition_id=edition.id, number=3, raw_content="raw v2", scan_uri="u2")
    )

    assert stored.id == first.id
    assert stored.raw_content == "raw v2"
    assert stored.scan_uri == "u2"
    assert stored.corrected_content == "fixed"
    assert stored.ready is True
    count = db.conn.execute("SELECT count(*) AS n FROM pages").fetchone()["n"]
    assert count == 1


def test_save_fetched_page_requires_existing_edition(db):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.save_fetched_page(Page(edition_id=uuid4(), number=0, raw_content="x"))


def test_get_page_by_number(db, edition):
    page = db.create_page(
        Page(edition_id=edition.id, number=5, raw_content="a", scan_uri="u")
    )
    work = Work(author="Other")
    other = db.upsert_edition(work, Edition(work_id=work.id, year=1900))
    db.create_page(Page(edition_id=other.id, number=6, raw_content="b"))

    assert db.get_page_by_number(edition.id, 5) == page
    assert db.get_page_by_number(edition.id, 6) is None  # number of another edition
    assert db.get_page_by_number(uuid4(), 5) is None


@pytest.fixture
def stored_page(db, edition):
    return db.create_page(
        Page(
            edition_id=edition.id,
            number=7,
            raw_content="raw text",
            corrected_content="old fix",
            scan_uri="https://polona.pl/s.jpg",
        )
    )


def test_update_page_changes_both_fields(db, stored_page):
    updated = db.update_page(
        stored_page.edition_id, 7, corrected_content="new fix", ready=True
    )

    assert updated.corrected_content == "new fix"
    assert updated.ready is True
    assert db.get_page(stored_page.id) == updated


def test_update_page_leaves_everything_else_alone(db, stored_page):
    updated = db.update_page(stored_page.edition_id, 7, ready=True)

    assert updated == stored_page.model_copy(update={"ready": True})
    assert updated.corrected_content == "old fix"  # not given: untouched


def test_update_page_can_clear_the_correction(db, stored_page):
    updated = db.update_page(stored_page.edition_id, 7, corrected_content=None)

    assert updated.corrected_content is None
    assert updated.raw_content == "raw text"


def test_update_page_only_touches_that_page(db, stored_page):
    other = db.create_page(
        Page(edition_id=stored_page.edition_id, number=8, raw_content="other")
    )

    db.update_page(stored_page.edition_id, 7, corrected_content="x", ready=True)

    assert db.get_page(other.id) == other


def test_update_page_unknown_page(db, stored_page):
    assert db.update_page(stored_page.edition_id, 99, ready=True) is None
    assert db.update_page(uuid4(), 7, ready=True) is None


def test_update_page_needs_something_to_update(db, stored_page):
    with pytest.raises(ValueError):
        db.update_page(stored_page.edition_id, 7)


@pytest.mark.parametrize("field", ["raw_content", "scan_uri", "number", "id"])
def test_update_page_cannot_change_other_columns(db, stored_page, field):
    with pytest.raises(TypeError):
        db.update_page(stored_page.edition_id, 7, **{field: "x"})


def test_fetched_page_keeps_raw_text_and_tei_separately(db, edition):
    tei = "<?xml version='1.0'?><TEI/>"
    page = Page(
        edition_id=edition.id,
        number=5,
        raw_content="raw ocr text",
        corrected_content=tei,
        scan_uri="u",
    )

    stored = db.save_fetched_page(page)

    assert stored.raw_content == "raw ocr text"
    assert stored.corrected_content == tei
    assert db.get_page_by_number(edition.id, 5) == stored

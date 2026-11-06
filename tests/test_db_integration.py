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


def test_upsert_page_inserts_then_updates(db, edition):
    first = db.upsert_page(Page(edition_id=edition.id, number=1, raw_content="Aia"))
    second = db.upsert_page(
        Page(
            edition_id=edition.id,
            number=1,
            raw_content="Aia",
            corrected_content="Ala",
        )
    )

    assert second.id == first.id
    assert second.corrected_content == "Ala"
    assert db.get_page(first.id) == second


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


def test_schema_migrates_older_database(db):
    db.conn.execute("DROP TABLE corrections, pages, editions, works")
    db.conn.execute("CREATE TABLE works (id uuid PRIMARY KEY, author text NOT NULL)")
    db.conn.execute(
        "CREATE TABLE editions (id uuid PRIMARY KEY,"
        " work_id uuid NOT NULL REFERENCES works (id),"
        " place text NOT NULL, year integer NOT NULL)"
    )

    migrated = Database()  # re-runs the schema on the old tables
    work = Work(author=None, title=None)
    stored = migrated.upsert_edition(work, Edition(work_id=work.id))
    migrated.close()

    assert stored.place is None


def test_page_edition_and_number_are_unique_together(db, edition):
    db.create_page(Page(edition_id=edition.id, number=1, raw_content="a"))

    with pytest.raises(psycopg.errors.UniqueViolation):
        db.create_page(Page(edition_id=edition.id, number=1, raw_content="b"))


def test_same_page_number_allowed_in_different_editions(db, edition):
    work = Work(author="Other")
    other = db.upsert_edition(work, Edition(work_id=work.id, year=1900))

    db.create_page(Page(edition_id=edition.id, number=1, raw_content="a"))
    db.create_page(Page(edition_id=other.id, number=1, raw_content="b"))

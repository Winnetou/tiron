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
        "TRUNCATE corrections, pages, editions, works RESTART IDENTITY CASCADE"
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

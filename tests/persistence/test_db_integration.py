"""CRUD tests against a real PostgreSQL.

Skipped unless INTEGRATION_DB=1 and the POSTGRES_* variables point to a
disposable database (its tables are emptied). They refuse to run unless
POSTGRES_HOST is local (see `refuse_remote_database` in conftest.py).
"""

import os
from uuid import uuid4

import psycopg
import pytest
from fastapi_pagination import Page as PaginatedPage
from fastapi_pagination import Params, set_page, set_params

from app.persistence.db import Database, WorkExists, get_conninfo
from app.persistence.ontology import (
    Correction,
    Edition,
    EditionMetadata,
    ExternalEdition,
    ExternalEditionSpelling,
    LegalSpelling,
    Page,
    Work,
)
from app.persistence.passwords import hash_password
from app.persistence.schema_creator import SchemaCreator
from app.text_tools.tei_xml import mark_section, text_to_tei_xml
from app.view_models import CollectionEdition
from tests.conftest import real_connect, refuse_remote_database

pytestmark = pytest.mark.skipif(
    os.environ.get("INTEGRATION_DB") != "1", reason="set INTEGRATION_DB=1"
)


@pytest.fixture
def db(monkeypatch):
    refuse_remote_database()  # the tables are emptied below
    monkeypatch.setattr("app.persistence.db.psycopg.connect", real_connect)
    monkeypatch.setenv("PROD", "false")  # the schema is created in a test database
    database = Database()
    SchemaCreator(database).create_schema()
    database.conn.execute(
        "TRUNCATE collection, corrections, legal_spellings, external_edition_spelling,"
        " external_edition, pages, editions, works, users"
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


def test_edition_fetch_columns_default_to_not_completed_and_no_page_count(db, edition):
    stored = db.get_edition(edition.id)

    assert stored.fetched_completed is False
    assert stored.page_count is None


def test_create_edition_writes_the_fetch_columns(db):
    work = db.create_work(Work(author="Bolesław Prus"))

    created = db.create_edition(
        Edition(work_id=work.id, fetched_completed=True, page_count=412)
    )

    stored = db.get_edition(created.id)
    assert (created.fetched_completed, created.page_count) == (True, 412)
    assert (stored.fetched_completed, stored.page_count) == (True, 412)


def test_edition_page_count_must_be_at_least_1(db, edition):
    for bad in (0, -3):
        with pytest.raises(psycopg.errors.CheckViolation):
            db.conn.execute(
                "UPDATE editions SET page_count = %s WHERE id = %s", (bad, edition.id)
            )


def test_edition_fetch_columns_can_be_set_and_page_count_can_be_null_again(db, edition):
    db.conn.execute(
        "UPDATE editions SET fetched_completed = true, page_count = 1 WHERE id = %s",
        (edition.id,),
    )
    stored = db.get_edition(edition.id)
    assert (stored.fetched_completed, stored.page_count) == (True, 1)

    db.conn.execute(
        "UPDATE editions SET page_count = NULL WHERE id = %s", (edition.id,)
    )
    assert db.get_edition(edition.id).page_count is None


def test_edition_fetched_completed_cannot_be_null(db, edition):
    with pytest.raises(psycopg.errors.NotNullViolation):
        db.conn.execute(
            "UPDATE editions SET fetched_completed = NULL WHERE id = %s", (edition.id,)
        )


def test_edition_requires_existing_work(db):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.create_edition(Edition(work_id=uuid4(), place="X", year=1900))


def test_page_roundtrip(db, edition):
    page = db.create_page(Page(edition_id=edition.id, number=1, raw_ocr_content="Aia"))

    assert db.get_page(page.id) == page
    assert db.get_page(uuid4()) is None


def test_correction_roundtrip(db, edition):
    page = db.create_page(Page(edition_id=edition.id, number=1, raw_ocr_content="Aia"))

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


def test_page_edition_and_number_are_unique_together(db, edition):
    db.create_page(Page(edition_id=edition.id, number=1, raw_ocr_content="a"))

    with pytest.raises(psycopg.errors.UniqueViolation):
        db.create_page(Page(edition_id=edition.id, number=1, raw_ocr_content="b"))


def test_same_page_number_allowed_in_different_editions(db, edition):
    work = Work(author="Other")
    db.create_work(work)
    other = db.create_edition(Edition(work_id=work.id, year=1900))

    db.create_page(Page(edition_id=edition.id, number=1, raw_ocr_content="a"))
    db.create_page(Page(edition_id=other.id, number=1, raw_ocr_content="b"))


def test_page_ready_defaults_to_false_and_roundtrips(db, edition):
    page = db.create_page(Page(edition_id=edition.id, number=1, raw_ocr_content="a"))
    ready = db.create_page(
        Page(edition_id=edition.id, number=2, raw_ocr_content="b", ready=True)
    )

    assert db.get_page(page.id).ready is False
    assert db.get_page(ready.id).ready is True


def test_page_scan_uri_roundtrips(db, edition):
    uri = "https://polona.pl/iiif/3/x/full/max/0/default.jpg"
    page = db.create_page(
        Page(edition_id=edition.id, number=1, raw_ocr_content="a", scan_uri=uri)
    )
    without = db.create_page(Page(edition_id=edition.id, number=2, raw_ocr_content="b"))

    assert db.get_page(page.id).scan_uri == uri
    assert db.get_page(without.id).scan_uri is None


def test_save_fetched_page_inserts(db, edition):
    page = Page(edition_id=edition.id, number=3, raw_ocr_content="raw", scan_uri="u")

    stored = db.save_fetched_page(page)

    assert stored == page
    assert db.get_page(page.id) == page


def test_save_fetched_page_refreshes_only_the_scan_and_keeps_the_rest(db, edition):
    first = db.save_fetched_page(
        Page(edition_id=edition.id, number=3, raw_ocr_content="raw", scan_uri="u1")
    )
    # a user's work on the page
    db.conn.execute(
        "UPDATE pages SET xml_content_corrected = 'fixed', ready = true WHERE id = %s",
        (first.id,),
    )

    stored = db.save_fetched_page(
        Page(edition_id=edition.id, number=3, raw_ocr_content="raw v2", scan_uri="u2")
    )

    assert stored.id == first.id
    assert stored.raw_ocr_content == "raw"  # written once, never changes
    assert stored.scan_uri == "u2"
    assert stored.xml_content_corrected == "fixed"
    assert stored.ready is True
    count = db.conn.execute("SELECT count(*) AS n FROM pages").fetchone()["n"]
    assert count == 1


def test_save_fetched_page_requires_existing_edition(db):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.save_fetched_page(Page(edition_id=uuid4(), number=0, raw_ocr_content="x"))


def test_get_page_by_number(db, edition):
    page = db.create_page(
        Page(edition_id=edition.id, number=5, raw_ocr_content="a", scan_uri="u")
    )
    work = Work(author="Other")
    db.create_work(work)
    other = db.create_edition(Edition(work_id=work.id, year=1900))
    db.create_page(Page(edition_id=other.id, number=6, raw_ocr_content="b"))

    assert db.get_page_by_number(edition.id, 5) == page
    assert db.get_page_by_number(edition.id, 6) is None  # number of another edition
    assert db.get_page_by_number(uuid4(), 5) is None


@pytest.fixture
def stored_page(db, edition):
    return db.create_page(
        Page(
            edition_id=edition.id,
            number=7,
            raw_ocr_content="raw text",
            xml_content_corrected="old fix",
            scan_uri="https://polona.pl/s.jpg",
        )
    )


def test_update_page_changes_both_fields(db, stored_page):
    updated = db.update_page(
        stored_page.edition_id, 7, xml_content_corrected="new fix", ready=True
    )

    assert updated.xml_content_corrected == "new fix"
    assert updated.ready is True
    assert db.get_page(stored_page.id) == updated


def test_update_page_leaves_everything_else_alone(db, stored_page):
    updated = db.update_page(stored_page.edition_id, 7, ready=True)

    assert updated == stored_page.model_copy(update={"ready": True})
    assert updated.xml_content_corrected == "old fix"  # not given: untouched


def test_update_page_can_clear_the_correction(db, stored_page):
    updated = db.update_page(stored_page.edition_id, 7, xml_content_corrected=None)

    assert updated.xml_content_corrected is None
    assert updated.raw_ocr_content == "raw text"


def test_update_page_only_touches_that_page(db, stored_page):
    other = db.create_page(
        Page(edition_id=stored_page.edition_id, number=8, raw_ocr_content="other")
    )

    db.update_page(stored_page.edition_id, 7, xml_content_corrected="x", ready=True)

    assert db.get_page(other.id) == other


def test_update_page_unknown_page(db, stored_page):
    assert db.update_page(stored_page.edition_id, 99, ready=True) is None
    assert db.update_page(uuid4(), 7, ready=True) is None


def test_update_page_needs_something_to_update(db, stored_page):
    with pytest.raises(ValueError):
        db.update_page(stored_page.edition_id, 7)


@pytest.mark.parametrize("field", ["raw_ocr_content", "scan_uri", "number", "id"])
def test_update_page_cannot_change_other_columns(db, stored_page, field):
    with pytest.raises(TypeError):
        db.update_page(stored_page.edition_id, 7, **{field: "x"})


def test_fetched_page_keeps_raw_text_and_tei_separately(db, edition):
    tei = "<?xml version='1.0'?><TEI/>"
    page = Page(
        edition_id=edition.id,
        number=5,
        raw_ocr_content="raw ocr text",
        xml_content_corrected=tei,
        scan_uri="u",
    )

    stored = db.save_fetched_page(page)

    assert stored.raw_ocr_content == "raw ocr text"
    assert stored.xml_content_corrected == tei
    assert db.get_page_by_number(edition.id, 5) == stored


class TestApplyCorrection:
    @pytest.fixture
    def stored(self, db, edition):
        return db.create_page(
            Page(
                edition_id=edition.id,
                number=3,
                raw_ocr_content="raw",
                xml_content_corrected="<TEI>old</TEI>",
                scan_uri="u",
            )
        )

    def corrections(self, db):
        return db.conn.execute(
            'SELECT page_id, "before", "after" FROM corrections ORDER BY id'
        ).fetchall()

    def test_saves_the_content_without_a_correction_when_not_recording(
        self, db, stored
    ):
        updated = db.apply_correction(
            stored.edition_id,
            3,
            lambda xml: xml.replace("old", "new"),
            "old",
            "",
            record=False,
        )

        assert updated.xml_content_corrected == "<TEI>new</TEI>"
        assert self.corrections(db) == []

    def test_saves_the_new_content_and_records_the_correction(self, db, stored):
        updated = db.apply_correction(
            stored.edition_id, 3, lambda xml: xml.replace("old", "new"), "old", "new"
        )

        assert updated.xml_content_corrected == "<TEI>new</TEI>"
        assert db.get_page(stored.id) == updated
        assert self.corrections(db) == [
            {"page_id": stored.id, "before": "old", "after": "new"}
        ]

    def test_changes_nothing_else_of_the_page(self, db, stored):
        updated = db.apply_correction(stored.edition_id, 3, lambda xml: "x", "a", "b")

        assert updated == stored.model_copy(update={"xml_content_corrected": "x"})

    def test_replace_gets_the_current_content(self, db, stored):
        seen = []

        def replace(xml):
            seen.append(xml)
            return xml + "!"

        db.apply_correction(stored.edition_id, 3, replace, "a", "b")
        db.apply_correction(stored.edition_id, 3, replace, "b", "c")

        assert seen == ["<TEI>old</TEI>", "<TEI>old</TEI>!"]
        assert db.get_page(stored.id).xml_content_corrected == "<TEI>old</TEI>!!"
        assert len(self.corrections(db)) == 2

    def test_a_page_without_xml_content_corrected_gets_an_empty_string(
        self, db, edition
    ):
        page = db.create_page(
            Page(edition_id=edition.id, number=8, raw_ocr_content="raw")
        )
        seen = []

        db.apply_correction(
            edition.id, 8, lambda xml: seen.append(xml) or "new", "a", "b"
        )

        assert seen == [""]
        assert db.get_page(page.id).xml_content_corrected == "new"

    def test_no_such_page(self, db, stored):
        assert (
            db.apply_correction(stored.edition_id, 99, lambda xml: "x", "a", "b")
            is None
        )
        assert db.apply_correction(uuid4(), 3, lambda xml: "x", "a", "b") is None
        assert self.corrections(db) == []
        assert db.get_page(stored.id) == stored

    def test_if_replace_raises_nothing_is_changed(self, db, stored):
        def refuse(xml):
            raise ValueError("no")

        with pytest.raises(ValueError, match="no"):
            db.apply_correction(stored.edition_id, 3, refuse, "a", "b")

        assert db.get_page(stored.id) == stored
        assert self.corrections(db) == []

    @pytest.mark.parametrize(("before", "after"), [("", "b"), ("a", "")])
    def test_if_the_correction_is_not_valid_the_page_is_not_changed_either(
        self, db, stored, before, after
    ):
        with pytest.raises(ValueError):  # pydantic's ValidationError is one
            db.apply_correction(
                stored.edition_id, 3, lambda xml: "changed", before, after
            )

        assert db.get_page(stored.id) == stored  # rolled back with the correction
        assert self.corrections(db) == []

    def test_other_pages_are_left_alone(self, db, stored):
        other = db.create_page(
            Page(
                edition_id=stored.edition_id,
                number=4,
                raw_ocr_content="r",
                xml_content_corrected="o",
            )
        )

        db.apply_correction(stored.edition_id, 3, lambda xml: "x", "a", "b")

        assert db.get_page(other.id) == other

    def test_the_row_is_locked_while_it_is_corrected(self, db, stored):
        """Another connection that wants the same page waits until the first is
        done, and then sees its result (so two corrections cannot overwrite each
        other)."""
        import threading
        import time

        from tests.conftest import real_connect

        order = []
        second = real_connect(get_conninfo(), autocommit=True)

        def slow(xml):
            order.append("first reads")
            time.sleep(0.4)
            return xml + "1"

        def other():
            time.sleep(0.1)  # start while the first is holding the lock
            with second.transaction():
                row = second.execute(
                    "SELECT xml_content_corrected FROM pages WHERE id = %s FOR UPDATE",
                    (stored.id,),
                ).fetchone()
                order.append(f"second sees {row[0]!r}")

        thread = threading.Thread(target=other)
        thread.start()
        db.apply_correction(stored.edition_id, 3, slow, "a", "b")
        thread.join()
        second.close()

        assert order == ["first reads", "second sees '<TEI>old</TEI>1'"]


class TestLegalSpellings:
    def rows(self, db):
        return db.conn.execute(
            "SELECT edition_id, spelling FROM legal_spellings ORDER BY spelling"
        ).fetchall()

    def test_adds_a_spelling_to_an_edition(self, db, edition):
        added = db.add_legal_spelling(
            LegalSpelling(edition_id=edition.id, spelling="jey")
        )

        assert added is True
        assert self.rows(db) == [{"edition_id": edition.id, "spelling": "jey"}]

    def test_the_same_spelling_again_is_not_added_twice(self, db, edition):
        legal = LegalSpelling(edition_id=edition.id, spelling="jey")

        first = db.add_legal_spelling(legal)
        second = db.add_legal_spelling(legal)

        assert (first, second) == (True, False)
        assert len(self.rows(db)) == 1

    def test_the_pair_is_unique_in_the_table_itself(self, db, edition):
        db.conn.execute(
            "INSERT INTO legal_spellings (edition_id, spelling) VALUES (%s, 'jey')",
            (edition.id,),
        )

        with pytest.raises(psycopg.errors.UniqueViolation):
            db.conn.execute(
                "INSERT INTO legal_spellings (edition_id, spelling) VALUES (%s, 'jey')",
                (edition.id,),
            )

    def test_the_same_spelling_can_be_legal_in_many_editions(self, db, edition):
        work = Work(author="Other")
        db.create_work(work)
        other = db.create_edition(Edition(work_id=work.id, year=1900))

        added = [
            db.add_legal_spelling(LegalSpelling(edition_id=e.id, spelling="jey"))
            for e in (edition, other)
        ]

        assert added == [True, True]
        assert {row["edition_id"] for row in self.rows(db)} == {edition.id, other.id}

    def test_an_edition_can_have_many_spellings(self, db, edition):
        added = [
            db.add_legal_spelling(LegalSpelling(edition_id=edition.id, spelling=s))
            for s in ("jey", "tey", "raczey")
        ]

        assert added == [True, True, True]
        assert [row["spelling"] for row in self.rows(db)] == ["jey", "raczey", "tey"]

    def test_case_matters(self, db, edition):
        added = [
            db.add_legal_spelling(LegalSpelling(edition_id=edition.id, spelling=s))
            for s in ("jey", "Jey", "JEY")
        ]

        assert added == [True, True, True]

    def test_the_edition_must_be_stored(self, db):
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            db.add_legal_spelling(LegalSpelling(edition_id=uuid4(), spelling="jey"))

        assert self.rows(db) == []

    def test_an_empty_spelling_is_refused_by_the_table_too(self, db, edition):
        with pytest.raises(psycopg.errors.CheckViolation):
            db.conn.execute(
                "INSERT INTO legal_spellings (edition_id, spelling) VALUES (%s, '')",
                (edition.id,),
            )

    def test_the_table_has_exactly_the_two_columns(self, db):
        columns = db.conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'legal_spellings' ORDER BY ordinal_position"
        ).fetchall()

        assert [c["column_name"] for c in columns] == ["edition_id", "spelling"]

    def edition_of_year(self, db, year, author="Other"):
        work = Work(author=author)
        db.create_work(work)
        return db.create_edition(Edition(work_id=work.id, year=year))

    def legalize(self, db, edition, *spellings):
        for spelling in spellings:
            db.add_legal_spelling(
                LegalSpelling(edition_id=edition.id, spelling=spelling)
            )

    def test_gets_the_spellings_of_an_edition(self, db, edition):
        other = self.edition_of_year(db, 1950)  # not from the same time
        self.legalize(db, edition, "jey", "tey")
        self.legalize(db, other, "raczey")

        assert db.get_legal_spellings(edition.id) == {"jey", "tey"}
        assert db.get_legal_spellings(other.id) == {"raczey"}

    def test_an_edition_without_spellings_or_not_stored_has_none(self, db, edition):
        assert db.get_legal_spellings(edition.id) == set()
        assert db.get_legal_spellings(uuid4()) == set()

    def test_the_spellings_of_editions_from_the_same_time_count_too(self, db, edition):
        earlier = self.edition_of_year(db, 1885)
        later = self.edition_of_year(db, 1897)
        self.legalize(db, edition, "jey")
        self.legalize(db, earlier, "tey")
        self.legalize(db, later, "raczey")

        assert db.get_legal_spellings(edition.id) == {"jey", "tey", "raczey"}
        # (the edition of 1890 is within 10 years of both, the two are 12 apart)
        assert db.get_legal_spellings(earlier.id) == {"jey", "tey"}
        assert db.get_legal_spellings(later.id) == {"jey", "raczey"}

    def test_ten_years_either_way_are_included_and_no_more(self, db, edition):
        near = [self.edition_of_year(db, 1880), self.edition_of_year(db, 1900)]
        far = [self.edition_of_year(db, 1879), self.edition_of_year(db, 1901)]
        for e, spelling in zip(near + far, ("n1", "n2", "f1", "f2"), strict=True):
            self.legalize(db, e, spelling)

        assert db.get_legal_spellings(edition.id) == {"n1", "n2"}  # 1890 +- 10

    def test_the_window_is_the_constant(self):
        from app.persistence.db import LEGAL_SPELLINGS_YEARS

        assert LEGAL_SPELLINGS_YEARS == 10

    def test_editions_of_an_unknown_year_are_not_included(self, db, edition):
        work = Work(author="Other")
        db.create_work(work)
        unknown = db.create_edition(Edition(work_id=work.id, year=None))
        self.legalize(db, edition, "jey")
        self.legalize(db, unknown, "tey")

        assert db.get_legal_spellings(edition.id) == {"jey"}

    def test_an_edition_of_an_unknown_year_has_only_its_own(self, db, edition):
        work = Work(author="Other")
        db.create_work(work)
        unknown = db.create_edition(Edition(work_id=work.id, year=None))
        self.legalize(db, edition, "jey")
        self.legalize(db, unknown, "tey")

        assert db.get_legal_spellings(unknown.id) == {"tey"}

    def test_a_spelling_that_is_legal_in_several_editions_is_there_once(
        self, db, edition
    ):
        other = self.edition_of_year(db, 1892)
        self.legalize(db, edition, "jey")
        self.legalize(db, other, "jey")

        assert db.get_legal_spellings(edition.id) == {"jey"}

    def test_case_still_matters_across_editions(self, db, edition):
        other = self.edition_of_year(db, 1892)
        self.legalize(db, edition, "jey")
        self.legalize(db, other, "Jey")

        assert db.get_legal_spellings(edition.id) == {"jey", "Jey"}

    def test_the_edition_itself_is_enough_with_nobody_else_near(self, db, edition):
        self.legalize(db, edition, "jey")

        assert db.get_legal_spellings(edition.id) == {"jey"}

    def test_the_schema_can_be_created_again(self, db, edition):
        db.add_legal_spelling(LegalSpelling(edition_id=edition.id, spelling="jey"))

        again = Database()  # runs the schema once more, on the existing tables
        again.close()

        assert len(self.rows(db)) == 1  # and nothing was lost


class TestLegalSpellingsThroughTheEndpoint:
    """POST /editions/{id}/legal-spellings with the real database behind it."""

    @pytest.fixture
    def client(self, db, monkeypatch):
        from fastapi.testclient import TestClient

        import app.main as main

        monkeypatch.setattr(main, "db", db)
        client = TestClient(main.app)
        # logged in for real: a user in the table, and the login endpoint
        add_user(db, "anna", "secret")
        login = client.post(
            "/auth/login", json={"username": "anna", "password": "secret"}
        )
        assert login.status_code == 200
        return client

    def stored(self, db):
        return db.conn.execute(
            "SELECT edition_id, spelling FROM legal_spellings ORDER BY spelling"
        ).fetchall()

    def test_stores_the_spelling_for_the_edition(self, client, db, edition):
        response = client.post(
            f"/editions/{edition.id}/legal-spellings", json={"spelling": "jey"}
        )

        assert response.status_code == 201
        assert response.json() == {"edition_id": str(edition.id), "spelling": "jey"}
        assert self.stored(db) == [{"edition_id": edition.id, "spelling": "jey"}]

    def test_again_is_a_200_and_stores_nothing_more(self, client, db, edition):
        url = f"/editions/{edition.id}/legal-spellings"
        client.post(url, json={"spelling": "jey"})

        response = client.post(url, json={"spelling": "jey"})

        assert response.status_code == 200
        assert len(self.stored(db)) == 1

    def test_an_edition_that_is_not_stored_is_a_404(self, client, db):
        response = client.post(
            f"/editions/{uuid4()}/legal-spellings", json={"spelling": "jey"}
        )

        assert response.status_code == 404
        assert self.stored(db) == []

    def test_each_edition_has_its_own_spellings(self, client, db, edition):
        work = Work(author="Other")
        db.create_work(work)
        other = db.create_edition(Edition(work_id=work.id, year=1900))

        for e in (edition, other):
            assert (
                client.post(
                    f"/editions/{e.id}/legal-spellings", json={"spelling": "jey"}
                ).status_code
                == 201
            )

        assert {row["edition_id"] for row in self.stored(db)} == {edition.id, other.id}

    def test_the_spell_check_then_counts_the_spelling_as_correct(
        self, client, db, edition
    ):
        # the dictionary: knows the ordinary words only
        import app.text_tools.spelling as spelling

        class Dictionary:
            def lookup(self, word):
                return word.lower() in {"ala", "ma"}

        spelling._checker = Dictionary()
        try:
            body = {"text": "Ala ma jey tey", "edition_id": str(edition.id)}
            before = client.post("/spellcheck", json=body).json()["words"]
            client.post(
                f"/editions/{edition.id}/legal-spellings", json={"spelling": "jey"}
            )
            after = client.post("/spellcheck", json=body).json()["words"]
        finally:
            spelling._checker = None

        assert [w["text"] for w in before if not w["correct"]] == ["jey", "tey"]
        assert [w["text"] for w in after if not w["correct"]] == ["tey"]

    def test_a_refused_spelling_stores_nothing(self, client, db, edition):
        response = client.post(
            f"/editions/{edition.id}/legal-spellings", json={"spelling": "je y"}
        )

        assert response.status_code == 422
        assert self.stored(db) == []


class TestEditionMetadata:
    def edition_of(self, db, author, title, place="Kraków", year=1880):
        work = db.create_work(Work(author=author, title=title))
        return db.create_edition(Edition(work_id=work.id, place=place, year=year))

    def test_reads_the_edition_and_its_work(self, db):
        edition = self.edition_of(db, "Prus", "Lalka")

        found = db.get_edition_metadata(edition.id)

        assert found == EditionMetadata(
            id=edition.id,
            work_id=edition.work_id,
            work_editions=1,
            author="Prus",
            title="Lalka",
            place="Kraków",
            year=1880,
        )

    def test_says_how_many_editions_the_work_has(self, db):
        first = self.edition_of(db, "Prus", "Lalka")
        db.create_edition(Edition(work_id=first.work_id, year=1900))

        assert db.get_edition_metadata(first.id).work_editions == 2

    def test_no_such_edition(self, db):
        assert db.get_edition_metadata(uuid4()) is None
        assert db.update_edition_metadata(uuid4(), {"year": 1890}) is None

    def test_changes_place_and_year_of_the_edition_alone(self, db):
        first = self.edition_of(db, "Prus", "Lalka")
        second = db.create_edition(
            Edition(work_id=first.work_id, place="Wilno", year=1900)
        )

        found = db.update_edition_metadata(first.id, {"place": "Lwów", "year": 1890})

        assert (found.place, found.year, found.author, found.title) == (
            "Lwów",
            1890,
            "Prus",
            "Lalka",
        )
        other = db.get_edition_metadata(second.id)  # another edition: not changed
        assert (other.place, other.year) == ("Wilno", 1900)

    def test_author_and_title_are_changed_in_the_work_itself(self, db):
        first = self.edition_of(db, "Prus", "Lalka")
        second = db.create_edition(Edition(work_id=first.work_id, year=1900))

        found = db.update_edition_metadata(
            first.id, {"author": "Prus, Bolesław", "title": "Lalka (powieść)"}
        )

        assert found.work_id == first.work_id  # the same work, changed in place
        assert (found.author, found.title) == ("Prus, Bolesław", "Lalka (powieść)")
        # the other edition of the work has the change too
        other = db.get_edition_metadata(second.id)
        assert (other.author, other.title) == ("Prus, Bolesław", "Lalka (powieść)")
        assert db.get_work(first.work_id).author == "Prus, Bolesław"
        assert len(db.conn.execute("SELECT id FROM works").fetchall()) == 1

    def test_a_field_that_is_not_given_is_left_alone_and_none_clears(self, db):
        edition = self.edition_of(db, "Prus", "Lalka")

        found = db.update_edition_metadata(edition.id, {"place": None})

        assert (found.place, found.year, found.title) == (None, 1880, "Lalka")

    def test_an_author_can_be_cleared_and_the_title_stays(self, db):
        edition = self.edition_of(db, "Prus", "Lalka")

        found = db.update_edition_metadata(edition.id, {"author": None})

        assert (found.author, found.title) == (None, "Lalka")

    def test_another_work_with_the_same_author_and_title_is_refused(self, db):
        self.edition_of(db, "Prus", "Lalka")
        other = self.edition_of(db, "Prus", "Faraon")

        with pytest.raises(WorkExists):
            db.update_edition_metadata(other.id, {"title": "LALKA", "place": "Lwów"})

        # nothing is changed, the place too
        found = db.get_edition_metadata(other.id)
        assert (found.title, found.place) == ("Faraon", "Kraków")

    def test_unknown_fields_are_refused(self, db):
        edition = self.edition_of(db, "Prus", "Lalka")

        with pytest.raises(ValueError):
            db.update_edition_metadata(edition.id, {"work_id": str(uuid4())})


def test_list_collection_pages_the_ids_in_order(db):
    ids = sorted(uuid4() for _ in range(5))
    for edition_id in ids:
        db.conn.execute("INSERT INTO collection (id) VALUES (%s)", (edition_id,))
    set_page(PaginatedPage[CollectionEdition])

    with set_params(Params(page=2, size=2)):
        page = db.list_collection()

    assert [item.id for item in page.items] == ids[2:4]
    assert (page.total, page.page, page.size) == (5, 2, 2)


class TestSearchCollection:
    @pytest.fixture
    def found(self, db):
        """Searches the collection of a few editions, returns the (author, title)s."""
        for author, title in [
            ("Bolesław Prus", "Lalka"),
            ("Bolesław Prus", "Faraon"),
            ("Henryk Sienkiewicz", "Potop"),
            ("Adam Mickiewicz", "Pan Tadeusz"),
            ("Adam Mickiewicz", "100% polski_tytuł"),
        ]:
            work = db.create_work(Work(author=author, title=title))
            edition = db.create_edition(Edition(work_id=work.id, year=1890))
            db.conn.execute("INSERT INTO collection (id) VALUES (%s)", (edition.id,))
        # in the collection but not stored, and a work without a title
        db.conn.execute("INSERT INTO collection (id) VALUES (%s)", (uuid4(),))
        no_title = db.create_edition(
            Edition(work_id=db.create_work(Work(author="Prusak")).id, year=1890)
        )
        db.conn.execute("INSERT INTO collection (id) VALUES (%s)", (no_title.id,))
        # stored, but not in the collection
        outside = db.create_work(Work(author="Prus Outside", title="Not listed"))
        db.create_edition(Edition(work_id=outside.id, year=1890))
        set_page(PaginatedPage[CollectionEdition])

        def search(author=None, title=None, page=1, size=10):
            with set_params(Params(page=page, size=size)):
                result = db.search_collection(author, title)
            return sorted((i.author, i.title) for i in result.items), result

        return search

    def test_by_author(self, found):
        authors, _ = found(author="Prus")

        assert authors == [
            ("Bolesław Prus", "Faraon"),
            ("Bolesław Prus", "Lalka"),
            ("Prusak", None),
        ]

    def test_by_title(self, found):
        titles, _ = found(title="tad")

        assert titles == [("Adam Mickiewicz", "Pan Tadeusz")]

    def test_with_both_both_must_match(self, found):
        assert found(author="prus", title="lalka")[0] == [("Bolesław Prus", "Lalka")]
        assert found(author="prus", title="potop")[0] == []

    def test_without_regard_to_case_and_around_the_value(self, found):
        assert found(author="  MICKIEWICZ ")[0] == [
            ("Adam Mickiewicz", "100% polski_tytuł"),
            ("Adam Mickiewicz", "Pan Tadeusz"),
        ]

    def test_percent_and_underscore_are_not_wildcards(self, found):
        assert found(title="%")[0] == [("Adam Mickiewicz", "100% polski_tytuł")]
        assert found(title="_")[0] == [("Adam Mickiewicz", "100% polski_tytuł")]
        assert found(title="l_lka")[0] == []
        assert found(title="a%a")[0] == []

    def test_a_backslash_is_an_ordinary_character(self, found):
        assert found(title="\\")[0] == []

    def test_only_the_collection_and_only_stored_editions(self, found):
        # "Prus Outside" is not in the collection, and the edition that is not
        # stored has no author: neither is found
        assert ("Prus Outside", "Not listed") not in found(author="Prus")[0]
        assert len(found(author="")[1].items) == 7 - 1  # all of the stored ones

    def test_is_paged_and_counts_all_the_matches(self, found):
        _, first = found(author="Prus", page=1, size=2)
        _, second = found(author="Prus", page=2, size=2)

        assert (first.total, first.pages, len(first.items)) == (3, 2, 2)
        assert len(second.items) == 1
        assert not {i.id for i in first.items} & {i.id for i in second.items}

    def test_nothing_found(self, found):
        items, result = found(author="Nobody")

        assert (items, result.total) == ([], 0)


def test_list_collection_has_the_author_and_title_of_the_work(db):
    work = db.create_work(Work(author="Kraszewski", title="Stara baśń"))
    stored = db.create_edition(Edition(work_id=work.id, year=1876))
    no_title = db.create_edition(
        Edition(work_id=db.create_work(Work(author="Prus")).id, year=1890)
    )
    not_stored = uuid4()
    for edition_id in (stored.id, no_title.id, not_stored):
        db.conn.execute("INSERT INTO collection (id) VALUES (%s)", (edition_id,))
    set_page(PaginatedPage[CollectionEdition])

    with set_params(Params(page=1, size=10)):
        found = {
            item.id: (item.author, item.title) for item in db.list_collection().items
        }

    assert found == {
        stored.id: ("Kraszewski", "Stara baśń"),
        no_title.id: ("Prus", None),
        not_stored: (None, None),
    }


def test_update_edition_sets_the_page_count_only(db, edition):
    updated = db.update_edition(edition.id, page_count=12)

    assert (updated.page_count, updated.fetched_completed) == (12, False)
    assert db.get_edition(edition.id) == updated


def test_update_edition_sets_fetched_completed_only(db, edition):
    db.update_edition(edition.id, page_count=12)

    updated = db.update_edition(edition.id, fetched_completed=True)

    assert (updated.fetched_completed, updated.page_count) == (True, 12)


def test_update_edition_can_clear_the_page_count(db, edition):
    db.update_edition(edition.id, page_count=12)

    assert db.update_edition(edition.id, page_count=None).page_count is None


def test_update_edition_of_an_unknown_edition_is_none(db):
    assert db.update_edition(uuid4(), page_count=3) is None


class TestSimilarMisspellings:
    def legal(self, db, edition, *spellings):
        """The corrected spellings are legal in the edition: the dictionary is a
        modern one and does not know "saméj"; only a correction to a correct (or a
        legal) word is suggested."""
        for spelling in spellings:
            db.add_legal_spelling(
                LegalSpelling(edition_id=edition.id, spelling=spelling)
            )

    def pages(self, db, edition, texts):
        for number, text in texts.items():
            db.create_page(
                Page(
                    edition_id=edition.id,
                    number=number,
                    raw_ocr_content=text,
                    xml_content_corrected=str(text_to_tei_xml(text)),
                )
            )

    def test_finds_the_same_mistake_in_every_page_of_the_edition(self, db, edition):
        self.pages(
            db,
            edition,
            {
                0: "Ala ma kota",
                1: "To samćj i jćj tu.",
                2: "Nic tu nie ma\nale ćjx jest.",
            },
        )
        self.legal(db, edition, "saméj", "jéj", "éjx")

        found = db.get_similar_misspellings(edition.id, "samćj", "saméj")

        assert [(m.page_number, [t.text for t in m.tokens]) for m in found] == [
            (1, ["samćj", "jćj"]),
            (2, ["ćjx"]),
        ]

    def test_each_token_comes_with_the_corrected_one(self, db, edition):
        self.pages(db, edition, {1: "To samćj i jćj, bardzićj tu."})
        self.legal(db, edition, "saméj", "jéj", "bardziéj")

        (match,) = db.get_similar_misspellings(edition.id, "jćj", "jéj")

        assert [(t.text, t.suggestion) for t in match.tokens] == [
            ("samćj", "saméj"),
            ("jćj", "jéj"),
            ("bardzićj", "bardziéj"),
        ]

    def test_each_token_comes_with_its_context(self, db, edition):
        self.pages(db, edition, {1: "Raz. To samćj, a nie."})

        (match,) = db.get_similar_misspellings(edition.id, "samćj", "saméj")

        assert [(t.before, t.after) for t in match.tokens] == [("To", ", a")]

    def test_the_offsets_are_those_of_the_text_of_the_page(self, db, edition):
        text = "To samćj i jćj tu."
        self.pages(db, edition, {1: text})
        self.legal(db, edition, "saméj", "jéj")

        (match,) = db.get_similar_misspellings(edition.id, "samćj", "saméj")

        assert [text[t.start : t.end] for t in match.tokens] == ["samćj", "jćj"]

    def test_a_suggestion_that_is_not_a_correct_word_is_doubtful(self, db, edition):
        self.pages(db, edition, {1: "To samćj i jćj tu."})

        # "saméj" and "jéj" are neither in the dictionary nor legal in the edition
        (match,) = db.get_similar_misspellings(edition.id, "samćj", "saméj")

        assert [(t.text, t.sure) for t in match.tokens] == [
            ("samćj", False),
            ("jćj", False),
        ]

    def test_only_the_suggestions_that_are_correct_are_sure(self, db, edition):
        self.pages(db, edition, {1: "To samćj i jćj tu."})
        self.legal(db, edition, "jéj")

        (match,) = db.get_similar_misspellings(edition.id, "samćj", "saméj")

        assert [(t.text, t.suggestion, t.sure) for t in match.tokens] == [
            ("samćj", "saméj", False),
            ("jćj", "jéj", True),
        ]

    def test_a_word_that_is_correct_is_never_the_start_of_a_suggestion(
        self, db, edition
    ):
        # "zasiedliśmy" is a word: even when "zasiedliémy" is legal in the edition,
        # it is not a misreading of anything
        self.pages(db, edition, {1: "Zasiedliśmy tu, a samśmy nie."})
        self.legal(db, edition, "zasiedliémy", "samémy")

        found = db.get_similar_misspellings(edition.id, "samśmy", "samémy")

        assert [t.text for m in found for t in m.tokens] == ["samśmy"]

    def test_a_word_in_a_quotation_in_another_language_is_left_alone(self, db, edition):
        xml = str(text_to_tei_xml("To samćj i jćj tu."))
        xml = xml.replace("samćj", '<quote xml:lang="la">samćj</quote>')
        db.create_page(
            Page(
                edition_id=edition.id,
                number=1,
                raw_ocr_content="x",
                xml_content_corrected=xml,
            )
        )

        (match,) = db.get_similar_misspellings(edition.id, "samćj", "saméj")

        assert [t.text for t in match.tokens] == ["jćj"]

    def test_pages_of_other_editions_are_not_looked_at(self, db, edition):
        work = db.create_work(Work(author="Other"))
        other = db.create_edition(Edition(work_id=work.id, year=1900))
        self.pages(db, other, {0: "samćj"})
        self.pages(db, edition, {0: "Ala ma kota"})

        assert db.get_similar_misspellings(edition.id, "samćj", "saméj") == []

    def test_an_edition_without_pages_has_no_matches(self, db, edition):
        assert db.get_similar_misspellings(edition.id, "samćj", "saméj") == []

    def test_a_page_without_corrected_content_is_left_out(self, db, edition):
        db.create_page(Page(edition_id=edition.id, number=0, raw_ocr_content="samćj"))

        assert db.get_similar_misspellings(edition.id, "samćj", "saméj") == []

    def test_the_pages_are_in_the_order_of_their_numbers(self, db, edition):
        self.pages(db, edition, {5: "jćj", 2: "jćj", 9: "jćj"})
        self.legal(db, edition, "jéj")

        found = db.get_similar_misspellings(edition.id, "jćj", "jéj")

        assert [m.page_number for m in found] == [2, 5, 9]


class TestSimilarPageNumbers:
    def pages(self, db, edition, texts):
        for number, text in texts.items():
            db.create_page(
                Page(
                    edition_id=edition.id,
                    number=number,
                    raw_ocr_content=text,
                    xml_content_corrected=str(text_to_tei_xml(text)),
                )
            )

    def test_finds_the_page_numbers_that_are_like_the_marked_one(self, db, edition):
        self.pages(
            db,
            edition,
            {
                0: "TYTUŁ",
                1: "1\n\nAla",
                2: "2\n\nAla",
                3: "- 3 -\n\nAla",
                4: "9\n\nAla",
            },
        )
        db.apply_correction(
            edition.id,
            1,
            lambda xml: mark_section(xml, 0, 1, "page_number", "1"),
            "",
            "",
            record=False,
        )

        found = db.get_similar_page_numbers(edition.id, 1)

        assert found.offset == 0
        assert [(c.page_number, c.text, c.fits_sequence) for c in found.candidates] == [
            (2, "2", True),
            (4, "9", False),
        ]

    def test_none_for_a_page_that_is_not_stored(self, db, edition):
        assert db.get_similar_page_numbers(edition.id, 5) is None

    def test_pages_of_other_editions_are_not_looked_at(self, db, edition):
        work = db.create_work(Work(author="Other"))
        other = db.create_edition(Edition(work_id=work.id, year=1900))
        self.pages(db, other, {1: "5\n\nAla"})
        self.pages(db, edition, {1: "1\n\nAla"})
        db.apply_correction(
            edition.id,
            1,
            lambda xml: mark_section(xml, 0, 1, "page_number", "1"),
            "",
            "",
            record=False,
        )

        assert db.get_similar_page_numbers(edition.id, 1).candidates == []


class TestApplyCorrections:
    def page(self, db, edition, text="Ala ma przeczy\ntać"):
        db.create_page(
            Page(
                edition_id=edition.id,
                number=1,
                raw_ocr_content=text,
                xml_content_corrected=str(text_to_tei_xml(text)),
            )
        )

    def corrections(self, db):
        return [
            (c["before"], c["after"])
            for c in db.conn.execute(
                "SELECT before, after FROM corrections ORDER BY id"
            ).fetchall()
        ]

    def test_saves_the_content_and_records_every_correction(self, db, edition):
        self.page(db, edition)

        page = db.apply_corrections(
            edition.id,
            1,
            lambda xml: xml.replace("przeczy", "przeczy-"),
            [("przeczy", "przeczy-"), ("a", "b")],
        )

        assert "przeczy-" in page.xml_content_corrected
        assert sorted(self.corrections(db)) == [("a", "b"), ("przeczy", "przeczy-")]

    def test_nothing_is_recorded_for_an_empty_list(self, db, edition):
        self.page(db, edition)

        db.apply_corrections(edition.id, 1, lambda xml: xml, [])

        assert self.corrections(db) == []

    def test_nothing_is_changed_if_the_replacement_fails(self, db, edition):
        self.page(db, edition)

        def fail(xml):
            raise ValueError("no")

        with pytest.raises(ValueError):
            db.apply_corrections(edition.id, 1, fail, [("a", "b")])

        assert self.corrections(db) == []

    def test_none_if_the_page_is_not_stored(self, db, edition):
        assert db.apply_corrections(edition.id, 9, lambda xml: xml, []) is None


class TestRubbishLines:
    def page(self, db, edition, number, text, mark=None):
        xml = str(text_to_tei_xml(text))
        if mark:
            xml = mark_section(xml, *mark, "header", text[mark[0] : mark[1]])
        db.create_page(
            Page(
                edition_id=edition.id,
                number=number,
                raw_ocr_content=text,
                xml_content_corrected=xml,
            )
        )

    def test_finds_the_rubbish_lines_of_the_edition_page_by_page(self, db, edition):
        self.page(
            db, edition, 1, "Ala ma kota\n\nI *>• ' -V. ^oSkv. ;-'. i M , . ypton"
        )
        self.page(db, edition, 2, "Ala ma kota")
        self.page(db, edition, 3, "Ala\n;;; ,,,\nkota", (4, 11))
        self.page(db, edition, 4, "***\n\n12")

        found = db.get_rubbish_lines(edition.id)

        assert [
            (p.page_number, [(x.text, x.sure) for x in p.lines]) for p in found
        ] == [
            (1, [("I *>• ' -V. ^oSkv. ;-'. i M , . ypton", True)]),
            (4, [("***", True)]),
        ]

    def test_none_for_an_edition_without_pages(self, db, edition):
        assert db.get_rubbish_lines(edition.id) == []


def add_user(db, username, password):
    db.conn.execute(
        "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
        (username, hash_password(password)),
    )


def test_check_user_with_the_right_password(db):
    add_user(db, "anna", "secret")

    assert db.check_user("anna", "secret") is True


def test_check_user_with_a_wrong_password_or_an_unknown_user(db):
    add_user(db, "anna", "secret")

    assert db.check_user("anna", "Secret") is False
    assert db.check_user("anna", "") is False
    assert db.check_user("Anna", "secret") is False  # the username is exact
    assert db.check_user("nobody", "secret") is False


def test_check_user_is_not_fooled_by_sql_in_the_username(db):
    add_user(db, "anna", "secret")

    assert db.check_user("anna' OR '1'='1", "secret") is False
    assert db.check_user("' OR 1=1 --", "x") is False


def test_users_have_a_username_and_a_password_hash_and_nothing_else(db):
    columns = db.conn.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_name = 'users' ORDER BY ordinal_position"
    ).fetchall()

    assert [c["column_name"] for c in columns] == ["username", "password_hash"]


def test_a_username_is_unique_and_neither_field_is_empty(db):
    add_user(db, "anna", "secret")

    with pytest.raises(psycopg.errors.UniqueViolation):
        add_user(db, "anna", "other")
    with pytest.raises(psycopg.errors.CheckViolation):
        add_user(db, "", "secret")
    with pytest.raises(psycopg.errors.CheckViolation):
        db.conn.execute("INSERT INTO users VALUES ('bob', '')")


class TestLoginThroughTheEndpoint:
    """POST /auth/login with the real database behind it."""

    @pytest.fixture
    def client(self, db, monkeypatch):
        from fastapi.testclient import TestClient

        import app.main as main

        monkeypatch.setattr(main, "db", db)
        add_user(db, "anna", "secret")
        return TestClient(main.app)

    def login(self, client, username, password):
        return client.post(
            "/auth/login", json={"username": username, "password": password}
        )

    def test_a_user_in_the_table_logs_in_and_is_let_in(self, client, db, edition):
        url = f"/editions/{edition.id}/metadata"
        assert client.get(url).status_code == 401

        assert self.login(client, "anna", "secret").json() == {"username": "anna"}

        assert client.get(url).status_code == 200

    def test_a_wrong_password_and_an_unknown_user_are_401(self, client):
        wrong = self.login(client, "anna", "nope")
        unknown = self.login(client, "bob", "secret")

        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json() == unknown.json()


def test_create_user_then_check_user(db):
    db.create_user("anna", "secret")

    assert db.check_user("anna", "secret") is True
    assert db.check_user("anna", "other") is False
    stored = db.conn.execute("SELECT password_hash FROM users").fetchone()
    assert stored["password_hash"].startswith("scrypt$")  # not the password


def test_create_user_refuses_a_username_that_is_taken(db):
    from app.persistence.db import UserExists

    db.create_user("anna", "secret")

    with pytest.raises(UserExists):
        db.create_user("anna", "other")

    assert db.check_user("anna", "secret") is True  # nothing was changed
    assert db.check_user("anna", "other") is False


class TestExternalEdition:
    def test_creates_and_gets_an_external_edition(self, db):
        external = ExternalEdition(
            isbn="978-83-7327-000-0", author="Bolesław Prus", title="Lalka", year=1890
        )
        assert db.create_external_edition(external) == external
        assert db.get_external_edition(external.id) == external

    def test_the_isbn_is_optional(self, db):
        external = db.create_external_edition(
            ExternalEdition(author="Adam Mickiewicz", title="Pan Tadeusz", year=1834)
        )
        assert db.get_external_edition(external.id).isbn is None

    def test_get_of_an_unknown_id_is_none(self, db):
        assert db.get_external_edition(uuid4()) is None

    def test_updates_only_the_given_fields(self, db):
        external = db.create_external_edition(
            ExternalEdition(isbn="1", author="A", title="T", year=1900)
        )
        updated = db.update_external_edition(external.id, title="U", year=1901)
        assert updated == external.model_copy(update={"title": "U", "year": 1901})
        assert db.get_external_edition(external.id) == updated

    def test_the_isbn_can_be_cleared(self, db):
        external = db.create_external_edition(
            ExternalEdition(isbn="1", author="A", title="T", year=1900)
        )
        assert db.update_external_edition(external.id, isbn=None).isbn is None

    def test_update_of_an_unknown_id_is_none(self, db):
        assert db.update_external_edition(uuid4(), year=1900) is None

    def test_update_needs_a_field(self, db):
        with pytest.raises(ValueError, match="at least one"):
            db.update_external_edition(uuid4())

    @pytest.mark.parametrize("field", ["author", "title", "year"])
    def test_a_required_field_cannot_be_cleared(self, db, field):
        with pytest.raises(ValueError, match="isbn"):
            db.update_external_edition(uuid4(), **{field: None})

    @pytest.mark.parametrize(
        "bad",
        [{"isbn": ""}, {"author": ""}, {"title": ""}, {"year": 1499}, {"year": 2031}],
    )
    def test_the_database_refuses_bad_values(self, db, bad):
        external = db.create_external_edition(
            ExternalEdition(author="A", title="T", year=1900)
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            db.update_external_edition(external.id, **bad)


class TestExternalEditionSpellings:
    @pytest.fixture
    def external(self, db):
        return db.create_external_edition(
            ExternalEdition(author="Bolesław Prus", title="Lalka", year=1890)
        )

    def rows(self, db):
        return db.conn.execute(
            "SELECT external_edition_id, spelling FROM external_edition_spelling"
            " ORDER BY spelling"
        ).fetchall()

    def test_adds_a_spelling(self, db, external):
        added = db.create_external_edition_spelling(
            ExternalEditionSpelling(external_edition_id=external.id, spelling="jey")
        )

        assert added is True
        assert self.rows(db) == [
            {"external_edition_id": external.id, "spelling": "jey"}
        ]

    def test_the_same_spelling_again_is_not_added_twice(self, db, external):
        spelling = ExternalEditionSpelling(
            external_edition_id=external.id, spelling="jey"
        )

        first = db.create_external_edition_spelling(spelling)
        second = db.create_external_edition_spelling(spelling)

        assert (first, second) == (True, False)
        assert len(self.rows(db)) == 1

    def test_case_makes_another_spelling(self, db, external):
        for text in ("jey", "Jey"):
            db.create_external_edition_spelling(
                ExternalEditionSpelling(external_edition_id=external.id, spelling=text)
            )

        assert {row["spelling"] for row in self.rows(db)} == {"Jey", "jey"}

    def test_the_same_spelling_can_be_in_many_external_editions(self, db, external):
        other = db.create_external_edition(
            ExternalEdition(author="Other", title="Book", year=1900)
        )

        added = [
            db.create_external_edition_spelling(
                ExternalEditionSpelling(external_edition_id=e.id, spelling="jey")
            )
            for e in (external, other)
        ]

        assert added == [True, True]

    def test_an_unknown_external_edition_is_refused(self, db):
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            db.create_external_edition_spelling(
                ExternalEditionSpelling(external_edition_id=uuid4(), spelling="jey")
            )

    def test_an_empty_spelling_is_refused_by_the_table(self, db, external):
        with pytest.raises(psycopg.errors.CheckViolation):
            db.conn.execute(
                "INSERT INTO external_edition_spelling (external_edition_id, spelling)"
                " VALUES (%s, '')",
                (external.id,),
            )

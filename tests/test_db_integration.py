"""CRUD tests against a real PostgreSQL.

Skipped unless INTEGRATION_DB=1 and the POSTGRES_* variables point to a
disposable database (tables are dropped and recreated).
"""

import os
from uuid import uuid4

import psycopg
import pytest

from app.db import Database, get_conninfo
from app.ontology import Correction, Edition, LegalSpelling, Page, Work
from tests.conftest import real_connect

pytestmark = pytest.mark.skipif(
    os.environ.get("INTEGRATION_DB") != "1", reason="set INTEGRATION_DB=1"
)


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr("app.db.psycopg.connect", real_connect)
    database = Database()
    database.conn.execute(
        "TRUNCATE collection, corrections, legal_spellings, pages, editions, works"
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


class TestApplyCorrection:
    @pytest.fixture
    def stored(self, db, edition):
        return db.create_page(
            Page(
                edition_id=edition.id,
                number=3,
                raw_content="raw",
                corrected_content="<TEI>old</TEI>",
                scan_uri="u",
            )
        )

    def corrections(self, db):
        return db.conn.execute(
            'SELECT page_id, "before", "after" FROM corrections ORDER BY id'
        ).fetchall()

    def test_saves_the_new_content_and_records_the_correction(self, db, stored):
        updated = db.apply_correction(
            stored.edition_id, 3, lambda xml: xml.replace("old", "new"), "old", "new"
        )

        assert updated.corrected_content == "<TEI>new</TEI>"
        assert db.get_page(stored.id) == updated
        assert self.corrections(db) == [
            {"page_id": stored.id, "before": "old", "after": "new"}
        ]

    def test_changes_nothing_else_of_the_page(self, db, stored):
        updated = db.apply_correction(stored.edition_id, 3, lambda xml: "x", "a", "b")

        assert updated == stored.model_copy(update={"corrected_content": "x"})

    def test_replace_gets_the_current_content(self, db, stored):
        seen = []

        def replace(xml):
            seen.append(xml)
            return xml + "!"

        db.apply_correction(stored.edition_id, 3, replace, "a", "b")
        db.apply_correction(stored.edition_id, 3, replace, "b", "c")

        assert seen == ["<TEI>old</TEI>", "<TEI>old</TEI>!"]
        assert db.get_page(stored.id).corrected_content == "<TEI>old</TEI>!!"
        assert len(self.corrections(db)) == 2

    def test_a_page_without_corrected_content_gets_an_empty_string(self, db, edition):
        page = db.create_page(Page(edition_id=edition.id, number=8, raw_content="raw"))
        seen = []

        db.apply_correction(
            edition.id, 8, lambda xml: seen.append(xml) or "new", "a", "b"
        )

        assert seen == [""]
        assert db.get_page(page.id).corrected_content == "new"

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
                raw_content="r",
                corrected_content="o",
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
                    "SELECT corrected_content FROM pages WHERE id = %s FOR UPDATE",
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
        other = db.upsert_edition(work, Edition(work_id=work.id, year=1900))

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

    def test_gets_the_spellings_of_an_edition(self, db, edition):
        work = Work(author="Other")
        other = db.upsert_edition(work, Edition(work_id=work.id, year=1900))
        for e, spelling in ((edition, "jey"), (edition, "tey"), (other, "raczey")):
            db.add_legal_spelling(LegalSpelling(edition_id=e.id, spelling=spelling))

        assert db.get_legal_spellings(edition.id) == {"jey", "tey"}
        assert db.get_legal_spellings(other.id) == {"raczey"}

    def test_an_edition_without_spellings_or_not_stored_has_none(self, db, edition):
        assert db.get_legal_spellings(edition.id) == set()
        assert db.get_legal_spellings(uuid4()) == set()

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
        return TestClient(main.app)

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
        other = db.upsert_edition(work, Edition(work_id=work.id, year=1900))

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
        import app.spelling as spelling

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

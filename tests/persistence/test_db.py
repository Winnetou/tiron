from unittest import mock
from uuid import uuid4

import pytest
from psycopg.conninfo import conninfo_to_dict

from app.persistence.db import Database, get_conninfo
from app.persistence.ontology import PageMatches, TokenMatch
from app.text_tools.tei_xml import mark_section, text_to_tei_xml

ENV = {
    "POSTGRES_DB": "tiron",
    "POSTGRES_USER": "user",
    "POSTGRES_PASSWORD": "secret",
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (*ENV, "POSTGRES_HOST", "POSTGRES_PORT"):
        monkeypatch.delenv(name, raising=False)


def test_conninfo_uses_env_and_defaults(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)

    assert conninfo_to_dict(get_conninfo()) == {
        "host": "localhost",
        "port": "5432",
        "dbname": "tiron",
        "user": "user",
        "password": "secret",
    }


def test_conninfo_host_and_port_override(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("POSTGRES_HOST", "db")
    monkeypatch.setenv("POSTGRES_PORT", "6543")

    info = conninfo_to_dict(get_conninfo())

    assert info["host"] == "db"
    assert info["port"] == "6543"


def test_conninfo_missing_required_var(monkeypatch):
    monkeypatch.setenv("POSTGRES_DB", "tiron")

    with pytest.raises(KeyError, match="POSTGRES_USER"):
        get_conninfo()


def test_database_connects_on_instantiation(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    connect = mock.Mock()
    monkeypatch.setattr("app.persistence.db.psycopg.connect", connect)

    db = Database()

    connect.assert_called_once()
    assert db.conn is connect.return_value


def test_database_creates_schema_on_instantiation(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    connect = mock.Mock()
    monkeypatch.setattr("app.persistence.db.psycopg.connect", connect)

    Database()

    sql = connect.return_value.execute.call_args.args[0]
    for table in (
        "works",
        "editions",
        "pages",
        "corrections",
        "collection",
        "legal_spellings",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql


@pytest.mark.parametrize(
    "changes", [{}, {"fetched_completed": True, "page_count": 3}], ids=["none", "both"]
)
def test_update_edition_needs_exactly_one_column(monkeypatch, changes):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr("app.persistence.db.psycopg.connect", mock.Mock())
    db = Database()

    with pytest.raises(ValueError, match="exactly one"):
        db.update_edition(uuid4(), **changes)


def test_get_similar_misspellings_looks_at_the_text_of_each_page(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    connect = mock.Mock()
    monkeypatch.setattr("app.persistence.db.psycopg.connect", connect)
    xml = lambda text: str(text_to_tei_xml(text))  # noqa: E731
    rows = [
        {"number": 0, "xml_content_corrected": xml("Ala ma kota")},
        {"number": 1, "xml_content_corrected": xml("To samćj i jćj tu.")},
        {"number": 2, "xml_content_corrected": None},
        {"number": 3, "xml_content_corrected": "<not tei/>"},
    ]

    def execute(query, params=()):
        result = mock.Mock()
        result.fetchall.return_value = [] if "legal_spellings" in query else rows
        return result

    connect.return_value.execute = mock.Mock(side_effect=execute)
    known = {"to", "i", "tu", "ala", "ma", "kota", "jéj", "saméj"}
    checker = mock.Mock(**{"lookup.side_effect": lambda word: word.lower() in known})
    monkeypatch.setattr("app.text_tools.spelling.get_checker", lambda: checker)
    edition_id = uuid4()

    found = Database().get_similar_misspellings(edition_id, "samćj", "saméj")

    assert found == [
        PageMatches(
            page_number=1,
            tokens=[
                TokenMatch(
                    text="samćj",
                    start=3,
                    end=8,
                    suggestion="saméj",
                    before="To",
                    after="i",
                ),
                TokenMatch(
                    text="jćj",
                    start=11,
                    end=14,
                    suggestion="jéj",
                    before="i",
                    after="tu",
                ),
            ],
        )
    ]
    assert connect.return_value.execute.call_args.args[1] == (edition_id,)


def test_get_similar_misspellings_refuses_the_same_spelling(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr("app.persistence.db.psycopg.connect", mock.Mock())

    with pytest.raises(ValueError, match="same"):
        Database().get_similar_misspellings(uuid4(), "kot", "kot")


class TestSimilarPageNumbers:
    def xml(self, text, mark=None):
        xml = str(text_to_tei_xml(text))
        if mark:
            xml = mark_section(xml, *mark, "page_number", text[mark[0] : mark[1]])
        return xml

    def database(self, monkeypatch, rows):
        for name, value in ENV.items():
            monkeypatch.setenv(name, value)
        connect = mock.Mock()
        monkeypatch.setattr("app.persistence.db.psycopg.connect", connect)
        connect.return_value.execute.return_value.fetchall.return_value = [
            {"number": number, "xml_content_corrected": xml} for number, xml in rows
        ]
        return Database()

    def test_the_pages_with_a_number_like_the_marked_one(self, monkeypatch):
        db = self.database(
            monkeypatch,
            [
                (3, self.xml("TYTUŁ\n\nAla")),
                (4, self.xml("1\n\nAla ma kota", (0, 1))),
                (5, self.xml("2\n\nA kot ma Ale")),
                (6, self.xml("3\n\nI tak dalej")),
                (7, self.xml("12 Rozdział\n\nTreść")),
                (8, self.xml("7\n\nNumer, który nie pasuje")),
                (9, None),
                (10, "<not tei/>"),
            ],
        )

        found = db.get_similar_page_numbers(uuid4(), 4)

        assert (found.where, found.offset) == ("first", 3)
        assert (found.example.page_number, found.example.text) == (4, "1")
        assert [(c.page_number, c.text, c.fits_sequence) for c in found.candidates] == [
            (5, "2", True),
            (6, "3", True),
            (8, "7", False),
        ]

    def test_a_candidate_has_its_offsets_and_the_line_next_to_it(self, monkeypatch):
        db = self.database(
            monkeypatch,
            [(1, self.xml("1\n\nAla", (0, 1))), (2, self.xml("2\n\nAla ma kota"))],
        )

        (candidate,) = db.get_similar_page_numbers(uuid4(), 1).candidates

        assert (candidate.start, candidate.end) == (0, 1)
        assert candidate.context == "Ala ma kota"
        assert candidate.number == 2

    def test_a_page_with_a_page_number_marked_already_is_left_out(self, monkeypatch):
        db = self.database(
            monkeypatch,
            [
                (1, self.xml("1\n\nAla", (0, 1))),
                (2, self.xml("2\n\nAla", (0, 1))),
                (3, self.xml("3\n\nAla")),
            ],
        )

        found = db.get_similar_page_numbers(uuid4(), 1)

        assert [c.page_number for c in found.candidates] == [3]

    def test_none_for_a_page_that_is_not_stored(self, monkeypatch):
        db = self.database(monkeypatch, [(1, self.xml("1\n\nAla", (0, 1)))])

        assert db.get_similar_page_numbers(uuid4(), 9) is None

    def test_a_page_without_a_marked_page_number_is_refused(self, monkeypatch):
        db = self.database(monkeypatch, [(1, self.xml("1\n\nAla"))])

        with pytest.raises(ValueError, match="No page number is marked"):
            db.get_similar_page_numbers(uuid4(), 1)

    def test_a_marked_number_that_is_not_alone_is_refused(self, monkeypatch):
        db = self.database(monkeypatch, [(1, self.xml("84 KATEDRA\n\nAla", (0, 2)))])

        with pytest.raises(ValueError, match="alone"):
            db.get_similar_page_numbers(uuid4(), 1)

    def test_a_page_without_xml_is_refused(self, monkeypatch):
        db = self.database(monkeypatch, [(1, None)])

        with pytest.raises(ValueError, match="No page number is marked"):
            db.get_similar_page_numbers(uuid4(), 1)


class TestMissingHyphens:
    def database(self, monkeypatch, row, legal=()):
        for name, value in ENV.items():
            monkeypatch.setenv(name, value)
        connect = mock.Mock()
        monkeypatch.setattr("app.persistence.db.psycopg.connect", connect)

        def execute(query, params=()):
            result = mock.Mock()
            result.fetchone.return_value = row
            result.fetchall.return_value = [{"spelling": s} for s in legal]
            return result

        connect.return_value.execute = execute
        return Database()

    @pytest.fixture(autouse=True)
    def dictionary(self, monkeypatch):
        from app.text_tools import spelling

        known = {"przeczytać", "ala", "ma"}
        checker = mock.Mock(**{"lookup.side_effect": lambda w: w.lower() in known})
        monkeypatch.setattr(spelling, "get_checker", lambda: checker)

    def xml(self, text):
        return str(text_to_tei_xml(text))

    def test_the_line_ends_of_the_page(self, monkeypatch):
        db = self.database(
            monkeypatch, {"xml_content_corrected": self.xml("Ala ma przeczy\ntać")}
        )

        found = db.get_missing_hyphens(uuid4(), 4)

        assert [(m.text, m.suggestion, m.next_word) for m in found] == [
            ("przeczy", "przeczy-", "tać")
        ]

    def test_the_legal_spellings_of_the_edition_count(self, monkeypatch):
        db = self.database(
            monkeypatch,
            {"xml_content_corrected": self.xml("Ala ma przeczy\ntać")},
            legal={"przeczy", "tać"},
        )

        # both parts are legal on their own, so it is not sure that it is split
        (match,) = db.get_missing_hyphens(uuid4(), 4)

        assert match.ambiguous is True

    def test_none_if_the_page_is_not_stored(self, monkeypatch):
        db = self.database(monkeypatch, None)

        assert db.get_missing_hyphens(uuid4(), 4) is None

    def test_a_page_without_xml_has_none(self, monkeypatch):
        db = self.database(monkeypatch, {"xml_content_corrected": None})

        assert db.get_missing_hyphens(uuid4(), 4) == []

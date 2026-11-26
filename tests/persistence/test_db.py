from unittest import mock
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from app.persistence.db import Database, ReconnectingConnection, get_conninfo
from app.persistence.ontology import PageMatches, TokenMatch
from app.text_tools.tei_xml import mark_section, text_to_tei_xml

ENV = {
    "POSTGRES_DB": "tiron",
    "POSTGRES_USER": "user",
    "POSTGRES_PASSWORD": "secret",
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        *ENV,
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_SSLMODE",
        "POSTGRES_SSLROOTCERT",
    ):
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
        "connect_timeout": "10",
        "keepalives": "1",
        "keepalives_idle": "30",
        "keepalives_interval": "10",
        "keepalives_count": "3",
    }


def test_conninfo_host_and_port_override(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("POSTGRES_HOST", "db")
    monkeypatch.setenv("POSTGRES_PORT", "6543")

    info = conninfo_to_dict(get_conninfo())

    assert info["host"] == "db"
    assert info["port"] == "6543"


def test_conninfo_has_no_tls_settings_unless_they_are_set(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)

    info = conninfo_to_dict(get_conninfo())

    assert "sslmode" not in info
    assert "sslrootcert" not in info


def test_conninfo_tls_settings(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("POSTGRES_SSLMODE", "verify-ca")
    monkeypatch.setenv("POSTGRES_SSLROOTCERT", "/certs/server-ca.pem")

    info = conninfo_to_dict(get_conninfo())

    assert info["sslmode"] == "verify-ca"
    assert info["sslrootcert"] == "/certs/server-ca.pem"


def test_conninfo_empty_tls_settings_are_left_out(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("POSTGRES_SSLMODE", "")

    assert "sslmode" not in conninfo_to_dict(get_conninfo())


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
    assert db.conn._conn is connect.return_value


def test_database_does_not_create_the_schema_on_instantiation(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    connect = mock.Mock()
    monkeypatch.setattr("app.persistence.db.psycopg.connect", connect)

    Database()

    connect.return_value.execute.assert_not_called()  # that is SchemaCreator's


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
    # the last query is the one for the legal spellings (of the edition and its time)
    assert connect.return_value.execute.call_args.args[1] == {
        "edition_id": edition_id,
        "years": 10,
    }


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


# --- the connection that connects again, and the SELECT 1 check ---


def _connections(monkeypatch, *connections):
    """psycopg.connect answers with these connections, one after the other."""
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    connect = mock.Mock(side_effect=list(connections))
    monkeypatch.setattr("app.persistence.db.psycopg.connect", connect)
    return connect


def _connection(**attributes):
    conn = mock.MagicMock(closed=False, broken=False)
    for name, value in attributes.items():
        setattr(conn, name, value)
    return conn


def test_a_lost_connection_is_opened_again_before_a_statement(monkeypatch):
    first, second = _connection(), _connection()
    connect = _connections(monkeypatch, first, second)
    conn = ReconnectingConnection()
    first.broken = True

    conn.execute("SELECT 1")

    assert connect.call_count == 2
    first.close.assert_called_once()
    first.execute.assert_not_called()
    second.execute.assert_called_once_with("SELECT 1", None)


def test_a_closed_connection_is_opened_again_before_a_statement(monkeypatch):
    first, second = _connection(), _connection()
    connect = _connections(monkeypatch, first, second)
    conn = ReconnectingConnection()
    first.closed = True

    conn.execute("SELECT 1", (1,))

    assert connect.call_count == 2
    second.execute.assert_called_once_with("SELECT 1", (1,))


def test_a_live_connection_is_kept(monkeypatch):
    first = _connection()
    connect = _connections(monkeypatch, first)
    conn = ReconnectingConnection()

    conn.execute("SELECT 1")
    conn.execute("SELECT 2")

    connect.assert_called_once()
    assert first.execute.call_count == 2


def test_a_statement_that_finds_the_connection_lost_is_sent_again(monkeypatch):
    first, second = _connection(), _connection()
    connect = _connections(monkeypatch, first, second)
    conn = ReconnectingConnection()

    def lose(*args):
        first.broken = True
        raise psycopg.OperationalError("server closed the connection")

    first.execute.side_effect = lose

    result = conn.execute("SELECT 1", (1,))

    assert connect.call_count == 2
    assert result is second.execute.return_value
    second.execute.assert_called_once_with("SELECT 1", (1,))


def test_an_error_of_a_live_connection_is_raised_and_not_retried(monkeypatch):
    first = _connection()
    connect = _connections(monkeypatch, first)
    conn = ReconnectingConnection()
    first.execute.side_effect = psycopg.errors.QueryCanceled("canceled")

    with pytest.raises(psycopg.errors.QueryCanceled):
        conn.execute("SELECT pg_sleep(100)")

    connect.assert_called_once()
    first.execute.assert_called_once()


def test_a_statement_in_a_transaction_is_not_sent_again(monkeypatch):
    first, second = _connection(), _connection()
    connect = _connections(monkeypatch, first, second)
    conn = ReconnectingConnection()

    def lose(*args):
        first.broken = True
        raise psycopg.OperationalError("server closed the connection")

    first.execute.side_effect = lose

    with pytest.raises(psycopg.OperationalError), conn.transaction():
        conn.execute("UPDATE works SET title = 'x'")

    connect.assert_called_once()  # no second connection, no second try
    # but the next statement, outside the transaction, finds a new connection
    conn.execute("SELECT 1")
    assert connect.call_count == 2
    second.execute.assert_called_once_with("SELECT 1", None)


def test_the_transaction_is_that_of_the_connection(monkeypatch):
    first = _connection()
    _connections(monkeypatch, first)
    conn = ReconnectingConnection()

    with conn.transaction() as transaction:
        pass

    first.transaction.assert_called_once_with()
    assert transaction is first.transaction.return_value.__enter__.return_value


def test_a_failed_connect_is_raised(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        "app.persistence.db.psycopg.connect",
        mock.Mock(side_effect=psycopg.OperationalError("connection refused")),
    )

    with pytest.raises(psycopg.OperationalError, match="refused"):
        ReconnectingConnection()


def test_close_closes_the_connection(monkeypatch):
    first = _connection()
    _connections(monkeypatch, first)

    ReconnectingConnection().close()

    first.close.assert_called_once()


def test_is_reachable_asks_select_1(monkeypatch):
    first = _connection()
    _connections(monkeypatch, first)
    db = Database()
    first.execute.return_value.fetchone.return_value = {"ok": 1}

    assert db.is_reachable() is True
    assert first.execute.call_args.args[0] == "SELECT 1 AS ok"


def test_is_reachable_is_false_when_the_database_does_not_answer(monkeypatch, caplog):
    first = _connection()
    _connections(monkeypatch, first)
    db = Database()
    first.execute.side_effect = psycopg.OperationalError("connection refused")

    assert db.is_reachable() is False
    assert "not reachable" in caplog.text
    assert "connection refused" in caplog.text


def test_is_reachable_is_false_for_an_unexpected_answer(monkeypatch):
    first = _connection()
    _connections(monkeypatch, first)
    db = Database()
    first.execute.return_value.fetchone.return_value = None

    assert db.is_reachable() is False

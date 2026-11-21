from unittest import mock
from uuid import uuid4

import pytest
from psycopg.conninfo import conninfo_to_dict

from app.persistence.db import Database, get_conninfo
from app.persistence.ontology import PageMatches, TokenMatch
from app.text_tools.tei_xml import text_to_tei_xml

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
    connect.return_value.execute.return_value.fetchall.return_value = [
        {"number": 0, "xml_content_corrected": xml("Ala ma kota")},
        {"number": 1, "xml_content_corrected": xml("To samćj i jćj tu.")},
        {"number": 2, "xml_content_corrected": None},
        {"number": 3, "xml_content_corrected": "<not tei/>"},
    ]
    edition_id = uuid4()

    found = Database().get_similar_misspellings(edition_id, "samćj", "saméj")

    assert found == [
        PageMatches(
            page_number=1,
            tokens=[
                TokenMatch(text="samćj", start=3, end=8, suggestion="saméj"),
                TokenMatch(text="jćj", start=11, end=14, suggestion="jéj"),
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

from unittest import mock
from uuid import uuid4

import pytest
from psycopg.conninfo import conninfo_to_dict

from app.db import Database, get_conninfo
from app.ontology import Page

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
    monkeypatch.setattr("app.db.psycopg.connect", connect)

    db = Database()

    connect.assert_called_once()
    assert db.conn is connect.return_value


def test_database_creates_schema_on_instantiation(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    connect = mock.Mock()
    monkeypatch.setattr("app.db.psycopg.connect", connect)

    Database()

    sql = connect.return_value.execute.call_args.args[0]
    for table in ("works", "editions", "pages", "corrections", "collection"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql


@pytest.mark.parametrize("present", [True, False])
def test_check_edition_in_collection_returns_bool(monkeypatch, present):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    connect = mock.Mock()
    connect.return_value.execute.return_value.fetchone.return_value = {
        "present": present
    }
    monkeypatch.setattr("app.db.psycopg.connect", connect)
    edition_id = uuid4()

    assert Database().check_edition_in_collection(edition_id) is present
    args = connect.return_value.execute.call_args.args
    assert "FROM collection" in args[0]
    assert args[1] == (edition_id,)


def test_update_page_is_a_stub(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr("app.db.psycopg.connect", mock.Mock())

    with pytest.raises(NotImplementedError):
        Database().update_page(Page(edition_id=uuid4(), number=0, raw_content="x"))

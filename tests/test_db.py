from unittest import mock

import pytest
from psycopg.conninfo import conninfo_to_dict

from app.db import Database, get_conninfo

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
    for table in ("works", "editions", "pages", "corrections"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql

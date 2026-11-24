import re
from unittest import mock

import pytest

from app.persistence.schema import SCHEMA, TABLES
from app.persistence.schema_creator import (
    SchemaCreationForbidden,
    SchemaCreator,
    SchemaMissing,
    ensure_schema,
)


def database(*, tables=TABLES):
    """A database whose connection has these tables."""
    db = mock.Mock()

    def execute(query, params=()):
        result = mock.Mock()
        if "to_regclass" in query:
            result.fetchone.return_value = {"found": params[0] in db.tables}
        return result

    db.tables = set(tables)
    db.conn.execute = mock.Mock(side_effect=execute)
    return db


def created(db):
    return [c for c in db.conn.execute.call_args_list if c.args[0] == SCHEMA]


@pytest.fixture
def prod(monkeypatch):
    monkeypatch.setenv("PROD", "true")


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setenv("PROD", "false")


def test_the_tables_are_all_the_tables_of_the_schema():
    assert set(TABLES) == set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SCHEMA))


def test_schema_exists_when_all_the_tables_are_there():
    assert SchemaCreator(database()).schema_exists() is True


@pytest.mark.parametrize("missing", TABLES)
def test_schema_does_not_exist_when_a_table_is_missing(missing):
    db = database(tables=set(TABLES) - {missing})

    assert SchemaCreator(db).schema_exists() is False


def test_schema_does_not_exist_in_an_empty_database():
    assert SchemaCreator(database(tables=())).schema_exists() is False


def test_create_schema_runs_the_schema_when_prod_is_false(local):
    db = database(tables=())

    SchemaCreator(db).create_schema()

    assert len(created(db)) == 1


def test_create_schema_is_forbidden_when_prod_is_true(prod):
    db = database(tables=())

    with pytest.raises(SchemaCreationForbidden, match="PROD"):
        SchemaCreator(db).create_schema()

    assert created(db) == []


def test_create_schema_is_forbidden_when_prod_is_not_set(monkeypatch):
    monkeypatch.delenv("PROD", raising=False)
    db = database(tables=())

    with pytest.raises(SchemaCreationForbidden):
        SchemaCreator(db).create_schema()

    assert created(db) == []


def test_ensure_schema_leaves_an_existing_schema_alone(local):
    db = database()

    ensure_schema(db)

    assert created(db) == []


def test_ensure_schema_creates_a_missing_schema_when_prod_is_false(local):
    db = database(tables=())

    def execute(query, params=()):
        result = mock.Mock()
        if query == SCHEMA:
            db.tables = set(TABLES)
        else:
            result.fetchone.return_value = {"found": params[0] in db.tables}
        return result

    db.conn.execute = mock.Mock(side_effect=execute)

    ensure_schema(db)

    assert len(created(db)) == 1


def test_ensure_schema_creates_what_is_missing_of_a_partial_schema(local):
    db = database(tables={"works"})

    def execute(query, params=()):
        result = mock.Mock()
        if query == SCHEMA:
            db.tables = set(TABLES)
        else:
            result.fetchone.return_value = {"found": params[0] in db.tables}
        return result

    db.conn.execute = mock.Mock(side_effect=execute)

    ensure_schema(db)

    assert len(created(db)) == 1


def test_ensure_schema_does_not_create_it_when_prod_is_true(prod):
    db = database(tables=())

    with pytest.raises(SchemaMissing, match="PROD is true"):
        ensure_schema(db)

    assert created(db) == []


def test_ensure_schema_is_fine_with_an_existing_schema_when_prod_is_true(prod):
    db = database()

    ensure_schema(db)

    assert created(db) == []


def test_ensure_schema_fails_if_the_tables_are_still_missing_after_creating(local):
    db = database(tables=())  # the creation does nothing here

    with pytest.raises(SchemaMissing, match="not there"):
        ensure_schema(db)

import pytest
from _pytest.outcomes import Failed

from tests.conftest import refuse_remote_database


@pytest.fixture(autouse=True)
def test_database(monkeypatch):
    monkeypatch.setenv("POSTGRES_DB", "test")


@pytest.mark.parametrize(
    "host", [None, "localhost", "127.0.0.1", "::1", "/var/run/postgresql"]
)
def test_a_local_database_is_allowed(monkeypatch, host):
    if host is None:
        monkeypatch.delenv("POSTGRES_HOST", raising=False)  # the default: localhost
    else:
        monkeypatch.setenv("POSTGRES_HOST", host)

    refuse_remote_database()


@pytest.mark.parametrize(
    "host",
    [
        "10.1.2.3",
        "db",
        "host.docker.internal",
        "tiron.abc123.europe-west1.sql.goog",
        "localhost.example.com",
        "",
    ],
)
def test_a_database_that_is_not_local_is_refused(monkeypatch, host):
    monkeypatch.setenv("POSTGRES_HOST", host)

    with pytest.raises(Failed, match="Refusing to run the integration tests"):
        refuse_remote_database()


@pytest.mark.parametrize("name", ["test", "tiron_test", "Test_db", "integration-tests"])
def test_a_database_with_test_in_its_name_is_allowed(monkeypatch, name):
    monkeypatch.setenv("POSTGRES_DB", name)

    refuse_remote_database()


@pytest.mark.parametrize("name", ["tiron", "prod", "u", ""])
def test_a_database_without_test_in_its_name_is_refused(monkeypatch, name):
    monkeypatch.setenv("POSTGRES_DB", name)

    with pytest.raises(Failed, match=r"POSTGRES_DB=.* \"test\" in its name"):
        refuse_remote_database()


def test_a_missing_database_name_is_refused(monkeypatch):
    monkeypatch.delenv("POSTGRES_DB", raising=False)

    with pytest.raises(Failed, match="POSTGRES_DB"):
        refuse_remote_database()


def test_the_host_is_checked_too_when_the_name_is_fine(monkeypatch):
    monkeypatch.setenv("POSTGRES_DB", "test")
    monkeypatch.setenv("POSTGRES_HOST", "10.1.2.3")

    with pytest.raises(Failed, match="POSTGRES_HOST"):
        refuse_remote_database()

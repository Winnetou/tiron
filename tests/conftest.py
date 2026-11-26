"""Importing app.main instantiates Database, which connects on creation.
Replace the real connection with a mock so tests need no PostgreSQL."""

import os
from unittest import mock

import psycopg
import pytest

# Kept for integration tests that need a real connection.
real_connect = psycopg.connect

os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
# production (the default) does not start without it
os.environ.setdefault("SESSION_SECRET", "a-secret-for-the-tests")
# The unit tests of the spell check load the pure Python dictionary (or fakes), whatever
# is installed; the tests of the native one switch it on (see test_spelling.py)
os.environ["NATIVE_HUNSPELL"] = "0"

mock.patch("psycopg.connect").start()


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def refuse_remote_database() -> None:
    """Fail unless the database is local and disposable: POSTGRES_HOST is this
    machine (or a Unix socket directory) and POSTGRES_DB has "test" in its name.

    The integration tests empty the tables of the database they run against, so a
    wrong POSTGRES_HOST (production, in a shell that still has its settings) or the
    development database (it is local too, but called "tiron") must stop them before
    they touch it. It fails: it does not skip, so that nobody takes the tests for
    passed.
    """
    host = os.environ.get("POSTGRES_HOST", "localhost").strip()
    if host not in LOCAL_HOSTS and not host.startswith("/"):
        pytest.fail(
            f"Refusing to run the integration tests against POSTGRES_HOST={host!r}: "
            "they empty the tables, and are only run against a local, disposable "
            "database (localhost, 127.0.0.1 or ::1).",
            pytrace=False,
        )
    name = os.environ.get("POSTGRES_DB", "")
    if "test" not in name.lower():
        pytest.fail(
            f"Refusing to run the integration tests against POSTGRES_DB={name!r}: "
            "they empty the tables, and are only run against a database that has "
            '"test" in its name.',
            pytrace=False,
        )


@pytest.fixture(autouse=True)
def no_broker(monkeypatch):
    """Tests have no RabbitMQ: sending the task is recorded, not done."""
    from app.celery_app import handle_new_edition_created

    delay = mock.Mock()
    monkeypatch.setattr(handle_new_edition_created, "delay", delay)
    return delay

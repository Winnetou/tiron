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

mock.patch("psycopg.connect").start()


@pytest.fixture(autouse=True)
def no_broker(monkeypatch):
    """Tests have no RabbitMQ: sending the task is recorded, not done."""
    from app.celery_app import handle_new_edition_created

    delay = mock.Mock()
    monkeypatch.setattr(handle_new_edition_created, "delay", delay)
    return delay

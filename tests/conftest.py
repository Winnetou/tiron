"""Importing app.main instantiates Database, which connects on creation.
Replace the real connection with a mock so tests need no PostgreSQL."""

import os
from unittest import mock

import psycopg

# Kept for integration tests that need a real connection.
real_connect = psycopg.connect

os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")

mock.patch("psycopg.connect").start()

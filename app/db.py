"""Persistence layer: PostgreSQL access via psycopg.

Connection details are read from environment variables:

    POSTGRES_DB        (required)
    POSTGRES_USER      (required)
    POSTGRES_PASSWORD  (required)
    POSTGRES_HOST      (default: localhost)
    POSTGRES_PORT      (default: 5432)
"""

import os
from uuid import UUID

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from app.ontology import Correction, Edition, Page, Work


def get_conninfo() -> str:
    """Build a libpq connection string from the environment.

    Raises KeyError naming the variable if a required one is missing.
    """
    return make_conninfo(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


SCHEMA = """
CREATE TABLE IF NOT EXISTS works (
    id uuid PRIMARY KEY,
    author text,
    title text
);

CREATE TABLE IF NOT EXISTS editions (
    id uuid PRIMARY KEY,
    work_id uuid NOT NULL REFERENCES works (id),
    place text,
    year integer CHECK (year BETWEEN 1500 AND 2030)
);

CREATE TABLE IF NOT EXISTS pages (
    id uuid PRIMARY KEY,
    edition_id uuid NOT NULL REFERENCES editions (id),
    number integer NOT NULL CHECK (number >= 0),
    raw_content text NOT NULL,
    corrected_content text,
    ready boolean NOT NULL DEFAULT false,
    UNIQUE (edition_id, number)
);

CREATE TABLE IF NOT EXISTS corrections (
    id uuid PRIMARY KEY,
    page_id uuid NOT NULL REFERENCES pages (id),
    "before" text NOT NULL CHECK ("before" <> ''),
    "after" text NOT NULL CHECK ("after" <> '')
);

CREATE TABLE IF NOT EXISTS collection (
    id uuid PRIMARY KEY
);

-- migrations for databases created before these changes
ALTER TABLE pages ADD COLUMN IF NOT EXISTS ready boolean NOT NULL DEFAULT false;
ALTER TABLE works ADD COLUMN IF NOT EXISTS title text;
ALTER TABLE works ALTER COLUMN author DROP NOT NULL;
ALTER TABLE editions ALTER COLUMN place DROP NOT NULL;
ALTER TABLE editions ALTER COLUMN year DROP NOT NULL;

-- one work per (author, title), case-insensitive; unknown values never match
CREATE UNIQUE INDEX IF NOT EXISTS works_author_title_key
    ON works (lower(author), lower(title))
    WHERE author IS NOT NULL AND title IS NOT NULL;
"""


class Database:
    """Persistence layer. Connects to PostgreSQL when instantiated."""

    def __init__(self) -> None:
        # autocommit: the connection is long-lived, so avoid idle open transactions
        self.conn: psycopg.Connection = psycopg.connect(
            get_conninfo(), row_factory=dict_row, autocommit=True
        )
        self.conn.execute(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # Works

    def create_work(self, work: Work) -> Work:
        row = self.conn.execute(
            "INSERT INTO works (id, author, title) VALUES (%s, %s, %s) RETURNING *",
            (work.id, work.author, work.title),
        ).fetchone()
        return Work.model_validate(row)

    def get_work(self, work_id: UUID) -> Work | None:
        row = self.conn.execute(
            "SELECT * FROM works WHERE id = %s", (work_id,)
        ).fetchone()
        return Work.model_validate(row) if row else None

    # Editions

    def create_edition(self, edition: Edition) -> Edition:
        row = self.conn.execute(
            "INSERT INTO editions (id, work_id, place, year)"
            " VALUES (%s, %s, %s, %s) RETURNING *",
            (edition.id, edition.work_id, edition.place, edition.year),
        ).fetchone()
        return Edition.model_validate(row)

    def _get_or_create_work(self, work: Work) -> Work:
        """Return the stored work with the same (author, title), ignoring case,
        or store the given one. Works with an unknown author or title never match."""
        row = self.conn.execute(
            "INSERT INTO works (id, author, title) VALUES (%s, %s, %s)"
            " ON CONFLICT (lower(author), lower(title))"
            " WHERE author IS NOT NULL AND title IS NOT NULL"
            " DO NOTHING RETURNING *",
            (work.id, work.author, work.title),
        ).fetchone()
        if row is None:
            row = self.conn.execute(
                "SELECT * FROM works WHERE lower(author) = lower(%s)"
                " AND lower(title) = lower(%s)",
                (work.author, work.title),
            ).fetchone()
        return Work.model_validate(row)

    def upsert_edition(self, work: Work, edition: Edition) -> Edition:
        """Store the edition unless it already exists, in which case the stored
        edition is returned and nothing is written.

        The edition is attached to the stored work with the same (author, title)
        if there is one, so many editions share a work; `work` is stored only
        when there is no such work. Atomic: a work is never stored without its
        edition. The returned edition carries the work_id actually used."""
        row = None
        with self.conn.transaction():
            if self.get_edition(edition.id) is not None:
                raise psycopg.Rollback
            stored_work = self._get_or_create_work(work)
            row = self.conn.execute(
                "INSERT INTO editions (id, work_id, place, year)"
                " VALUES (%s, %s, %s, %s)"
                " ON CONFLICT (id) DO NOTHING RETURNING *",
                (edition.id, stored_work.id, edition.place, edition.year),
            ).fetchone()
            if row is None:
                raise psycopg.Rollback  # created concurrently: discard our work
        if row is None:
            return self.get_edition(edition.id)
        return Edition.model_validate(row)

    def get_edition(self, edition_id: UUID) -> Edition | None:
        row = self.conn.execute(
            "SELECT * FROM editions WHERE id = %s", (edition_id,)
        ).fetchone()
        return Edition.model_validate(row) if row else None

    # Pages

    def create_page(self, page: Page) -> Page:
        row = self.conn.execute(
            "INSERT INTO pages"
            " (id, edition_id, number, raw_content, corrected_content, ready)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
            (
                page.id,
                page.edition_id,
                page.number,
                page.raw_content,
                page.corrected_content,
                page.ready,
            ),
        ).fetchone()
        return Page.model_validate(row)

    def get_page(self, page_id: UUID) -> Page | None:
        row = self.conn.execute(
            "SELECT * FROM pages WHERE id = %s", (page_id,)
        ).fetchone()
        return Page.model_validate(row) if row else None

    def upsert_page(self, page: Page) -> Page:
        """Insert the page, or update the content of the existing page with
        the same (edition_id, number). The stored page keeps its original id."""
        row = self.conn.execute(
            "INSERT INTO pages"
            " (id, edition_id, number, raw_content, corrected_content, ready)"
            " VALUES (%s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (edition_id, number) DO UPDATE SET"
            " raw_content = EXCLUDED.raw_content,"
            " corrected_content = EXCLUDED.corrected_content,"
            " ready = EXCLUDED.ready"
            " RETURNING *",
            (
                page.id,
                page.edition_id,
                page.number,
                page.raw_content,
                page.corrected_content,
                page.ready,
            ),
        ).fetchone()
        return Page.model_validate(row)

    # Corrections

    def create_correction(self, correction: Correction) -> Correction:
        row = self.conn.execute(
            'INSERT INTO corrections (id, page_id, "before", "after")'
            " VALUES (%s, %s, %s, %s) RETURNING *",
            (correction.id, correction.page_id, correction.before, correction.after),
        ).fetchone()
        return Correction.model_validate(row)

    def get_correction(self, correction_id: UUID) -> Correction | None:
        row = self.conn.execute(
            "SELECT * FROM corrections WHERE id = %s", (correction_id,)
        ).fetchone()
        return Correction.model_validate(row) if row else None

    # Collection

    def check_edition_in_collection(self, edition_id: UUID) -> bool:
        row = self.conn.execute(
            "SELECT EXISTS (SELECT 1 FROM collection WHERE id = %s) AS present",
            (edition_id,),
        ).fetchone()
        return row["present"]

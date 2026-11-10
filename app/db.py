"""Persistence layer: PostgreSQL access via psycopg.

Connection details are read from environment variables:

    POSTGRES_DB        (required)
    POSTGRES_USER      (required)
    POSTGRES_PASSWORD  (required)
    POSTGRES_HOST      (default: localhost)
    POSTGRES_PORT      (default: 5432)
"""

import os
from collections.abc import Callable
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from app.ontology import Correction, Edition, LegalSpelling, Page, Work


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
    scan_uri text,
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

-- spellings that are legal in an edition; each edition has each spelling once
CREATE TABLE IF NOT EXISTS legal_spellings (
    edition_id uuid NOT NULL REFERENCES editions (id),
    spelling text NOT NULL CHECK (spelling <> ''),
    PRIMARY KEY (edition_id, spelling)
);

-- one work per (author, title), case-insensitive; unknown values never match
CREATE UNIQUE INDEX IF NOT EXISTS works_author_title_key
    ON works (lower(author), lower(title))
    WHERE author IS NOT NULL AND title IS NOT NULL;
"""


UNSET: Any = object()  # "leave this column as it is" (None is a real value)


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
            " (id, edition_id, number, raw_content, corrected_content,"
            " scan_uri, ready)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (
                page.id,
                page.edition_id,
                page.number,
                page.raw_content,
                page.corrected_content,
                page.scan_uri,
                page.ready,
            ),
        ).fetchone()
        return Page.model_validate(row)

    def get_page(self, page_id: UUID) -> Page | None:
        row = self.conn.execute(
            "SELECT * FROM pages WHERE id = %s", (page_id,)
        ).fetchone()
        return Page.model_validate(row) if row else None

    def apply_correction(
        self,
        edition_id: UUID,
        number: int,
        replace: Callable[[str], str],
        before: str,
        after: str,
    ) -> Page | None:
        """Correct the corrected content of a page and record the Correction.

        `replace` is given the page's current corrected content (an empty string
        if it has none) and returns the new one, which is saved; the Correction
        (`before` -> `after`) is stored for the page. All of it happens in one
        transaction with the page's row locked, so the content `replace` sees is
        the one that is replaced, another edit cannot get in between, and the
        page is never changed without its Correction being recorded, or the
        other way round. If `replace` raises, nothing is changed.

        Returns the updated page, or None if there is no such page.
        """
        with self.conn.transaction():
            row = self.conn.execute(
                "SELECT * FROM pages WHERE edition_id = %s AND number = %s FOR UPDATE",
                (edition_id, number),
            ).fetchone()
            if row is None:
                return None
            page = Page.model_validate(row)
            corrected_content = replace(page.corrected_content or "")
            updated = self.conn.execute(
                "UPDATE pages SET corrected_content = %s WHERE id = %s RETURNING *",
                (corrected_content, page.id),
            ).fetchone()
            self.create_correction(
                Correction(page_id=page.id, before=before, after=after)
            )
        return Page.model_validate(updated)

    def update_page(
        self,
        edition_id: UUID,
        number: int,
        *,
        corrected_content: str | None = UNSET,
        ready: bool = UNSET,
    ) -> Page | None:
        """Change the corrected content and/or the ready flag of a page, and
        return the updated page (None if there is no such page).

        These are the only two columns that can be changed: the signature has
        no way to reach the others. An argument left out is not touched;
        corrected_content=None clears the correction.
        """
        changes = {
            column: value
            for column, value in (
                ("corrected_content", corrected_content),
                ("ready", ready),
            )
            if value is not UNSET
        }
        if not changes:
            raise ValueError("Nothing to update")
        assignments = sql.SQL(", ").join(
            sql.SQL("{} = %s").format(sql.Identifier(column)) for column in changes
        )
        query = sql.SQL(
            "UPDATE pages SET {} WHERE edition_id = %s AND number = %s RETURNING *"
        ).format(assignments)
        row = self.conn.execute(
            query, (*changes.values(), edition_id, number)
        ).fetchone()
        return Page.model_validate(row) if row else None

    def get_page_by_number(self, edition_id: UUID, number: int) -> Page | None:
        row = self.conn.execute(
            "SELECT * FROM pages WHERE edition_id = %s AND number = %s",
            (edition_id, number),
        ).fetchone()
        return Page.model_validate(row) if row else None

    def save_fetched_page(self, page: Page) -> Page:
        """Store a page freshly fetched from Polona and return the stored page.

        If the page (same edition and number) is already stored, only the
        fetched data (raw_content, scan_uri) is refreshed: its id, corrected
        content and ready flag are kept, so re-fetching never loses work."""
        row = self.conn.execute(
            "INSERT INTO pages"
            " (id, edition_id, number, raw_content, corrected_content,"
            " scan_uri, ready)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (edition_id, number) DO UPDATE SET"
            " raw_content = EXCLUDED.raw_content,"
            " scan_uri = EXCLUDED.scan_uri"
            " RETURNING *",
            (
                page.id,
                page.edition_id,
                page.number,
                page.raw_content,
                page.corrected_content,
                page.scan_uri,
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

    # Legal spellings

    def add_legal_spelling(self, legal: LegalSpelling) -> bool:
        """Store a spelling as legal in an edition.

        Returns True if it was added, and False if the edition already had that
        spelling (the pair is unique), in which case nothing is changed. The
        edition must be stored: otherwise psycopg raises ForeignKeyViolation.
        """
        row = self.conn.execute(
            "INSERT INTO legal_spellings (edition_id, spelling) VALUES (%s, %s)"
            " ON CONFLICT DO NOTHING RETURNING edition_id",
            (legal.edition_id, legal.spelling),
        ).fetchone()
        return row is not None

    def get_legal_spellings(self, edition_id: UUID) -> set[str]:
        """The spellings that are legal in an edition (none if it has none, or is
        not stored)."""
        rows = self.conn.execute(
            "SELECT spelling FROM legal_spellings WHERE edition_id = %s",
            (edition_id,),
        ).fetchall()
        return {row["spelling"] for row in rows}

    # Collection

    def check_edition_in_collection(self, edition_id: UUID) -> bool:
        row = self.conn.execute(
            "SELECT EXISTS (SELECT 1 FROM collection WHERE id = %s) AS present",
            (edition_id,),
        ).fetchone()
        return row["present"]

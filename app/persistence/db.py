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
from fastapi_pagination import create_page
from fastapi_pagination.api import resolve_params
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from app.persistence.ontology import (
    Correction,
    Edition,
    LegalSpelling,
    Page,
    PageMatches,
    PageNumberMatch,
    SimilarPageNumbers,
    TokenMatch,
    Work,
)
from app.text_tools.misspellings import cluster_fix, tokens_with_cluster
from app.text_tools.page_numbers import (
    PAGE_NUMBER_OPEN,
    find_page_number,
    marked_page_number,
    pattern_of,
)
from app.text_tools.tei_xml import InvalidTei, tei_xml_sections, tei_xml_to_text


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
    year integer CHECK (year BETWEEN 1500 AND 2030),
    fetched_completed boolean NOT NULL DEFAULT false,
    page_count integer CHECK (page_count >= 1)
);


CREATE TABLE IF NOT EXISTS pages (
    id uuid PRIMARY KEY,
    edition_id uuid NOT NULL REFERENCES editions (id),
    number integer NOT NULL CHECK (number >= 0),
    raw_ocr_content text NOT NULL,
    xml_content_corrected text,
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
            "INSERT INTO editions"
            " (id, work_id, place, year, fetched_completed, page_count)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
            (
                edition.id,
                edition.work_id,
                edition.place,
                edition.year,
                edition.fetched_completed,
                edition.page_count,
            ),
        ).fetchone()
        return Edition.model_validate(row)

    def get_edition(self, edition_id: UUID) -> Edition | None:
        row = self.conn.execute(
            "SELECT * FROM editions WHERE id = %s", (edition_id,)
        ).fetchone()
        return Edition.model_validate(row) if row else None

    def update_edition(
        self,
        edition_id: UUID,
        *,
        fetched_completed: bool = UNSET,
        page_count: int | None = UNSET,
    ) -> Edition | None:
        """Change one of the fetch columns of an edition, `fetched_completed` or
        `page_count`, and return the updated edition (None if there is no such
        edition).

        Exactly one of them is given, and the other is not touched; these are the
        only two columns that can be changed. page_count=None clears the count.
        Raises ValueError if none or both are given.
        """
        changes = {
            column: value
            for column, value in (
                ("fetched_completed", fetched_completed),
                ("page_count", page_count),
            )
            if value is not UNSET
        }
        if len(changes) != 1:
            raise ValueError(
                "Give exactly one of fetched_completed and page_count to update"
            )
        (column, value), *_ = changes.items()
        query = sql.SQL("UPDATE editions SET {} = %s WHERE id = %s RETURNING *").format(
            sql.Identifier(column)
        )
        row = self.conn.execute(query, (value, edition_id)).fetchone()
        return Edition.model_validate(row) if row else None

    # Pages

    def create_page(self, page: Page) -> Page:
        row = self.conn.execute(
            "INSERT INTO pages"
            " (id, edition_id, number, raw_ocr_content, xml_content_corrected,"
            " scan_uri, ready)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (
                page.id,
                page.edition_id,
                page.number,
                page.raw_ocr_content,
                page.xml_content_corrected,
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
        record: bool = True,
    ) -> Page | None:
        """Correct the corrected content of a page and record the Correction
        (unless `record` is False, for a change that is not a correction: a
        removed token).

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
            xml_content_corrected = replace(page.xml_content_corrected or "")
            updated = self.conn.execute(
                "UPDATE pages SET xml_content_corrected = %s WHERE id = %s RETURNING *",
                (xml_content_corrected, page.id),
            ).fetchone()
            if record:
                self.create_correction(
                    Correction(page_id=page.id, before=before, after=after)
                )
        return Page.model_validate(updated)

    def update_page(
        self,
        edition_id: UUID,
        number: int,
        *,
        xml_content_corrected: str | None = UNSET,
        ready: bool = UNSET,
    ) -> Page | None:
        """Change the corrected content and/or the ready flag of a page, and
        return the updated page (None if there is no such page).

        These are the only two columns that can be changed: the signature has
        no way to reach the others. An argument left out is not touched;
        xml_content_corrected=None clears the correction.
        """
        changes = {
            column: value
            for column, value in (
                ("xml_content_corrected", xml_content_corrected),
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

    def get_similar_misspellings(
        self, edition_id: UUID, incorrect_spelling: str, corrected_spelling: str
    ) -> list[PageMatches]:
        """The tokens in the pages of an edition that have the same mistake as
        `incorrect_spelling`, which is corrected to `corrected_spelling`: the
        same OCR mistake in other words, so that the correction can be offered
        for the whole edition. Each token comes with a `suggestion`: itself with
        the mistake corrected ("jćj" and "samćj" become "jéj" and "saméj").

        The mistake is the cluster of letters from the first one that differs to
        the end of the word (see `wrong_cluster`): "samćj" corrected to "saméj"
        finds "samćj" and also "jćj" and every other token with "ćj" in it, case
        does not matter. Every page of the edition is looked at, in the text of
        its corrected content (what the user sees). A page that has matches comes
        back with its number and the tokens, with their offsets in that text; a
        page without matches is left out, and so is one whose XML cannot be read.
        The pages are in the order of their numbers.

        Raises ValueError if the two spellings are the same, or the incorrect one
        is empty.
        """
        fix = cluster_fix(incorrect_spelling, corrected_spelling)
        rows = self.conn.execute(
            "SELECT number, xml_content_corrected FROM pages"
            " WHERE edition_id = %s ORDER BY number",
            (edition_id,),
        ).fetchall()
        found = []
        for row in rows:
            if not row["xml_content_corrected"]:
                continue
            try:
                text = tei_xml_to_text(row["xml_content_corrected"])
            except InvalidTei:
                continue
            tokens = [
                TokenMatch(text=token, start=start, end=end, suggestion=suggestion)
                for token, start, end, suggestion in tokens_with_cluster(text, fix)
            ]
            if tokens:
                found.append(PageMatches(page_number=row["number"], tokens=tokens))
        return found

    def get_similar_page_numbers(
        self, edition_id: UUID, number: int
    ) -> SimilarPageNumbers | None:
        """(Only page numbers for now; headers and footers can be done the same way
        later, see `app/text_tools/page_numbers.py`.)

        The page numbers in the pages of an edition that are like the one that
        is marked (<fw type="pageNum">) in page `number`: the pattern is that
        page number's line, which must be the first or the last of its page and
        have nothing but the number and the dashes around it. A page is a
        candidate if the same line of it is a number like that, with dashes around
        it or not as in the example, and no page number is marked in it yet. A
        candidate `fits_sequence` if its page less its number is the same as in
        the example. They are in the order of the pages, and the text is that of
        the corrected content, as the user sees it; a page without it, or with XML
        that cannot be read, is left out.

        Returns None if there is no such page. Raises ValueError, with the reason,
        if no page number is marked in it, or it is not one that can be looked
        for (see `pattern_of`).
        """
        rows = self.conn.execute(
            "SELECT number, xml_content_corrected FROM pages"
            " WHERE edition_id = %s ORDER BY number",
            (edition_id,),
        ).fetchall()
        example_row = next((row for row in rows if row["number"] == number), None)
        if example_row is None:
            return None
        if not example_row["xml_content_corrected"]:
            raise ValueError("No page number is marked on this page")
        try:
            marked = marked_page_number(example_row["xml_content_corrected"])
        except InvalidTei as exc:
            raise ValueError(f"The XML of the page cannot be read: {exc}") from exc
        if marked is None:
            raise ValueError("No page number is marked on this page")
        text, start, end = marked
        pattern = pattern_of(text, start, end)
        found = find_page_number(text, pattern)
        if found is None:  # (the marked part is the number, so this is not so)
            raise ValueError("The page number is not like its own pattern")
        offset = number - found.number

        candidates = []
        for row in rows:
            xml = row["xml_content_corrected"]
            if row["number"] == number or not xml:
                continue
            try:
                page_text = tei_xml_to_text(xml)
                has_one = any(
                    section["open"] == PAGE_NUMBER_OPEN
                    for section in tei_xml_sections(xml)
                )
            except InvalidTei:
                continue
            match = None if has_one else find_page_number(page_text, pattern)
            if match is not None:
                candidates.append(
                    PageNumberMatch(
                        page_number=row["number"],
                        start=match.start,
                        end=match.end,
                        text=match.text,
                        number=match.number,
                        context=match.context,
                        fits_sequence=row["number"] - match.number == offset,
                    )
                )
        return SimilarPageNumbers(
            example=PageNumberMatch(
                page_number=number,
                start=found.start,
                end=found.end,
                text=found.text,
                number=found.number,
                context=found.context,
                fits_sequence=True,
            ),
            where=pattern.where,
            offset=offset,
            candidates=candidates,
        )

    def get_page_by_number(self, edition_id: UUID, number: int) -> Page | None:
        row = self.conn.execute(
            "SELECT * FROM pages WHERE edition_id = %s AND number = %s",
            (edition_id, number),
        ).fetchone()
        return Page.model_validate(row) if row else None

    def save_fetched_page(self, page: Page) -> Page:
        """Store a page freshly fetched from Polona and return the stored page.

        If the page (same edition and number) is already stored, only scan_uri
        is refreshed. raw_ocr_content is written once, when the page is first
        fetched, and never changes after that; the id, xml_content_corrected
        and ready flag are kept too, so re-fetching never loses work."""
        row = self.conn.execute(
            "INSERT INTO pages"
            " (id, edition_id, number, raw_ocr_content, xml_content_corrected,"
            " scan_uri, ready)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (edition_id, number) DO UPDATE SET"
            " scan_uri = EXCLUDED.scan_uri"
            " RETURNING *",
            (
                page.id,
                page.edition_id,
                page.number,
                page.raw_ocr_content,
                page.xml_content_corrected,
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
        """Store a spelling as legal in an edition ("legal", not "correct": what
        was correct in the 19th century often is not anymore, and we have no
        better word for it).

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

    def list_collection(self) -> Any:
        """A page of the ids of the editions in the collection, in the order of
        the ids (so the pages do not change between requests). Which page, and
        how long, is up to the request that is being served: fastapi-pagination
        reads it from there.

        The two queries are run here, not by fastapi-pagination's psycopg
        helper: that one switches the row factory of the connection while it
        counts, and the connection is shared by the requests that run at the
        same time, so two of them could leave it switched."""
        params = resolve_params()
        raw = params.to_raw_params().as_limit_offset()
        rows = self.conn.execute(
            "SELECT id FROM collection ORDER BY id LIMIT %s OFFSET %s",
            (raw.limit, raw.offset),
        ).fetchall()
        total = self.conn.execute("SELECT count(*) AS n FROM collection").fetchone()
        return create_page([row["id"] for row in rows], total=total["n"], params=params)

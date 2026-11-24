"""Persistence layer: PostgreSQL access via psycopg.

Connection details are read from environment variables:

    POSTGRES_DB        (required)
    POSTGRES_USER      (required)
    POSTGRES_PASSWORD  (required)
    POSTGRES_HOST      (default: localhost)
    POSTGRES_PORT      (default: 5432)
    POSTGRES_SSLMODE   (optional: `require`, `verify-ca` or `verify-full` for TLS;
                        unset, libpq's default `prefer` is used)
    POSTGRES_SSLROOTCERT (optional: the CA certificate that the server is checked
                        against, for `verify-ca` and `verify-full`)
"""

import logging
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
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
    EditionMetadata,
    EntityForm,
    EntityMatch,
    LegalSpelling,
    MissingHyphen,
    Page,
    PageEntityMatches,
    PageMatches,
    PageNumberMatch,
    PageRubbish,
    PageTokenOccurrences,
    RubbishLine,
    SimilarEntities,
    SimilarPageNumbers,
    SpellingGroup,
    SpellingOccurrence,
    TokenMatch,
    TokenOccurrence,
    Work,
)
from app.persistence.passwords import DUMMY_HASH, verify_password
from app.text_tools.hyphens import find_missing_hyphens
from app.text_tools.inflection import find_forms, forms_of_phrase
from app.text_tools.misspellings import cluster_fix, tokens_with_cluster
from app.text_tools.ner import find_entities
from app.text_tools.page_numbers import (
    PAGE_NUMBER_OPEN,
    find_page_number,
    marked_page_number,
    pattern_of,
)
from app.text_tools.rubbish import find_rubbish_lines
from app.text_tools.spelling import check_words, tokenize, word_verdict
from app.text_tools.spelling_chunks import find_chunk_matches
from app.text_tools.tei_xml import (
    InvalidTei,
    foreign_language_ranges,
    tei_xml_entities,
    tei_xml_sections,
    tei_xml_to_text,
)
from app.text_tools.token_occurrences import (
    check_token,
    context_after,
    context_before,
    find_token,
)

logger = logging.getLogger(__name__)

# How long a connection attempt may take (seconds), so that an unreachable database
# is an error and not a request that waits for ever.
CONNECT_TIMEOUT = 10

# TCP keepalives, so that a connection that a firewall or a NAT has dropped silently
# (they do that to idle connections) is noticed, and not waited on.
_KEEPALIVES = {
    "keepalives": "1",
    "keepalives_idle": "30",
    "keepalives_interval": "10",
    "keepalives_count": "3",
}


def get_conninfo() -> str:
    """Build a libpq connection string from the environment.

    Raises KeyError naming the variable if a required one is missing. The TLS
    settings (`POSTGRES_SSLMODE`, `POSTGRES_SSLROOTCERT`) are only in it if they
    are set.
    """
    tls = {
        option: os.environ[name]
        for option, name in (
            ("sslmode", "POSTGRES_SSLMODE"),
            ("sslrootcert", "POSTGRES_SSLROOTCERT"),
        )
        if os.environ.get(name)
    }
    return make_conninfo(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        connect_timeout=CONNECT_TIMEOUT,
        **_KEEPALIVES,
        **tls,
    )


def _lost(conn: psycopg.Connection) -> bool:
    """Whether the connection is closed or broken (the server went away)."""
    return conn.closed is True or conn.broken is True


class ReconnectingConnection:
    """The connection of `Database`, which opens itself again when it is lost.

    A database in the cloud drops connections now and then: it is restarted for
    maintenance, fails over, or the network between loses an idle one. A single
    long-lived connection would then fail every request until the backend is
    restarted. This one is used like a psycopg connection (`execute`,
    `transaction`, `close`), and:

    - if it is found lost before a statement, it is opened again first;
    - if it is lost while a statement is sent, it is opened again and the
      statement is sent once more, but only outside a transaction. The rest of a
      transaction went with the connection, so there the error is raised (the
      next statement opens a new connection).

    A statement that is sent again may have been done already, if the connection
    went down after the database did it and before the answer came back: that is
    rare, and the statements here are single, so it is accepted.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._transactions = 0
        self._conn = self._connect()

    @staticmethod
    def _connect() -> psycopg.Connection:
        # autocommit: the connection is long-lived, so avoid idle open transactions
        return psycopg.connect(get_conninfo(), row_factory=dict_row, autocommit=True)

    def _reconnect(self, lost: psycopg.Connection) -> None:
        """Open a new connection in place of `lost`, unless another thread did."""
        with self._lock:
            if self._conn is not lost:
                return
            logger.warning("The database connection was lost: connecting again")
            with suppress(psycopg.Error):
                lost.close()
            self._conn = self._connect()

    def _live(self) -> psycopg.Connection:
        conn = self._conn
        if _lost(conn):
            self._reconnect(conn)
            conn = self._conn
        return conn

    def execute(self, query: Any, params: Any = None) -> psycopg.Cursor:
        conn = self._live()
        try:
            return conn.execute(query, params)
        except psycopg.OperationalError:
            if self._transactions or not _lost(conn):
                raise
            self._reconnect(conn)
            return self._conn.execute(query, params)

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        conn = self._live()
        self._transactions += 1
        try:
            with conn.transaction() as transaction:
                yield transaction
        finally:
            self._transactions -= 1

    def close(self) -> None:
        self._conn.close()


class WorkExists(ValueError):
    """Another work has the author and title that were asked for."""


UNSET: Any = object()  # "leave this column as it is" (None is a real value)


# The most places that are given for a spelling (it is counted all the same).
_OCCURRENCES_SHOWN = 5
# The most words of a named entity that its forms are looked for.
_MOST_WORDS = 5


class Database:
    """Persistence layer. Connects to PostgreSQL when instantiated, and connects
    again if the connection is lost (see `ReconnectingConnection`)."""

    def __init__(self) -> None:
        self.conn = ReconnectingConnection()

    def close(self) -> None:
        self.conn.close()

    # Users

    def check_user(self, username: str, password: str) -> bool:
        """Whether there is a user with this username and this password.

        An unknown username and a wrong password are the same False, and take the
        same time (the password is checked against a dummy hash when there is no
        such user), so the answer does not say which of the two it was.
        """
        row = self.conn.execute(
            "SELECT password_hash FROM users WHERE username = %s", (username,)
        ).fetchone()
        stored = row["password_hash"] if row else DUMMY_HASH
        return verify_password(password, stored) and row is not None

    def is_reachable(self) -> bool:
        """Whether the database answers `SELECT 1`. Never raises: a database that
        cannot be reached (or does not answer) is False, and the reason is logged."""
        try:
            row = self.conn.execute("SELECT 1 AS ok").fetchone()
        except psycopg.Error as exc:
            logger.warning("The database is not reachable: %s", exc)
            return False
        return row is not None and row["ok"] == 1

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

    def get_edition_metadata(self, edition_id: UUID) -> EditionMetadata | None:
        """What is known of an edition: the `author` and `title` of its work (and
        the work's id, and how many editions the work has), and the `place` and `year`
        of the edition itself. None if there is no such edition."""
        row = self.conn.execute(
            "SELECT e.id, e.work_id, w.author, w.title, e.place, e.year,"
            " (SELECT count(*) FROM editions o WHERE o.work_id = e.work_id)"
            " AS work_editions"
            " FROM editions e JOIN works w ON w.id = e.work_id WHERE e.id = %s",
            (edition_id,),
        ).fetchone()
        return EditionMetadata.model_validate(row) if row else None

    def update_edition_metadata(
        self, edition_id: UUID, changes: dict[str, str | int | None]
    ) -> EditionMetadata | None:
        """Change what is known of an edition: `changes` has some of `author`,
        `title`, `place` and `year` with their new values (None clears one); the
        others are left as they are. Returns the metadata as it is now, or None if
        there is no such edition.

        `place` and `year` are changed in the edition. `author` and `title` are
        changed in its work, in place: the other editions of the work have the
        change too. A work is one per author and title (case does not matter), so
        if another work has the new author and title already nothing is changed
        and WorkExists is raised.
        """
        unknown = set(changes) - {"author", "title", "place", "year"}
        if unknown:
            raise ValueError(f"Cannot change {', '.join(sorted(unknown))}")
        try:
            with self.conn.transaction():
                row = self.conn.execute(
                    "SELECT e.work_id FROM editions e WHERE e.id = %s FOR UPDATE",
                    (edition_id,),
                ).fetchone()
                if row is None:
                    return None
                for column in ("author", "title"):
                    if column in changes:
                        self.conn.execute(
                            sql.SQL("UPDATE works SET {} = %s WHERE id = %s").format(
                                sql.Identifier(column)
                            ),
                            (changes[column], row["work_id"]),
                        )
                for column in ("place", "year"):
                    if column in changes:
                        self.conn.execute(
                            sql.SQL("UPDATE editions SET {} = %s WHERE id = %s").format(
                                sql.Identifier(column)
                            ),
                            (changes[column], edition_id),
                        )
        except psycopg.errors.UniqueViolation as exc:
            raise WorkExists("Another work has this author and title already") from exc
        return self.get_edition_metadata(edition_id)

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
        return self.apply_corrections(
            edition_id, number, replace, [(before, after)] if record else []
        )

    def apply_corrections(
        self,
        edition_id: UUID,
        number: int,
        replace: Callable[[str], str],
        corrections: list[tuple[str, str]],
    ) -> Page | None:
        """Like `apply_correction`, for a change that is several corrections at
        once: each (`before`, `after`) of `corrections` is recorded for the page,
        in the same transaction as the new corrected content (none is recorded
        if the list is empty).

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
            for before, after in corrections:
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

        A correction is only suggested for a word that the spell check flags: a token
        that is spelled correctly (or is a legal spelling of the edition) is left
        alone, however much it looks like the mistake. If the suggestion is a correct
        word (or a legal spelling) the match is `sure`; if not, it is kept but is
        not `sure` (doubtful). "Flagged" is as `POST /spellcheck` says it, in the text
        of the page (a word split over two lines is judged as one word).

        Raises ValueError if the two spellings are the same, or the incorrect one
        is empty, and SpellcheckUnavailable if the dictionary cannot be loaded.
        """
        fix = cluster_fix(incorrect_spelling, corrected_spelling)
        rows = self.conn.execute(
            "SELECT number, xml_content_corrected FROM pages"
            " WHERE edition_id = %s ORDER BY number",
            (edition_id,),
        ).fetchall()
        legal = self.get_legal_spellings(edition_id)
        correct_suggestion = word_verdict(legal)
        found = []
        for row in rows:
            if not row["xml_content_corrected"]:
                continue
            try:
                text = tei_xml_to_text(row["xml_content_corrected"])
                foreign = foreign_language_ranges(row["xml_content_corrected"])
            except InvalidTei:
                continue
            candidates = tokens_with_cluster(text, fix)
            if not candidates:
                continue
            correct_here = {
                w.start: w.correct for w in check_words(text, legal, foreign)
            }
            all_tokens = tokenize(text)
            index_at = {match.start(): i for i, match in enumerate(all_tokens)}
            tokens = [
                TokenMatch(
                    text=token,
                    start=start,
                    end=end,
                    suggestion=suggestion,
                    sure=correct_suggestion(suggestion),
                    before=context_before(text, all_tokens, index_at[start]),
                    after=context_after(text, all_tokens, index_at[start]),
                )
                for token, start, end, suggestion in candidates
                # only a wrong word is ever corrected, never a correct one
                if not correct_here.get(start, True)
            ]
            if tokens:
                found.append(PageMatches(page_number=row["number"], tokens=tokens))
        return found

    def get_missing_hyphens(
        self, edition_id: UUID, number: int
    ) -> list[MissingHyphen] | None:
        """The line ends of a page where the OCR dropped a hyphen (see
        `app/text_tools/hyphens.py`), in the text of the corrected content, as
        the user sees it, with the spellings that are legal in the edition taken
        as correct. A page without XML has none.

        Returns None if there is no such page. Raises InvalidTei if its XML cannot
        be read, and SpellcheckUnavailable if the dictionary cannot be loaded.
        """
        row = self.conn.execute(
            "SELECT xml_content_corrected FROM pages"
            " WHERE edition_id = %s AND number = %s",
            (edition_id, number),
        ).fetchone()
        if row is None:
            return None
        if not row["xml_content_corrected"]:
            return []
        text = tei_xml_to_text(row["xml_content_corrected"])
        foreign = foreign_language_ranges(row["xml_content_corrected"])
        legal = self.get_legal_spellings(edition_id)
        return [
            MissingHyphen.model_validate(vars(found))
            for found in find_missing_hyphens(text, legal)
            # a word in a quotation in another language is not Polish
            if not any(a <= found.start and found.end <= b for a, b in foreign)
        ]

    def get_token_occurrences(
        self, edition_id: UUID, token: str
    ) -> list[PageTokenOccurrences]:
        """Every place of `token` in the pages of an edition, exactly (see
        `app/text_tools/token_occurrences.py`), page by page in the order of the
        pages, in the text of the corrected content as the user sees it, each with
        the tokens around it. A page without a place, without XML, or with XML that
        cannot be read is left out.

        Raises InvalidToken if `token` is not a single token.
        """
        check_token(token)
        rows = self.conn.execute(
            "SELECT number, xml_content_corrected FROM pages"
            " WHERE edition_id = %s ORDER BY number",
            (edition_id,),
        ).fetchall()
        found = []
        for row in rows:
            xml = row["xml_content_corrected"]
            if not xml:
                continue
            try:
                text = tei_xml_to_text(xml)
            except InvalidTei:
                continue
            places = [
                TokenOccurrence.model_validate(vars(place))
                for place in find_token(text, token)
            ]
            if places:
                found.append(
                    PageTokenOccurrences(page_number=row["number"], occurrences=places)
                )
        return found

    def get_chunk_matches(
        self, edition_id: UUID, substrings: list[str]
    ) -> list[SpellingGroup]:
        """The words of the pages of an edition that have one of `substrings` in
        them (see `app/text_tools/spelling_chunks.py`) and that the spell check
        flags, so that they can be accepted as legal spellings of the edition. A
        word is listed once, with how many times it is in the edition and the first
        places (`_OCCURRENCES_SHOWN`) in the order of the pages. The spellings that
        are legal in the edition count as correct, so a word that is accepted
        already is not listed. The text is that of the corrected content, as the
        user sees it; a page without XML, or with XML that cannot be read, is left
        out. The words that are in the edition most often come first, then the
        others in the order of their first place.

        Raises SpellcheckUnavailable if the dictionary cannot be loaded.
        """
        rows = self.conn.execute(
            "SELECT number, xml_content_corrected FROM pages"
            " WHERE edition_id = %s ORDER BY number",
            (edition_id,),
        ).fetchall()
        legal = self.get_legal_spellings(edition_id)
        places: dict[str, list[SpellingOccurrence]] = {}
        counts: dict[str, int] = {}
        for row in rows:
            xml = row["xml_content_corrected"]
            if not xml:
                continue
            try:
                text = tei_xml_to_text(xml)
                foreign = foreign_language_ranges(xml)
            except InvalidTei:
                continue
            for match in find_chunk_matches(text, substrings, legal, foreign):
                counts[match.text] = counts.get(match.text, 0) + 1
                shown = places.setdefault(match.text, [])
                if len(shown) < _OCCURRENCES_SHOWN:
                    shown.append(
                        SpellingOccurrence(
                            page_number=row["number"],
                            start=match.start,
                            end=match.end,
                            before=match.before,
                            after=match.after,
                        )
                    )
        order = {word: index for index, word in enumerate(counts)}
        return [
            SpellingGroup(text=word, count=counts[word], occurrences=places[word])
            for word in sorted(counts, key=lambda w: (-counts[w], order[w]))
        ]

    def get_similar_entities(
        self, edition_id: UUID, text: str, label: str
    ) -> SimilarEntities:
        """The places in the pages of an edition that have the named entity `text`
        (a name of one or more words, as a person marked it with `label`) in any
        of its forms in the cases (see `app/text_tools/inflection.py`), in the text
        of the corrected content as the user sees it. A place that is in an entity
        that is marked already (of any label) is left out. A match is `sure` if
        the NER model finds an entity there too, with the same label; the model
        is run only on the pages that have a match. A page without matches, without
        XML, or with XML that cannot be read is left out.

        Raises ValueError if `text` is not a name (no letters, or more than five
        words), and NerModelUnavailable if the model cannot be loaded.
        """
        words = text.split()
        if (
            not words
            or len(words) > _MOST_WORDS
            or not all(any(character.isalpha() for character in word) for word in words)
        ):
            raise ValueError(
                f"The entity must be a name of one to {_MOST_WORDS} words with letters"
            )
        forms = forms_of_phrase(" ".join(words), label)
        wanted = {form.text for form in forms}
        rows = self.conn.execute(
            "SELECT number, xml_content_corrected FROM pages"
            " WHERE edition_id = %s ORDER BY number",
            (edition_id,),
        ).fetchall()
        pages = []
        for row in rows:
            xml = row["xml_content_corrected"]
            if not xml:
                continue
            try:
                page_text = tei_xml_to_text(xml)
                marked = tei_xml_entities(xml)
            except InvalidTei:
                continue
            found = [
                match
                for match in find_forms(page_text, wanted)
                if not any(
                    match.start < entity["end"] and match.end > entity["start"]
                    for entity in marked
                )
            ]
            if not found:
                continue
            recognized = [
                entity for entity in find_entities(page_text) if entity.label == label
            ]
            pages.append(
                PageEntityMatches(
                    page_number=row["number"],
                    matches=[
                        EntityMatch(
                            start=match.start,
                            end=match.end,
                            text=match.text,
                            before=match.before,
                            after=match.after,
                            sure=any(
                                match.start < entity.end and match.end > entity.start
                                for entity in recognized
                            ),
                        )
                        for match in found
                    ],
                )
            )
        return SimilarEntities(
            label=label,
            forms=[
                EntityForm(text=form.text, cases=sorted(form.cases)) for form in forms
            ],
            pages=pages,
        )

    def get_rubbish_lines(self, edition_id: UUID) -> list[PageRubbish]:
        """The lines of the pages of an edition that are OCR rubbish (see
        `app/text_tools/rubbish.py`), page by page, in the order of the pages, in
        the text of the corrected content as the user sees it. A line in a section
        that is marked (a header, a page number, ...) is left alone, and so is a
        page without lines like that, without XML, or with XML that cannot be read.
        The spellings that are legal in the edition count as correct words.

        Raises SpellcheckUnavailable if the dictionary cannot be loaded.
        """
        rows = self.conn.execute(
            "SELECT number, xml_content_corrected FROM pages"
            " WHERE edition_id = %s ORDER BY number",
            (edition_id,),
        ).fetchall()
        legal = self.get_legal_spellings(edition_id)
        found = []
        for row in rows:
            xml = row["xml_content_corrected"]
            if not xml:
                continue
            try:
                text = tei_xml_to_text(xml)
                protected = [
                    (section["start"], section["end"])
                    for section in tei_xml_sections(xml)
                ]
            except InvalidTei:
                continue
            lines = [
                RubbishLine.model_validate(vars(line))
                for line in find_rubbish_lines(text, legal, protected)
            ]
            if lines:
                found.append(PageRubbish(page_number=row["number"], lines=lines))
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
        """A page of the editions in the collection, in the order of their ids (so
        the pages do not change between requests): each is its id and the author
        and title of its work, or None for those that are not known (the edition
        is not stored yet, or its work has no author or no title). Which page,
        and how long, is up to the request that is being served:
        fastapi-pagination reads it from there.

        The two queries are run here, not by fastapi-pagination's psycopg
        helper: that one switches the row factory of the connection while it
        counts, and the connection is shared by the requests that run at the
        same time, so two of them could leave it switched."""
        params = resolve_params()
        raw = params.to_raw_params().as_limit_offset()
        rows = self.conn.execute(
            "SELECT c.id, w.author, w.title FROM collection c"
            " LEFT JOIN editions e ON e.id = c.id"
            " LEFT JOIN works w ON w.id = e.work_id"
            " ORDER BY c.id LIMIT %s OFFSET %s",
            (raw.limit, raw.offset),
        ).fetchall()
        total = self.conn.execute("SELECT count(*) AS n FROM collection").fetchone()
        return create_page(
            [
                {"id": row["id"], "author": row["author"], "title": row["title"]}
                for row in rows
            ],
            total=total["n"],
            params=params,
        )

"""The command line that imports an external edition from wolnelektury.pl.

    python -m app.external_editions        (or `make external-edition`)

It asks for the link to a `.txt`, fetches it, and shows the author and the title that
it found: each can be accepted (Enter), edited (e) or cancelled (c), and cancelling
anywhere stops everything with nothing saved. The year is not in the text, so it is
asked for. Then the edition is saved as an `ExternalEdition`, and each different word
of the text as an `ExternalEditionSpelling` of it (in one batch), all in one
transaction. An edition with the same author, title and year (case aside) is not saved
a second time: the script says so and stops.

The database is the one of the POSTGRES_* variables (see `app/persistence/db.py`).
"""

from collections.abc import Callable
from typing import Any

from app.external_editions.parsing import (
    ParsedText,
    fetch_text,
    is_valid_link,
    join_author,
    parse,
    split_author,
    words,
)
from app.persistence.db import ExternalEditionExists
from app.persistence.ontology import ExternalEdition

YEAR_FROM, YEAR_TO = 1500, 2030  # as `ExternalEdition.year`
CANCEL = "c"


class Cancelled(Exception):
    """The user cancelled."""


def _ask(prompt: str, input_: Callable[[str], str]) -> str:
    answer = input_(prompt).strip()
    if answer.lower() == CANCEL:
        raise Cancelled
    return answer


def _confirm_title(
    value: str, input_: Callable[[str], str], print_: Callable[[str], None]
) -> str:
    """The title: Enter accepts it, `e` replaces it (Enter keeps it), `c` cancels."""
    while True:
        print_(f"Title: {value}")
        answer = _ask("  Enter to accept, e to edit, c to cancel: ", input_).lower()
        if answer == "":
            return value
        if answer == "e":
            return _ask(f"  Title [{value}]: ", input_) or value
        print_("  Please answer Enter, e or c.")


def _confirm_author(
    value: str, input_: Callable[[str], str], print_: Callable[[str], None]
) -> str:
    """The author as split into first name(s) and last name: Enter accepts, `e`
    edits both (Enter keeps one), `c` cancels. The result is written like the
    catalogue does it, "Mickiewicz, Adam"."""
    first, last = split_author(value)
    while True:
        print_(f"Author: first name(s) '{first}', last name '{last}'")
        answer = _ask("  Enter to accept, e to edit, c to cancel: ", input_).lower()
        if answer == "":
            return join_author(first, last)
        if answer == "e":
            first = _ask(f"  First name(s) [{first}]: ", input_) or first
            last = _ask(f"  Last name [{last}]: ", input_) or last
        else:
            print_("  Please answer Enter, e or c.")


def _ask_link(
    input_: Callable[[str], str],
    print_: Callable[[str], None],
    fetch: Callable[[str], str],
) -> ParsedText:
    while True:
        link = _ask("Link to the .txt (c to cancel): ", input_)
        if not is_valid_link(link):
            print_("The link must start with https://wolnelektury.pl/media/book/txt/")
            print_("and end with txt.")
            continue
        try:
            return parse(fetch(link))
        except (OSError, ValueError) as error:  # ParseError is a ValueError
            print_(f"Cannot use that link: {error}")


def _ask_year(input_: Callable[[str], str], print_: Callable[[str], None]) -> int:
    while True:
        answer = _ask(
            f"Year of the edition ({YEAR_FROM}-{YEAR_TO}, c to cancel): ", input_
        )
        if answer.isdigit() and YEAR_FROM <= int(answer) <= YEAR_TO:
            return int(answer)
        print_(f"A year between {YEAR_FROM} and {YEAR_TO}, please.")


def run(
    db: Any,
    *,
    input_: Callable[[str], str] = input,
    print_: Callable[[str], None] = print,
    fetch: Callable[[str], str] = fetch_text,
) -> ExternalEdition | None:
    """Import one external edition; the saved `ExternalEdition`, or None if the user
    cancelled (nothing is saved then)."""
    try:
        text = _ask_link(input_, print_, fetch)
        author = _confirm_author(text.author, input_, print_)
        title = _confirm_title(text.title, input_, print_)
        year = _ask_year(input_, print_)
    except Cancelled:
        print_("Cancelled, nothing was saved.")
        return None

    spellings = words(text.body)
    try:
        with db.conn.transaction():
            external = db.create_external_edition(
                ExternalEdition(author=author, title=title, year=year)
            )
            added = db.create_external_edition_spellings(external.id, spellings)
    except ExternalEditionExists:
        print_(
            f"{author}, {title} ({year}) is in the database already: nothing was saved."
        )
        return None
    print_(f"Saved {author}, {title} ({year}) with {added} spellings.")
    return external


def main() -> int:
    from app.persistence.db import Database

    db = Database()
    try:
        run(db)
    except KeyboardInterrupt, EOFError:
        print("\nCancelled, nothing was saved.")
        return 1
    finally:
        db.close()
    return 0

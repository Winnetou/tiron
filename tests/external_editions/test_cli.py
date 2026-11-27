from contextlib import contextmanager

import pytest

from app.external_editions.cli import run
from app.external_editions.parsing import LINK_START
from app.persistence.db import ExternalEditionExists
from app.persistence.ontology import ExternalEditionSpelling
from tests.external_editions.test_parsing import LINK, SAMPLE

TEXT = SAMPLE.read_text(encoding="utf-8")
EXPECTED_WORDS = 4441


class FakeDatabase:
    """Records what is saved; the transaction says whether it was left by an error."""

    def __init__(self):
        self.editions, self.spellings = [], []
        self.transactions = []
        self.conn = self

    @contextmanager
    def transaction(self):
        self.transactions.append("open")
        try:
            yield
        except BaseException:
            self.transactions.append("rolled back")
            raise
        self.transactions.append("committed")

    def create_external_edition(self, external):
        self.editions.append(external)
        return external

    def create_external_edition_spellings(self, external_edition_id, spellings):
        self.spellings.extend(
            ExternalEditionSpelling(
                external_edition_id=external_edition_id, spelling=spelling
            )
            for spelling in spellings
        )
        return len(self.spellings)


def session(answers, *, text=TEXT):
    """(input, print, fetch, said, asked): the user's answers are given in order."""
    answers = iter(answers)
    said, asked, fetched = [], [], []

    def input_(prompt):
        asked.append(prompt)
        return next(answers)

    def fetch(link):
        fetched.append(link)
        if isinstance(text, Exception):
            raise text
        return text

    return (
        {"input_": input_, "print_": said.append, "fetch": fetch},
        said,
        asked,
        fetched,
    )


def test_accepting_everything_saves_the_edition_and_its_words():
    db = FakeDatabase()
    io, said, _, fetched = session([LINK, "", "", "1832"])

    external = run(db, **io)

    assert fetched == [LINK]
    assert (external.author, external.title, external.year) == (
        "Mickiewicz, Adam",
        "Księgi narodu polskiego i pielgrzymstwa polskiego",
        1832,
    )
    assert db.editions == [external]
    assert len(db.spellings) == EXPECTED_WORDS
    assert {s.external_edition_id for s in db.spellings} == {external.id}
    assert "pielgrzym" in {s.spelling for s in db.spellings}
    assert db.transactions == ["open", "committed"]
    assert f"with {EXPECTED_WORDS} spellings" in said[-1]
    assert "Author: first name(s) 'Adam', last name 'Mickiewicz'" in said


def test_the_author_and_the_title_can_be_edited():
    db = FakeDatabase()
    io, *_ = session([LINK, "e", "Adam Bernard", "", "", "e", "Księgi", "", "1832"])

    external = run(db, **io)

    assert external.author == "Mickiewicz, Adam Bernard"
    assert external.title == "Księgi"


def test_editing_the_last_name_only_keeps_a_last_name_of_many_words():
    db = FakeDatabase()
    io, *_ = session([LINK, "E", "", "de Balzac", "", "", "1832"])

    assert run(db, **io).author == "de Balzac, Adam"


def test_an_answer_that_is_not_understood_is_asked_again():
    db = FakeDatabase()
    io, said, *_ = session([LINK, "x", "", "?", "", "1832"])

    assert run(db, **io) is not None
    assert said.count("  Please answer Enter, e or c.") == 2


def test_a_bad_link_is_asked_again_without_fetching():
    db = FakeDatabase()
    io, said, _, fetched = session(["https://example.com/a.txt", LINK, "", "", "1832"])

    assert run(db, **io) is not None
    assert fetched == [LINK]
    assert any(LINK_START in line for line in said)


def test_a_link_that_cannot_be_fetched_or_read_is_asked_again():
    for problem in (OSError("no network"), ValueError("bad")):
        db = FakeDatabase()
        io, said, _, fetched = session([LINK, "c"], text=problem)

        assert run(db, **io) is None
        assert fetched == [LINK]
        assert any(str(problem) in line for line in said)


def test_a_text_that_is_not_laid_out_as_expected_is_reported():
    io, said, *_ = session([LINK, "c"], text="Just a line\n")

    assert run(FakeDatabase(), **io) is None
    assert any("footer" in line for line in said)


@pytest.mark.parametrize(
    "answers",
    [
        ["c"],
        [LINK, "c"],
        [LINK, "", "c"],
        [LINK, "e", "c"],
        [LINK, "e", "", "C"],
        [LINK, "", "", "c"],
        [LINK, "", "e", "c"],
    ],
)
def test_cancelling_anywhere_saves_nothing(answers):
    db = FakeDatabase()
    io, said, *_ = session(answers)

    assert run(db, **io) is None
    assert (db.editions, db.spellings, db.transactions) == ([], [], [])
    assert said[-1] == "Cancelled, nothing was saved."


@pytest.mark.parametrize("year", ["", "abc", "1499", "2031", "-1", "18 32", "1832.0"])
def test_a_bad_year_is_asked_again(year):
    db = FakeDatabase()
    io, said, *_ = session([LINK, "", "", year, "1832"])

    assert run(db, **io).year == 1832
    assert any("between" in line for line in said)


@pytest.mark.parametrize("year", ["1500", "2030"])
def test_the_ends_of_the_years_are_fine(year):
    io, *_ = session([LINK, "", "", year])

    assert run(FakeDatabase(), **io).year == int(year)


def test_the_saving_is_one_transaction_that_is_rolled_back_on_an_error():
    class Failing(FakeDatabase):
        def create_external_edition_spellings(self, external_edition_id, spellings):
            raise RuntimeError("database down")

    db = Failing()
    io, *_ = session([LINK, "", "", "1832"])

    with pytest.raises(RuntimeError):
        run(db, **io)
    assert db.transactions == ["open", "rolled back"]


def test_an_edition_that_is_there_already_is_reported_and_nothing_is_saved():
    class Taken(FakeDatabase):
        def create_external_edition(self, external):
            raise ExternalEditionExists("taken")

    db = Taken()
    io, said, *_ = session([LINK, "", "", "1832"])

    assert run(db, **io) is None
    assert db.spellings == []
    assert db.transactions == ["open", "rolled back"]
    assert said[-1] == (
        "Mickiewicz, Adam, Księgi narodu polskiego i pielgrzymstwa polskiego (1832) "
        "is in the database already: nothing was saved."
    )

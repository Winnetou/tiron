from pathlib import Path

import pytest

from app.external_editions.parsing import (
    ParseError,
    fetch_text,
    is_valid_link,
    join_author,
    parse,
    split_author,
    words,
)

LINK = (
    "https://wolnelektury.pl/media/book/txt/"
    "ksiegi-narodu-polskiego-i-pielgrzymstwa-polskiego.txt"
)
# A copy of the text at LINK (public domain), so that the tests need no network.
SAMPLE = Path(__file__).with_name(
    "ksiegi-narodu-polskiego-i-pielgrzymstwa-polskiego.txt"
)
FOOTER = (
    "-----\nTa lektura, podobnie jak tysiące innych, dostępna jest na stronie "
    "wolnelektury.pl.\nInne rzeczy.\n"
)


@pytest.fixture(scope="module")
def sample() -> str:
    return SAMPLE.read_text(encoding="utf-8")


class TestIsValidLink:
    def test_the_link_of_the_sample(self):
        assert is_valid_link(LINK)

    @pytest.mark.parametrize(
        "link",
        [
            "",
            "https://wolnelektury.pl/media/book/txt/",
            "https://wolnelektury.pl/media/book/txt/txt",
            "http://wolnelektury.pl/media/book/txt/a.txt",
            "https://wolnelektury.pl/media/book/epub/a.txt",
            "https://wolnelektury.pl/media/book/txt/a.pdf",
            "https://example.com/https://wolnelektury.pl/media/book/txt/a.txt",
            "https://wolnelektury.pl/media/book/txt/a b.txt",
            "https://wolnelektury.pl/media/book/txt/a.txt\n",
        ],
    )
    def test_refuses_the_others(self, link):
        assert not is_valid_link(link)


class TestFetchText:
    def test_refuses_a_bad_link_without_fetching(self, monkeypatch):
        def no_network(*args, **kwargs):
            raise AssertionError("must not fetch")

        monkeypatch.setattr("urllib.request.urlopen", no_network)
        with pytest.raises(ValueError, match="wolnelektury"):
            fetch_text("https://example.com/a.txt")

    def test_reads_the_answer_as_utf8(self, monkeypatch):
        class Answer:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return "Księgi".encode()

        seen = {}

        def urlopen(link, timeout):
            seen.update(link=link, timeout=timeout)
            return Answer()

        monkeypatch.setattr("urllib.request.urlopen", urlopen)
        assert fetch_text(LINK) == "Księgi"
        assert seen["link"] == LINK and seen["timeout"] > 0


class TestParseTheSample:
    def test_the_author_and_the_title(self, sample):
        text = parse(sample)

        assert text.author == "Adam Mickiewicz"
        assert text.title == "Księgi narodu polskiego i pielgrzymstwa polskiego"

    def test_the_body_starts_after_the_isbn(self, sample):
        body = parse(sample).body

        assert body.startswith("Księgi Narodu Polskiego.\nOd początku świata")
        assert "ISBN" not in body

    def test_the_body_ends_before_the_footer(self, sample):
        body = parse(sample).body

        assert body.endswith("W Imie Ojca i Syna i Ducha Świętego.\n\nAmen.")
        assert "wolnelektury" not in body
        assert "-----" not in body

    def test_the_words(self, sample):
        found = words(parse(sample).body)

        assert len(found) == len(set(found)) > 4000
        assert {"pielgrzym", "Pielgrzym", "naszéj"} <= set(found)
        assert found[:4] == ["Księgi", "Narodu", "Polskiego", "Od"]


class TestParse:
    def test_title_of_many_lines_and_no_isbn(self):
        text = parse(f"Jan Kochanowski\n\nTreny\ni inne\n\n\nTekst.\n\n\n{FOOTER}")

        assert text.author == "Jan Kochanowski"
        assert text.title == "Treny i inne"
        assert text.body == "Tekst."

    def test_crlf_and_a_byte_order_mark(self):
        raw = "﻿A B\r\n\r\nT\r\n\r\nTekst\r\n\r\n\r\n" + FOOTER.replace("\n", "\r\n")

        text = parse(raw)

        assert (text.author, text.title, text.body) == ("A B", "T", "Tekst")

    def test_only_what_is_before_the_first_footer_counts(self):
        text = parse(f"A B\n\nT\n\nx\n\n{FOOTER}y\n\n{FOOTER}")

        assert text.body == "x"

    def test_the_isbn_line_is_skipped_but_other_lines_are_not(self):
        assert parse(f"A B\n\nT\n\nISBN 1-2\n\nx\n\n{FOOTER}").body == "x"
        assert parse(f"A B\n\nT\n\nIsbn is not here\nx\n\n{FOOTER}").body == "x"
        assert parse(f"A B\n\nT\n\nOn ISBN\n\n{FOOTER}").body == "On ISBN"

    def test_a_text_without_the_footer_is_refused(self):
        with pytest.raises(ParseError, match="footer"):
            parse("A B\n\nT\n\nx\n-----\nsomething else\n")

    @pytest.mark.parametrize("raw", ["", "\n\n", "A B\n\n"])
    def test_a_text_without_author_or_title_is_refused(self, raw):
        with pytest.raises(ParseError):
            parse(raw + FOOTER)


class TestAuthor:
    @pytest.mark.parametrize(
        ("written", "first", "last"),
        [
            ("Adam Mickiewicz", "Adam", "Mickiewicz"),
            ("Józef Ignacy Kraszewski", "Józef Ignacy", "Kraszewski"),
            ("Anonim", "", "Anonim"),
            ("  Adam   Mickiewicz ", "Adam", "Mickiewicz"),
            ("", "", ""),
        ],
    )
    def test_the_last_word_is_the_last_name(self, written, first, last):
        assert split_author(written) == (first, last)

    @pytest.mark.parametrize(
        ("first", "last", "joined"),
        [
            ("Adam", "Mickiewicz", "Mickiewicz, Adam"),
            (" Józef Ignacy ", " Kraszewski ", "Kraszewski, Józef Ignacy"),
            ("", "Anonim", "Anonim"),
            ("Adam", "", "Adam"),
        ],
    )
    def test_join_writes_it_like_the_catalogue(self, first, last, joined):
        assert join_author(first, last) == joined


class TestWords:
    def test_different_words_in_order_case_matters(self):
        assert words("Jey tey jey, Jey! tey.") == ["Jey", "tey", "jey"]

    def test_hyphenated_words_stay_whole_and_signs_are_not_words(self):
        assert words("biało-czerwony *Boga* — „kot”") == [
            "biało-czerwony",
            "Boga",
            "kot",
        ]

    def test_leaves_out_numbers_and_words_with_digits_and_too_long_ones(self):
        assert words("rok 1832 a1 ok " + "x" * 101 + " " + "y" * 100) == [
            "rok",
            "ok",
            "y" * 100,
        ]

    def test_nothing_in_nothing_out(self):
        assert words("") == [] and words("1 2 ...") == []

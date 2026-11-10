import threading
import time

import phunspell
import pytest

from app import spelling
from app.models import Word
from app.spelling import SpellcheckUnavailable, check_words, get_checker


class FakeChecker:
    """Knows the words it is given; counts how often it is asked."""

    def __init__(self, *known):
        self.known = {word.lower() for word in known}
        self.asked = []

    def lookup(self, word):
        self.asked.append(word)
        return word.lower() in self.known


@pytest.fixture(autouse=True)
def fresh_dictionary(monkeypatch):
    monkeypatch.setattr(spelling, "_checker", None)


@pytest.fixture
def fake(monkeypatch):
    checker = FakeChecker("ala", "ma", "kota", "biało", "czerwony", "o", "wych")
    monkeypatch.setattr(spelling, "get_checker", lambda: checker)
    return checker


def words(text):
    return [(w.text, w.correct) for w in check_words(text)]


class TestWords:
    def test_every_word_with_offsets_and_a_verdict(self, fake):
        assert check_words("Ala ma kotta") == [
            Word(text="Ala", start=0, end=3, correct=True),
            Word(text="ma", start=4, end=6, correct=True),
            Word(text="kotta", start=7, end=12, correct=False),
        ]

    def test_the_offsets_point_at_the_words(self, fake):
        text = "Ala, ma (kota)!\nAla ma… kota."

        for word in check_words(text):
            assert text[word.start : word.end] == word.text

    def test_punctuation_and_spaces_are_not_words(self, fake):
        assert [w.text for w in check_words('  Ala,  "ma"; (kota)...\n\n')] == [
            "Ala",
            "ma",
            "kota",
        ]

    def test_empty_text_and_text_without_words(self, fake):
        assert check_words("") == []
        assert check_words("  \n ,.;: ()—… ") == []

    def test_offsets_count_code_points(self, fake):
        text = "😀😀 Ala 😀 ma"

        assert [(w.text, w.start, w.end) for w in check_words(text)] == [
            ("Ala", 3, 6),
            ("ma", 9, 11),
        ]

    def test_polish_letters(self, fake):
        assert [w.text for w in check_words("zażółć gęślą jaźń ŁÓDŹ")] == [
            "zażółć",
            "gęślą",
            "jaźń",
            "ŁÓDŹ",
        ]

    def test_a_hyphen_inside_a_word_belongs_to_it(self, fake):
        assert words("biało-czerwony") == [("biało-czerwony", True)]

    def test_an_apostrophe_inside_a_word_belongs_to_it(self, fake):
        assert [w.text for w in check_words("d'Artagnan o’Hara")] == [
            "d'Artagnan",
            "o’Hara",
        ]

    def test_a_hyphen_at_the_end_of_a_line_does_not(self, fake):
        # "o-" / "wych": the two halves of a word split over two lines
        assert [(w.text, w.start, w.end) for w in check_words("o-\nwych")] == [
            ("o", 0, 1),
            ("wych", 3, 7),
        ]

    @pytest.mark.parametrize(
        "text", ["-Ala", "Ala-", "--Ala--", "Ala - ma", "Ala -- ma"]
    )
    def test_loose_hyphens_are_not_part_of_words(self, fake, text):
        assert [w.text for w in check_words(text)] == [
            part for part in text.replace("-", " ").split()
        ]

    def test_underscores_are_not_letters(self, fake):
        assert [w.text for w in check_words("Ala_ma")] == ["Ala", "ma"]


class TestVerdicts:
    def test_unknown_words_are_wrong(self, fake):
        assert words("Ala jey") == [("Ala", True), ("jey", False)]

    def test_words_with_digits_are_not_checked_and_count_as_correct(self, fake):
        assert words("1840 l840 A4 10.") == [
            ("1840", True),
            ("l840", True),
            ("A4", True),
            ("10", True),
        ]
        assert fake.asked == []  # nothing was looked up

    def test_a_hyphenated_word_is_right_if_all_its_parts_are(self, fake):
        # not in the dictionary as a whole, but "biało" and "czerwony" are
        assert words("biało-czerwony") == [("biało-czerwony", True)]

    def test_a_hyphenated_word_is_wrong_if_one_part_is(self, fake):
        assert words("biało-czerwoni") == [("biało-czerwoni", False)]

    def test_a_hyphenated_word_in_the_dictionary_as_a_whole(self, monkeypatch):
        checker = FakeChecker("biało-czerwony")
        monkeypatch.setattr(spelling, "get_checker", lambda: checker)

        assert words("biało-czerwony") == [("biało-czerwony", True)]

    def test_each_distinct_word_is_looked_up_once(self, fake):
        check_words("ala ma kota ala ma kota ala jey jey")

        assert sorted(fake.asked) == ["ala", "jey", "kota", "ma"]

    def test_the_same_word_has_the_same_verdict_everywhere(self, fake):
        assert [w.correct for w in check_words("jey Ala jey")] == [False, True, False]


class TestLegalSpellings:
    def test_a_legal_spelling_is_correct_although_the_dictionary_does_not_know_it(
        self, fake
    ):
        assert words("Ala jey") == [("Ala", True), ("jey", False)]
        assert [(w.text, w.correct) for w in check_words("Ala jey", {"jey"})] == [
            ("Ala", True),
            ("jey", True),
        ]

    def test_only_the_legal_ones_change(self, fake):
        result = check_words("jey tey ma", {"jey"})

        assert [(w.text, w.correct) for w in result] == [
            ("jey", True),
            ("tey", False),
            ("ma", True),
        ]

    def test_a_lowercase_spelling_also_makes_its_capital_forms_correct(self, fake):
        result = check_words("jey Jey JEY jEy", {"jey"})

        assert [w.correct for w in result] == [True, True, True, True]

    def test_a_capital_spelling_does_not_make_the_lowercase_form_correct(self, fake):
        result = check_words("Jey jey JEY", {"Jey"})

        assert [w.correct for w in result] == [True, False, False]

    def test_a_legal_spelling_is_matched_as_a_whole_word(self, fake):
        result = check_words("jeyy ajey jey", {"jey"})

        assert [w.correct for w in result] == [False, False, True]

    def test_hyphenated_words_are_matched_as_a_whole(self, fake):
        assert [w.correct for w in check_words("tey-jey", {"tey-jey"})] == [True]
        assert [w.correct for w in check_words("tey-jey", {"jey"})] == [False]

    def test_legal_spellings_are_not_looked_up_in_the_dictionary(self, fake):
        check_words("jey ma", {"jey"})

        assert fake.asked == ["ma"]

    def test_none_given_is_the_same_as_none_legal(self, fake):
        assert (
            check_words("Ala jey")
            == check_words("Ala jey", set())
            == check_words("Ala jey", [])
        )

    def test_the_offsets_do_not_change(self, fake):
        assert [(w.start, w.end) for w in check_words("Ala jey", {"jey"})] == [
            (w.start, w.end) for w in check_words("Ala jey")
        ]

    def test_words_with_digits_are_unaffected(self, fake):
        assert [w.correct for w in check_words("1840 jey", {"jey"})] == [True, True]


class TestGetChecker:
    def test_loads_the_polish_dictionary(self, monkeypatch):
        loaded = []
        monkeypatch.setattr(
            spelling.phunspell,
            "Phunspell",
            lambda language: loaded.append(language) or "checker",
        )

        assert get_checker() == "checker"
        assert loaded == ["pl_PL"]

    def test_loads_only_once(self, monkeypatch):
        loaded = []
        monkeypatch.setattr(
            spelling.phunspell,
            "Phunspell",
            lambda language: loaded.append(language) or object(),
        )

        assert get_checker() is get_checker()
        assert len(loaded) == 1

    def test_loads_only_once_with_concurrent_first_requests(self, monkeypatch):
        loaded = []

        def slow_load(language):
            time.sleep(0.1)
            loaded.append(language)
            return object()

        monkeypatch.setattr(spelling.phunspell, "Phunspell", slow_load)
        results = []
        threads = [
            threading.Thread(target=lambda: results.append(get_checker()))
            for _ in range(5)
        ]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(loaded) == 1
        assert len({id(result) for result in results}) == 1

    def test_a_missing_dictionary_is_reported_and_tried_again_later(self, monkeypatch):
        def missing(language):
            raise phunspell.phunspell.PhunspellError("dictionary not found")

        monkeypatch.setattr(spelling.phunspell, "Phunspell", missing)

        with pytest.raises(SpellcheckUnavailable, match="pl_PL"):
            get_checker()
        monkeypatch.setattr(
            spelling.phunspell, "Phunspell", lambda language: "now there"
        )
        assert get_checker() == "now there"  # the failure was not cached

    def test_check_words_reports_a_missing_dictionary(self, monkeypatch):
        def missing(language):
            raise phunspell.phunspell.PhunspellError("dictionary not found")

        monkeypatch.setattr(spelling.phunspell, "Phunspell", missing)

        with pytest.raises(SpellcheckUnavailable):
            check_words("Ala")

    def test_the_dictionary_is_not_loaded_by_importing_the_app(self):
        import app.main  # noqa: F401

        assert spelling._checker is None


@pytest.fixture(scope="module")
def real_dictionary():
    return phunspell.Phunspell("pl_PL")  # ~2 s: once for all these tests


class TestWithTheRealDictionary:
    """The Polish dictionary that comes with phunspell."""

    @pytest.fixture(autouse=True)
    def use_the_real_dictionary(self, monkeypatch, real_dictionary):
        monkeypatch.setattr(spelling, "_checker", real_dictionary)

    def test_ordinary_polish_is_correct(self):
        text = "Wielu Biskupów krakowskich przekazało kościołowi złoty krzyż."

        assert all(w.correct for w in check_words(text))

    def test_the_spelling_errors_of_old_print_and_ocr_are_found(self):
        text = "dawnieyszych jey tey raczey wcałey krzyz"

        assert [w.correct for w in check_words(text)] == [False] * 6

    def test_capital_letters_do_not_matter(self):
        assert all(w.correct for w in check_words("KATEDRA Katedra katedra"))

    def test_short_words_that_are_words(self):
        assert all(w.correct for w in check_words("a i o u w z"))

    def test_compounds_with_a_hyphen(self):
        assert all(w.correct for w in check_words("biało-czerwony Biało-czerwony"))

    def test_proper_names_are_checked_like_any_word(self):
        wrong = [
            w.text
            for w in check_words("Mickiewicz Kowalski Kurozwęekij")
            if not w.correct
        ]

        assert wrong == ["Kurozwęekij"]

    def test_a_whole_page_of_ocr_is_quick(self):
        text = ("Wielu Biskupów krak., że dawnieyszych kościół. " * 40).strip()
        start = time.perf_counter()

        result = check_words(text)

        assert time.perf_counter() - start < 3
        assert len(result) == 40 * 6

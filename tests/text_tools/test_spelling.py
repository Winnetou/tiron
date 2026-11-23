import threading
import time

import phunspell
import pytest

from app.text_tools import spelling
from app.text_tools.spelling import (
    NotAWord,
    SpellcheckUnavailable,
    check_words,
    get_checker,
    suggest_spelling,
)
from app.view_models import Word


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


class Suggesting(FakeChecker):
    """A checker that also suggests: `suggestions` by the word that is asked for."""

    def __init__(self, suggestions, *known):
        super().__init__(*known)
        self.suggestions = suggestions

    def suggest(self, word):
        yield from self.suggestions.get(word, [])


@pytest.fixture
def suggesting(monkeypatch):
    checker = Suggesting(
        {
            "kótka": ["kotka", "kotką", "krótka", "kitka"],
            "tćm": ["tam", "tom", "tym", "tum"],
            "zle": ["źle", "zle", "z le", "złe"],
        },
        "ala",
        "ma",
        "tu",
        "kotka",
        "kotką",
        "krótka",
        "kitka",
        "tam",
        "tom",
        "tym",
        "źle",
        "złe",
    )
    monkeypatch.setattr(spelling, "get_checker", lambda: checker)
    return checker


def words(text):
    return [(w.text, w.correct) for w in check_words(text)]


class TestIgnoredStretches:
    def test_words_in_an_ignored_stretch_are_correct(self, fake):
        text = "ala mens sana ma"

        result = [(w.text, w.correct) for w in check_words(text, (), [(4, 13)])]

        assert result == [("ala", True), ("mens", True), ("sana", True), ("ma", True)]

    def test_words_outside_it_are_judged_as_always(self, fake):
        result = [
            (w.text, w.correct) for w in check_words("ala mens sana", (), [(4, 8)])
        ]

        assert result == [("ala", True), ("mens", True), ("sana", False)]

    def test_a_word_that_is_only_partly_in_it_is_judged(self, fake):
        result = [(w.text, w.correct) for w in check_words("ala mens", (), [(4, 6)])]

        assert result == [("ala", True), ("mens", False)]


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

    def test_signs_are_tokens_that_count_as_correct(self, fake):
        assert words('  Ala,  "ma"; (kota)...\n\n') == [
            ("Ala", True),
            (",", True),
            ('"', True),
            ("ma", True),
            ('";', True),
            ("(", True),
            ("kota", True),
            (")...", True),
        ]

    def test_a_lonely_sign_and_signs_glued_to_a_word(self, fake):
        text = "Ala $ ma (|}kota"

        assert words(text) == [
            ("Ala", True),
            ("$", True),
            ("ma", True),
            ("(|}", True),
            ("kota", True),
        ]
        for word in check_words(text):
            assert text[word.start : word.end] == word.text

    def test_only_white_space_is_not_a_token(self, fake):
        assert check_words("") == []
        assert check_words("  \n\t ") == []

    def test_offsets_count_code_points(self, fake):
        text = "😀😀 Ala 😀 ma"

        assert [(w.text, w.start, w.end) for w in check_words(text)] == [
            ("😀😀", 0, 2),
            ("Ala", 3, 6),
            ("😀", 7, 8),
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

    def test_the_parts_of_a_word_split_over_lines_stay_tokens_of_their_own(self, fake):
        # "o-" / "wych": the two halves of a word split over two lines
        assert [(w.text, w.start, w.end) for w in check_words("o-\nwych")] == [
            ("o", 0, 1),
            ("-", 1, 2),
            ("wych", 3, 7),
        ]

    @pytest.mark.parametrize(
        "text", ["-Ala", "Ala-", "--Ala--", "Ala - ma", "Ala -- ma"]
    )
    def test_loose_hyphens_are_not_part_of_words(self, fake, text):
        # they are signs: tokens of their own, which are not spelled
        assert [
            (w.text, w.correct) for w in check_words(text) if "-" not in w.text
        ] == [(part, True) for part in text.replace("-", " ").split()]
        assert all(w.correct for w in check_words(text))

    def test_underscores_are_not_letters(self, fake):
        assert [w.text for w in check_words("Ala_ma")] == ["Ala", "_", "ma"]


class TestWordsSplitOverLines:
    @pytest.fixture
    def checker(self, monkeypatch):
        known = FakeChecker("przeczytać", "ala", "ma", "biało-czerwony", "dom", "a")
        monkeypatch.setattr(spelling, "get_checker", lambda: known)
        return known

    def test_a_word_that_is_right_joined_is_right_in_both_parts(self, checker):
        assert words("przeczy-\ntać") == [
            ("przeczy", True),
            ("-", True),
            ("tać", True),
        ]

    def test_without_the_split_the_parts_are_wrong(self, checker):
        assert words("przeczy tać") == [("przeczy", False), ("tać", False)]

    def test_a_word_that_is_wrong_joined_is_wrong_in_both_parts(self, checker):
        assert words("przeczy-\ntaćx") == [
            ("przeczy", False),
            ("-", True),
            ("taćx", False),
        ]

    def test_the_hyphen_is_a_sign_and_so_it_is_correct(self, checker):
        assert [w.correct for w in check_words("przeczy-\ntaćx") if w.text == "-"] == [
            True
        ]

    def test_a_hyphen_that_belongs_to_the_word_is_kept_when_that_is_the_right_one(
        self, checker
    ):
        # "biało-czerwony" is the word, "białoczerwony" is not
        assert words("biało-\nczerwony") == [
            ("biało", True),
            ("-", True),
            ("czerwony", True),
        ]

    def test_the_offsets_of_the_parts_are_their_own(self, checker):
        text = "Ala ma przeczy-\ntać."

        for word in check_words(text):
            assert text[word.start : word.end] == word.text

    def test_the_text_around_is_checked_as_always(self, checker):
        assert words("Ala jey przeczy-\ntać ma") == [
            ("Ala", True),
            ("jey", False),
            ("przeczy", True),
            ("-", True),
            ("tać", True),
            ("ma", True),
        ]

    def test_the_whole_word_is_looked_up_not_the_parts(self, checker):
        check_words("przeczy-\ntać")

        assert checker.asked == ["przeczytać"]

    def test_a_legal_spelling_is_matched_as_the_whole_word(self, checker):
        assert [w.correct for w in check_words("je-\ny", {"jey"})] == [True] * 3

    def test_a_legal_spelling_with_the_hyphen_kept_too(self, checker):
        assert [w.correct for w in check_words("tey-\njey", {"tey-jey"})] == [True] * 3

    def test_a_word_split_over_three_lines_that_is_wrong_is_wrong_in_all_parts(
        self, checker
    ):
        assert [w.correct for w in check_words("prze-\nczy-\ntaćx")] == [
            False,
            True,
            False,
            True,
            False,
        ]

    def test_a_word_split_over_three_lines_is_checked_as_one(self, monkeypatch):
        known = FakeChecker("przeczytać")
        monkeypatch.setattr(spelling, "get_checker", lambda: known)

        assert [w.correct for w in check_words("prze-\nczy-\ntać")] == [True] * 5

    def test_an_empty_line_is_not_a_line_break(self, checker):
        # a paragraph break: the two words are not one
        assert words("przeczy-\n\ntać") == [
            ("przeczy", False),
            ("-", True),
            ("tać", False),
        ]

    def test_a_space_after_the_hyphen_is_not_a_line_break(self, checker):
        assert words("przeczy- tać") == [
            ("przeczy", False),
            ("-", True),
            ("tać", False),
        ]

    def test_a_hyphen_that_is_not_right_after_the_word_is_not_a_split(self, checker):
        assert words("przeczy -\ntać") == [
            ("przeczy", False),
            ("-", True),
            ("tać", False),
        ]

    def test_a_hyphen_before_a_sign_is_not_a_split(self, checker):
        assert [w.correct for w in check_words("przeczy-\n,tać")] == [
            False,
            True,
            True,
            False,
        ]

    def test_a_word_before_a_hyphen_at_the_end_of_the_text_is_alone(self, checker):
        assert words("przeczy-") == [("przeczy", False), ("-", True)]

    def test_a_hyphen_line_between_two_words_that_are_right_alone(self, checker):
        assert [w.correct for w in check_words("ala-\nma")] == [True] * 3

    def test_each_distinct_whole_word_is_looked_up_once(self, checker):
        check_words("przeczy-\ntać przeczy-\ntać")

        assert checker.asked == ["przeczytać"]


class TestVerdicts:
    def test_unknown_words_are_wrong(self, fake):
        assert words("Ala jey") == [("Ala", True), ("jey", False)]

    def test_words_with_digits_are_not_checked_and_count_as_correct(self, fake):
        assert words("1840 l840 A4 10.") == [
            ("1840", True),
            ("l840", True),
            ("A4", True),
            ("10", True),
            (".", True),
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

    def test_words_split_over_lines_are_checked_as_whole_words(self):
        text = "Wielu Biskupów przeczy-\ntać dawnieyszych biało-\nczerwony"

        assert [w.text for w in check_words(text) if not w.correct] == ["dawnieyszych"]

    def test_a_split_word_that_is_wrong_joined_is_wrong(self):
        result = check_words("Biskup przeczy-\ntaćx")

        assert [w.text for w in result if not w.correct] == ["przeczy", "taćx"]

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
        assert len(result) == 40 * 8


def suggest(text, word, occurrence=0, legal=()):
    start = -1
    for _ in range(occurrence + 1):
        start = text.index(word, start + 1)
    return suggest_spelling(text, start, start + len(word), legal)


class TestSuggestSpelling:
    def test_a_word_in_the_middle_of_its_line_gets_the_closest_replacements(
        self, suggesting
    ):
        assert suggest("Ala ma kótka tu", "kótka") == ["kotka", "kotką", "krótka"]

    def test_at_most_three(self, suggesting):
        assert len(suggest("Ala ma kótka tu", "kótka")) == 3

    @pytest.mark.parametrize(
        "text",
        [
            "kótka ma tu",  # the first word of the line
            "Ala ma kótka",  # the last word of the line
            "kótka",  # the only word of the line
            "Ala ma tu\nkótka\nAla ma tu",  # alone on its line
            "Ala ma kótka\nAla ma tu",  # at the end of the line
            "Ala ma tu\nkótka ma tu",  # at the start of the line
        ],
    )
    def test_the_first_and_the_last_word_of_a_line_get_none(self, suggesting, text):
        assert suggest(text, "kótka") == []

    def test_a_sign_after_the_last_word_does_not_make_it_mid_line(self, suggesting):
        assert suggest("Ala ma kótka.", "kótka") == []
        assert suggest("Ala ma kótka, tu", "kótka") == ["kotka", "kotką", "krótka"]

    def test_a_word_split_over_two_lines_gets_none(self, suggesting):
        # "kó-" / "tka": the parts are the end and the start of a line
        text = "Ala ma kó-\ntka tu"
        assert suggest(text, "kó") == []
        assert suggest(text, "tka") == []

    def test_each_line_is_judged_on_its_own(self, suggesting):
        text = "Ala ma kótka tu\nAla kótka ma\nkótka ma tu"
        assert suggest(text, "kótka", 0) != []
        assert suggest(text, "kótka", 1) != []
        assert suggest(text, "kótka", 2) == []  # the first word of the third line

    def test_a_correct_word_gets_none(self, suggesting):
        assert suggest("Ala ma kotka tu", "kotka") == []

    def test_a_legal_spelling_of_the_edition_gets_none(self, suggesting):
        assert suggest("Ala ma kótka tu", "kótka", legal={"kótka"}) == []

    def test_only_single_words_that_differ_from_the_word(self, suggesting):
        # the dictionary offers "zle" itself and "z le" (two words) for "zle"
        assert suggest("Ala ma zle tu", "zle") == ["źle", "złe"]

    def test_nothing_to_suggest(self, suggesting):
        assert suggest("Ala ma xyzzy tu", "xyzzy") == []

    def test_offsets_that_are_not_one_word(self, suggesting):
        with pytest.raises(NotAWord):
            suggest_spelling("Ala ma kótka tu", 0, 6)
        with pytest.raises(NotAWord):
            suggest_spelling("Ala ma kótka tu", 3, 4)  # the middle of a word
        with pytest.raises(NotAWord):
            suggest_spelling("Ala, ma kótka", 3, 4)  # a sign, not a word

    def test_offsets_are_code_points(self, suggesting):
        assert suggest("😀 Ala ma kótka tu", "kótka") == ["kotka", "kotką", "krótka"]

    def test_no_dictionary(self, monkeypatch):
        def unavailable():
            raise SpellcheckUnavailable("no dictionary")

        monkeypatch.setattr(spelling, "get_checker", unavailable)

        with pytest.raises(SpellcheckUnavailable):
            suggest_spelling("Ala ma kótka tu", 7, 12)

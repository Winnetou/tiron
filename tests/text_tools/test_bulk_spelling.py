import pytest

from app.text_tools import spelling
from app.text_tools.bulk_spelling import (
    Family,
    find_inner_flagged_words,
    group_into_families,
    same_family,
)


class FakeChecker:
    def __init__(self, *known):
        self.known = {word.lower() for word in known}

    def lookup(self, word):
        return word.lower() in self.known


@pytest.fixture(autouse=True)
def dictionary(monkeypatch):
    checker = FakeChecker("ala", "ma", "kot", "i", "w")
    monkeypatch.setattr(spelling, "get_checker", lambda: checker)


def flagged(text, legal=()):
    return [m.text for m in find_inner_flagged_words(text, legal)]


class TestFindInnerFlaggedWords:
    def test_flagged_words_inside_a_line(self):
        assert flagged("Ala ma Konwencya i Policya w kot") == ["Konwencya", "Policya"]

    def test_not_the_first_and_the_last_word_of_a_line(self):
        assert flagged("Konwencya ma Policya\nala Tendencya ma\nTendencya") == [
            "Tendencya"
        ]

    def test_not_the_first_and_the_last_word_of_the_page(self):
        assert flagged("Xyz ala Konwencya ala Zyx") == ["Konwencya"]

    def test_signs_do_not_count_as_words(self):
        assert flagged("— Xyz ala Konwencya ala Zyx.") == ["Konwencya"]
        assert flagged("§ Xyz ala Konwencya ala Zyx,\n") == ["Konwencya"]

    def test_a_line_of_one_word_has_nothing_inside(self):
        assert flagged("Konwencya\nala Konwencya ala\nKonwencya") == ["Konwencya"]

    def test_a_word_cut_by_the_end_of_a_line_is_not_looked_at(self):
        assert flagged("ala ma kot Konwen-\ncya ala ma") == []

    def test_legal_spellings_count_as_correct(self):
        assert flagged("ala Konwencya ala Policya ala", {"Policya"}) == ["Konwencya"]

    def test_offsets_and_context(self):
        text = "ala ma Konwencya i kot"

        (match,) = find_inner_flagged_words(text)

        assert text[match.start : match.end] == "Konwencya"
        assert match.before == "ma"
        assert match.after == "i"


class TestSameFamily:
    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("Konwencya", "Konwencyi"),
            ("Konwencya", "konwencyami"),
            ("Konwencyą", "Konwencyę"),
        ],
    )
    def test_one_word_with_other_endings(self, a, b):
        assert same_family(a, b)

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("Konwencya", "Konstytucya"),
            ("dom", "domu"),  # a stem of less than four letters
            ("Konwencya", "Konwencyalnych"),  # more than four letters left over
        ],
    )
    def test_other_words(self, a, b):
        assert not same_family(a, b)


class TestGroupIntoFamilies:
    def test_the_families_and_their_words_are_the_most_common_first(self):
        counts = {
            "Konstytucya": 5,
            "Konwencyi": 4,
            "Konwencya": 9,
            "Konwencyami": 4,
        }

        assert group_into_families(counts) == [
            Family("konwency", 17, ["Konwencya", "Konwencyi", "Konwencyami"]),
            Family("konstytucya", 5, ["Konstytucya"]),
        ]

    def test_a_tie_is_left_in_the_order_given(self):
        assert [f.spellings for f in group_into_families({"Aaaaa": 4, "Bbbbb": 4})] == [
            ["Aaaaa"],
            ["Bbbbb"],
        ]

    def test_a_family_of_one_has_its_word_as_the_stem(self):
        assert group_into_families({"Policya": 4}) == [
            Family("policya", 4, ["Policya"])
        ]

    def test_nothing(self):
        assert group_into_families({}) == []

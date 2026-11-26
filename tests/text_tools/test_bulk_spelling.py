import pytest

from app.text_tools import spelling
from app.text_tools.bulk_spelling import (
    MORE_THAN,
    Family,
    find_inner_flagged_words,
    group_into_families,
    only_frequent_words_looked_up,
    same_family,
)


class FakeChecker:
    def __init__(self, *known):
        self.known = {word.lower() for word in known}
        self.lookups = []

    def lookup(self, word):
        self.lookups.append(word)
        return word.lower() in self.known


@pytest.fixture(autouse=True)
def dictionary(monkeypatch):
    checker = FakeChecker("ala", "ma", "kot", "i", "w")
    monkeypatch.setattr(spelling, "get_checker", lambda: checker)
    return checker


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


class TestVerdictSharedByTheTexts:
    PAGES = [
        "ala Konwencya ma kot i Policya ala",
        "ala Konwencya ma Policya kot Tendencya ala",
        "ala ma Konwencya kot ala",
    ]

    def test_every_distinct_word_is_looked_up_once_for_all_the_pages(self, dictionary):
        verdict = spelling.word_verdict()

        for page in self.PAGES:
            find_inner_flagged_words(page, verdict=verdict)

        assert sorted(dictionary.lookups) == sorted(set(dictionary.lookups))

    def test_each_page_has_its_own_lookups_without_it(self, dictionary):
        for page in self.PAGES:
            find_inner_flagged_words(page)

        assert dictionary.lookups.count("Konwencya") == 3

    def test_the_answers_are_the_same_with_it_or_without(self):
        verdict = spelling.word_verdict({"Policya"})

        for page in self.PAGES:
            assert [
                m.text for m in find_inner_flagged_words(page, {"Policya"}, (), verdict)
            ] == flagged(page, {"Policya"})

    def test_check_words_takes_it_too(self, dictionary):
        verdict = spelling.word_verdict()

        spelling.check_words("ala Konwencya", verdict=verdict)
        spelling.check_words("kot Konwencya", verdict=verdict)

        assert dictionary.lookups.count("Konwencya") == 1


class TestOnlyFrequentWordsLookedUp:
    # "Konwencya" is inside a line 4 times, "Policya" 2 times (and 2 more at the
    # edge of a line, which do not count), "Tendencya" once
    PAGES = [
        "ala Konwencya ma Policya kot\nPolicya ala Konwencya ma",
        "ala Konwencya ma Policya kot\nala Tendencya Konwencya ma Policya",
    ]

    def pages(self, ignore=()):
        return [(page, ignore, spelling.tokenize(page)) for page in self.PAGES]

    def counted(self, verdict):
        found = {}
        for page in self.PAGES:
            for match in find_inner_flagged_words(page, verdict=verdict):
                found[match.text] = found.get(match.text, 0) + 1
        return found

    def test_only_the_words_that_can_be_offered_are_looked_up(self, dictionary):
        verdict = only_frequent_words_looked_up(self.pages(), spelling.word_verdict())

        self.counted(verdict)

        assert {w for w in dictionary.lookups if w[0].isupper()} == {"Konwencya"}

    def test_every_word_that_is_offered_is_still_found_as_often(self):
        plain = self.counted(spelling.word_verdict())
        fast = self.counted(
            only_frequent_words_looked_up(self.pages(), spelling.word_verdict())
        )

        assert {w: n for w, n in plain.items() if n > MORE_THAN} == {
            w: n for w, n in fast.items() if n > MORE_THAN
        }
        assert plain["Konwencya"] == fast["Konwencya"] == 4

    def test_the_first_and_the_last_word_of_a_line_do_not_count(self, dictionary):
        # "Policya" is there 4 times, but 2 of them at the edge of a line: 2 count
        # (not more than 3), so it is not looked up
        verdict = only_frequent_words_looked_up(self.pages(), spelling.word_verdict())

        self.counted(verdict)

        assert "Policya" not in dictionary.lookups

    def test_words_in_an_ignored_stretch_do_not_count(self, dictionary):
        text = "ala Konwencya Konwencya Konwencya Konwencya ala"
        ignore = [(14, 23)]  # the second one

        # 4 inside the line, 3 without the ignored one: not more than 3, so not
        # looked up (it would be flagged if it was)
        page = (text, ignore, spelling.tokenize(text))
        verdict = only_frequent_words_looked_up([page], spelling.word_verdict())
        assert verdict("Konwencya") is True
        # two such pages: 6 > 3, so it is looked up, and flagged
        verdict = only_frequent_words_looked_up([page, page], spelling.word_verdict())
        assert verdict("Konwencya") is False

    def test_nothing(self):
        verdict = only_frequent_words_looked_up([], spelling.word_verdict())

        assert verdict("Konwencya") is True


class TestTokensGivenByTheCaller:
    TEXT = "ala Konwencya ma Policya kot i Konwencya w kot"

    def test_the_same_words_as_when_it_tokenizes_itself(self):
        tokens = spelling.tokenize(self.TEXT)

        given = find_inner_flagged_words(self.TEXT, tokens=tokens)

        assert given == find_inner_flagged_words(self.TEXT)
        assert [m.text for m in given] == ["Konwencya", "Policya", "Konwencya"]

    def test_the_text_is_not_tokenized_again(self, monkeypatch):
        tokens = spelling.tokenize(self.TEXT)

        def never(text):
            raise AssertionError("tokenized again")

        monkeypatch.setattr(spelling, "tokenize", never)
        monkeypatch.setattr("app.text_tools.bulk_spelling.tokenize", never)

        assert find_inner_flagged_words(self.TEXT, tokens=tokens)

    def test_check_words_takes_them_too(self):
        tokens = spelling.tokenize(self.TEXT)

        assert spelling.check_words(self.TEXT, tokens=tokens) == spelling.check_words(
            self.TEXT
        )


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

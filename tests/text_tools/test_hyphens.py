import pytest

from app.text_tools import spelling
from app.text_tools.hyphens import MissingHyphen, find_missing_hyphens


class FakeChecker:
    def __init__(self, *known):
        self.known = {word.lower() for word in known}

    def lookup(self, word):
        return word.lower() in self.known


@pytest.fixture(autouse=True)
def dictionary(monkeypatch):
    checker = FakeChecker(
        "przeczytać", "do", "mu", "domu", "ala", "ma", "kota", "prze", "czytał",
        "przeczytał", "to", "stoi", "tostoi",
    )  # fmt: skip
    monkeypatch.setattr(spelling, "get_checker", lambda: checker)


def found(text, legal=()):
    return [
        (m.text, m.suggestion, m.next_word) for m in find_missing_hyphens(text, legal)
    ]


class TestFindMissingHyphens:
    def test_a_word_split_without_a_hyphen(self):
        text = "Ala ma przeczy\ntać kota"

        assert find_missing_hyphens(text) == [
            MissingHyphen(
                start=7,
                end=14,
                text="przeczy",
                suggestion="przeczy-",
                next_word="tać",
                joined="przeczytać",
                ambiguous=False,
            )
        ]
        assert text[7:14] == "przeczy"

    def test_the_joined_word_is_the_two_parts_together(self):
        (match,) = find_missing_hyphens("Ala ma przeczy,\ntać")

        assert match.joined == "przeczytać"

    def test_the_hyphen_is_there_already(self):
        assert found("Ala ma przeczy-\ntać kota") == []

    def test_the_joined_word_must_be_correct(self):
        assert found("Ala ma kota\nnoc") == []
        assert found("Ala ma przeczx\ntać") == []

    def test_both_parts_correct_is_ambiguous(self):
        (match,) = find_missing_hyphens("Ala ma do\nmu")

        assert (match.text, match.ambiguous) == ("do", True)

    def test_a_part_that_is_not_a_word_is_not_ambiguous(self):
        (match,) = find_missing_hyphens("Ala ma przeczy\ntać")

        assert match.ambiguous is False

    @pytest.mark.parametrize("sign", [",", "'", "~", ".", ";;"])
    def test_a_sign_glued_to_the_word_is_taken_for_the_hyphen(self, sign):
        text = f"Ala ma przeczy{sign}\ntać"

        (match,) = find_missing_hyphens(text)

        assert (match.start, match.end) == (7, 14 + len(sign))
        assert match.text == f"przeczy{sign}"
        assert match.suggestion == "przeczy-"

    def test_a_longer_run_of_signs_is_not(self):
        assert found("Ala ma przeczy...\ntać") == []

    def test_a_dash_after_the_word_is_not_a_stray_sign(self):
        assert found("Ala ma przeczy—\ntać") == []

    def test_a_space_before_the_line_break_means_no_split(self):
        assert found("Ala ma przeczy \ntać") == []

    def test_an_empty_line_is_a_paragraph_break(self):
        assert found("Ala ma przeczy\n\ntać") == []

    def test_the_last_line_has_no_next_line(self):
        assert found("Ala ma przeczy") == []
        assert found("Ala ma przeczy,") == []

    def test_each_part_has_two_letters_at_least(self):
        assert found("Ala ma d\nomu") == []
        assert found("Ala ma dom\nu") == []

    def test_digits_are_not_parts(self):
        assert found("Ala 12\n3") == []

    def test_the_next_line_must_start_with_a_word(self):
        assert found("Ala ma przeczy\n(tać)") == []

    def test_two_correct_words_that_do_not_make_a_word_are_not_found(self):
        assert found("Ala ma kota\ndo") == []
        assert found("Ala to\nmu kota") == []

    def test_legal_spellings_count_as_correct(self):
        assert found("Ala ma dawnieyszych") == []
        assert found("Ala ma dawnie\nyszych") == []
        assert found("Ala ma dawnie\nyszych", {"dawnieyszych"}) == [
            ("dawnie", "dawnie-", "yszych")
        ]

    def test_several_on_a_page_in_order(self):
        text = "Ala ma przeczy\ntać kota i przeczy,\ntać"

        assert [m.start for m in find_missing_hyphens(text)] == [7, 26]

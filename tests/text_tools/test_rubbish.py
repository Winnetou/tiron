import pytest

from app.text_tools import spelling
from app.text_tools.rubbish import RubbishLine, find_rubbish_lines


class FakeChecker:
    def __init__(self, *known):
        self.known = {word.lower() for word in known}

    def lookup(self, word):
        return word.lower() in self.known


@pytest.fixture(autouse=True)
def dictionary(monkeypatch):
    checker = FakeChecker("szczerą", "niecierpliwością", "Zob", "str", "Mil", "ala")
    monkeypatch.setattr(spelling, "get_checker", lambda: checker)


def lines(text, legal=(), protected=()):
    return [(m.text, m.sure) for m in find_rubbish_lines(text, legal, protected)]


class TestFindRubbishLines:
    def test_a_line_of_signs_is_sure_rubbish(self):
        assert lines("Ala ma\n; . l-v ■ ■ :\nszczerą") == [("; . l-v ■ ■ :", True)]

    def test_the_text_of_the_example_of_the_user(self):
        text = "I *>• ' -V. ^oSkv. ;-'. i M , . ypton"

        assert lines(text) == [(text, True)]

    def test_offsets_are_those_of_the_line(self):
        text = "Ala szczerą\n; . l-v ■ ■ :\nszczerą"

        (found,) = find_rubbish_lines(text)

        assert text[found.start : found.end] == found.text
        assert (found.start, found.end) == (12, 25)

    def test_the_whole_line_is_found(self):
        assert find_rubbish_lines("***") == [RubbishLine(0, 3, "***", True)]

    @pytest.mark.parametrize("line", ["*", ".", "\\", "♦", "**", "' ", "i"])
    def test_a_line_shorter_than_three_characters_is_never_rubbish(self, line):
        assert lines(f"Ala\n{line}\nkota") == []
        assert lines(line) == []

    def test_three_characters_is_enough(self):
        assert lines("Ala\n;;;\nkota") == [(";;;", True)]
        assert lines("Ala\n;;\nkota") == []

    @pytest.mark.parametrize(
        "line",
        [
            "...",
            "....",
            "---",
            "-----",
            "???",
            "!!!",
            ". . .",
            "- -  -",
            "?? ?",
        ],
    )
    def test_several_of_one_of_the_signs_are_never_rubbish(self, line):
        assert lines(f"Ala\n{line}\nkota") == []

    @pytest.mark.parametrize("line", [".-.", "?!?", "-.-", "..-", "*.*", "***", "...;"])
    def test_a_mix_of_signs_is_not_covered_by_that(self, line):
        assert lines(f"Ala\n{line}\nkota") == [(line, True)]

    def test_the_signs_with_a_word_are_judged_as_before(self):
        assert lines("Ala!....") == [("Ala!....", False)]

    def test_white_space_at_the_ends_does_not_count(self):
        assert lines("Ala\n *  \nkota") == []

    def test_a_line_with_a_correct_word_is_not_sure(self):
        assert lines("fy-.i., si Mil ■ -uli! i v.v.'.") == [
            ("fy-.i., si Mil ■ -uli! i v.v.'.", False)
        ]

    def test_a_good_line_is_not_rubbish(self):
        assert lines("szczerą niecierpliwością wyglądałem owego dnia") == []

    def test_a_line_of_misspelled_words_is_not_rubbish(self):
        # signs do not outnumber the letters: the spell check is for that
        assert lines("IMIENINY.") == []
        assert lines("Wyszedłem nieprzyzwoicie, a potem") == []

    def test_the_first_token_of_a_good_line_is_not_a_line_of_its_own(self):
        assert lines("£r~*>e szczerą niecierpliwością wyglądałem") == []

    @pytest.mark.parametrize("line", ["0", "16", "7 (", "3*", "- 12 -"])
    def test_a_line_with_digits_and_no_letters_is_a_page_number(self, line):
        assert lines(f"Ala\n{line}\nAla") == []

    @pytest.mark.parametrize("line", ["I.", "II.", "XIV", "IV,", "MCM"])
    def test_a_roman_numeral_is_a_chapter(self, line):
        assert lines(f"{line}\n\nAla") == []

    def test_a_line_in_a_protected_section_is_left_alone(self):
        text = "Ala\n; . :\nAla"

        assert lines(text, protected=[(4, 9)]) == []
        assert lines(text, protected=[(0, 3)]) == [("; . :", True)]

    def test_a_stretch_that_only_touches_the_line_does_not_protect_it(self):
        text = "Ala\n; . :\nAla"

        assert lines(text, protected=[(0, 4)]) == [("; . :", True)]

    def test_legal_spellings_count_as_words(self):
        text = "(1) wsi ;;; ,,, ..."

        assert lines(text) == [(text, True)]
        assert lines(text, legal={"wsi"}) == [(text, False)]

    def test_empty_lines_and_an_empty_text(self):
        assert find_rubbish_lines("") == []
        assert lines("Ala\n\n; . :\n\nAla") == [("; . :", True)]

    def test_several_lines_in_order(self):
        assert [m.text for m in find_rubbish_lines(";;;\nAla\n:::\n")] == [";;;", ":::"]

    def test_every_line_of_a_page_of_signs(self):
        assert len(find_rubbish_lines("* * *\n; ; ;\n~ ~ ~")) == 3

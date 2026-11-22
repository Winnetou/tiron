import pytest

from app.text_tools.page_numbers import (
    PageNumberPattern,
    find_page_number,
    marked_page_number,
    pattern_of,
)
from app.text_tools.tei_xml import mark_section, text_to_tei_xml


class TestPatternOf:
    def test_a_number_alone_on_the_first_line(self):
        assert pattern_of("315\n\nbłędy zbyt", 0, 3) == PageNumberPattern(
            where="first",
            dashes_before=False,
            dashes_after=False,
            marks_dashes_before=False,
            marks_dashes_after=False,
        )

    def test_a_number_alone_on_the_last_line(self):
        assert pattern_of("Ala ma kota\n\n315", 13, 16).where == "last"

    def test_dashes_around_the_number_are_part_of_the_pattern(self):
        pattern = pattern_of("- 315 -\nAla", 2, 5)

        assert (pattern.dashes_before, pattern.dashes_after) == (True, True)

    def test_the_dashes_are_marked_if_the_example_was_marked_with_them(self):
        with_dashes = pattern_of("- 315 -\nAla", 0, 7)
        digits_only = pattern_of("- 315 -\nAla", 2, 5)

        assert (with_dashes.marks_dashes_before, with_dashes.marks_dashes_after) == (
            True,
            True,
        )
        assert (digits_only.marks_dashes_before, digits_only.marks_dashes_after) == (
            False,
            False,
        )

    @pytest.mark.parametrize("line", ["— 12 —", "-12-", "–12–", "-- 12 --", "- 12"])
    def test_any_dashes_with_or_without_spaces(self, line):
        number = line.index("12")

        assert pattern_of(f"{line}\nAla", number, number + 2).where == "first"

    def test_a_number_in_the_middle_of_the_page_is_not_a_pattern(self):
        with pytest.raises(ValueError, match="first or the last line"):
            pattern_of("Ala\n12\nma", 4, 6)

    def test_a_number_with_words_on_its_line_is_not_a_pattern(self):
        with pytest.raises(ValueError, match="alone"):
            pattern_of("84 KATEDRA\nAla", 0, 2)

    def test_a_line_that_is_not_a_number_is_not_a_pattern(self):
        with pytest.raises(ValueError, match="alone"):
            pattern_of("KATEDRA\nAla", 0, 7)

    def test_five_digits_are_not_a_page_number(self):
        with pytest.raises(ValueError, match="alone"):
            pattern_of("33378\nAla", 0, 5)


class TestFindPageNumber:
    PLAIN = pattern_of("315\n\nbłędy", 0, 3)
    DASHED = pattern_of("- 315 -\nAla", 0, 7)
    DASHED_DIGITS = pattern_of("- 315 -\nAla", 2, 5)
    LAST = pattern_of("Ala\n\n- 5 -", 7, 8)

    def test_the_first_line_that_is_a_number(self):
        found = find_page_number("316\n\nAla ma kota", self.PLAIN)

        assert (found.start, found.end, found.text, found.number) == (0, 3, "316", 316)

    def test_the_offsets_point_at_the_text(self):
        text = "- 16 -\n\nAla"

        found = find_page_number(text, self.DASHED)

        assert text[found.start : found.end] == found.text == "- 16 -"

    def test_dashes_of_any_kind_when_the_pattern_has_dashes(self):
        for line in ["— 316 —", "-316-", "– 316 –", "-- 316 --"]:
            assert find_page_number(f"{line}\nAla", self.DASHED).number == 316

    def test_dashes_are_not_found_when_the_pattern_has_none(self):
        assert find_page_number("- 316 -\nAla", self.PLAIN) is None

    def test_no_dashes_are_not_found_when_the_pattern_has_them(self):
        assert find_page_number("316\nAla", self.DASHED) is None

    def test_a_pattern_marked_without_the_dashes_marks_the_digits_only(self):
        text = "- 316 -\nAla"

        found = find_page_number(text, self.DASHED_DIGITS)

        assert found.text == "316"
        assert text[found.start : found.end] == "316"

    def test_the_line_must_be_the_number_and_nothing_else(self):
        assert find_page_number("12 Rozdział pierwszy\nAla", self.PLAIN) is None
        assert find_page_number("Rozdział 12\nAla", self.PLAIN) is None
        assert find_page_number("1 2\nAla", self.PLAIN) is None

    def test_a_page_that_starts_with_something_else(self):
        assert find_page_number("TREŚĆ\n\n12", self.PLAIN) is None

    def test_a_page_without_text(self):
        assert find_page_number("", self.PLAIN) is None

    def test_the_last_line_when_the_pattern_is_for_the_last_line(self):
        text = "Ala ma\nkota\n\n— 6 —"

        found = find_page_number(text, self.LAST)

        assert (found.number, text[found.start : found.end]) == (6, "6")

    def test_the_last_line_is_not_looked_at_for_a_first_line_pattern(self):
        assert find_page_number("Ala\n\n316", self.PLAIN) is None

    def test_the_context_is_the_next_line_that_has_something_in_it(self):
        found = find_page_number("316\n\nAla ma kota\nA kot ma Ale", self.PLAIN)

        assert found.context == "Ala ma kota"

    def test_the_context_for_the_last_line_is_the_one_before(self):
        found = find_page_number("Ala ma kota\nA kot\n\n- 6 -", self.LAST)

        assert found.context == "A kot"

    def test_the_context_is_short(self):
        found = find_page_number("316\n" + "a" * 200, self.PLAIN)

        assert len(found.context) == 60

    def test_a_page_with_only_the_number_has_no_context(self):
        assert find_page_number("316", self.PLAIN).context == ""

    def test_counts_code_points_after_emoji(self):
        text = "- 16 -\n😀"

        found = find_page_number(text, self.DASHED)

        assert (found.start, found.end) == (0, 6)


class TestMarkedPageNumber:
    def marked(self, text, start, end):
        xml = str(text_to_tei_xml(text))
        return mark_section(xml, start, end, "page_number", text[start:end])

    def test_the_first_marked_page_number_with_the_text_of_the_page(self):
        xml = self.marked("12\n\nAla ma kota", 0, 2)

        assert marked_page_number(xml) == ("12\n\nAla ma kota", 0, 2)

    def test_none_when_no_page_number_is_marked(self):
        assert marked_page_number(str(text_to_tei_xml("12\n\nAla"))) is None

    def test_other_sections_are_not_page_numbers(self):
        xml = str(text_to_tei_xml("84 KATEDRA\n\nAla"))
        xml = mark_section(xml, 0, 10, "header", "84 KATEDRA")

        assert marked_page_number(xml) is None

import pytest

from app.text_tools import spelling
from app.text_tools.spelling_chunks import (
    MOST_ENDINGS,
    MOST_LENGTH,
    ChunkMatch,
    InvalidChunk,
    chunk_substrings,
    find_chunk_matches,
)


class FakeChecker:
    def __init__(self, *known):
        self.known = {word.lower() for word in known}

    def lookup(self, word):
        return word.lower() in self.known


@pytest.fixture(autouse=True)
def dictionary(monkeypatch):
    checker = FakeChecker("ala", "ma", "kot", "konwencja", "dobra", "cyna")
    monkeypatch.setattr(spelling, "get_checker", lambda: checker)


class TestChunkSubstrings:
    def test_the_core_with_each_ending(self):
        assert chunk_substrings("cy", ["a", "i", "ami"]) == ["cya", "cyi", "cyami"]

    def test_one_ending(self):
        assert chunk_substrings("cy", ["a"]) == ["cya"]

    def test_an_empty_ending_is_the_core_alone(self):
        assert chunk_substrings("cy", ["", "a"]) == ["cy", "cya"]

    def test_spaces_do_not_count(self):
        assert chunk_substrings(" cy ", [" a ", "i "]) == ["cya", "cyi"]

    def test_the_same_ending_twice_counts_once(self):
        assert chunk_substrings("cy", ["a", "a", " a"]) == ["cya"]

    def test_letters_with_a_hyphen_or_an_apostrophe_inside(self):
        assert chunk_substrings("ó-j", ["ę"]) == ["ó-ję"]
        assert chunk_substrings("a'b", ["c"]) == ["a'bc"]

    def test_the_core_must_not_be_empty(self):
        with pytest.raises(InvalidChunk, match="core"):
            chunk_substrings("  ", ["a"])

    def test_there_must_be_an_ending(self):
        with pytest.raises(InvalidChunk, match="at least one ending"):
            chunk_substrings("cy", [])

    def test_not_too_many_endings(self):
        with pytest.raises(InvalidChunk, match="at most"):
            chunk_substrings("cy", [str(n) for n in range(MOST_ENDINGS + 1)])

    @pytest.mark.parametrize("part", ["a" * (MOST_LENGTH + 1), "c y", "c1", "c+", "-c"])
    def test_a_part_that_is_too_long_or_not_letters(self, part):
        with pytest.raises(InvalidChunk):
            chunk_substrings(part, ["a"])
        with pytest.raises(InvalidChunk):
            chunk_substrings("cy", [part])


def found(text, substrings, legal=()):
    return find_chunk_matches(text, substrings, legal)


class TestFindChunkMatches:
    def test_the_flagged_words_that_have_the_substring_in_them(self):
        text = "Ala ma Konwencya, tendencyalny kot."

        assert [m.text for m in found(text, ["cya"])] == ["Konwencya", "tendencyalny"]

    def test_anywhere_in_the_word(self):
        assert [m.text for m in found("xcyax cyax xcya cya", ["cya"])] == [
            "xcyax",
            "cyax",
            "xcya",
            "cya",
        ]

    def test_any_of_the_substrings(self):
        text = "Konwencya Konwencyi Konwencyami Konwencyo"

        assert [m.text for m in found(text, ["cya", "cyi", "cyami"])] == [
            "Konwencya",
            "Konwencyi",
            "Konwencyami",
        ]

    def test_the_case_of_the_letters_does_not_matter(self):
        assert [m.text for m in found("KONWENCYA Konwencya", ["cya"])] == [
            "KONWENCYA",
            "Konwencya",
        ]

    def test_a_correct_word_is_not_listed(self):
        assert found("Konwencja cyna", ["cyn", "cj"]) == []

    def test_a_legal_spelling_is_not_listed(self):
        assert found("Konwencya", ["cya"], legal={"Konwencya"}) == []

    def test_the_offsets_are_those_of_the_word(self):
        text = "Ala ma Konwencya tu"

        (match,) = found(text, ["cya"])

        assert text[match.start : match.end] == "Konwencya"

    def test_the_context_is_the_token_before_and_after(self):
        assert found("Ala, Konwencya.", ["cya"]) == [
            ChunkMatch(start=5, end=14, text="Konwencya", before=",", after=".")
        ]

    def test_at_the_ends_of_the_text_there_is_no_context(self):
        (match,) = found("Konwencya", ["cya"])

        assert (match.before, match.after) == ("", "")

    def test_signs_are_not_words(self):
        assert found("cya... ;;", ["cya"]) != []
        assert [m.text for m in found("... cya", ["."])] == []

    def test_a_word_split_over_two_lines_is_judged_as_one_word(self):
        # "konwen-" / "cja" is "konwencja", a correct word: not flagged
        assert found("Ala konwen-\ncja ma", ["cja", "konwen"]) == []

    def test_an_empty_substring_matches_nothing(self):
        assert found("Konwencya", [""]) == []

    def test_no_text_no_matches(self):
        assert found("", ["cya"]) == []

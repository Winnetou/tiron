import pytest

from app.text_tools.token_occurrences import (
    InvalidToken,
    TokenOccurrence,
    check_token,
    find_token,
)


class TestCheckToken:
    @pytest.mark.parametrize(
        "token",
        ["tern", "Tern", "biało-czerwony", "o'Brien", "(|}", "...", "tćm", "1840"],
    )
    def test_a_single_token(self, token):
        assert check_token(token) == token

    @pytest.mark.parametrize(
        "token", ["", " ", " tern", "tern ", "dwa słowa", "tern,", "(tern", "a\nb"]
    )
    def test_not_a_single_token(self, token):
        with pytest.raises(InvalidToken):
            check_token(token)

    def test_the_reason_is_given(self):
        with pytest.raises(InvalidToken, match="not a single token"):
            check_token("tern,")
        with pytest.raises(InvalidToken, match="empty"):
            check_token("")


class TestFindToken:
    def test_every_place_with_its_context(self):
        text = "W tern dniu, tern raz."

        assert find_token(text, "tern") == [
            TokenOccurrence(start=2, end=6, text="tern", before="W", after="dniu"),
            TokenOccurrence(start=13, end=17, text="tern", before="dniu,", after="raz"),
        ]

    def test_the_offsets_are_those_of_the_token(self):
        text = "Ala tern ma"

        (place,) = find_token(text, "tern")

        assert text[place.start : place.end] == "tern"

    def test_whole_tokens_only(self):
        assert find_token("terne intern ternu tern-a", "tern") == []

    def test_the_case_counts(self):
        assert [p.text for p in find_token("tern Tern TERN", "tern")] == ["tern"]

    def test_a_word_with_a_hyphen_is_one_token(self):
        text = "biało-czerwony i biało"

        assert [p.text for p in find_token(text, "biało-czerwony")] == [
            "biało-czerwony"
        ]
        assert find_token(text, "biało")[0].start == 17

    def test_a_sign_before_it_does_not_leave_the_context_without_a_word(self):
        (place,) = find_token("Ala ma dom. tern kota", "tern")

        assert (place.before, place.after) == ("dom.", "kota")

    def test_a_sign_after_it_is_in_the_context_with_the_next_word(self):
        (place,) = find_token("Ala tern, ma kota", "tern")

        assert (place.before, place.after) == ("Ala", ", ma")

    def test_signs_on_both_sides_are_kept_as_they_are_written(self):
        (place,) = find_token("Jak (tern) ma — kota", "tern")

        assert (place.before, place.after) == ("Jak (", ") ma")

    def test_several_signs_between_the_word_and_the_token(self):
        (place,) = find_token("To jest... — tern - kota", "tern")

        assert (place.before, place.after) == ("jest... —", "- kota")

    def test_a_line_break_is_a_space_in_the_context(self):
        (place,) = find_token("Ala ma.\ntern\n\nkota", "tern")

        assert (place.before, place.after) == ("ma.", "kota")

    def test_only_signs_when_there_is_no_word_beyond_them(self):
        assert [(p.before, p.after) for p in find_token("...tern...", "tern")] == [
            ("...", "...")
        ]
        assert [(p.before, p.after) for p in find_token("? tern", "tern")] == [
            ("?", "")
        ]

    def test_a_number_is_a_word_for_the_context(self):
        (place,) = find_token("W 1840. tern 12 razy", "tern")

        assert (place.before, place.after) == ("1840.", "12")

    def test_the_context_of_a_run_of_signs_is_that_of_a_word(self):
        (place,) = find_token("Ala. (|} ma", "(|}")

        assert (place.before, place.after) == ("Ala.", "ma")

    def test_a_run_of_signs_is_a_token(self):
        (place,) = find_token("Ala (|} ma", "(|}")

        assert (place.before, place.after) == ("Ala", "ma")

    def test_found_whether_the_dictionary_knows_it_or_not(self):
        # no dictionary is used: "tern" is not looked up
        assert len(find_token("tern tem", "tern")) == 1
        assert len(find_token("tern tem", "tem")) == 1

    def test_across_a_line_break(self):
        (place,) = find_token("Ala\ntern\nma", "tern")

        assert (place.before, place.after) == ("Ala", "ma")

    def test_at_the_ends_of_the_text_there_is_no_context(self):
        (place,) = find_token("tern", "tern")

        assert (place.before, place.after) == ("", "")

    def test_no_text_no_place(self):
        assert find_token("", "tern") == []

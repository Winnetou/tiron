import pytest

from app.text_tools.misspellings import (
    ClusterFix,
    cluster_fix,
    tokens_with_cluster,
    wrong_cluster,
)


class TestClusterFix:
    @pytest.mark.parametrize(
        ("incorrect", "corrected", "wrong", "right"),
        [
            ("samćj", "saméj", "ćj", "éj"),
            ("jćj", "jéj", "ćj", "éj"),
            ("tćgo", "tégo", "ćgo", "égo"),
            ("dawnieyszych", "dawniejszych", "yszych", "jszych"),
            ("Samćj", "Saméj", "ćj", "éj"),
            ("kot", "ko", "ot", "o"),  # a letter is gone
        ],
    )
    def test_from_the_first_wrong_letter_to_the_end_of_the_word(
        self, incorrect, corrected, wrong, right
    ):
        assert cluster_fix(incorrect, corrected) == ClusterFix(wrong, right)

    def test_the_letters_before_the_mistake_are_not_part_of_it(self):
        assert cluster_fix("samćj", "saméj") == cluster_fix("jćj", "jéj")

    def test_a_single_wrong_letter_at_the_end_is_taken_with_the_one_before(self):
        assert cluster_fix("kotć", "kotę") == ClusterFix("tć", "tę")

    def test_a_single_wrong_letter_in_a_word_of_one_letter_is_the_word(self):
        assert cluster_fix("ć", "é") == ClusterFix("ć", "é")

    def test_letters_that_are_missing_make_it_a_fix_of_the_whole_word(self):
        assert cluster_fix("kot", "kota") == ClusterFix("kot", "kota", whole_word=True)

    def test_the_same_spelling_is_refused(self):
        with pytest.raises(ValueError, match="same"):
            cluster_fix("kot", "kot")

    def test_an_empty_incorrect_spelling_is_refused(self):
        with pytest.raises(ValueError, match="empty"):
            cluster_fix("", "kot")

    def test_wrong_cluster_is_the_wrong_part(self):
        assert wrong_cluster("samćj", "saméj") == "ćj"


FIX = ClusterFix("ćj", "éj")


class TestTokensWithCluster:
    TEXT = "Samćj i jćj, ćgo oraz ma Ćj kota."

    def test_every_token_that_has_the_cluster_with_its_offsets_and_suggestion(self):
        assert tokens_with_cluster(self.TEXT, FIX) == [
            ("Samćj", 0, 5, "Saméj"),
            ("jćj", 8, 11, "jéj"),
            ("Ćj", 25, 27, "Éj"),
        ]

    def test_the_offsets_point_at_the_tokens(self):
        for token, start, end, _ in tokens_with_cluster(self.TEXT, FIX):
            assert self.TEXT[start:end] == token

    def test_case_does_not_matter(self):
        assert [t for t, *_ in tokens_with_cluster("JĆJ jćj", FIX)] == ["JĆJ", "jćj"]

    def test_the_suggestion_keeps_the_capital_letters(self):
        found = tokens_with_cluster("JĆJ Ćjx jćj", FIX)

        assert [s for *_, s in found] == ["JÉJ", "Éjx", "jéj"]

    def test_the_cluster_may_be_inside_the_token(self):
        assert tokens_with_cluster("xćjx", FIX) == [("xćjx", 0, 4, "xéjx")]

    def test_every_occurrence_in_a_token_is_corrected(self):
        assert tokens_with_cluster("ćjćj", FIX) == [("ćjćj", 0, 4, "éjéj")]

    def test_another_cluster_is_not_found(self):
        assert tokens_with_cluster(self.TEXT, ClusterFix("ćgo", "égo")) == [
            ("ćgo", 13, 16, "égo")
        ]

    def test_signs_are_not_tokens_to_find(self):
        assert tokens_with_cluster("ćj ... ,", ClusterFix("..", "xx")) == []

    def test_no_match_is_an_empty_list(self):
        assert tokens_with_cluster("Ala ma kota", FIX) == []

    def test_counts_code_points_after_emoji(self):
        assert tokens_with_cluster("😀 jćj", FIX) == [("jćj", 2, 5, "jéj")]

    def test_a_fix_of_a_whole_word_finds_only_that_word(self):
        fix = ClusterFix("kot", "kota", whole_word=True)

        found = tokens_with_cluster("kot Kot kotek", fix)

        assert found == [("kot", 0, 3, "kota"), ("Kot", 4, 7, "Kota")]

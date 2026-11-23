import pytest

from app.text_tools.inflection import (
    PhraseMatch,
    find_forms,
    forms_of_phrase,
    forms_of_word,
)


def texts(word, label=""):
    return [form.text for form in forms_of_word(word, label)]


def cases(word, form, label=""):
    return next(f.cases for f in forms_of_word(word, label) if f.text == form)


class TestSurnamesInSki:
    @pytest.mark.parametrize(
        "word",
        ["Machnicki", "Machnickiego", "Machnickiemu", "Machnickim", "Machnicka"],
    )
    def test_the_same_forms_from_any_of_them(self, word):
        assert set(texts(word, "persName")) >= {
            "Machnicki",
            "Machnickiego",
            "Machnickiemu",
            "Machnickim",
            "Machnickich",
            "Machnicka",
            "Machnickiej",
            "Machnicką",
            "Machniccy",
        }

    def test_the_word_is_first(self):
        assert texts("Machnickiego", "persName")[0] == "Machnickiego"

    def test_the_cases_of_the_forms(self):
        assert cases("Machnicki", "Machnickiego", "persName") == {"gen.sg", "acc.sg"}
        assert cases("Machnicki", "Machnickiemu", "persName") == {"dat.sg"}

    def test_the_old_spelling(self):
        assert "Machnickiéj" in texts("Machnicki", "persName")
        assert "Machnickiej" in texts("Machnickiéj", "persName")

    def test_it_is_the_only_family(self):
        # "Machnickiego" is not a masculine noun "Machnickiego"
        assert not any(t.startswith("Machnickiegoa") for t in texts("Machnickiego"))
        assert len(texts("Machnickiego", "persName")) < 15

    @pytest.mark.parametrize("word", ["Polski", "Wielki", "Drogi"])
    def test_other_adjectives_in_ki_and_gi(self, word):
        assert len(texts(word, "persName")) > 8

    def test_the_plural_of_the_men(self):
        assert "Polscy" in texts("Polski", "persName")
        assert "Drodzy" in texts("Drogi", "persName")


class TestPersonalNouns:
    def test_a_masculine_first_name(self):
        assert set(texts("Jan", "persName")) >= {
            "Jan",
            "Jana",
            "Janowi",
            "Janem",
            "Janie",
        }

    def test_after_k_the_instrumental_is_iem(self):
        forms = texts("Marek", "persName")

        assert {"Marek", "Marka", "Markowi", "Markiem", "Marku"} <= set(forms)
        assert "Markem" not in forms

    def test_ec_loses_the_e(self):
        assert {"Chmielec", "Chmielca", "Chmielcowi", "Chmielcem"} <= set(
            texts("Chmielec", "persName")
        )

    def test_a_feminine_first_name(self):
        assert {"Anna", "Anny", "Annie", "Annę", "Anną"} <= set(
            texts("Anna", "persName")
        )

    def test_a_hard_adjective(self):
        assert {"Biały", "Białego", "Białemu", "Białym"} <= set(
            texts("Biały", "persName")
        )

    def test_a_word_without_a_capital_stays_as_it_is(self):
        assert texts("de", "persName") == ["de"]

    def test_a_word_with_other_signs_stays_as_it_is(self):
        assert texts("A1", "persName") == ["A1"]


class TestPlaces:
    def test_a_masculine_place_with_o_acute(self):
        assert {"Kraków", "Krakowa", "Krakowie", "Krakowem", "Krakowowi"} <= set(
            texts("Kraków", "placeName")
        )

    def test_from_an_oblique_form_to_the_nominative(self):
        assert {"Kraków", "Krakowa", "Krakowie"} <= set(texts("Krakowa", "placeName"))
        assert "Kraków" in texts("Krakowie", "placeName")

    def test_a_place_in_n_acute(self):
        assert {"Poznań", "Poznania", "Poznaniu", "Poznaniem"} <= set(
            texts("Poznań", "placeName")
        )
        assert "Poznań" in texts("Poznaniu", "placeName")

    def test_a_neuter_place(self):
        assert {"Zamczysko", "Zamczyska", "Zamczysku", "Zamczyskiem"} <= set(
            texts("Zamczysko", "placeName")
        )

    def test_a_feminine_place_softens_before_ie(self):
        assert {"Warszawa", "Warszawy", "Warszawie", "Warszawę", "Warszawą"} <= set(
            texts("Warszawa", "placeName")
        )
        assert {"Polska", "Polsce", "Polski", "Polskę", "Polską"} <= set(
            texts("Polska", "placeName")
        )
        assert "Polska" in texts("Polsce", "placeName")

    def test_a_place_is_not_a_surname_in_ski(self):
        assert "Zamczyskiego" not in texts("Zamczyska", "placeName")

    def test_the_old_spelling(self):
        assert "Krakowém" in texts("Kraków", "placeName")


class TestPhrases:
    def test_the_words_are_in_the_same_case(self):
        forms = [f.text for f in forms_of_phrase("Jan Machnicki", "persName")]

        assert forms[0] == "Jan Machnicki"
        assert "Jana Machnickiego" in forms
        assert "Janowi Machnickiemu" in forms
        assert "Jana Machnickiemu" not in forms  # genitive and dative

    def test_a_word_without_a_capital_stays(self):
        forms = [f.text for f in forms_of_phrase("Jan de Machnicki", "persName")]

        assert "Jana de Machnickiego" in forms

    def test_one_word_is_the_word(self):
        assert forms_of_phrase("Machnicki", "persName") == forms_of_word(
            "Machnicki", "persName"
        )

    def test_nothing_for_nothing(self):
        assert forms_of_phrase("") == []
        assert forms_of_phrase("   ") == []

    def test_the_forms_are_not_endless(self):
        assert (
            len(forms_of_phrase("Jan Anna Marek Machnicki Kowalski", "persName")) <= 500
        )


class TestFindForms:
    def test_whole_tokens_with_their_surroundings(self):
        text = "Rozmawiałem z panem Machnickiego wczoraj."

        assert find_forms(text, {"Machnickiego"}) == [
            PhraseMatch(20, 32, "Machnickiego", "panem", "wczoraj")
        ]
        assert text[20:32] == "Machnickiego"

    def test_not_a_part_of_a_word(self):
        assert (
            find_forms("Machnickiegoż i XMachnicki", {"Machnickiego", "Machnicki"})
            == []
        )

    def test_case_matters(self):
        assert find_forms("machnicki", {"Machnicki"}) == []

    def test_the_context_is_the_nearest_word_before_and_after(self):
        (match,) = find_forms("Ala ma Machnicki tu", {"Machnicki"})

        assert (match.before, match.after) == ("ma", "tu")

    def test_a_sign_next_to_it_does_not_leave_the_context_without_a_word(self):
        (match,) = find_forms("Ala ma dom. Machnicki; kota", {"Machnicki"})

        assert (match.before, match.after) == ("dom.", "; kota")
        (match,) = find_forms("Ala, Machnicki.", {"Machnicki"})
        assert (match.before, match.after) == ("Ala,", ".")

    def test_the_context_of_a_name_of_several_words_is_outside_the_whole_name(self):
        (match,) = find_forms("Pan Jan\nMachnicki, jest tu", {"Jan Machnicki"})

        assert (match.before, match.after) == ("Pan", ", jest")

    def test_signs_on_both_sides_are_kept_as_they_are_written(self):
        (match,) = find_forms("Jak (Machnicki) ma — kota", {"Machnicki"})

        assert (match.before, match.after) == ("Jak (", ") ma")

    def test_at_the_ends_of_the_text_there_is_no_context(self):
        (match,) = find_forms("Machnicki", {"Machnicki"})

        assert (match.before, match.after) == ("", "")

    def test_words_that_are_next_to_each_other(self):
        text = "Był Jan Machnicki, a potem Jana\nMachnickiego."

        found = find_forms(text, {"Jan Machnicki", "Jana Machnickiego"})

        assert [m.text for m in found] == ["Jan Machnicki", "Jana\nMachnickiego"]

    def test_not_words_that_are_apart(self):
        assert find_forms("Jan i Machnicki", {"Jan Machnicki"}) == []
        assert find_forms("Jan  Machnicki", {"Jan Machnicki"}) == []
        assert find_forms("Jan\n\nMachnicki", {"Jan Machnicki"}) == []

    def test_the_longest_wins_where_they_overlap(self):
        found = find_forms("Jan Machnicki", {"Jan Machnicki", "Machnicki", "Jan"})

        assert [m.text for m in found] == ["Jan Machnicki"]

    def test_a_name_with_a_hyphen_is_one_token(self):
        assert [
            m.text for m in find_forms("Nowak-Jeziorański", {"Nowak-Jeziorański"})
        ] == ["Nowak-Jeziorański"]

    def test_nothing_is_found_without_forms(self):
        assert find_forms("Ala ma kota", set()) == []

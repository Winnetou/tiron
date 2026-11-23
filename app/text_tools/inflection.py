"""The forms of a Polish name in the cases, and finding them in a text.

A name that is marked as a named entity ("Machnicki") appears in the rest of the
edition in other cases: "Machnickiego", "Machnickiemu", "Machnickim", the feminine
"Machnicka", "Machnickiej", and for a place "Kraków", "Krakowa", "Krakowie". The
spaCy model tags them well, but its lemmas of names it does not know are not
usable (it makes "machnickiego" of "Machnickiego"), so the forms are made here, by
rules, from the word that the user marked.

The word is taken apart by its ending, for every family of endings it fits
(a surname in -ski/-cki/-dzki, an adjective in -y, a masculine noun, a feminine in
-a, a neuter in -o, a masculine in -ń, -ek, -ec), and the forms of each family are
made from the stem. A word can fit several families ("Anna" is a feminine nominative
and a masculine genitive): all of them are used, as a wrong form is harmless, it
is just not in the text. Which forms were made is shown to the human, who can drop
one. The spelling of the 19th century is covered: "-éj" for "-ej", "-ém" for "-em".

A name of several words ("Jan Machnicki") is made of the forms of each word, in
the same case. A word that does not start with a capital ("de") stays as it is.
It is a rule-based guess for the common patterns, not a morphological analyser:
what it does not know, it does not find.
"""

import itertools
import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.text_tools.spelling import tokenize
from app.text_tools.token_occurrences import context_after, context_before

# The most forms of a name of several words that are made (the product of the
# forms of its words, in the same case).
_MOST_FORMS = 500
_LEAST_STEM = 3
_VOWELS = "aeiouyąęó"

Cases = frozenset[str]


@dataclass(frozen=True)
class Form:
    """A form of a name: its `text`, and the `cases` it can be in ("gen.sg",
    "dat.pl", ...)."""

    text: str
    cases: Cases


def _cases(*names: str) -> Cases:
    return frozenset(names)


def _is_consonant_final(stem: str) -> bool:
    return bool(stem) and stem[-1].lower() not in _VOWELS


Ending = tuple[str, Cases]

# The families of endings. Each is (ending, cases); the forms are the stem and an
# ending. Only the singular is made for nouns (a name is rarely in the plural), and
# the plural too for a surname in -ski, which is.
_ADJECTIVE_SOFT: list[Ending] = [  # -ski, -cki, -dzki, -ki, -gi: "Machnicki"
    ("i", _cases("nom.sg")),
    ("iego", _cases("gen.sg", "acc.sg")),
    ("iemu", _cases("dat.sg")),
    ("im", _cases("ins.sg", "loc.sg", "dat.pl")),
    ("ich", _cases("gen.pl", "loc.pl", "acc.pl")),
    ("imi", _cases("ins.pl")),
    ("a", _cases("nom.sg")),  # the feminine
    ("iej", _cases("gen.sg", "dat.sg", "loc.sg")),
    ("ą", _cases("acc.sg", "ins.sg")),
]
_ADJECTIVE_HARD: list[Ending] = [  # -y: "Biały"
    ("y", _cases("nom.sg")),
    ("ego", _cases("gen.sg", "acc.sg")),
    ("emu", _cases("dat.sg")),
    ("ym", _cases("ins.sg", "loc.sg", "dat.pl")),
    ("ych", _cases("gen.pl", "loc.pl", "acc.pl")),
    ("ymi", _cases("ins.pl")),
]
_MASCULINE: list[Ending] = [  # "Jan", "Kraków"
    ("", _cases("nom.sg", "acc.sg")),
    ("a", _cases("gen.sg", "acc.sg")),
    ("owi", _cases("dat.sg")),
    ("em", _cases("ins.sg")),
    ("ie", _cases("loc.sg", "voc.sg")),
    ("u", _cases("loc.sg", "voc.sg", "dat.sg")),
]
_MASCULINE_EK: list[Ending] = [  # "Marek": the e is dropped: "Marka"
    ("ek", _cases("nom.sg", "acc.sg")),
    ("ka", _cases("gen.sg", "acc.sg")),
    ("kowi", _cases("dat.sg")),
    ("kiem", _cases("ins.sg")),
    ("ku", _cases("loc.sg", "voc.sg", "dat.sg")),
]
_MASCULINE_EC: list[Ending] = [  # "Chmielec": "Chmielca"
    ("ec", _cases("nom.sg", "acc.sg")),
    ("ca", _cases("gen.sg", "acc.sg")),
    ("cowi", _cases("dat.sg")),
    ("cem", _cases("ins.sg")),
    ("cu", _cases("loc.sg", "voc.sg", "dat.sg")),
]
_MASCULINE_SOFT_N: list[Ending] = [  # "Poznań": "Poznania", "Poznaniu"
    ("ń", _cases("nom.sg", "acc.sg")),
    ("nia", _cases("gen.sg", "acc.sg")),
    ("niowi", _cases("dat.sg")),
    ("niem", _cases("ins.sg")),
    ("niu", _cases("loc.sg", "voc.sg", "dat.sg")),
]
_NEUTER: list[Ending] = [  # "Zamczysko"
    ("o", _cases("nom.sg", "acc.sg")),
    ("a", _cases("gen.sg")),
    ("u", _cases("dat.sg", "loc.sg")),
    ("em", _cases("ins.sg")),
]
_FEMININE: list[Ending] = [  # "Warszawa", "Anna"
    ("a", _cases("nom.sg")),
    ("y", _cases("gen.sg")),
    ("ie", _cases("dat.sg", "loc.sg")),
    ("ę", _cases("acc.sg")),
    ("ą", _cases("ins.sg")),
]
# The stem of a feminine noun softens before -ie: "Polsk" gives "Polsce".
_SOFTEN = {"ch": "sz", "k": "c", "g": "dz", "ł": "l", "r": "rz", "t": "ci", "d": "dzi"}
# and back again, to find the stem of "Polsce"
_HARDEN = {"ce": "k", "dze": "g", "le": "ł", "rze": "r", "cie": "t", "dzie": "d"}
_NOT_VOWEL_TAIL = r"[^aeiouyąęó]+$"


def _ends(word: str, family: list[Ending]) -> list[str]:
    """The stems that the word has in a family: for each ending that it has (the
    bare nominative of a masculine has none), the word less the ending."""
    stems = []
    for ending in sorted({e for e, _ in family}, key=len, reverse=True):
        if word.endswith(ending) and len(word) - len(ending) >= _LEAST_STEM:
            stems.append(word[: len(word) - len(ending)])
    return stems


def _make(stem: str, family: list[Ending]) -> list[tuple[str, Cases]]:
    return [(stem + ending, cases) for ending, cases in family]


def _adjective_soft(word: str) -> list[tuple[str, Cases]]:
    forms: list[tuple[str, Cases]] = []
    for stem in _ends(word, _ADJECTIVE_SOFT):
        if len(stem) < 4 or stem[-1] not in "kg":
            continue
        forms += _make(stem, _ADJECTIVE_SOFT)
        # the plural of the men: Machniccy, Polscy, Drodzy
        forms.append(
            (stem[:-1] + ("cy" if stem[-1] == "k" else "dzy"), _cases("nom.pl"))
        )
    return forms


def _adjective_hard(word: str) -> list[tuple[str, Cases]]:
    forms: list[tuple[str, Cases]] = []
    for stem in _ends(word, _ADJECTIVE_HARD):
        if len(stem) >= 4 and stem[-1] not in "ikg" and _is_consonant_final(stem):
            forms += _make(stem, _ADJECTIVE_HARD)
    return forms


def _masculine(word: str) -> list[tuple[str, Cases]]:
    """A masculine noun, from any of its forms. "ó" of the nominative is "o" in
    the others (Kraków, Krakowa), so both are tried; after k and g the instrumental
    is "-iem" and there is no "-ie" locative."""
    forms: list[tuple[str, Cases]] = []
    for ending, _ in _MASCULINE:
        if not word.endswith(ending) or len(word) - len(ending) < _LEAST_STEM:
            continue
        stem = word[: len(word) - len(ending)]
        if not _is_consonant_final(stem):
            continue
        if ending == "" and stem.endswith(("ń", "ek", "ec")):
            continue  # those have a family of their own
        nominative, oblique = stem, stem
        if re.search("ó" + _NOT_VOWEL_TAIL, stem):
            oblique = re.sub("ó(" + _NOT_VOWEL_TAIL[:-1] + ")$", r"o\1", stem)
        elif ending and re.search("o" + _NOT_VOWEL_TAIL, stem):
            nominative = re.sub("o(" + _NOT_VOWEL_TAIL[:-1] + ")$", r"ó\1", stem)
            forms.append((nominative, _cases("nom.sg", "acc.sg")))
        hard = oblique[-1] in "kg"
        for end, cases in _MASCULINE:
            if end == "":
                forms.append((nominative, cases))
            elif end == "ie" and hard:
                continue
            elif end == "em" and hard:
                forms.append((oblique + "iem", cases))
            else:
                forms.append((oblique + end, cases))
    return forms


def _special_masculine(word: str) -> list[tuple[str, Cases]]:
    """The masculines in -ek, -ec and -ń, and the ones that are not a nominative."""
    forms: list[tuple[str, Cases]] = []
    for family in (_MASCULINE_EK, _MASCULINE_EC, _MASCULINE_SOFT_N):
        for ending, _ in family:
            if word.endswith(ending) and len(word) - len(ending) >= _LEAST_STEM - 1:
                forms += _make(word[: len(word) - len(ending)], family)
                break
    return forms


def _neuter(word: str) -> list[tuple[str, Cases]]:
    forms: list[tuple[str, Cases]] = []
    for stem in _ends(word, _NEUTER):
        if not _is_consonant_final(stem):
            continue
        hard = stem[-1] in "kg"
        for ending, cases in _NEUTER:
            forms.append((stem + ("iem" if hard and ending == "em" else ending), cases))
    return forms


def _feminine(word: str) -> list[tuple[str, Cases]]:
    stems = [s for s in _ends(word, _FEMININE) if _is_consonant_final(s)]
    for soft, hard in _HARDEN.items():
        if word.endswith(soft) and len(word) - len(soft) >= _LEAST_STEM - 1:
            stems.append(word[: -len(soft)] + hard)
    forms: list[tuple[str, Cases]] = []
    for stem in dict.fromkeys(stems):
        for ending, cases in _FEMININE:
            if ending == "ie":
                softened = next(
                    (stem[: -len(h)] + _SOFTEN[h] for h in _SOFTEN if stem.endswith(h)),
                    None,
                )
                forms.append((softened + "e" if softened else stem + "ie", cases))
            elif ending == "y" and stem[-1] in "kg":
                forms.append((stem + "i", cases))
            else:
                forms.append((stem + ending, cases))
    return forms


def _with_old_spelling(forms: Iterable[tuple[str, Cases]]) -> list[Form]:
    """The forms, and the 19th-century spelling of them: "-éj" for "-ej" and "-ém"
    for "-em" at the end of the word."""
    made: dict[str, Cases] = {}
    for text, cases in forms:
        made[text] = made.get(text, frozenset()) | cases
        for new, old in (("ej", "éj"), ("em", "ém")):
            if text.endswith(new):
                old_text = text[: -len(new)] + old
                made[old_text] = made.get(old_text, frozenset()) | cases
    return [Form(text, cases) for text, cases in made.items()]


def _modern(word: str) -> str:
    """The word with the old spelling of its ending written as it is now."""
    for old, new in (("éj", "ej"), ("ém", "em")):
        if word.endswith(old):
            return word[: -len(old)] + new
    return word


def forms_of_word(word: str, label: str = "") -> list[Form]:
    """The forms of a word in the cases, with the cases each can be in. The word
    itself comes first. A word that does not start with a capital, or fits no
    family, has only itself.

    `label` is what the word is (`persName`, `placeName`, ...) and says which
    families to try: a person's name is a surname in -ski/-cki (which is then the
    only family: "Machnickiego" is not a noun) or a masculine, feminine or
    -ek/-ec noun; a place is a noun, masculine, feminine, neuter, or in -ń. Another
    label (or none) tries them all.
    """
    if not word or not word[0].isupper():
        return [Form(word, _cases("any"))]
    if not all(character.isalpha() or character in "-'’" for character in word):
        return [Form(word, _cases("any"))]
    modern = _modern(word)
    person = label == "persName"
    place = label in ("placeName", "geogName")
    forms: list[tuple[str, Cases]] = []
    if not place:
        forms = _adjective_soft(modern)
        if forms:
            person = True  # a surname in -ski: the only family
    if not forms:
        if not place:
            forms += _adjective_hard(modern)
        if not forms:
            forms += _masculine(modern) + _special_masculine(modern)
            forms += _feminine(modern)
            if not person:
                forms += _neuter(modern)
    made: dict[str, Cases] = {}
    for form in _with_old_spelling(forms):
        made[form.text] = made.get(form.text, frozenset()) | form.cases
    first = made.pop(word, None)
    ordered = [Form(word, first or _cases("any"))]
    ordered += [Form(text, cases) for text, cases in sorted(made.items())]
    return ordered


def forms_of_phrase(phrase: str, label: str = "") -> list[Form]:
    """The forms of a name of one or more words, each in the same case: "Jan
    Machnicki" gives "Jana Machnickiego", "Janowi Machnickiemu", ... A word
    without a capital ("de") stays as it is. The phrase itself is the first."""
    words = phrase.split()
    if not words:
        return []
    if len(words) == 1:
        return forms_of_word(words[0], label)
    per_word = [forms_of_word(word, label) for word in words]
    first = " ".join(words)
    made: dict[str, Cases] = {}
    for combination in itertools.product(*per_word):
        shared: set[str] | None = None
        for form in combination:
            if form.cases == {"any"}:
                continue
            shared = set(form.cases) if shared is None else shared & form.cases
        if shared is not None and not shared:
            continue  # the words are not in one case
        text = " ".join(form.text for form in combination)
        made.setdefault(text, frozenset(shared or {"any"}))
        if len(made) >= _MOST_FORMS:
            break
    made.pop(first, None)
    return [Form(first, _cases("any"))] + [
        Form(text, cases) for text, cases in sorted(made.items())
    ]


@dataclass(frozen=True)
class PhraseMatch:
    """A form found in a text: the characters `start` to `end` are `text`; `before`
    and `after` are its context: the nearest word before it and the nearest word after
    it, with the signs between them and the form ("dom." and ", kota"), so that a full
    stop or a comma next to it does not leave nothing to read (empty at the ends of
    the text, where there is no word)."""

    start: int
    end: int
    text: str
    before: str
    after: str


def find_forms(text: str, forms: Iterable[str]) -> list[PhraseMatch]:
    """Where the forms are in `text`, in order. Each is a word, or words that are
    next to each other (with one space or one line break between them). A match is
    the whole tokens, never a part of a word. The longest is taken where two
    overlap."""
    wanted = set(forms)
    sizes = sorted({len(form.split()) for form in wanted}, reverse=True)
    tokens = tokenize(text)
    taken_to = 0
    found = []
    for index, token in enumerate(tokens):
        if token.start() < taken_to:
            continue
        for size in sizes:
            group = tokens[index : index + size]
            if len(group) < size:
                continue
            gaps_ok = all(
                text[a.end() : b.start()] in (" ", "\n")
                for a, b in itertools.pairwise(group)
            )
            candidate = " ".join(match.group() for match in group)
            if gaps_ok and candidate in wanted:
                last = group[-1]
                found.append(
                    PhraseMatch(
                        start=token.start(),
                        end=last.end(),
                        text=text[token.start() : last.end()],
                        before=context_before(text, tokens, index),
                        after=context_after(text, tokens, index + size - 1),
                    )
                )
                taken_to = last.end()
                break
    return found

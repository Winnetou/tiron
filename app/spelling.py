"""Spell checking with phunspell and the Polish Hunspell dictionary."""

import re
import threading
from collections.abc import Collection

import phunspell

from app.models import Word

LANGUAGE = "pl_PL"

# A word: letters and digits, with single hyphens or apostrophes inside it
# ("biało-czerwony"). A hyphen at the end of a line ("o-" / "wych") is not part
# of a word, so the two halves are two words.
_WORD = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*")
# A sign: a run of other characters that are not white space ("$", "(|}", "..."),
# on its own or glued to a word. It is not spelled, but it can be corrected.
_TOKEN = re.compile(rf"{_WORD.pattern}|(?:[^\w\s]|_)+")
_JOINERS = re.compile(r"['’-]")

_checker: phunspell.Phunspell | None = None
_lock = threading.Lock()


class SpellcheckUnavailable(RuntimeError):
    """The dictionary could not be loaded."""


def get_checker() -> phunspell.Phunspell:
    """The Polish dictionary, loaded once and then kept (it takes about 2 s).

    The backend calls this when it starts (see `lifespan` in app/main.py), so no
    request has to wait for it. It is not done when this module is imported, so
    importing the app does not need it.
    """
    global _checker
    with _lock:
        if _checker is None:
            try:
                _checker = phunspell.Phunspell(LANGUAGE)
            except phunspell.phunspell.PhunspellError as error:
                raise SpellcheckUnavailable(
                    f"The {LANGUAGE} dictionary is not available"
                ) from error
        return _checker


def _is_correct(word: str, checker: phunspell.Phunspell) -> bool:
    if not any(character.isalnum() for character in word):
        return True  # signs are not spelled
    if any(character.isdigit() for character in word):
        return True  # numbers, and words with digits in them, are not checked
    if checker.lookup(word):
        return True
    # "biało-czerwony" is fine if it is in the dictionary as a whole (above), or
    # if all its parts are.
    parts = _JOINERS.split(word)
    return len(parts) > 1 and all(checker.lookup(part) for part in parts)


def check_words(text: str, legal: Collection[str] = ()) -> list[Word]:
    """Every word of `text`, in order, and whether it is spelled correctly.

    `start` and `end` are character offsets in `text` (counted in code points),
    so text[start:end] is the word. Words that contain digits are not checked
    and are reported as correct. A run of signs ("$", "(|}", ",") is a token too,
    reported as correct, so that it can be corrected like a word; only white
    space is not a token.

    `legal` are spellings that are legal in the edition the text is from: such a
    word is correct even if the dictionary does not know it. They match the way
    the dictionary does: a legal "jey" also makes "Jey" and "JEY" correct (a
    sentence can start with it), but a legal "Jey" does not make "jey" correct.

    Raises SpellcheckUnavailable if the dictionary cannot be loaded.
    """
    checker = get_checker()
    verdicts: dict[str, bool] = {}  # each distinct word is looked up once
    words = []
    for match in _TOKEN.finditer(text):
        word = match.group()
        if word not in verdicts:
            verdicts[word] = (
                word in legal or word.lower() in legal or _is_correct(word, checker)
            )
        words.append(
            Word(
                text=word,
                start=match.start(),
                end=match.end(),
                correct=verdicts[word],
            )
        )
    return words

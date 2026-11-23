"""Spell checking with phunspell and the Polish Hunspell dictionary."""

import itertools
import logging
import re
import threading
from collections.abc import Callable, Collection, Iterator

import phunspell

from app.view_models import Word

logger = logging.getLogger(__name__)

LANGUAGE = "pl_PL"

# A word: letters and digits, with single hyphens or apostrophes inside it
# ("biało-czerwony").
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
                logger.error(
                    "The %s dictionary could not be loaded: %s", LANGUAGE, error
                )
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


def tokenize(text: str) -> list[re.Match[str]]:
    """The tokens of a text, in order: words and runs of signs (white space is
    not a token). The match has the token as `group()` and its offsets."""
    return list(_TOKEN.finditer(text))


# The most parts of one word that are split over lines and checked as a whole
# ("o-" / "wy-" / "ch"): a word on more lines than that is not looked for.
_MOST_PARTS = 4


def _split_words(text: str, tokens: list[re.Match[str]]) -> dict[int, list[int]]:
    """The words that are split over lines, by the index in `tokens` of each of
    their parts: every part has the whole list of indexes of the parts.

    A word is split when a word is followed, with nothing between, by a hyphen,
    which is followed by one line break and a word ("przeczy-" / "tać"). Three
    tokens are then one word: the part, the hyphen and the next part. An empty
    line is a paragraph break, not a line break, so it does not count.
    """
    found: dict[int, list[int]] = {}
    index = 0
    while index + 2 < len(tokens):
        parts = [index]
        while len(parts) < _MOST_PARTS:
            at = parts[-1]
            if at + 2 >= len(tokens):
                break
            word, hyphen, next_word = tokens[at : at + 3]
            if (
                _WORD.fullmatch(word.group())
                and hyphen.group() == "-"
                and word.end() == hyphen.start()
                and text[hyphen.end() : next_word.start()] == "\n"
                and _WORD.fullmatch(next_word.group())
            ):
                parts.append(at + 2)
            else:
                break
        if len(parts) > 1:
            for part in parts:
                found[part] = parts
            index = parts[-1] + 1
        else:
            index += 1
    return found


def _joined(pieces: list[str]) -> Iterator[str]:
    """The ways to join the parts of a word that is split over lines: each hyphen
    at the end of a line is the word's own, or only a mark of the split."""
    for hyphens in itertools.product(("", "-"), repeat=len(pieces) - 1):
        glues = (*hyphens, "")
        yield "".join(piece + glue for piece, glue in zip(pieces, glues, strict=True))


def word_verdict(legal: Collection[str] = ()) -> Callable[[str], bool]:
    """A function that says whether a word is spelled correctly (or is legal, see
    `check_words`). It remembers its answers, so each distinct word is looked up
    once: make one for a text and use it for all the words of it.

    Raises SpellcheckUnavailable if the dictionary cannot be loaded.
    """
    checker = get_checker()
    verdicts: dict[str, bool] = {}

    def verdict(word: str) -> bool:
        if word not in verdicts:
            verdicts[word] = (
                word in legal or word.lower() in legal or _is_correct(word, checker)
            )
        return verdicts[word]

    return verdict


def check_words(
    text: str,
    legal: Collection[str] = (),
    ignore: Collection[tuple[int, int]] = (),
) -> list[Word]:
    """Every word of `text`, in order, and whether it is spelled correctly.

    `start` and `end` are character offsets in `text` (counted in code points),
    so text[start:end] is the word. Words that contain digits are not checked
    and are reported as correct. A run of signs ("$", "(|}", ",") is a token too,
    reported as correct, so that it can be corrected like a word; only white
    space is not a token.

    A word split over two lines ("przeczy-" / "tać": a word, a hyphen at the end
    of the line, a line break and a word) is checked as one word. Its parts stay
    tokens of their own, with their own offsets (so each can be corrected), but
    both get the verdict of the whole: correct if the word joined ("przeczytać")
    is correct, or the word with the hyphen kept ("biało-czerwony"), because the
    hyphen at the end of a line may be a hyphen of the word or a mark of the
    split. The hyphen itself is a sign, so it is correct. A word that is split
    over pages is not looked for: only the text of one page is known here.

    `legal` are spellings that are legal in the edition the text is from: such a
    word is reported as correct even if the dictionary does not know it. ("Legal",
    not "correct": the dictionary is a modern one, and what was correct in the
    19th century often is not anymore; we have no better word. "Correct" is the
    verdict of this function, "legal" is what a person decided for the edition.)

    They match the way the dictionary does: a legal "jey" also makes "Jey" and
    "JEY" correct (a sentence can start with it), but a legal "Jey" does not make
    "jey" correct.

    `ignore` are stretches of `text` (`start`, `end`: offsets, counted in code
    points) that are not Polish, a quotation in Latin: the words in them are not
    checked, they are reported as correct. (The dictionary is Polish; what is in
    another language is not misspelt for it.)

    Raises SpellcheckUnavailable if the dictionary cannot be loaded.
    """
    verdict = word_verdict(legal)

    tokens = tokenize(text)
    split = _split_words(text, tokens)
    whole: dict[int, bool] = {}  # the verdict of each split word, by its first part
    for parts in split.values():
        if parts[0] not in whole:
            pieces = [tokens[part].group() for part in parts]
            whole[parts[0]] = any(verdict(word) for word in _joined(pieces))

    words = []
    for index, match in enumerate(tokens):
        word = match.group()
        if any(a <= match.start() and match.end() <= b for a, b in ignore):
            correct = True
        else:
            correct = whole[split[index][0]] if index in split else verdict(word)
        words.append(
            Word(text=word, start=match.start(), end=match.end(), correct=correct)
        )
    logger.debug("%d words in %d characters", len(words), len(text))
    return words

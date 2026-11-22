"""Finding the OCR rubbish: lines that are not text.

The OCR reads a fleuron, a printer's ornament or the print that shows through
from the other side of the page as text, and produces nonsense, whole lines of it:
`I *>• ' -V. ^oSkv. ;-'. i M , . ypton`. This finds those lines of a page, so that
they can be removed (in bulk, for the edition).

A line is a candidate when signs outnumber letters in it: the letters are less
than 60 % of the letters and signs together (digits are not counted). It is
`sure` if there is no correct word of three letters or more in it. If there is one
the line may be text with a lot of punctuation (a note, a list of references), and
a human has to say. Not candidates, whatever they look like:

- a line shorter than three characters (`*`, `.`, `\\`): too little to say that it
  is rubbish, and not worth a human's attention,
- a line of one and the same sign, `?` `.` `-` or `!`, several times (`...`, `---`,
  `???`, `!!!`, `. . .`): white space between them does not matter. A mix of them
  (`.-.`, `?!?`) is not covered by this,
- a line that has digits and no letters (`0`, `16`, `7 (`: page numbers, and what
  stands for them),
- a Roman numeral (`II.`: the number of a chapter),
- a line in a section that is marked (a header, a footer, a page number, a table
  of contents, a note).

Only whole lines are found, never a token in the middle of a good line (the
`£r~*>e` that starts a line of text): that is a damaged word, closer to a
correction than to a removal.
"""

import re
from collections.abc import Collection
from dataclasses import dataclass

from app.text_tools.spelling import _WORD, tokenize, word_verdict

# Below this share of letters among the letters and the signs, a line is rubbish.
_LEAST_LETTER_SHARE = 0.6
# A line shorter than this is never rubbish (white space at its ends not counted).
_LEAST_LENGTH = 3
# A word of this many letters, if it is correct, shows that the line is text.
_REAL_WORD_LETTERS = 3
# The signs that are never rubbish when a line is made of several of one of them.
_REPEATED_SIGNS = "?.-!"
_ROMAN_NUMERAL = re.compile(r"[IVXLCDM]+[.,]?")
_LINE = re.compile(r"[^\n]+")


@dataclass(frozen=True)
class RubbishLine:
    """A line of the text that is not text: the characters `start` to `end` are
    `text`, a whole line. `sure` is False if it has a correct word in it."""

    start: int
    end: int
    text: str
    sure: bool


def find_rubbish_lines(
    text: str,
    legal: Collection[str] = (),
    protected: Collection[tuple[int, int]] = (),
) -> list[RubbishLine]:
    """The lines of `text` that are rubbish, in order.

    `legal` are the spellings that are legal in the edition (see `check_words`),
    which count as correct words. `protected` are stretches of the text (start,
    end) that are marked as a section: a line that overlaps one is left alone.

    Raises SpellcheckUnavailable if the dictionary cannot be loaded.
    """
    verdict = word_verdict(legal)
    found = []
    for match in _LINE.finditer(text):
        line = match.group()
        start, end = match.start(), match.end()
        if any(start < to and end > at for at, to in protected):
            continue
        if len(line.strip()) < _LEAST_LENGTH:
            continue
        signs_only = "".join(line.split())
        if (
            len(set(signs_only)) == 1
            and signs_only[0] in _REPEATED_SIGNS
            and len(signs_only) > 1
        ):
            continue  # a line of dots, dashes, ...: not rubbish
        letters = sum(character.isalpha() for character in line)
        digits = sum(character.isdigit() for character in line)
        signs = sum(
            not character.isalnum() and not character.isspace() for character in line
        )
        if letters == 0 and digits > 0:
            continue  # a page number, or what stands for one
        if _ROMAN_NUMERAL.fullmatch(line.strip()):
            continue  # the number of a chapter
        if letters and letters / (letters + signs) >= _LEAST_LETTER_SHARE:
            continue
        real = any(
            _WORD.fullmatch(token.group())
            and sum(character.isalpha() for character in token.group())
            >= _REAL_WORD_LETTERS
            and not any(character.isdigit() for character in token.group())
            and verdict(token.group())
            for token in tokenize(line)
        )
        found.append(RubbishLine(start=start, end=end, text=line, sure=not real))
    return found

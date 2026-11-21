"""Finding the same OCR mistake in other places: the tokens of a text that have the
same wrong cluster of letters as a spelling that was corrected, and what each of
them would be corrected to."""

import re
from dataclasses import dataclass

from app.text_tools.spelling import tokenize

# The shortest cluster that is looked for: a single letter ("ć") is in far too
# many words that are right.
_SHORTEST_CLUSTER = 2


@dataclass(frozen=True)
class ClusterFix:
    """A mistake and its correction: `wrong` is the cluster of letters that the
    OCR got wrong, and `right` is what it should be ("ćj" and "éj"). With
    `whole_word` only the whole word is meant, not a cluster in it."""

    wrong: str
    right: str
    whole_word: bool = False


def cluster_fix(incorrect: str, corrected: str) -> ClusterFix:
    """The mistake in `incorrect`, which was corrected to `corrected`: the cluster
    of letters from the first letter that differs to the end of the word, and what
    that cluster is in the corrected word. The letters before it are left out,
    because they are not the mistake and differ from word to word.

    "samćj" corrected to "saméj" gives "ćj" and "éj", and "tćgo" corrected to
    "tégo" gives "ćgo" and "égo": the OCR reads the old Polish "é" as "ć", and the
    same cluster is wrong in other words ("jćj", "samćj"). A cluster is at least
    two letters long: a single changed letter at the end is taken with the letter
    before it. If `incorrect` is only the beginning of `corrected` (letters are
    missing), there is no cluster to speak of: the fix is for the whole word.

    Raises ValueError if the two are the same or `incorrect` is empty.
    """
    if not incorrect:
        raise ValueError("The incorrect spelling must not be empty")
    if incorrect == corrected:
        raise ValueError("The incorrect and the corrected spelling are the same")
    common = 0
    for wrong, right in zip(incorrect, corrected, strict=False):
        if wrong != right:
            break
        common += 1
    if common == len(incorrect):
        return ClusterFix(incorrect, corrected, whole_word=True)
    start = max(0, min(common, len(incorrect) - _SHORTEST_CLUSTER))
    return ClusterFix(incorrect[start:], corrected[start:])


def wrong_cluster(incorrect: str, corrected: str) -> str:
    """The cluster of letters that is wrong (see `cluster_fix`)."""
    return cluster_fix(incorrect, corrected).wrong


def _suggest(token: str, fix: ClusterFix) -> str:
    """The token with the wrong cluster replaced by the right one, wherever it is
    in the token, keeping capital letters: "Ćj" becomes "Éj"."""
    if fix.whole_word:
        right = fix.right
        return right[0].upper() + right[1:] if token[0].isupper() else right

    def replace(match: re.Match[str]) -> str:
        found, right = match.group(), fix.right
        if len(found) > 1 and found.isupper():
            return right.upper()
        if found[0].isupper():
            return right[0].upper() + right[1:]
        return right

    return re.sub(re.escape(fix.wrong), replace, token, flags=re.IGNORECASE)


def tokens_with_cluster(text: str, fix: ClusterFix) -> list[tuple[str, int, int, str]]:
    """The word tokens of `text` that have the mistake of `fix`, case does not
    matter, as (token, start, end, suggestion): the offsets are those of `text`,
    counted in code points, so text[start:end] is the token, and the suggestion is
    the token with the mistake corrected. A token is a match if it contains the
    wrong cluster ("jćj", "samćj", "ćjxyz"), or, for a fix of a whole word, if it is
    that word."""
    wrong = fix.wrong.casefold()
    found = []
    for match in tokenize(text):
        token = match.group()
        if not any(character.isalnum() for character in token):
            continue
        folded = token.casefold()
        if (folded == wrong) if fix.whole_word else (wrong in folded):
            found.append((token, match.start(), match.end(), _suggest(token, fix)))
    return found

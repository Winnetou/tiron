"""Named entity recognition with spaCy and its large Polish model."""

import threading

import spacy
from spacy.language import Language

from app.models import Entity

MODEL_NAME = "pl_core_news_lg"

_nlp: Language | None = None
_lock = threading.Lock()


class NerModelUnavailable(RuntimeError):
    """The spaCy model could not be loaded (it is not installed)."""


def get_nlp() -> Language:
    """The Polish model, loaded once and then kept.

    The backend calls this when it starts (see `lifespan` in app/main.py), so
    that no request has to wait for the load. It is not done when this module
    is imported, though: loading takes many seconds and a lot of memory, and
    importing the app (as the tests do) must not need the model. If it was not
    loaded at startup, the first call loads it.
    """
    global _nlp
    with _lock:
        if _nlp is None:
            try:
                _nlp = spacy.load(MODEL_NAME)
            except OSError as error:
                raise NerModelUnavailable(
                    f"The spaCy model {MODEL_NAME} is not installed"
                ) from error
        return _nlp


def find_entities(text: str) -> list[Entity]:
    """The named entities in `text`, in the order they appear.

    Each one has its text, its label (the model's, e.g. persName, placeName,
    orgName, geogName, date) and where it is in `text`: `start` and `end` are
    character offsets, so text[start:end] is the entity's text.

    Raises NerModelUnavailable if the model is not installed.
    """
    nlp = get_nlp()
    return [
        Entity(
            text=entity.text,
            label=entity.label_,
            start=entity.start_char,
            end=entity.end_char,
        )
        for entity in nlp(text).ents
    ]

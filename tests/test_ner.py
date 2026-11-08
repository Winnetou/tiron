import importlib.util
import threading
import time

import pytest
import spacy

from app import ner
from app.models import Entity
from app.ner import NerModelUnavailable, find_entities, get_nlp


def pipeline_with_ruler():
    """A real spaCy pipeline with rule-based entities instead of the big model."""
    nlp = spacy.blank("pl")
    ruler = nlp.add_pipe("entity_ruler")
    ruler.add_patterns(
        [
            {"label": "persName", "pattern": "Adam Mickiewicz"},
            {"label": "placeName", "pattern": "Warszawa"},
            {"label": "placeName", "pattern": "Łódź"},
            {"label": "orgName", "pattern": "Biblioteka Narodowa"},
        ]
    )
    return nlp


@pytest.fixture
def ruler(monkeypatch):
    monkeypatch.setattr(ner, "get_nlp", pipeline_with_ruler)


@pytest.fixture(autouse=True)
def no_cached_model(monkeypatch):
    monkeypatch.setattr(ner, "_nlp", None)


class TestFindEntities:
    def test_returns_text_label_and_offsets_in_order(self, ruler):
        text = (
            "Adam Mickiewicz mieszkał w Warszawie, a Warszawa to Biblioteka Narodowa."
        )

        entities = find_entities(text)

        assert entities == [
            Entity(text="Adam Mickiewicz", label="persName", start=0, end=15),
            Entity(text="Warszawa", label="placeName", start=40, end=48),
            Entity(text="Biblioteka Narodowa", label="orgName", start=52, end=71),
        ]

    def test_offsets_point_at_the_entity_in_the_text(self, ruler):
        text = "Łódź, potem Adam Mickiewicz.\nPotem Warszawa."

        entities = find_entities(text)

        assert [e.text for e in entities] == ["Łódź", "Adam Mickiewicz", "Warszawa"]
        for entity in entities:
            assert text[entity.start : entity.end] == entity.text

    def test_no_entities_gives_an_empty_list(self, ruler):
        assert find_entities("Ala ma kota.") == []

    @pytest.mark.parametrize("text", ["", " ", "\n\n"])
    def test_empty_text_gives_an_empty_list(self, ruler, text):
        assert find_entities(text) == []

    def test_the_same_entity_twice_is_found_twice(self, ruler):
        entities = find_entities("Warszawa i Warszawa")

        assert [(e.start, e.end) for e in entities] == [(0, 8), (11, 19)]


class TestGetNlp:
    def test_loads_the_large_polish_model(self, monkeypatch):
        loaded = []
        monkeypatch.setattr(
            ner.spacy, "load", lambda name: loaded.append(name) or "model"
        )

        assert get_nlp() == "model"
        assert loaded == ["pl_core_news_lg"]

    def test_loads_only_once(self, monkeypatch):
        loaded = []
        monkeypatch.setattr(
            ner.spacy, "load", lambda name: loaded.append(name) or object()
        )

        first = get_nlp()
        second = get_nlp()

        assert first is second
        assert len(loaded) == 1

    def test_loads_only_once_with_concurrent_first_requests(self, monkeypatch):
        loaded = []

        def slow_load(name):
            time.sleep(0.1)
            loaded.append(name)
            return object()

        monkeypatch.setattr(ner.spacy, "load", slow_load)
        results = []
        threads = [
            threading.Thread(target=lambda: results.append(get_nlp())) for _ in range(5)
        ]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(loaded) == 1
        assert len({id(r) for r in results}) == 1

    def test_missing_model_is_reported_and_tried_again_later(self, monkeypatch):
        def missing(name):
            raise OSError("[E050] Can't find model 'pl_core_news_lg'")

        monkeypatch.setattr(ner.spacy, "load", missing)

        with pytest.raises(NerModelUnavailable, match="pl_core_news_lg"):
            get_nlp()
        monkeypatch.setattr(ner.spacy, "load", lambda name: "now installed")
        assert get_nlp() == "now installed"  # the failure was not cached

    def test_the_model_is_not_loaded_by_importing_the_app(self):
        # importing app.main (done by the other tests) must not load the model
        import app.main  # noqa: F401

        assert ner._nlp is None


@pytest.mark.skipif(
    importlib.util.find_spec("pl_core_news_lg") is None,
    reason="pl_core_news_lg is not installed (it is in the Docker image)",
)
class TestWithTheRealModel:
    def test_finds_people_places_and_dates(self, monkeypatch):
        monkeypatch.setattr(ner, "_nlp", None)
        text = (
            "Adam Mickiewicz urodził się w Zaosiu, a potem mieszkał w Paryżu "
            "do 1855 roku."
        )

        entities = find_entities(text)

        found = {e.text: e.label for e in entities}
        assert found["Adam Mickiewicz"] == "persName"
        assert found["Paryżu"] == "placeName"
        for entity in entities:
            assert text[entity.start : entity.end] == entity.text
        assert entities == sorted(entities, key=lambda e: e.start)

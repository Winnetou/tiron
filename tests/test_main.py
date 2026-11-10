import logging
from unittest import mock
from uuid import uuid4

import psycopg.errors
import pytest
import spacy
from fastapi.testclient import TestClient

import app.main as app_main
import app.ner as ner
import app.spelling as spelling
from app.main import app, db
from app.models import Entity, PageView, Word
from app.ner import NerModelUnavailable
from app.ontology import Edition, LegalSpelling, Page
from app.polona_connector import PolonaError
from app.spelling import SpellcheckUnavailable
from app.tei_xml import tei_xml_to_text, text_to_tei_xml

client = TestClient(app)


@pytest.fixture
def in_collection(monkeypatch):
    check = mock.Mock(return_value=True)
    monkeypatch.setattr(db, "check_edition_in_collection", check)
    return check


def test_get_page_edition_not_in_collection(in_collection):
    in_collection.return_value = False
    edition_id = uuid4()

    response = client.get(f"/editions/{edition_id}/pages/1")

    assert response.status_code == 404
    in_collection.assert_called_once_with(edition_id)


META = {
    "author": "Prus, Bolesław (1847-1912)",
    "title": "Lalka",
    "place": "Warszawa",
    "year": 1890,
}


@pytest.fixture
def polona(monkeypatch):
    monkeypatch.setattr("app.main.fetch_edition_metadata", mock.Mock(return_value=META))
    monkeypatch.setattr(db, "get_edition", mock.Mock(return_value=None))
    monkeypatch.setattr(db, "get_page_by_number", mock.Mock(return_value=None))
    monkeypatch.setattr(db, "upsert_edition", mock.Mock())
    monkeypatch.setattr(db, "save_fetched_page", mock.Mock(side_effect=lambda p: p))
    fetch = mock.Mock(
        return_value={
            "scan_uri": "https://polona.pl/iiif/3/x/full/max/0/default.jpg",
            "raw_content": "Ala ma kota",
        }
    )
    monkeypatch.setattr("app.main.fetch_page", fetch)
    return fetch


def test_get_page_edition_not_in_collection_skips_polona(in_collection, polona):
    in_collection.return_value = False

    client.get(f"/editions/{uuid4()}/pages/1")

    polona.assert_not_called()


def test_get_page_returns_polona_content(in_collection, polona):
    edition_id = uuid4()

    response = client.get(f"/editions/{edition_id}/pages/0")

    assert response.status_code == 200
    body = response.json()
    assert body["edition_id"] == str(edition_id)
    assert body["number"] == 0
    assert body["content"] == str(text_to_tei_xml("Ala ma kota"))  # TEI, not raw
    assert "raw_content" not in body
    assert "corrected_content" not in body
    assert body["ready"] is False
    assert body["scan_uri"].endswith("default.jpg")
    polona.assert_called_once_with(edition_id, 0)


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (PolonaError("out of range", not_found=True), 404),
        (PolonaError("Could not reach polona.pl"), 502),
    ],
)
def test_get_page_polona_errors(in_collection, polona, error, expected_status):
    polona.side_effect = error

    response = client.get(f"/editions/{uuid4()}/pages/1")

    assert response.status_code == expected_status
    assert response.json()["detail"] == (
        "Page not found" if error.not_found else str(error)
    )


@pytest.mark.parametrize(
    "path",
    [
        "/editions/not-a-uuid/pages/1",
        f"/editions/{uuid4()}/pages/abc",
        f"/editions/{uuid4()}/pages/-1",
    ],
)
def test_get_page_invalid_args(path):
    assert client.get(path).status_code == 422


def test_old_page_route_removed():
    assert client.get(f"/pages/{uuid4()}").status_code == 404


def test_the_old_corrections_stub_is_gone():
    body = {"page_id": str(uuid4()), "before": "Aia", "after": "Ala"}

    assert client.post("/corrections", json=body).status_code == 404


def test_openapi_uses_ontology_models():
    schemas = client.get("/openapi.json").json()["components"]["schemas"]

    assert {"PageView", "WordCorrection"} <= schemas.keys()


def test_get_page_creates_work_and_edition_when_missing(in_collection, polona):
    edition_id = uuid4()

    client.get(f"/editions/{edition_id}/pages/1")

    db.upsert_edition.assert_called_once()
    work, edition = db.upsert_edition.call_args.args
    assert work.author == META["author"]
    assert work.title == "Lalka"
    assert edition.id == edition_id
    assert edition.work_id == work.id
    assert (edition.place, edition.year) == ("Warszawa", 1890)


def test_get_page_skips_metadata_when_edition_exists(in_collection, polona):
    db.get_edition.return_value = Edition(work_id=uuid4())
    edition_id = uuid4()

    response = client.get(f"/editions/{edition_id}/pages/1")

    assert response.status_code == 200
    db.upsert_edition.assert_not_called()
    app_main.fetch_edition_metadata.assert_not_called()


def test_get_page_metadata_errors(in_collection, polona):
    app_main.fetch_edition_metadata.side_effect = PolonaError("down")

    assert client.get(f"/editions/{uuid4()}/pages/1").status_code == 502
    db.upsert_edition.assert_not_called()


def test_get_page_persists_the_fetched_page_and_returns_the_stored_one(
    in_collection, polona
):
    edition_id = uuid4()
    stored = Page(
        edition_id=edition_id,
        number=4,
        raw_content="stored",
        corrected_content="corrected earlier",
        ready=True,
    )
    db.save_fetched_page.side_effect = None
    db.save_fetched_page.return_value = stored

    response = client.get(f"/editions/{edition_id}/pages/4")

    (saved,) = db.save_fetched_page.call_args.args
    assert saved.edition_id == edition_id
    assert saved.number == 4
    assert saved.raw_content == "Ala ma kota"
    assert saved.scan_uri.endswith("default.jpg")
    body = response.json()
    assert body["id"] == str(stored.id)
    assert body["content"] == "corrected earlier"  # corrected beats raw
    assert body["ready"] is True


def test_get_page_does_not_persist_when_polona_fails(in_collection, polona):
    polona.side_effect = PolonaError("down")

    client.get(f"/editions/{uuid4()}/pages/1")

    db.save_fetched_page.assert_not_called()


def test_get_page_returns_stored_page_without_calling_polona(in_collection, polona):
    edition_id = uuid4()
    stored = Page(
        edition_id=edition_id,
        number=7,
        raw_content="from the database",
        corrected_content="fixed",
        scan_uri="https://polona.pl/stored.jpg",
        ready=True,
    )
    db.get_page_by_number.return_value = stored

    response = client.get(f"/editions/{edition_id}/pages/7")

    assert response.status_code == 200
    assert response.json() == {
        "id": str(stored.id),
        "edition_id": str(edition_id),
        "number": 7,
        "scan_uri": "https://polona.pl/stored.jpg",
        "ready": True,
        "content": "fixed",
    }
    db.get_page_by_number.assert_called_once_with(edition_id, 7)
    polona.assert_not_called()
    app_main.fetch_edition_metadata.assert_not_called()
    db.upsert_edition.assert_not_called()
    db.save_fetched_page.assert_not_called()


def test_get_page_still_requires_the_edition_in_the_collection(in_collection, polona):
    in_collection.return_value = False

    response = client.get(f"/editions/{uuid4()}/pages/7")

    assert response.status_code == 404
    db.get_page_by_number.assert_not_called()


def test_get_page_fetches_from_polona_when_not_stored(in_collection, polona):
    edition_id = uuid4()

    client.get(f"/editions/{edition_id}/pages/7")

    db.get_page_by_number.assert_called_once_with(edition_id, 7)
    polona.assert_called_once_with(edition_id, 7)


@pytest.mark.parametrize(
    ("raw", "corrected", "expected"),
    [
        ("raw text", "corrected text", "corrected text"),
        ("raw text", None, "raw text"),
        ("raw text", "", "raw text"),
    ],
)
def test_page_view_content(raw, corrected, expected):
    page = Page(
        edition_id=uuid4(),
        number=1,
        raw_content=raw,
        corrected_content=corrected,
        scan_uri="https://polona.pl/s.jpg",
        ready=True,
    )

    view = PageView.from_page(page)

    assert view.content == expected
    assert (view.id, view.edition_id, view.number) == (
        page.id,
        page.edition_id,
        page.number,
    )
    assert (view.scan_uri, view.ready) == (page.scan_uri, True)


class TestUpdatePage:
    @pytest.fixture
    def update(self, monkeypatch):
        mock_update = mock.Mock(
            side_effect=lambda edition_id, number, **changes: Page(
                edition_id=edition_id,
                number=number,
                raw_content="raw text",
                scan_uri="https://polona.pl/s.jpg",
                **changes,
            )
        )
        monkeypatch.setattr(db, "update_page", mock_update)
        return mock_update

    def test_updates_corrected_content_and_ready(self, update):
        edition_id = uuid4()

        response = client.patch(
            f"/editions/{edition_id}/pages/3",
            json={"corrected_content": "fixed text", "ready": True},
        )

        assert response.status_code == 200
        update.assert_called_once_with(
            edition_id, 3, corrected_content="fixed text", ready=True
        )
        body = response.json()
        assert body["content"] == "fixed text"
        assert body["ready"] is True
        assert body["number"] == 3

    def test_only_given_fields_are_passed_on(self, update):
        edition_id = uuid4()

        client.patch(f"/editions/{edition_id}/pages/3", json={"ready": True})
        client.patch(f"/editions/{edition_id}/pages/3", json={"corrected_content": "x"})

        assert update.call_args_list == [
            mock.call(edition_id, 3, ready=True),
            mock.call(edition_id, 3, corrected_content="x"),
        ]

    def test_null_corrected_content_clears_the_correction(self, update):
        edition_id = uuid4()

        response = client.patch(
            f"/editions/{edition_id}/pages/3", json={"corrected_content": None}
        )

        assert response.status_code == 200
        update.assert_called_once_with(edition_id, 3, corrected_content=None)
        assert response.json()["content"] == "raw text"  # falls back to raw

    @pytest.mark.parametrize(
        "body",
        [
            {"raw_content": "x"},
            {"scan_uri": "https://evil.example/s.jpg"},
            {"number": 9},
            {"id": str(uuid4())},
            {"edition_id": str(uuid4())},
            {"ready": True, "raw_content": "x"},  # valid + forbidden field
            {},  # nothing to update
            {"ready": None},  # ready cannot be null
            {"ready": "maybe"},
            {"corrected_content": 5},
        ],
    )
    def test_rejects_anything_else(self, update, body):
        response = client.patch(f"/editions/{uuid4()}/pages/3", json=body)

        assert response.status_code == 422
        update.assert_not_called()

    def test_does_not_check_the_collection(self, update, monkeypatch):
        check = mock.Mock(return_value=False)
        monkeypatch.setattr(db, "check_edition_in_collection", check)

        response = client.patch(f"/editions/{uuid4()}/pages/3", json={"ready": True})

        assert response.status_code == 200
        check.assert_not_called()
        update.assert_called_once()

    def test_page_not_stored(self, update):
        update.side_effect = None
        update.return_value = None

        response = client.patch(f"/editions/{uuid4()}/pages/3", json={"ready": True})

        assert response.status_code == 404
        assert response.json()["detail"] == "Page not found"

    def test_never_calls_polona(self, update, polona):
        client.patch(f"/editions/{uuid4()}/pages/3", json={"ready": True})

        polona.assert_not_called()

    def test_invalid_path_arguments(self, update):
        assert (
            client.patch("/editions/nope/pages/3", json={"ready": True}).status_code
            == 422
        )
        assert (
            client.patch(
                f"/editions/{uuid4()}/pages/-1", json={"ready": True}
            ).status_code
            == 422
        )


def test_get_page_saves_raw_text_and_its_tei_xml(in_collection, polona):
    edition_id = uuid4()
    polona.return_value = {
        "scan_uri": "https://polona.pl/s.jpg",
        "raw_content": "84 KATEDRA\n\ncle przekazał kamie\nniami <10> & tys.",
    }

    client.get(f"/editions/{edition_id}/pages/5")

    (saved,) = db.save_fetched_page.call_args.args
    assert saved.raw_content == "84 KATEDRA\n\ncle przekazał kamie\nniami <10> & tys."
    assert saved.corrected_content == str(text_to_tei_xml(saved.raw_content))
    assert saved.corrected_content.startswith("<?xml")
    assert '<TEI xmlns="http://www.tei-c.org/ns/1.0">' in saved.corrected_content
    assert "<p>84 KATEDRA</p>" in saved.corrected_content
    assert "kamie<lb/>niami &lt;10&gt; &amp; tys." in saved.corrected_content
    assert saved.scan_uri == "https://polona.pl/s.jpg"
    assert not saved.ready


def test_get_page_passes_the_raw_text_to_the_converter(
    in_collection, polona, monkeypatch
):
    seen = []

    def converter(text):
        seen.append(text)
        return "<converted/>"

    monkeypatch.setattr("app.main.text_to_tei_xml", converter)

    response = client.get(f"/editions/{uuid4()}/pages/5")

    assert seen == ["Ala ma kota"]
    assert response.json()["content"] == "<converted/>"
    (saved,) = db.save_fetched_page.call_args.args
    assert (saved.raw_content, saved.corrected_content) == (
        "Ala ma kota",
        "<converted/>",
    )


def test_get_page_does_not_convert_a_stored_page(in_collection, polona, monkeypatch):
    db.get_page_by_number.return_value = Page(
        edition_id=uuid4(), number=5, raw_content="stored raw"
    )
    converter = mock.Mock()
    monkeypatch.setattr("app.main.text_to_tei_xml", converter)

    client.get(f"/editions/{uuid4()}/pages/5")

    converter.assert_not_called()


class TestTeiToText:
    VALID = str(text_to_tei_xml("84 KATEDRA\n\ncle przekazał kamie\nniami <10> & tys."))

    def test_returns_just_the_text(self):
        response = client.post("/tei/text", json={"xml": self.VALID})

        assert response.status_code == 200
        assert response.json() == {
            "text": "84 KATEDRA\n\ncle przekazał kamie\nniami <10> & tys."
        }

    def test_the_header_is_not_in_the_text(self):
        text = client.post("/tei/text", json={"xml": self.VALID}).json()["text"]

        assert "OCR text of a page" not in text
        assert "<" not in text.replace("<10>", "")

    @pytest.mark.parametrize(
        ("xml", "message"),
        [
            ("not xml at all", "not well-formed"),
            ("<a/>", "root element must be <TEI>"),
            ('<TEI xmlns="http://www.tei-c.org/ns/1.0"/>', "no <teiHeader>"),
            ("<!DOCTYPE x><TEI/>", "DOCTYPE and ENTITY"),
            ("", "empty"),
        ],
    )
    def test_invalid_xml_is_rejected_with_the_reason(self, xml, message):
        response = client.post("/tei/text", json={"xml": xml})

        assert response.status_code == 422
        assert message in response.json()["detail"]

    @pytest.mark.parametrize("body", [{}, {"xml": None}, {"xml": 5}, {"text": "x"}, []])
    def test_bad_request_bodies(self, body):
        assert client.post("/tei/text", json=body).status_code == 422

    def test_too_large_input_is_rejected(self):
        response = client.post("/tei/text", json={"xml": "x" * 1_000_001})

        assert response.status_code == 422

    def test_touches_neither_the_database_nor_polona(self, polona, monkeypatch):
        for name in ("get_page_by_number", "save_fetched_page", "update_page"):
            monkeypatch.setattr(db, name, mock.Mock())

        client.post("/tei/text", json={"xml": self.VALID})

        db.get_page_by_number.assert_not_called()
        db.save_fetched_page.assert_not_called()
        db.update_page.assert_not_called()
        polona.assert_not_called()

    def test_is_in_the_openapi_schema(self):
        paths = client.get("/openapi.json").json()["paths"]

        assert "post" in paths["/tei/text"]


class TestNer:
    ENTITIES = [
        Entity(text="Adam Mickiewicz", label="persName", start=0, end=15),
        Entity(text="Warszawie", label="placeName", start=29, end=38),
    ]

    @pytest.fixture
    def find(self, monkeypatch):
        mock_find = mock.Mock(return_value=self.ENTITIES)
        monkeypatch.setattr("app.main.find_entities", mock_find)
        return mock_find

    def test_returns_the_recognized_entities(self, find):
        text = "Adam Mickiewicz mieszkał w Warszawie."

        response = client.post("/ner", json={"text": text})

        assert response.status_code == 200
        assert response.json() == {
            "entities": [
                {"text": "Adam Mickiewicz", "label": "persName", "start": 0, "end": 15},
                {"text": "Warszawie", "label": "placeName", "start": 29, "end": 38},
            ]
        }
        find.assert_called_once_with(text)

    def test_no_entities_is_an_empty_list(self, find):
        find.return_value = []

        response = client.post("/ner", json={"text": "Ala ma kota."})

        assert response.status_code == 200
        assert response.json() == {"entities": []}

    def test_model_not_installed_is_a_503(self, find):
        find.side_effect = NerModelUnavailable("The spaCy model x is not installed")

        response = client.post("/ner", json={"text": "text"})

        assert response.status_code == 503
        assert "not installed" in response.json()["detail"]

    @pytest.mark.parametrize(
        "body", [{}, {"text": None}, {"text": 5}, {"xml": "x"}, []]
    )
    def test_bad_request_bodies(self, find, body):
        assert client.post("/ner", json=body).status_code == 422
        find.assert_not_called()

    def test_text_length_limit(self, find):
        assert client.post("/ner", json={"text": "x" * 100_000}).status_code == 200
        assert client.post("/ner", json={"text": "x" * 100_001}).status_code == 422

    def test_touches_neither_the_database_nor_polona(self, find, polona, monkeypatch):
        for name in ("get_page_by_number", "save_fetched_page", "update_page"):
            monkeypatch.setattr(db, name, mock.Mock())

        client.post("/ner", json={"text": "text"})

        db.get_page_by_number.assert_not_called()
        db.save_fetched_page.assert_not_called()
        db.update_page.assert_not_called()
        polona.assert_not_called()

    def test_is_in_the_openapi_schema(self):
        schema = client.get("/openapi.json").json()

        assert "post" in schema["paths"]["/ner"]
        assert {"NerRequest", "NerResult", "Entity"} <= schema["components"][
            "schemas"
        ].keys()


class TestModelLoadedAtStartup:
    @pytest.fixture(autouse=True)
    def fresh_model_state(self, monkeypatch):
        monkeypatch.setattr(ner, "_nlp", None)
        monkeypatch.setattr("app.main.get_checker", mock.Mock())  # tested below

    def test_the_model_is_loaded_when_the_backend_starts(self, monkeypatch):
        loaded = mock.Mock()
        monkeypatch.setattr("app.main.get_nlp", loaded)

        with TestClient(app):
            loaded.assert_called_once_with()

    def test_the_backend_only_serves_once_the_model_is_loaded(self, monkeypatch):
        events = []
        monkeypatch.setattr("app.main.get_nlp", lambda: events.append("loaded"))

        with TestClient(app) as started:
            events.append("first request")
            started.get("/openapi.json")

        assert events == ["loaded", "first request"]

    def test_requests_use_the_model_loaded_at_startup(self, monkeypatch):
        loads = []

        def load(name):
            loads.append(name)
            return spacy.blank("pl")

        monkeypatch.setattr(ner.spacy, "load", load)

        with TestClient(app) as started:
            assert loads == ["pl_core_news_lg"]  # loaded before any request
            for _ in range(3):
                assert started.post("/ner", json={"text": "Ala"}).status_code == 200

        assert loads == ["pl_core_news_lg"]  # and never again

    def test_it_is_not_loaded_without_starting_the_backend(self, monkeypatch):
        # the tests' plain `client` does not run the startup: no model needed
        loaded = mock.Mock()
        monkeypatch.setattr("app.main.get_nlp", loaded)

        client.get("/openapi.json")

        loaded.assert_not_called()

    def test_a_missing_model_does_not_stop_the_backend(self, monkeypatch, caplog):
        def missing(name):
            raise OSError("[E050] Can't find model")

        monkeypatch.setattr(ner.spacy, "load", missing)

        with (
            caplog.at_level(logging.WARNING, logger="uvicorn.error"),
            TestClient(app) as started,
        ):
            response = started.post("/ner", json={"text": "Ala"})
            other = started.post("/tei/text", json={"xml": "x"})

        assert "pl_core_news_lg is not installed" in caplog.text
        assert "503" in caplog.text
        assert response.status_code == 503
        assert other.status_code == 422  # the rest of the API works

    def test_the_model_is_still_loaded_on_demand_if_startup_could_not(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            ner.spacy, "load", mock.Mock(side_effect=OSError("not yet"))
        )
        with TestClient(app) as started:
            assert started.post("/ner", json={"text": "Ala"}).status_code == 503
            monkeypatch.setattr(ner.spacy, "load", lambda name: spacy.blank("pl"))

            assert started.post("/ner", json={"text": "Ala"}).status_code == 200


class TestTagEntity:
    TEXT = "Adam Mickiewicz mieszkał w Paryżu.\nPotem w Warszawie."
    XML = str(text_to_tei_xml(TEXT))

    def body(self, entity, label="persName", **extra):
        start = self.TEXT.index(entity)
        return {
            "start": start,
            "end": start + len(entity),
            "text": entity,
            "label": label,
            **extra,
        }

    def url(self, edition_id=None, number=4):
        return f"/editions/{edition_id or uuid4()}/pages/{number}/entities"

    @pytest.fixture
    def stored(self, monkeypatch):
        """A stored page with this TEI XML, and an update_page that stores it."""
        state = {
            "page": Page(
                edition_id=uuid4(),
                number=4,
                raw_content=self.TEXT,
                corrected_content=self.XML,
                scan_uri="https://polona.pl/s.jpg",
            )
        }

        def update(edition_id, number, **changes):
            state["page"] = state["page"].model_copy(update=changes)
            return state["page"]

        monkeypatch.setattr(
            db, "get_page_by_number", mock.Mock(side_effect=lambda *a: state["page"])
        )
        monkeypatch.setattr(db, "update_page", mock.Mock(side_effect=update))
        return state

    def test_marks_the_entity_and_saves_the_new_xml(self, stored):
        page = stored["page"]

        response = client.post(
            self.url(page.edition_id), json=self.body("Adam Mickiewicz")
        )

        assert response.status_code == 200
        assert (
            "<persName>Adam Mickiewicz</persName> mieszkał"
            in response.json()["content"]
        )
        db.update_page.assert_called_once()
        args, kwargs = db.update_page.call_args
        assert args == (page.edition_id, 4)
        assert list(kwargs) == ["corrected_content"]  # nothing else is touched
        assert "<persName>Adam Mickiewicz</persName>" in kwargs["corrected_content"]

    def test_the_response_is_the_updated_page_view(self, stored):
        page = stored["page"]

        body = client.post(
            self.url(page.edition_id), json=self.body("Paryżu", "placeName")
        ).json()

        assert set(body) == {
            "id",
            "edition_id",
            "number",
            "scan_uri",
            "ready",
            "content",
        }
        assert body["id"] == str(page.id)
        assert "<placeName>Paryżu</placeName>" in body["content"]

    def test_the_text_does_not_change_so_the_offsets_stay_valid(self, stored):
        page = stored["page"]
        entities = [
            ("Adam Mickiewicz", "persName"),
            ("Paryżu", "placeName"),
            ("Warszawie", "placeName"),
        ]
        bodies = [
            self.body(entity, label) for entity, label in entities
        ]  # all up front

        for body in bodies:
            assert client.post(self.url(page.edition_id), json=body).status_code == 200

        xml = stored["page"].corrected_content
        assert tei_xml_to_text(xml) == self.TEXT
        for entity, label in entities:
            assert f"<{label}>{entity}</{label}>" in xml

    def test_marking_it_again_changes_nothing(self, stored):
        page = stored["page"]
        client.post(self.url(page.edition_id), json=self.body("Paryżu", "placeName"))
        db.update_page.reset_mock()

        response = client.post(
            self.url(page.edition_id), json=self.body("Paryżu", "placeName")
        )

        assert response.status_code == 200
        db.update_page.assert_not_called()

    def test_page_not_stored(self, stored):
        db.get_page_by_number.side_effect = None
        db.get_page_by_number.return_value = None

        response = client.post(self.url(), json=self.body("Paryżu"))

        assert response.status_code == 404
        assert response.json()["detail"] == "Page not found"
        db.update_page.assert_not_called()

    def test_the_text_at_the_offsets_is_not_the_entity(self, stored):
        body = self.body("Paryżu") | {"text": "Londynie"}

        response = client.post(self.url(stored["page"].edition_id), json=body)

        assert response.status_code == 409
        assert "page has changed" in response.json()["detail"]
        db.update_page.assert_not_called()

    def test_offsets_outside_the_text(self, stored):
        body = {"start": 900, "end": 910, "text": "Paryżu", "label": "placeName"}

        response = client.post(self.url(stored["page"].edition_id), json=body)

        assert response.status_code == 409
        db.update_page.assert_not_called()

    @pytest.mark.parametrize(
        ("corrected_content", "message"),
        [
            (None, "no TEI XML"),
            ("", "no TEI XML"),
            ("Adam Mickiewicz mieszkał w Paryżu.", "not well-formed"),
            ("<a/>", "root element must be <TEI>"),
        ],
    )
    def test_a_page_without_valid_tei_xml(self, stored, corrected_content, message):
        stored["page"] = stored["page"].model_copy(
            update={"corrected_content": corrected_content}
        )

        response = client.post(
            self.url(stored["page"].edition_id), json=self.body("Paryżu")
        )

        assert response.status_code == 422
        assert message in response.json()["detail"]
        db.update_page.assert_not_called()

    def test_an_entity_that_cannot_be_marked(self, stored):
        # "Paryżu.\nPotem" spans a line break: fine; paragraphs are what fails
        text = "one two\n\nthree four"
        stored["page"] = stored["page"].model_copy(
            update={"corrected_content": str(text_to_tei_xml(text))}
        )
        body = {"start": 4, "end": 14, "text": "two\n\nthree", "label": "persName"}

        response = client.post(self.url(stored["page"].edition_id), json=body)

        assert response.status_code == 422
        assert "more than one paragraph" in response.json()["detail"]
        db.update_page.assert_not_called()

    @pytest.mark.parametrize(
        "label", ["persName", "placeName", "orgName", "geogName", "date", "time"]
    )
    def test_every_label_of_the_model(self, stored, label):
        response = client.post(
            self.url(stored["page"].edition_id), json=self.body("Paryżu", label)
        )

        assert response.status_code == 200
        assert f"<{label}>Paryżu</{label}>" in response.json()["content"]

    @pytest.mark.parametrize(
        "change",
        [
            {"label": "p"},
            {"label": "script"},
            {"label": "PersName"},
            {"label": ""},
            {"label": None},
            {"end": 0},  # not after start
            {"start": 5, "end": 5},
            {"start": -1},
            {"text": ""},
            {"text": None},
            {"start": "a"},
            {"raw_content": "x"},  # not a field of this request
            {"corrected_content": "<TEI/>"},
        ],
    )
    def test_bad_requests_are_rejected_before_anything_happens(self, stored, change):
        body = self.body("Paryżu", "placeName") | change

        response = client.post(self.url(stored["page"].edition_id), json=body)

        assert response.status_code == 422
        db.get_page_by_number.assert_not_called()
        db.update_page.assert_not_called()

    @pytest.mark.parametrize("missing", ["start", "end", "text", "label"])
    def test_every_field_is_needed(self, stored, missing):
        body = self.body("Paryżu", "placeName")
        del body[missing]

        assert client.post(self.url(), json=body).status_code == 422

    def test_invalid_path_arguments(self, stored):
        body = self.body("Paryżu")

        assert (
            client.post("/editions/nope/pages/4/entities", json=body).status_code == 422
        )
        assert client.post(self.url(number=-1), json=body).status_code == 422

    def test_does_not_check_the_collection_or_call_polona(
        self, polona, stored, monkeypatch
    ):  # `stored` last: it replaces the database mock that `polona` sets
        check = mock.Mock(return_value=False)
        monkeypatch.setattr(db, "check_edition_in_collection", check)

        response = client.post(
            self.url(stored["page"].edition_id), json=self.body("Paryżu", "placeName")
        )

        assert response.status_code == 200
        check.assert_not_called()
        polona.assert_not_called()

    def test_is_in_the_openapi_schema(self):
        schema = client.get("/openapi.json").json()

        path = "/editions/{edition_id}/pages/{number}/entities"
        assert "post" in schema["paths"][path]
        assert "EntityTag" in schema["components"]["schemas"]


class TestDictionaryLoadedAtStartup:
    @pytest.fixture(autouse=True)
    def no_model(self, monkeypatch):
        monkeypatch.setattr("app.main.get_nlp", mock.Mock())  # not what is tested

    def test_the_dictionary_is_loaded_when_the_backend_starts(self, monkeypatch):
        loaded = mock.Mock()
        monkeypatch.setattr("app.main.get_checker", loaded)

        with TestClient(app):
            loaded.assert_called_once_with()

    def test_it_is_not_loaded_without_starting_the_backend(self, monkeypatch):
        loaded = mock.Mock()
        monkeypatch.setattr("app.main.get_checker", loaded)

        client.get("/openapi.json")

        loaded.assert_not_called()

    def test_requests_use_the_dictionary_loaded_at_startup(self, monkeypatch):
        monkeypatch.setattr(spelling, "_checker", None)
        loads = []

        class Checker:
            def lookup(self, word):
                return True

        def load(language):
            loads.append(language)
            return Checker()

        monkeypatch.setattr(spelling.phunspell, "Phunspell", load)

        with TestClient(app) as started:
            assert loads == ["pl_PL"]  # loaded before any request
            for _ in range(3):
                assert (
                    started.post("/spellcheck", json={"text": "Ala"}).status_code == 200
                )

        assert loads == ["pl_PL"]  # and never again

    def test_a_missing_dictionary_does_not_stop_the_backend(self, monkeypatch, caplog):
        monkeypatch.setattr(spelling, "_checker", None)

        def missing(language):
            raise spelling.phunspell.phunspell.PhunspellError("dictionary not found")

        monkeypatch.setattr(spelling.phunspell, "Phunspell", missing)

        with (
            caplog.at_level(logging.WARNING, logger="uvicorn.error"),
            TestClient(app) as started,
        ):
            response = started.post("/spellcheck", json={"text": "Ala"})
            other = started.post("/tei/text", json={"xml": "x"})

        assert "dictionary is not available" in caplog.text
        assert "/spellcheck will answer 503" in caplog.text
        assert response.status_code == 503
        assert other.status_code == 422  # the rest of the API works


class TestSpellcheck:
    @pytest.fixture
    def check(self, monkeypatch):
        words = [
            Word(text="Ala", start=0, end=3, correct=True),
            Word(text="jey", start=7, end=10, correct=False),
        ]
        mock_check = mock.Mock(return_value=words)
        monkeypatch.setattr("app.main.check_words", mock_check)
        return mock_check

    def test_returns_every_word_with_its_verdict(self, check):
        response = client.post("/spellcheck", json={"text": "Ala ma jey"})

        assert response.status_code == 200
        assert response.json() == {
            "words": [
                {"text": "Ala", "start": 0, "end": 3, "correct": True},
                {"text": "jey", "start": 7, "end": 10, "correct": False},
            ]
        }
        check.assert_called_once_with("Ala ma jey", ())  # no edition: no legal ones

    def test_no_words_is_an_empty_list(self, check):
        check.return_value = []

        assert client.post("/spellcheck", json={"text": "..."}).json() == {"words": []}

    def test_dictionary_not_available_is_a_503(self, check):
        check.side_effect = SpellcheckUnavailable(
            "The pl_PL dictionary is not available"
        )

        response = client.post("/spellcheck", json={"text": "Ala"})

        assert response.status_code == 503
        assert "not available" in response.json()["detail"]

    @pytest.mark.parametrize(
        "body", [{}, {"text": None}, {"text": 5}, {"xml": "x"}, []]
    )
    def test_bad_request_bodies(self, check, body):
        assert client.post("/spellcheck", json=body).status_code == 422
        check.assert_not_called()

    def test_text_length_limit(self, check):
        assert (
            client.post("/spellcheck", json={"text": "x" * 100_000}).status_code == 200
        )
        assert (
            client.post("/spellcheck", json={"text": "x" * 100_001}).status_code == 422
        )

    def test_touches_neither_the_database_nor_polona(self, check, polona, monkeypatch):
        for name in ("get_page_by_number", "save_fetched_page", "update_page"):
            monkeypatch.setattr(db, name, mock.Mock())

        client.post("/spellcheck", json={"text": "Ala"})

        db.get_page_by_number.assert_not_called()
        db.update_page.assert_not_called()
        polona.assert_not_called()

    def test_with_the_real_dictionary(self):
        text = "Ala ma kota, a jey nie ma."

        response = client.post("/spellcheck", json={"text": text})

        assert response.status_code == 200
        words = response.json()["words"]
        assert [w["text"] for w in words] == [
            "Ala",
            "ma",
            "kota",
            "a",
            "jey",
            "nie",
            "ma",
        ]
        assert [w["text"] for w in words if not w["correct"]] == ["jey"]
        assert all(text[w["start"] : w["end"]] == w["text"] for w in words)

    def test_is_in_the_openapi_schema(self):
        schema = client.get("/openapi.json").json()

        assert "post" in schema["paths"]["/spellcheck"]
        assert {"SpellcheckRequest", "SpellcheckResult", "Word"} <= schema[
            "components"
        ]["schemas"].keys()


class TestCorrectWord:
    TEXT = "Wielu Biskupów krak., że dawnieyszych kościół.\nTo jey tey."
    XML = str(text_to_tei_xml(TEXT))

    def body(self, word, after, occurrence=0):
        start = -1
        for _ in range(occurrence + 1):
            start = self.TEXT.index(word, start + 1)
        return {
            "start": start,
            "end": start + len(word),
            "before": word,
            "after": after,
        }

    def url(self, edition_id=None, number=4):
        return f"/editions/{edition_id or uuid4()}/pages/{number}/corrections"

    @pytest.fixture
    def stored(self, monkeypatch):
        """A stored page, and an apply_correction that works like the real one."""
        state = {
            "page": Page(
                edition_id=uuid4(),
                number=4,
                raw_content=self.TEXT,
                corrected_content=self.XML,
            ),
            "corrections": [],
        }

        def apply(edition_id, number, replace, before, after):
            page = state["page"]
            if (edition_id, number) != (page.edition_id, page.number):
                return None
            new_xml = replace(page.corrected_content or "")  # may raise: nothing saved
            state["page"] = page.model_copy(update={"corrected_content": new_xml})
            state["corrections"].append((before, after))
            return state["page"]

        monkeypatch.setattr(db, "apply_correction", mock.Mock(side_effect=apply))
        return state

    def post(self, stored, body):
        return client.post(self.url(stored["page"].edition_id), json=body)

    def test_replaces_the_word_in_the_xml_and_records_the_correction(self, stored):
        response = self.post(stored, self.body("dawnieyszych", "dawniejszych"))

        assert response.status_code == 200
        assert "dawniejszych" in response.json()["content"]
        assert "dawnieyszych" not in response.json()["content"]
        assert stored["corrections"] == [("dawnieyszych", "dawniejszych")]
        edition_id, number, replace, before, after = db.apply_correction.call_args.args
        assert (edition_id, number) == (stored["page"].edition_id, 4)
        assert (before, after) == ("dawnieyszych", "dawniejszych")
        assert callable(replace)

    def test_the_text_afterwards_is_the_old_text_with_the_word_replaced(self, stored):
        self.post(stored, self.body("dawnieyszych", "dawniejszych"))

        xml = stored["page"].corrected_content
        assert tei_xml_to_text(xml) == self.TEXT.replace("dawnieyszych", "dawniejszych")

    def test_the_response_is_the_updated_page_view(self, stored):
        body = self.post(stored, self.body("jey", "jej")).json()

        assert set(body) == {
            "id",
            "edition_id",
            "number",
            "scan_uri",
            "ready",
            "content",
        }
        assert body["id"] == str(stored["page"].id)

    def test_only_the_word_at_the_offsets_is_replaced(self, stored):
        self.post(stored, self.body("jey", "jej"))
        xml = stored["page"].corrected_content

        assert "To jej tey" in tei_xml_to_text(xml)

    def test_keeps_the_entities_that_are_marked_in_the_xml(self, stored):
        stored["page"] = stored["page"].model_copy(
            update={
                "corrected_content": self.XML.replace(
                    "kościół", "<placeName>kościół</placeName>"
                )
            }
        )

        self.post(stored, self.body("kościół", "kościoł"))

        assert "<placeName>kościoł</placeName>" in stored["page"].corrected_content

    def test_page_not_stored(self, stored):
        response = client.post(self.url(), json=self.body("jey", "jej"))

        assert response.status_code == 404
        assert response.json()["detail"] == "Page not found"
        assert stored["corrections"] == []

    def test_the_text_at_the_offsets_is_not_the_old_word(self, stored):
        body = self.body("jey", "jej") | {"before": "xyz"}

        response = self.post(stored, body)

        assert response.status_code == 409
        assert "page has changed" in response.json()["detail"]
        assert stored["corrections"] == []
        assert stored["page"].corrected_content == self.XML

    def test_offsets_outside_the_text(self, stored):
        body = {"start": 900, "end": 903, "before": "jey", "after": "jej"}

        assert self.post(stored, body).status_code == 409

    @pytest.mark.parametrize(
        ("corrected_content", "message"),
        [
            (None, "no TEI XML"),
            ("", "no TEI XML"),
            ("To jey tey.", "not well-formed"),
            ("<a/>", "root element must be <TEI>"),
        ],
    )
    def test_a_page_without_valid_tei_xml(self, stored, corrected_content, message):
        stored["page"] = stored["page"].model_copy(
            update={"corrected_content": corrected_content}
        )

        response = self.post(stored, self.body("jey", "jej"))

        assert response.status_code == 422
        assert message in response.json()["detail"]
        assert stored["corrections"] == []

    @pytest.mark.parametrize(
        ("after", "message"),
        [
            (" jej", "start or end with a space"),
            ("jej ", "start or end with a space"),
            ("je\nj", "one line"),
            ("jey", "same as the old"),
        ],
    )
    def test_a_new_text_that_is_not_acceptable(self, stored, after, message):
        response = self.post(stored, self.body("jey", after))

        assert response.status_code == 422
        assert message in response.json()["detail"]
        assert stored["corrections"] == []
        assert stored["page"].corrected_content == self.XML

    def test_the_old_text_cannot_have_spaces(self, stored):
        response = self.post(stored, self.body("To jey", "Tojey"))

        assert response.status_code == 422
        assert "spaces or line breaks" in response.json()["detail"]

    @pytest.mark.parametrize(
        "change",
        [
            {"after": ""},
            {"before": ""},
            {"after": None},
            {"end": 0},
            {"start": 5, "end": 5},
            {"start": -1},
            {"start": "a"},
            {"raw_content": "x"},  # not a field of this request
            {"corrected_content": "<TEI/>"},
        ],
    )
    def test_bad_requests_are_rejected_before_anything_happens(self, stored, change):
        response = self.post(stored, self.body("jey", "jej") | change)

        assert response.status_code == 422
        db.apply_correction.assert_not_called()

    @pytest.mark.parametrize("missing", ["start", "end", "before", "after"])
    def test_every_field_is_needed(self, stored, missing):
        body = self.body("jey", "jej")
        del body[missing]

        assert client.post(self.url(), json=body).status_code == 422

    def test_invalid_path_arguments(self, stored):
        body = self.body("jey", "jej")

        assert (
            client.post("/editions/nope/pages/4/corrections", json=body).status_code
            == 422
        )
        assert client.post(self.url(number=-1), json=body).status_code == 422

    def test_does_not_check_the_collection_or_call_polona(
        self, polona, stored, monkeypatch
    ):
        check = mock.Mock(return_value=False)
        monkeypatch.setattr(db, "check_edition_in_collection", check)

        response = self.post(stored, self.body("jey", "jej"))

        assert response.status_code == 200
        check.assert_not_called()
        polona.assert_not_called()

    def test_is_in_the_openapi_schema(self):
        schema = client.get("/openapi.json").json()

        path = "/editions/{edition_id}/pages/{number}/corrections"
        assert "post" in schema["paths"][path]
        assert "WordCorrection" in schema["components"]["schemas"]


class TestAddLegalSpelling:
    def url(self, edition_id=None):
        return f"/editions/{edition_id or uuid4()}/legal-spellings"

    @pytest.fixture
    def stored(self, monkeypatch):
        """A stored edition, and an add_legal_spelling that remembers what it got."""
        edition = Edition(work_id=uuid4(), place="Warszawa", year=1890)
        state = {"edition": edition, "added": set()}

        def add(legal):
            key = (legal.edition_id, legal.spelling)
            new = key not in state["added"]
            state["added"].add(key)
            return new

        monkeypatch.setattr(
            db,
            "get_edition",
            mock.Mock(side_effect=lambda i: edition if i == edition.id else None),
        )
        monkeypatch.setattr(db, "add_legal_spelling", mock.Mock(side_effect=add))
        return state

    def post(self, stored, body):
        return client.post(self.url(stored["edition"].id), json=body)

    def test_adds_the_spelling_and_says_so(self, stored):
        edition_id = stored["edition"].id

        response = self.post(stored, {"spelling": "jey"})

        assert response.status_code == 201
        assert response.json() == {"edition_id": str(edition_id), "spelling": "jey"}
        db.add_legal_spelling.assert_called_once_with(
            LegalSpelling(edition_id=edition_id, spelling="jey")
        )

    def test_the_same_spelling_again_is_ok_and_says_it_was_there(self, stored):
        self.post(stored, {"spelling": "jey"})

        response = self.post(stored, {"spelling": "jey"})

        assert response.status_code == 200
        assert response.json()["spelling"] == "jey"
        assert len(stored["added"]) == 1

    def test_another_spelling_of_the_same_edition_is_added(self, stored):
        assert self.post(stored, {"spelling": "jey"}).status_code == 201
        assert self.post(stored, {"spelling": "tey"}).status_code == 201
        assert self.post(stored, {"spelling": "Jey"}).status_code == 201  # case matters

        assert len(stored["added"]) == 3

    def test_edition_not_stored(self, stored):
        response = client.post(self.url(), json={"spelling": "jey"})

        assert response.status_code == 404
        assert response.json()["detail"] == "Edition not found"
        db.add_legal_spelling.assert_not_called()

    def test_the_edition_removed_in_the_meantime(self, stored):
        db.add_legal_spelling.side_effect = psycopg.errors.ForeignKeyViolation()

        response = self.post(stored, {"spelling": "jey"})

        assert response.status_code == 404
        assert response.json()["detail"] == "Edition not found"

    @pytest.mark.parametrize(
        "body",
        [
            {"spelling": ""},
            {"spelling": " "},
            {"spelling": " jey"},
            {"spelling": "jey "},
            {"spelling": "je y"},
            {"spelling": "je\ny"},
            {"spelling": "je\x00y"},
            {"spelling": "x" * 101},
            {"spelling": None},
            {"spelling": 5},
            {},
            {"word": "jey"},
            {
                "spelling": "jey",
                "edition_id": str(uuid4()),
            },  # the edition is in the path
            {"spelling": "jey", "extra": 1},
            [],
        ],
    )
    def test_bad_requests_are_rejected_before_anything_happens(self, stored, body):
        response = self.post(stored, body)

        assert response.status_code == 422
        db.get_edition.assert_not_called()
        db.add_legal_spelling.assert_not_called()

    @pytest.mark.parametrize(
        "spelling", ["jey", "Jey", "biało-czerwony", "o’Hara", "1840", "x" * 100]
    )
    def test_spellings_that_are_accepted(self, stored, spelling):
        assert self.post(stored, {"spelling": spelling}).status_code == 201

    def test_invalid_edition_id(self, stored):
        assert (
            client.post(
                "/editions/nope/legal-spellings", json={"spelling": "jey"}
            ).status_code
            == 422
        )

    def test_does_not_check_the_collection_or_call_polona(
        self, polona, stored, monkeypatch
    ):
        check = mock.Mock(return_value=False)
        monkeypatch.setattr(db, "check_edition_in_collection", check)

        response = self.post(stored, {"spelling": "jey"})

        assert response.status_code == 201
        check.assert_not_called()
        polona.assert_not_called()

    def test_changes_nothing_but_the_spelling(self, stored, monkeypatch):
        for name in (
            "update_page",
            "save_fetched_page",
            "apply_correction",
            "upsert_edition",
        ):
            monkeypatch.setattr(db, name, mock.Mock())

        self.post(stored, {"spelling": "jey"})

        for name in (
            "update_page",
            "save_fetched_page",
            "apply_correction",
            "upsert_edition",
        ):
            getattr(db, name).assert_not_called()

    def test_is_in_the_openapi_schema(self):
        schema = client.get("/openapi.json").json()

        assert "post" in schema["paths"]["/editions/{edition_id}/legal-spellings"]
        assert {"LegalSpelling", "NewLegalSpelling"} <= schema["components"][
            "schemas"
        ].keys()


class TestSpellcheckWithLegalSpellings:
    TEXT = "Ala ma jey tey nie ma. Jey!"

    @pytest.fixture
    def legal(self, monkeypatch):
        """The legal spellings of one edition, and a dictionary that knows only
        the ordinary words."""
        edition_id = uuid4()
        spellings = {"jey"}

        class Dictionary:
            def lookup(self, word):
                return word.lower() in {"ala", "ma", "nie"}

        monkeypatch.setattr(spelling, "_checker", Dictionary())
        monkeypatch.setattr(
            db,
            "get_legal_spellings",
            mock.Mock(side_effect=lambda i: spellings if i == edition_id else set()),
        )
        return edition_id

    def wrong(self, response):
        return [w["text"] for w in response.json()["words"] if not w["correct"]]

    def test_a_legal_spelling_of_the_edition_is_correct(self, legal):
        response = client.post(
            "/spellcheck", json={"text": self.TEXT, "edition_id": str(legal)}
        )

        assert response.status_code == 200
        assert self.wrong(response) == ["tey"]

    def test_capital_letters_of_a_legal_spelling_are_correct_too(self, legal):
        response = client.post(
            "/spellcheck", json={"text": "Jey JEY jey", "edition_id": str(legal)}
        )

        assert self.wrong(response) == []

    def test_without_an_edition_nothing_is_legal(self, legal):
        response = client.post("/spellcheck", json={"text": self.TEXT})

        assert self.wrong(response) == ["jey", "tey", "Jey"]
        db.get_legal_spellings.assert_not_called()

    def test_another_edition_has_other_spellings(self, legal):
        response = client.post(
            "/spellcheck", json={"text": self.TEXT, "edition_id": str(uuid4())}
        )

        assert self.wrong(response) == ["jey", "tey", "Jey"]

    def test_the_words_themselves_are_the_same(self, legal):
        with_edition = client.post(
            "/spellcheck", json={"text": self.TEXT, "edition_id": str(legal)}
        ).json()["words"]
        without = client.post("/spellcheck", json={"text": self.TEXT}).json()["words"]

        assert [(w["text"], w["start"], w["end"]) for w in with_edition] == [
            (w["text"], w["start"], w["end"]) for w in without
        ]

    def test_looks_the_spellings_up_by_the_edition(self, legal):
        client.post("/spellcheck", json={"text": "jey", "edition_id": str(legal)})

        db.get_legal_spellings.assert_called_once_with(legal)

    @pytest.mark.parametrize("edition_id", ["nope", 5, "", []])
    def test_the_edition_must_be_a_uuid(self, legal, edition_id):
        response = client.post(
            "/spellcheck", json={"text": "jey", "edition_id": edition_id}
        )

        assert response.status_code == 422

    def test_a_null_edition_is_the_same_as_none(self, legal):
        response = client.post("/spellcheck", json={"text": "jey", "edition_id": None})

        assert response.status_code == 200
        assert self.wrong(response) == ["jey"]


class TestMarkPageSection:
    TEXT = "84 KATEDRA\n\ncle przekazał\nI. Alfa\nII. Beta\nkoniec\n\nstopka 85"
    XML = str(text_to_tei_xml(TEXT))

    def body(self, selection, section, **extra):
        start = self.TEXT.index(selection)
        return {
            "start": start,
            "end": start + len(selection),
            "text": selection,
            "section": section,
            **extra,
        }

    def url(self, edition_id=None, number=4):
        return f"/editions/{edition_id or uuid4()}/pages/{number}/sections"

    @pytest.fixture
    def stored(self, monkeypatch):
        state = {
            "page": Page(
                edition_id=uuid4(),
                number=4,
                raw_content=self.TEXT,
                corrected_content=self.XML,
                scan_uri="https://polona.pl/s.jpg",
            )
        }

        def update(edition_id, number, **changes):
            state["page"] = state["page"].model_copy(update=changes)
            return state["page"]

        monkeypatch.setattr(
            db, "get_page_by_number", mock.Mock(side_effect=lambda *a: state["page"])
        )
        monkeypatch.setattr(db, "update_page", mock.Mock(side_effect=update))
        return state

    def post(self, stored, body):
        return client.post(self.url(stored["page"].edition_id), json=body)

    @pytest.mark.parametrize(
        ("selection", "section", "markup"),
        [
            ("84 KATEDRA", "header", '<fw type="header">84 KATEDRA</fw>'),
            ("stopka 85", "footer", '<fw type="footer">stopka 85</fw>'),
            ("85", "page_number", '<fw type="pageNum">85</fw>'),
        ],
    )
    def test_marks_the_text_with_the_element_of_the_section(
        self, stored, selection, section, markup
    ):
        response = self.post(stored, self.body(selection, section))

        assert response.status_code == 200
        assert markup in stored["page"].corrected_content
        assert tei_xml_to_text(stored["page"].corrected_content) == self.TEXT

    def test_the_table_of_contents_is_a_div_around_paragraphs(self, stored):
        response = self.post(stored, self.body("I. Alfa\nII. Beta", "toc"))

        assert response.status_code == 200
        xml = stored["page"].corrected_content
        assert '<div type="toc"><p><lb/>I. Alfa<lb/>II. Beta</p></div>' in xml
        assert "<div><p><lb/>koniec</p><p>stopka 85</p></div>" in xml

    def test_saves_the_new_xml_as_the_corrected_content_and_nothing_else(self, stored):
        page = stored["page"]

        self.post(stored, self.body("84 KATEDRA", "header"))

        db.update_page.assert_called_once()
        args, kwargs = db.update_page.call_args
        assert args == (page.edition_id, 4)
        assert list(kwargs) == ["corrected_content"]

    def test_the_response_is_the_updated_page_view(self, stored):
        body = self.post(stored, self.body("84 KATEDRA", "header")).json()

        assert set(body) == {
            "id",
            "edition_id",
            "number",
            "scan_uri",
            "ready",
            "content",
        }
        assert '<fw type="header">84 KATEDRA</fw>' in body["content"]

    def test_marking_again_changes_nothing_and_saves_nothing(self, stored):
        self.post(stored, self.body("84 KATEDRA", "header"))
        db.update_page.reset_mock()

        response = self.post(stored, self.body("84 KATEDRA", "header"))

        assert response.status_code == 200
        db.update_page.assert_not_called()

    def test_page_not_stored(self, stored):
        db.get_page_by_number.side_effect = None
        db.get_page_by_number.return_value = None

        response = client.post(self.url(), json=self.body("84 KATEDRA", "header"))

        assert response.status_code == 404
        assert response.json()["detail"] == "Page not found"
        db.update_page.assert_not_called()

    def test_the_text_at_the_offsets_is_not_the_selection(self, stored):
        body = self.body("84 KATEDRA", "header") | {"text": "other"}

        response = self.post(stored, body)

        assert response.status_code == 409
        assert "page has changed" in response.json()["detail"]
        db.update_page.assert_not_called()

    @pytest.mark.parametrize(
        ("selection", "section", "message"),
        [
            ("84 KATEDRA\n\ncle przekazał", "header", "more than one paragraph"),
            ("koniec\n\nstopka 85", "footer", "more than one paragraph"),
        ],
    )
    def test_a_section_that_cannot_be_marked_is_a_422_with_the_reason(
        self, stored, selection, section, message
    ):
        response = self.post(stored, self.body(selection, section))

        assert response.status_code == 422
        assert message in response.json()["detail"]
        db.update_page.assert_not_called()

    @pytest.mark.parametrize(
        ("corrected_content", "message"),
        [
            (None, "no TEI XML"),
            ("", "no TEI XML"),
            ("84 KATEDRA", "not well-formed"),
            ("<a/>", "root element must be <TEI>"),
        ],
    )
    def test_a_page_without_valid_tei_xml(self, stored, corrected_content, message):
        stored["page"] = stored["page"].model_copy(
            update={"corrected_content": corrected_content}
        )

        response = self.post(stored, self.body("84 KATEDRA", "header"))

        assert response.status_code == 422
        assert message in response.json()["detail"]
        db.update_page.assert_not_called()

    @pytest.mark.parametrize(
        "change",
        [
            {"section": "page"},
            {"section": "div"},
            {"section": "persName"},
            {"section": "Header"},
            {"section": ""},
            {"section": None},
            {"end": 0},
            {"start": 5, "end": 5},
            {"start": -1},
            {"text": ""},
            {"text": None},
            {"raw_content": "x"},  # not a field of this request
            {"label": "persName"},  # an entity field
        ],
    )
    def test_bad_requests_are_rejected_before_anything_happens(self, stored, change):
        response = self.post(stored, self.body("84 KATEDRA", "header") | change)

        assert response.status_code == 422
        db.get_page_by_number.assert_not_called()
        db.update_page.assert_not_called()

    @pytest.mark.parametrize("missing", ["start", "end", "text", "section"])
    def test_every_field_is_needed(self, stored, missing):
        body = self.body("84 KATEDRA", "header")
        del body[missing]

        assert client.post(self.url(), json=body).status_code == 422

    def test_invalid_path_arguments(self, stored):
        body = self.body("84 KATEDRA", "header")

        assert (
            client.post("/editions/nope/pages/4/sections", json=body).status_code == 422
        )
        assert client.post(self.url(number=-1), json=body).status_code == 422

    def test_does_not_check_the_collection_or_call_polona(
        self, polona, stored, monkeypatch
    ):
        check = mock.Mock(return_value=False)
        monkeypatch.setattr(db, "check_edition_in_collection", check)

        response = self.post(stored, self.body("84 KATEDRA", "header"))

        assert response.status_code == 200
        check.assert_not_called()
        polona.assert_not_called()

    def test_is_in_the_openapi_schema(self):
        schema = client.get("/openapi.json").json()

        path = "/editions/{edition_id}/pages/{number}/sections"
        assert "post" in schema["paths"][path]
        assert "SectionMark" in schema["components"]["schemas"]

import logging
from unittest import mock
from uuid import uuid4

import psycopg.errors
import pytest
import spacy
from fastapi.testclient import TestClient
from fastapi_pagination import paginate

import app.page_provider as page_provider
import app.text_tools.ner as ner
import app.text_tools.spelling as spelling
from app.main import app, db
from app.persistence.db import WorkExists
from app.persistence.ontology import Edition, EditionMetadata, LegalSpelling, Page
from app.polona.polona_connector import PolonaError
from app.text_tools.ner import NerModelUnavailable
from app.text_tools.spelling import SpellcheckUnavailable
from app.text_tools.tei_xml import (
    mark_section,
    tag_text_range,
    tei_xml_to_text,
    text_to_tei_xml,
)
from app.view_models import Entity, PageView, Word

client = TestClient(app)


META = {
    "author": "Prus, Bolesław (1847-1912)",
    "title": "Lalka",
    "place": "Warszawa",
    "year": 1890,
}


@pytest.fixture
def polona(monkeypatch):
    monkeypatch.setattr(
        "app.page_provider.fetch_edition_metadata", mock.Mock(return_value=META)
    )
    monkeypatch.setattr(db, "get_edition", mock.Mock(return_value=None))
    monkeypatch.setattr(db, "get_page_by_number", mock.Mock(return_value=None))
    monkeypatch.setattr(db, "create_work", mock.Mock(side_effect=lambda work: work))
    monkeypatch.setattr(
        db, "create_edition", mock.Mock(side_effect=lambda edition: edition)
    )
    monkeypatch.setattr(db, "save_fetched_page", mock.Mock(side_effect=lambda p: p))
    fetch = mock.Mock(
        return_value={
            "scan_uri": "https://polona.pl/iiif/3/x/full/max/0/default.jpg",
            "raw_ocr_content": "Ala ma kota",
        }
    )
    monkeypatch.setattr("app.page_provider.request_polona_edition_page", fetch)
    return fetch


def test_get_page_returns_polona_content(polona):
    edition_id = uuid4()

    response = client.get(f"/editions/{edition_id}/pages/0")

    assert response.status_code == 200
    body = response.json()
    assert body["edition_id"] == str(edition_id)
    assert body["number"] == 0
    assert body["content"] == str(text_to_tei_xml("Ala ma kota"))  # TEI, not raw
    assert "raw_ocr_content" not in body
    assert "xml_content_corrected" not in body
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
def test_get_page_polona_errors(polona, error, expected_status):
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


class TestEditionMetadata:
    META = EditionMetadata(
        id=uuid4(),
        work_id=uuid4(),
        work_editions=2,
        author="Prus, Bolesław",
        title="Lalka",
        place="Warszawa",
        year=1890,
    )

    def url(self, edition_id=None):
        return f"/editions/{edition_id or self.META.id}/metadata"

    def test_get_gives_the_metadata(self, monkeypatch):
        monkeypatch.setattr(
            db, "get_edition_metadata", mock.Mock(return_value=self.META)
        )

        response = client.get(self.url())

        assert response.status_code == 200
        assert response.json() == {
            "id": str(self.META.id),
            "work_id": str(self.META.work_id),
            "work_editions": 2,
            "author": "Prus, Bolesław",
            "title": "Lalka",
            "place": "Warszawa",
            "year": 1890,
        }

    def test_get_an_edition_that_is_not_stored(self, monkeypatch, polona):
        monkeypatch.setattr(db, "get_edition_metadata", mock.Mock(return_value=None))

        response = client.get(self.url())

        assert response.status_code == 404
        polona.assert_not_called()

    def test_patch_changes_what_is_given_and_answers_with_the_metadata(
        self, monkeypatch
    ):
        update = mock.Mock(return_value=self.META)
        monkeypatch.setattr(db, "update_edition_metadata", update)

        response = client.patch(
            self.url(), json={"author": "  Prus,   Bolesław ", "year": 1890}
        )

        assert response.status_code == 200
        assert response.json()["title"] == "Lalka"
        update.assert_called_once_with(
            self.META.id, {"author": "Prus, Bolesław", "year": 1890}
        )

    def test_null_and_blank_clear_a_field(self, monkeypatch):
        update = mock.Mock(return_value=self.META)
        monkeypatch.setattr(db, "update_edition_metadata", update)

        client.patch(self.url(), json={"title": "   ", "place": None})

        update.assert_called_once_with(self.META.id, {"title": None, "place": None})

    def test_an_edition_that_is_not_stored(self, monkeypatch):
        monkeypatch.setattr(db, "update_edition_metadata", mock.Mock(return_value=None))

        assert client.patch(self.url(), json={"year": 1890}).status_code == 404

    def test_a_work_that_exists_already_is_a_409(self, monkeypatch):
        monkeypatch.setattr(
            db,
            "update_edition_metadata",
            mock.Mock(side_effect=WorkExists("Another work has this author and title")),
        )

        response = client.patch(self.url(), json={"title": "Faraon"})

        assert response.status_code == 409
        assert "Another work" in response.json()["detail"]

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"year": 1400},
            {"year": 2031},
            {"year": "abc"},
            {"id": str(uuid4())},
            {"work_id": str(uuid4())},
            {"author": "x" * 301},
        ],
    )
    def test_a_body_that_is_not_valid_is_422(self, monkeypatch, body):
        update = mock.Mock(return_value=self.META)
        monkeypatch.setattr(db, "update_edition_metadata", update)

        assert client.patch(self.url(), json=body).status_code == 422
        update.assert_not_called()

    def test_not_a_uuid(self):
        assert client.get("/editions/not-a-uuid/metadata").status_code == 422


class TestListEditions:
    @pytest.fixture
    def collection(self, monkeypatch):
        ids = [uuid4() for _ in range(250)]
        items = [{"id": i, "author": None, "title": None} for i in ids]
        monkeypatch.setattr(db, "list_collection", lambda: paginate(items))
        return ids

    def test_the_first_page_has_100_ids(self, collection):
        body = client.get("/editions").json()

        assert [i["id"] for i in body["items"]] == [str(i) for i in collection[:100]]
        assert (body["total"], body["page"], body["size"], body["pages"]) == (
            250,
            1,
            100,
            3,
        )

    def test_the_next_pages(self, collection):
        second = client.get("/editions?page=2").json()
        last = client.get("/editions?page=3").json()

        assert [i["id"] for i in second["items"]] == [
            str(i) for i in collection[100:200]
        ]
        assert [i["id"] for i in last["items"]] == [str(i) for i in collection[200:]]

    def test_each_edition_has_its_author_and_title_or_null(self, monkeypatch):
        known, unknown = uuid4(), uuid4()
        items = [
            {"id": known, "author": "Kraszewski", "title": "Stara baśń"},
            {"id": unknown, "author": None, "title": None},
        ]
        monkeypatch.setattr(db, "list_collection", lambda: paginate(items))

        body = client.get("/editions").json()

        assert body["items"] == [
            {"id": str(known), "author": "Kraszewski", "title": "Stara baśń"},
            {"id": str(unknown), "author": None, "title": None},
        ]

    def test_a_page_past_the_last_is_empty(self, collection):
        body = client.get("/editions?page=4").json()

        assert body["items"] == []

    def test_a_page_cannot_be_longer_than_100(self, collection):
        assert client.get("/editions?size=101").status_code == 422
        assert len(client.get("/editions?size=10").json()["items"]) == 10

    def test_a_page_number_below_1_is_rejected(self, collection):
        assert client.get("/editions?page=0").status_code == 422

    def test_an_empty_collection(self, monkeypatch):
        monkeypatch.setattr(db, "list_collection", lambda: paginate([]))

        body = client.get("/editions").json()

        assert (body["items"], body["total"]) == ([], 0)

    def test_does_not_touch_the_pages_route(self, collection):
        assert client.get(f"/editions/{uuid4()}/pages/x").status_code == 422


def test_old_page_route_removed():
    assert client.get(f"/pages/{uuid4()}").status_code == 404


def test_the_old_corrections_stub_is_gone():
    body = {"page_id": str(uuid4()), "before": "Aia", "after": "Ala"}

    assert client.post("/corrections", json=body).status_code == 404


def test_openapi_uses_ontology_models():
    schemas = client.get("/openapi.json").json()["components"]["schemas"]

    assert {"PageView", "WordCorrection"} <= schemas.keys()


def test_get_page_creates_work_and_edition_when_missing(polona):
    edition_id = uuid4()

    client.get(f"/editions/{edition_id}/pages/1")

    db.create_work.assert_called_once()
    db.create_edition.assert_called_once()
    (work,) = db.create_work.call_args.args
    (edition,) = db.create_edition.call_args.args
    assert work.author == META["author"]
    assert work.title == "Lalka"
    assert edition.id == edition_id
    assert edition.work_id == work.id
    assert (edition.place, edition.year) == ("Warszawa", 1890)


def test_get_page_skips_metadata_when_edition_exists(polona):
    db.get_edition.return_value = Edition(work_id=uuid4())
    edition_id = uuid4()

    response = client.get(f"/editions/{edition_id}/pages/1")

    assert response.status_code == 200
    db.create_edition.assert_not_called()
    page_provider.fetch_edition_metadata.assert_not_called()


def test_get_page_metadata_errors(polona):
    page_provider.fetch_edition_metadata.side_effect = PolonaError("down")

    assert client.get(f"/editions/{uuid4()}/pages/1").status_code == 502
    db.create_edition.assert_not_called()


def test_get_page_persists_the_fetched_page_and_returns_the_stored_one(polona):
    edition_id = uuid4()
    stored = Page(
        edition_id=edition_id,
        number=4,
        raw_ocr_content="stored",
        xml_content_corrected="corrected earlier",
        ready=True,
    )
    db.save_fetched_page.side_effect = None
    db.save_fetched_page.return_value = stored

    response = client.get(f"/editions/{edition_id}/pages/4")

    (saved,) = db.save_fetched_page.call_args.args
    assert saved.edition_id == edition_id
    assert saved.number == 4
    assert saved.raw_ocr_content == "Ala ma kota"
    assert saved.scan_uri.endswith("default.jpg")
    body = response.json()
    assert body["id"] == str(stored.id)
    assert body["content"] == "corrected earlier"  # corrected beats raw
    assert body["ready"] is True


def test_get_page_does_not_persist_when_polona_fails(polona):
    polona.side_effect = PolonaError("down")

    client.get(f"/editions/{uuid4()}/pages/1")

    db.save_fetched_page.assert_not_called()


def test_get_page_returns_stored_page_without_calling_polona(polona):
    edition_id = uuid4()
    stored = Page(
        edition_id=edition_id,
        number=7,
        raw_ocr_content="from the database",
        xml_content_corrected="fixed",
        scan_uri="https://polona.pl/stored.jpg",
        ready=True,
    )
    db.get_edition.return_value = Edition(id=edition_id, work_id=uuid4())
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
    page_provider.fetch_edition_metadata.assert_not_called()
    db.create_edition.assert_not_called()
    db.save_fetched_page.assert_not_called()


def test_get_page_fetches_from_polona_when_not_stored(polona):
    edition_id = uuid4()

    client.get(f"/editions/{edition_id}/pages/7")

    # asked before and after taking the lock for the first fetch of the page
    assert db.get_page_by_number.call_args_list == [mock.call(edition_id, 7)] * 2
    polona.assert_called_once_with(edition_id, 7)


def test_page_view_content_is_the_xml_never_the_raw_ocr():
    page = Page(
        edition_id=uuid4(),
        number=1,
        raw_ocr_content="raw text",
        xml_content_corrected="<TEI/>",
        scan_uri="https://polona.pl/s.jpg",
        ready=True,
    )

    view = PageView.from_page(page)

    assert view.content == "<TEI/>"
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
                raw_ocr_content="raw text",
                scan_uri="https://polona.pl/s.jpg",
                **{"xml_content_corrected": "<TEI/>", **changes},
            )
        )
        monkeypatch.setattr(db, "update_page", mock_update)
        return mock_update

    def test_updates_xml_content_corrected_and_ready(self, update):
        edition_id = uuid4()

        response = client.patch(
            f"/editions/{edition_id}/pages/3",
            json={"xml_content_corrected": "fixed text", "ready": True},
        )

        assert response.status_code == 200
        update.assert_called_once_with(
            edition_id, 3, xml_content_corrected="fixed text", ready=True
        )
        body = response.json()
        assert body["content"] == "fixed text"
        assert body["ready"] is True
        assert body["number"] == 3

    def test_only_given_fields_are_passed_on(self, update):
        edition_id = uuid4()

        client.patch(f"/editions/{edition_id}/pages/3", json={"ready": True})
        client.patch(
            f"/editions/{edition_id}/pages/3", json={"xml_content_corrected": "x"}
        )

        assert update.call_args_list == [
            mock.call(edition_id, 3, ready=True),
            mock.call(edition_id, 3, xml_content_corrected="x"),
        ]

    @pytest.mark.parametrize(
        "body",
        [
            {"raw_ocr_content": "x"},
            {"scan_uri": "https://evil.example/s.jpg"},
            {"number": 9},
            {"id": str(uuid4())},
            {"edition_id": str(uuid4())},
            {"ready": True, "raw_ocr_content": "x"},  # valid + forbidden field
            {},  # nothing to update
            {"ready": None},  # ready cannot be null
            {"ready": "maybe"},
            {"xml_content_corrected": 5},
            {"xml_content_corrected": None},  # a page always keeps its XML
            {"xml_content_corrected": ""},
        ],
    )
    def test_rejects_anything_else(self, update, body):
        response = client.patch(f"/editions/{uuid4()}/pages/3", json=body)

        assert response.status_code == 422
        update.assert_not_called()

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


def test_get_page_saves_raw_text_and_its_tei_xml(polona):
    edition_id = uuid4()
    polona.return_value = {
        "scan_uri": "https://polona.pl/s.jpg",
        "raw_ocr_content": "84 KATEDRA\n\ncle przekazał kamie\nniami <10> & tys.",
    }

    client.get(f"/editions/{edition_id}/pages/5")

    (saved,) = db.save_fetched_page.call_args.args
    assert (
        saved.raw_ocr_content == "84 KATEDRA\n\ncle przekazał kamie\nniami <10> & tys."
    )
    assert saved.xml_content_corrected == str(text_to_tei_xml(saved.raw_ocr_content))
    assert saved.xml_content_corrected.startswith("<?xml")
    assert '<TEI xmlns="http://www.tei-c.org/ns/1.0">' in saved.xml_content_corrected
    assert "<p>84 KATEDRA</p>" in saved.xml_content_corrected
    assert "kamie<lb/>niami &lt;10&gt; &amp; tys." in saved.xml_content_corrected
    assert saved.scan_uri == "https://polona.pl/s.jpg"
    assert not saved.ready


def test_get_page_passes_the_raw_text_to_the_converter(polona, monkeypatch):
    seen = []

    def converter(text):
        seen.append(text)
        return "<converted/>"

    monkeypatch.setattr("app.page_provider.text_to_tei_xml", converter)

    response = client.get(f"/editions/{uuid4()}/pages/5")

    assert seen == ["Ala ma kota"]
    assert response.json()["content"] == "<converted/>"
    (saved,) = db.save_fetched_page.call_args.args
    assert (saved.raw_ocr_content, saved.xml_content_corrected) == (
        "Ala ma kota",
        "<converted/>",
    )


def test_get_page_does_not_convert_a_stored_page(polona, monkeypatch):
    db.get_page_by_number.return_value = Page(
        edition_id=uuid4(),
        number=5,
        raw_ocr_content="stored raw",
        xml_content_corrected="<TEI/>",
    )
    converter = mock.Mock()
    monkeypatch.setattr("app.page_provider.text_to_tei_xml", converter)

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
            caplog.at_level(logging.WARNING, logger="app.main"),
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
                raw_ocr_content=self.TEXT,
                xml_content_corrected=self.XML,
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

    @pytest.mark.parametrize("label", ["ref", "bibl", "cit"])
    def test_accepts_a_reference_a_bibliography_and_a_citation(self, stored, label):
        response = client.post(
            self.url(stored["page"].edition_id),
            json=self.body("Paryżu", label),
        )

        assert response.status_code == 200
        assert f"<{label}>" in response.json()["content"]
        assert "<cit><quote>Paryżu</quote></cit>" in response.json()["content"] or (
            label != "cit"
        )

    def test_still_refuses_an_element_that_is_not_a_label(self, stored):
        response = client.post(
            self.url(stored["page"].edition_id), json=self.body("Paryżu", "quote")
        )

        assert response.status_code == 422

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
        assert list(kwargs) == ["xml_content_corrected"]  # nothing else is touched
        assert "<persName>Adam Mickiewicz</persName>" in kwargs["xml_content_corrected"]

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

        xml = stored["page"].xml_content_corrected
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
        ("xml_content_corrected", "message"),
        [
            (None, "no TEI XML"),
            ("", "no TEI XML"),
            ("Adam Mickiewicz mieszkał w Paryżu.", "not well-formed"),
            ("<a/>", "root element must be <TEI>"),
        ],
    )
    def test_a_page_without_valid_tei_xml(self, stored, xml_content_corrected, message):
        stored["page"] = stored["page"].model_copy(
            update={"xml_content_corrected": xml_content_corrected}
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
            update={"xml_content_corrected": str(text_to_tei_xml(text))}
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
            {"raw_ocr_content": "x"},  # not a field of this request
            {"xml_content_corrected": "<TEI/>"},
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

    def test_does_not_call_polona(
        self, polona, stored
    ):  # `stored` last: it replaces the database mock that `polona` sets

        response = client.post(
            self.url(stored["page"].edition_id), json=self.body("Paryżu", "placeName")
        )

        assert response.status_code == 200
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
            caplog.at_level(logging.WARNING, logger="app.main"),
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
        check.assert_called_once_with("Ala ma jey", (), [])  # no edition: no legal ones

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
            ",",
            "a",
            "jey",
            "nie",
            "ma",
            ".",
        ]
        assert [w["text"] for w in words if not w["correct"]] == ["jey"]
        assert all(text[w["start"] : w["end"]] == w["text"] for w in words)

    def test_is_in_the_openapi_schema(self):
        schema = client.get("/openapi.json").json()

        assert "post" in schema["paths"]["/spellcheck"]
        assert {"SpellcheckRequest", "SpellcheckResult", "Word"} <= schema[
            "components"
        ]["schemas"].keys()


class TestAddToken:
    TEXT = "Ala ma kota\nnoc"
    XML = str(text_to_tei_xml(TEXT))

    def url(self, edition_id=None, number=4):
        return f"/editions/{edition_id or uuid4()}/pages/{number}/tokens"

    @pytest.fixture
    def stored(self, monkeypatch):
        state = {
            "page": Page(
                edition_id=uuid4(),
                number=4,
                raw_ocr_content=self.TEXT,
                xml_content_corrected=self.XML,
            ),
            "recorded": [],
        }

        def apply(edition_id, number, replace, before, after, record=True):
            page = state["page"]
            if (edition_id, number) != (page.edition_id, page.number):
                return None
            new_xml = replace(page.xml_content_corrected or "")
            state["page"] = page.model_copy(update={"xml_content_corrected": new_xml})
            if record:
                state["recorded"].append((before, after))
            return state["page"]

        monkeypatch.setattr(db, "apply_correction", mock.Mock(side_effect=apply))
        return state

    def post(self, stored, body):
        return client.post(self.url(stored["page"].edition_id), json=body)

    def test_puts_the_token_into_the_xml(self, stored):
        response = self.post(stored, {"position": 4, "text": "XX"})

        assert response.status_code == 200
        assert tei_xml_to_text(response.json()["content"]) == "Ala XX ma kota\nnoc"
        assert tei_xml_to_text(stored["page"].xml_content_corrected) == (
            "Ala XX ma kota\nnoc"
        )

    def test_the_token_may_be_several_words(self, stored):
        response = self.post(stored, {"position": 4, "text": "LIST SZÓSTY"})

        assert response.status_code == 200
        assert tei_xml_to_text(response.json()["content"]) == (
            "Ala LIST SZÓSTY ma kota\nnoc"
        )

    def test_records_no_correction(self, stored):
        self.post(stored, {"position": 4, "text": "XX"})

        assert stored["recorded"] == []

    def test_does_not_touch_the_raw_ocr_text(self, stored):
        self.post(stored, {"position": 4, "text": "XX"})

        assert stored["page"].raw_ocr_content == self.TEXT

    def test_page_not_stored(self, stored):
        response = client.post(self.url(), json={"position": 4, "text": "XX"})

        assert response.status_code == 404

    @pytest.mark.parametrize(
        "body",
        [
            {"position": 4, "text": ""},
            {"position": 4, "text": " a"},
            {"position": 4, "text": "a "},
            {"position": 4, "text": "a\nb"},
            {"position": 99, "text": "XX"},  # not in the text
            {"position": -1, "text": "XX"},
            {"position": 4},
            {"text": "XX"},
            {"position": "four", "text": "XX"},
            {"position": 4, "text": "XX", "extra": 1},
        ],
    )
    def test_rejects_what_is_not_acceptable_and_changes_nothing(self, stored, body):
        response = self.post(stored, body)

        assert response.status_code == 422
        assert stored["page"].xml_content_corrected == self.XML

    def test_a_page_without_xml_is_422(self, stored):
        stored["page"] = stored["page"].model_copy(update={"xml_content_corrected": ""})

        assert self.post(stored, {"position": 0, "text": "XX"}).status_code == 422


class TestAddTokenToAnEmptyPage:
    XML = str(text_to_tei_xml(""))

    @pytest.fixture
    def stored(self, monkeypatch):
        state = {
            "page": Page(
                edition_id=uuid4(),
                number=4,
                raw_ocr_content="",
                xml_content_corrected=self.XML,
            )
        }

        def apply(edition_id, number, replace, before, after, record=True):
            page = state["page"]
            new_xml = replace(page.xml_content_corrected or "")
            state["page"] = page.model_copy(update={"xml_content_corrected": new_xml})
            return state["page"]

        monkeypatch.setattr(db, "apply_correction", mock.Mock(side_effect=apply))
        return state

    def post(self, stored, position=0, text="LIST SZÓSTY"):
        return client.post(
            f"/editions/{stored['page'].edition_id}/pages/4/tokens",
            json={"position": position, "text": text},
        )

    def test_the_token_is_the_first_line_of_the_page(self, stored):
        response = self.post(stored)

        assert response.status_code == 200
        assert tei_xml_to_text(response.json()["content"]) == "LIST SZÓSTY"

    def test_a_position_after_the_nothing_there_is_422(self, stored):
        assert self.post(stored, position=1).status_code == 422
        assert stored["page"].xml_content_corrected == self.XML


class TestJoinTokens:
    TEXT = "Ala jest abso lutely tu"
    XML = str(text_to_tei_xml(TEXT))

    def url(self, edition_id=None, number=4):
        return f"/editions/{edition_id or uuid4()}/pages/{number}/joins"

    @pytest.fixture
    def stored(self, monkeypatch):
        state = {
            "page": Page(
                edition_id=uuid4(),
                number=4,
                raw_ocr_content=self.TEXT,
                xml_content_corrected=self.XML,
            ),
            "recorded": [],
        }

        def apply(edition_id, number, replace, before, after, record=True):
            page = state["page"]
            if (edition_id, number) != (page.edition_id, page.number):
                return None
            new_xml = replace(page.xml_content_corrected or "")
            state["page"] = page.model_copy(update={"xml_content_corrected": new_xml})
            if record:
                state["recorded"].append((before, after))
            return state["page"]

        monkeypatch.setattr(db, "apply_correction", mock.Mock(side_effect=apply))
        return state

    BODY = {"start": 9, "end": 20, "text": "abso lutely"}

    def post(self, stored, body=BODY):
        return client.post(self.url(stored["page"].edition_id), json=body)

    def test_removes_the_space_in_the_xml(self, stored):
        response = self.post(stored)

        assert response.status_code == 200
        assert tei_xml_to_text(response.json()["content"]) == "Ala jest absolutely tu"
        assert tei_xml_to_text(stored["page"].xml_content_corrected) == (
            "Ala jest absolutely tu"
        )

    def test_records_no_correction(self, stored):
        self.post(stored)

        assert stored["recorded"] == []

    def test_does_not_touch_the_raw_ocr_text(self, stored):
        self.post(stored)

        assert stored["page"].raw_ocr_content == self.TEXT

    def test_page_not_stored(self, stored):
        assert client.post(self.url(), json=self.BODY).status_code == 404

    def test_the_page_has_changed_is_409(self, stored):
        response = self.post(stored, {"start": 9, "end": 20, "text": "abso lutelz"})

        assert response.status_code == 409
        assert stored["page"].xml_content_corrected == self.XML

    @pytest.mark.parametrize(
        "body",
        [
            {"start": 9, "end": 13, "text": "abso"},  # one token
            {"start": 8, "end": 20, "text": " abso lutely"},
            {"start": 9, "end": 20},
            {"end": 20, "text": "abso lutely"},
            {"start": 20, "end": 9, "text": "abso lutely"},
            {"start": -1, "end": 20, "text": "abso lutely"},
            {"start": 9, "end": 20, "text": "abso lutely", "extra": 1},
        ],
    )
    def test_rejects_what_is_not_acceptable_and_changes_nothing(self, stored, body):
        assert self.post(stored, body).status_code == 422
        assert stored["page"].xml_content_corrected == self.XML

    def test_a_page_without_xml_is_422(self, stored):
        stored["page"] = stored["page"].model_copy(update={"xml_content_corrected": ""})

        assert self.post(stored).status_code == 422


class TestSplitLine:
    TEXT = "Ala jest abso lutely tu"
    XML = str(text_to_tei_xml(TEXT))

    def url(self, edition_id=None, number=4):
        return f"/editions/{edition_id or uuid4()}/pages/{number}/line-splits"

    @pytest.fixture
    def stored(self, monkeypatch):
        state = {
            "page": Page(
                edition_id=uuid4(),
                number=4,
                raw_ocr_content=self.TEXT,
                xml_content_corrected=self.XML,
            ),
            "recorded": [],
        }

        def apply(edition_id, number, replace, before, after, record=True):
            page = state["page"]
            if (edition_id, number) != (page.edition_id, page.number):
                return None
            new_xml = replace(page.xml_content_corrected or "")
            state["page"] = page.model_copy(update={"xml_content_corrected": new_xml})
            if record:
                state["recorded"].append((before, after))
            return state["page"]

        monkeypatch.setattr(db, "apply_correction", mock.Mock(side_effect=apply))
        return state

    BODY = {"start": 9, "end": 20, "text": "abso lutely"}

    def post(self, stored, body=BODY):
        return client.post(self.url(stored["page"].edition_id), json=body)

    def test_puts_a_line_break_at_the_space_in_the_xml(self, stored):
        response = self.post(stored)

        assert response.status_code == 200
        assert tei_xml_to_text(response.json()["content"]) == (
            "Ala jest abso\nlutely tu"
        )
        assert "<lb/>" in stored["page"].xml_content_corrected

    def test_records_no_correction(self, stored):
        self.post(stored)

        assert stored["recorded"] == []

    def test_does_not_touch_the_raw_ocr_text(self, stored):
        self.post(stored)

        assert stored["page"].raw_ocr_content == self.TEXT

    def test_page_not_stored(self, stored):
        assert client.post(self.url(), json=self.BODY).status_code == 404

    def test_the_page_has_changed_is_409(self, stored):
        response = self.post(stored, {"start": 9, "end": 20, "text": "abso lutelz"})

        assert response.status_code == 409
        assert stored["page"].xml_content_corrected == self.XML

    @pytest.mark.parametrize(
        "body",
        [
            {"start": 9, "end": 13, "text": "abso"},  # one token
            {"start": 4, "end": 20, "text": "jest abso lutely"},  # three tokens
            {"start": 8, "end": 20, "text": " abso lutely"},
            {"start": 9, "end": 20},
            {"end": 20, "text": "abso lutely"},
            {"start": 20, "end": 9, "text": "abso lutely"},
            {"start": -1, "end": 20, "text": "abso lutely"},
            {"start": 9, "end": 20, "text": "abso lutely", "extra": 1},
        ],
    )
    def test_rejects_what_is_not_acceptable_and_changes_nothing(self, stored, body):
        assert self.post(stored, body).status_code == 422
        assert stored["page"].xml_content_corrected == self.XML

    def test_a_page_without_xml_is_422(self, stored):
        stored["page"] = stored["page"].model_copy(update={"xml_content_corrected": ""})

        assert self.post(stored).status_code == 422

    def test_a_token_can_be_added_and_then_given_a_line_of_its_own(self, stored):
        added = client.post(
            f"/editions/{stored['page'].edition_id}/pages/4/tokens",
            json={"position": 23, "text": "XXX"},
        )
        assert tei_xml_to_text(added.json()["content"]) == "Ala jest abso lutely tu XXX"

        response = self.post(stored, {"start": 21, "end": 27, "text": "tu XXX"})

        assert response.status_code == 200
        assert tei_xml_to_text(response.json()["content"]) == (
            "Ala jest abso lutely tu\nXXX"
        )

    def test_is_in_the_openapi_schema(self):
        schema = client.get("/openapi.json").json()

        path = "/editions/{edition_id}/pages/{number}/line-splits"
        assert "post" in schema["paths"][path]
        assert "LineSplit" in schema["components"]["schemas"]


class TestJoinLines:
    TEXT = "Ala jest abso\nlutely tu"
    XML = str(text_to_tei_xml(TEXT))

    def url(self, edition_id=None, number=4):
        return f"/editions/{edition_id or uuid4()}/pages/{number}/line-joins"

    @pytest.fixture
    def stored(self, monkeypatch):
        state = {
            "page": Page(
                edition_id=uuid4(),
                number=4,
                raw_ocr_content=self.TEXT,
                xml_content_corrected=self.XML,
            ),
            "recorded": [],
        }

        def apply(edition_id, number, replace, before, after, record=True):
            page = state["page"]
            if (edition_id, number) != (page.edition_id, page.number):
                return None
            new_xml = replace(page.xml_content_corrected or "")
            state["page"] = page.model_copy(update={"xml_content_corrected": new_xml})
            if record:
                state["recorded"].append((before, after))
            return state["page"]

        monkeypatch.setattr(db, "apply_correction", mock.Mock(side_effect=apply))
        return state

    BODY = {"start": 9, "end": 20, "text": "abso\nlutely"}

    def post(self, stored, body=BODY):
        return client.post(self.url(stored["page"].edition_id), json=body)

    def test_removes_the_line_break_in_the_xml(self, stored):
        response = self.post(stored)

        assert response.status_code == 200
        assert tei_xml_to_text(response.json()["content"]) == "Ala jest abso lutely tu"
        assert "<lb" not in stored["page"].xml_content_corrected

    def test_records_no_correction(self, stored):
        self.post(stored)

        assert stored["recorded"] == []

    def test_does_not_touch_the_raw_ocr_text(self, stored):
        self.post(stored)

        assert stored["page"].raw_ocr_content == self.TEXT

    def test_page_not_stored(self, stored):
        assert client.post(self.url(), json=self.BODY).status_code == 404

    def test_the_page_has_changed_is_409(self, stored):
        response = self.post(stored, {"start": 9, "end": 20, "text": "abso\nlutelz"})

        assert response.status_code == 409
        assert stored["page"].xml_content_corrected == self.XML

    @pytest.mark.parametrize(
        "body",
        [
            {"start": 9, "end": 13, "text": "abso"},  # one token
            {"start": 9, "end": 20, "text": "abso lutely"},  # a space, not a break
            {"start": 9, "end": 20},
            {"end": 20, "text": "abso\nlutely"},
            {"start": 20, "end": 9, "text": "abso\nlutely"},
            {"start": -1, "end": 20, "text": "abso\nlutely"},
            {"start": 9, "end": 20, "text": "abso\nlutely", "extra": 1},
        ],
    )
    def test_rejects_what_is_not_acceptable_and_changes_nothing(self, stored, body):
        assert self.post(stored, body).status_code == 422
        assert stored["page"].xml_content_corrected == self.XML

    def test_a_page_without_xml_is_422(self, stored):
        stored["page"] = stored["page"].model_copy(update={"xml_content_corrected": ""})

        assert self.post(stored).status_code == 422

    def test_removes_the_empty_line_below_a_page_number(self, stored):
        text = "315\n\nbłędy zbyt rzeczywiste"
        stored["page"] = stored["page"].model_copy(
            update={"xml_content_corrected": str(text_to_tei_xml(text))}
        )

        response = self.post(stored, {"start": 0, "end": 10, "text": "315\n\nbłędy"})

        assert response.status_code == 200
        assert tei_xml_to_text(response.json()["content"]) == (
            "315\nbłędy zbyt rzeczywiste"
        )
        assert stored["recorded"] == []

    def test_an_empty_line_between_a_head_and_a_paragraph_is_422(self, stored):
        xml = str(text_to_tei_xml("Ala\n\nma")).replace(
            "<p>Ala</p>", "<head>Ala</head>"
        )
        stored["page"] = stored["page"].model_copy(
            update={"xml_content_corrected": xml}
        )

        response = self.post(stored, {"start": 0, "end": 7, "text": "Ala\n\nma"})

        assert response.status_code == 422
        assert stored["page"].xml_content_corrected == xml

    def test_undoes_a_line_split(self, stored):
        stored["page"] = stored["page"].model_copy(
            update={
                "xml_content_corrected": str(text_to_tei_xml("Ala jest abso lutely tu"))
            }
        )
        split = client.post(
            f"/editions/{stored['page'].edition_id}/pages/4/line-splits",
            json={"start": 9, "end": 20, "text": "abso lutely"},
        )
        assert tei_xml_to_text(split.json()["content"]) == "Ala jest abso\nlutely tu"

        response = self.post(stored)

        assert tei_xml_to_text(response.json()["content"]) == "Ala jest abso lutely tu"

    def test_is_in_the_openapi_schema(self):
        schema = client.get("/openapi.json").json()

        path = "/editions/{edition_id}/pages/{number}/line-joins"
        assert "post" in schema["paths"][path]
        assert "LineJoin" in schema["components"]["schemas"]


class TestSimilarMisspellings:
    def url(self, edition_id=None):
        return f"/editions/{edition_id or uuid4()}/similar-misspellings"

    PARAMS = {"incorrect_spelling": "jćj", "corrected_spelling": "jéj"}
    # what the dictionary knows: the corrected forms, not the misreadings
    KNOWN = {"to", "i", "tu", "ala", "ma", "kota", "jéj", "saméj", "bardziéj"}

    @pytest.fixture(autouse=True)
    def dictionary(self, monkeypatch):
        checker = mock.Mock(**{"lookup.side_effect": lambda w: w.lower() in self.KNOWN})
        monkeypatch.setattr(spelling, "get_checker", lambda: checker)

    def pages(self, monkeypatch, texts, legal=()):
        """The pages that the database holds, as texts, and the legal spellings."""
        rows = [
            {"number": n, "xml_content_corrected": str(text_to_tei_xml(text))}
            for n, text in enumerate(texts)
        ]

        def execute(query, params=()):
            result = mock.Mock()
            result.fetchall.return_value = (
                [{"spelling": spelling} for spelling in legal]
                if "legal_spellings" in query
                else rows
            )
            return result

        monkeypatch.setattr(db, "conn", mock.Mock(execute=execute))

    @pytest.fixture
    def stored(self, monkeypatch):
        """An edition that is stored, and pages that the database holds."""
        edition_id = uuid4()
        monkeypatch.setattr(
            db,
            "get_edition",
            mock.Mock(return_value=Edition(id=edition_id, work_id=uuid4())),
        )
        rows = [
            {"number": 0, "xml_content_corrected": str(text_to_tei_xml("Ala ma kota"))},
            {
                "number": 3,
                "xml_content_corrected": str(
                    text_to_tei_xml("To jćj i samćj, bardzićj tu.")
                ),
            },
            {"number": 4, "xml_content_corrected": None},
        ]

        def execute(query, params=()):
            result = mock.Mock()
            result.fetchall.return_value = [] if "legal_spellings" in query else rows
            return result

        monkeypatch.setattr(db, "conn", mock.Mock(execute=execute))
        return edition_id

    def get(self, edition_id, **params):
        return client.get(self.url(edition_id), params=params or self.PARAMS)

    def test_lists_the_pages_with_the_tokens_that_have_the_same_mistake(self, stored):
        response = self.get(stored)

        assert response.status_code == 200
        assert response.json() == [
            {
                "page_number": 3,
                "tokens": [
                    {
                        "text": "jćj",
                        "start": 3,
                        "end": 6,
                        "suggestion": "jéj",
                        "sure": True,
                        "before": "To",
                        "after": "i",
                    },
                    {
                        "text": "samćj",
                        "start": 9,
                        "end": 14,
                        "suggestion": "saméj",
                        "sure": True,
                        "before": "i",
                        "after": ", bardzićj",
                    },
                    {
                        "text": "bardzićj",
                        "start": 16,
                        "end": 24,
                        "suggestion": "bardziéj",
                        "sure": True,
                        "before": "samćj,",
                        "after": "tu",
                    },
                ],
            }
        ]

    def test_a_full_stop_in_the_context_is_kept_with_the_next_word(
        self, stored, monkeypatch
    ):
        self.pages(monkeypatch, ["Raz. Jćj tu, a nie."])

        (page,) = self.get(stored).json()

        assert [(t["before"], t["after"]) for t in page["tokens"]] == [("Raz.", "tu")]

    def test_the_offsets_point_at_the_tokens_in_the_text_of_the_page(self, stored):
        text = "To jćj i samćj, bardzićj tu."

        (page,) = self.get(stored).json()

        assert [text[t["start"] : t["end"]] for t in page["tokens"]] == [
            t["text"] for t in page["tokens"]
        ]

    def test_never_suggests_a_correction_from_a_correct_word(self, stored, monkeypatch):
        # "zasiedliśmy" is a word: it is not a misreading, whatever it looks like
        self.KNOWN = self.KNOWN | {"mićj", "zasiedliśmy"}
        self.pages(monkeypatch, ["To mićj jćj tu.", "mićj"])

        (page,) = self.get(stored).json()

        assert [t["text"] for t in page["tokens"]] == ["jćj"]

    def test_a_suggestion_that_is_not_a_correct_word_is_doubtful_not_sure(
        self, stored, monkeypatch
    ):
        # the dictionary does not know "zzéj": offered, but not sure
        self.pages(monkeypatch, ["To zzćj jćj tu."])

        (page,) = self.get(stored).json()

        assert [(t["text"], t["suggestion"], t["sure"]) for t in page["tokens"]] == [
            ("zzćj", "zzéj", False),
            ("jćj", "jéj", True),
        ]

    def test_a_page_with_only_doubtful_places_is_kept(self, stored, monkeypatch):
        self.pages(monkeypatch, ["To zzćj tu.", "To jćj tu."])

        pages = self.get(stored).json()

        assert [p["page_number"] for p in pages] == [0, 1]

    def test_the_zasiedlilismy_case_from_a_correct_word_to_a_wrong_one(
        self, stored, monkeypatch
    ):
        # "zasiedliśmy" is correct and "zasiedliémy" is not: never offered
        self.KNOWN = self.KNOWN | {"zasiedliśmy"}
        self.pages(monkeypatch, ["Zasiedliśmy tu."])

        assert (
            self.get(
                stored, incorrect_spelling="samśmy", corrected_spelling="samémy"
            ).json()
            == []
        )

    def test_a_legal_spelling_of_the_edition_counts_as_correct_for_the_suggestion(
        self, stored, monkeypatch
    ):
        self.pages(monkeypatch, ["To zzćj jćj tu."], legal={"zzéj"})

        (page,) = self.get(stored).json()

        assert [(t["text"], t["sure"]) for t in page["tokens"]] == [
            ("zzćj", True),
            ("jćj", True),
        ]

    def test_a_legal_spelling_of_the_edition_is_not_a_misspelling(
        self, stored, monkeypatch
    ):
        # "zzćj" is accepted in this edition: it is not flagged, so it is left alone
        self.pages(monkeypatch, ["To zzćj jćj tu."], legal={"zzćj", "zzéj"})

        (page,) = self.get(stored).json()

        assert [t["text"] for t in page["tokens"]] == ["jćj"]

    def test_the_dictionary_not_available_is_503(self, stored, monkeypatch):
        def unavailable():
            raise SpellcheckUnavailable("no dictionary")

        monkeypatch.setattr(spelling, "get_checker", unavailable)

        assert self.get(stored).status_code == 503

    def test_asks_the_database_for_the_edition_and_the_two_spellings(
        self, stored, monkeypatch
    ):
        search = mock.Mock(return_value=[])
        monkeypatch.setattr(db, "get_similar_misspellings", search)

        response = self.get(stored)

        assert response.json() == []
        search.assert_called_once_with(stored, "jćj", "jéj")

    def test_no_matches_is_an_empty_list(self, stored):
        assert (
            self.get(stored, incorrect_spelling="xyz", corrected_spelling="xyw").json()
            == []
        )

    def test_edition_not_stored(self, monkeypatch):
        monkeypatch.setattr(db, "get_edition", mock.Mock(return_value=None))

        response = self.get(uuid4())

        assert response.status_code == 404
        assert response.json()["detail"] == "Edition not found"

    def test_never_calls_polona(self, stored, polona):
        self.get(stored)

        polona.assert_not_called()

    @pytest.mark.parametrize(
        "params",
        [
            {"incorrect_spelling": "jćj"},
            {"corrected_spelling": "jéj"},
            {},
            {"incorrect_spelling": "", "corrected_spelling": "jéj"},
            {"incorrect_spelling": "jćj", "corrected_spelling": ""},
            {"incorrect_spelling": "j" * 101, "corrected_spelling": "jéj"},
            {"incorrect_spelling": "jćj", "corrected_spelling": "é" * 101},
        ],
    )
    def test_both_spellings_are_needed_and_must_be_short(self, stored, params):
        response = client.get(self.url(stored), params=params)

        assert response.status_code == 422

    def test_the_same_spelling_twice_is_422(self, stored):
        response = self.get(stored, incorrect_spelling="jćj", corrected_spelling="jćj")

        assert response.status_code == 422
        assert "same" in response.json()["detail"]

    def test_changes_nothing(self, stored, monkeypatch):
        for name in ("update_page", "apply_correction", "save_fetched_page"):
            monkeypatch.setattr(db, name, mock.Mock())

        self.get(stored)

        for name in ("update_page", "apply_correction", "save_fetched_page"):
            getattr(db, name).assert_not_called()

    def test_is_in_the_openapi_schema(self):
        schema = client.get("/openapi.json").json()

        path = "/editions/{edition_id}/similar-misspellings"
        assert "get" in schema["paths"][path]
        assert {"PageMatches", "TokenMatch"} <= schema["components"]["schemas"].keys()


class TestSimilarPageNumbers:
    def url(self, edition_id=None, number=4):
        return f"/editions/{edition_id or uuid4()}/pages/{number}/similar-page-numbers"

    def xml(self, text, mark=None):
        xml = str(text_to_tei_xml(text))
        if mark:
            xml = mark_section(xml, *mark, "page_number", text[mark[0] : mark[1]])
        return xml

    @pytest.fixture
    def rows(self, monkeypatch):
        """The pages that the database holds, as rows of (number, xml)."""
        rows = [
            {"number": 4, "xml_content_corrected": self.xml("1\n\nAla ma", (0, 1))},
            {"number": 5, "xml_content_corrected": self.xml("2\n\nA kot ma Ale")},
            {"number": 6, "xml_content_corrected": self.xml("TREŚĆ\n\n12")},
            {"number": 7, "xml_content_corrected": self.xml("9\n\nNie pasuje")},
        ]
        monkeypatch.setattr(
            db,
            "conn",
            mock.Mock(**{"execute.return_value.fetchall.return_value": rows}),
        )
        return rows

    def test_lists_the_example_and_the_pages_with_a_number_like_it(self, rows):
        response = client.get(self.url(uuid4(), 4))

        assert response.status_code == 200
        body = response.json()
        assert body["where"] == "first"
        assert body["offset"] == 3
        assert body["example"] == {
            "page_number": 4,
            "start": 0,
            "end": 1,
            "text": "1",
            "number": 1,
            "context": "Ala ma",
            "fits_sequence": True,
        }
        assert body["candidates"] == [
            {
                "page_number": 5,
                "start": 0,
                "end": 1,
                "text": "2",
                "number": 2,
                "context": "A kot ma Ale",
                "fits_sequence": True,
            },
            {
                "page_number": 7,
                "start": 0,
                "end": 1,
                "text": "9",
                "number": 9,
                "context": "Nie pasuje",
                "fits_sequence": False,
            },
        ]

    def test_the_offsets_point_at_what_is_to_be_marked(self, rows):
        body = client.get(self.url(uuid4(), 4)).json()

        for candidate in body["candidates"]:
            page = next(r for r in rows if r["number"] == candidate["page_number"])
            text = tei_xml_to_text(page["xml_content_corrected"])
            assert text[candidate["start"] : candidate["end"]] == candidate["text"]

    def test_page_not_stored(self, rows):
        response = client.get(self.url(uuid4(), 99))

        assert response.status_code == 404
        assert response.json()["detail"] == "Page not found"

    def test_no_page_number_marked_is_422(self, rows):
        response = client.get(self.url(uuid4(), 5))

        assert response.status_code == 422
        assert "No page number is marked" in response.json()["detail"]

    def test_a_number_that_is_not_alone_is_422(self, monkeypatch):
        xml = self.xml("84 KATEDRA\n\nAla", (0, 2))
        monkeypatch.setattr(
            db,
            "conn",
            mock.Mock(
                **{
                    "execute.return_value.fetchall.return_value": [
                        {"number": 1, "xml_content_corrected": xml}
                    ]
                }
            ),
        )

        response = client.get(self.url(uuid4(), 1))

        assert response.status_code == 422
        assert "alone" in response.json()["detail"]

    def test_never_calls_polona_and_changes_nothing(self, rows, polona, monkeypatch):
        for name in ("update_page", "apply_correction", "save_fetched_page"):
            monkeypatch.setattr(db, name, mock.Mock())

        client.get(self.url(uuid4(), 4))

        polona.assert_not_called()
        for name in ("update_page", "apply_correction", "save_fetched_page"):
            getattr(db, name).assert_not_called()

    def test_is_in_the_openapi_schema(self):
        schema = client.get("/openapi.json").json()

        path = "/editions/{edition_id}/pages/{number}/similar-page-numbers"
        assert "get" in schema["paths"][path]
        assert {"SimilarPageNumbers", "PageNumberMatch"} <= schema["components"][
            "schemas"
        ].keys()


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
                raw_ocr_content=self.TEXT,
                xml_content_corrected=self.XML,
            ),
            "corrections": [],
        }

        def apply(edition_id, number, replace, before, after, record=True):
            page = state["page"]
            if (edition_id, number) != (page.edition_id, page.number):
                return None
            new_xml = replace(
                page.xml_content_corrected or ""
            )  # may raise: nothing saved
            state["page"] = page.model_copy(update={"xml_content_corrected": new_xml})
            if record:
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

    def test_removes_a_token_without_recording_a_correction(self, stored):
        stored["page"] = stored["page"].model_copy(
            update={"xml_content_corrected": str(text_to_tei_xml("Ala $ ma"))}
        )
        body = {"start": 4, "end": 5, "before": "$", "after": ""}

        response = self.post(stored, body)

        assert response.status_code == 200
        assert tei_xml_to_text(stored["page"].xml_content_corrected) == "Ala ma"
        assert stored["corrections"] == []  # not a correction of a word

    def test_the_text_afterwards_is_the_old_text_with_the_word_replaced(self, stored):
        self.post(stored, self.body("dawnieyszych", "dawniejszych"))

        xml = stored["page"].xml_content_corrected
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
        xml = stored["page"].xml_content_corrected

        assert "To jej tey" in tei_xml_to_text(xml)

    def test_keeps_the_entities_that_are_marked_in_the_xml(self, stored):
        stored["page"] = stored["page"].model_copy(
            update={
                "xml_content_corrected": self.XML.replace(
                    "kościół", "<placeName>kościół</placeName>"
                )
            }
        )

        self.post(stored, self.body("kościół", "kościoł"))

        assert "<placeName>kościoł</placeName>" in stored["page"].xml_content_corrected

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
        assert stored["page"].xml_content_corrected == self.XML

    def test_offsets_outside_the_text(self, stored):
        body = {"start": 900, "end": 903, "before": "jey", "after": "jej"}

        assert self.post(stored, body).status_code == 409

    @pytest.mark.parametrize(
        ("xml_content_corrected", "message"),
        [
            (None, "no TEI XML"),
            ("", "no TEI XML"),
            ("To jey tey.", "not well-formed"),
            ("<a/>", "root element must be <TEI>"),
        ],
    )
    def test_a_page_without_valid_tei_xml(self, stored, xml_content_corrected, message):
        stored["page"] = stored["page"].model_copy(
            update={"xml_content_corrected": xml_content_corrected}
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
        assert stored["page"].xml_content_corrected == self.XML

    def test_the_old_text_cannot_have_spaces(self, stored):
        response = self.post(stored, self.body("To jey", "Tojey"))

        assert response.status_code == 422
        assert "spaces or line breaks" in response.json()["detail"]

    @pytest.mark.parametrize(
        "change",
        [
            {"before": ""},
            {"after": None},
            {"end": 0},
            {"start": 5, "end": 5},
            {"start": -1},
            {"start": "a"},
            {"raw_ocr_content": "x"},  # not a field of this request
            {"xml_content_corrected": "<TEI/>"},
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

    def test_does_not_call_polona(self, polona, stored):

        response = self.post(stored, self.body("jey", "jej"))

        assert response.status_code == 200
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

    def test_does_not_call_polona(self, polona, stored):

        response = self.post(stored, {"spelling": "jey"})

        assert response.status_code == 201
        polona.assert_not_called()

    def test_changes_nothing_but_the_spelling(self, stored, monkeypatch):
        for name in (
            "update_page",
            "save_fetched_page",
            "apply_correction",
            "create_work",
            "create_edition",
        ):
            monkeypatch.setattr(db, name, mock.Mock())

        self.post(stored, {"spelling": "jey"})

        for name in (
            "update_page",
            "save_fetched_page",
            "apply_correction",
            "create_work",
            "create_edition",
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
                raw_ocr_content=self.TEXT,
                xml_content_corrected=self.XML,
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
        assert markup in stored["page"].xml_content_corrected
        assert tei_xml_to_text(stored["page"].xml_content_corrected) == self.TEXT

    def test_a_note_with_its_number(self, stored):
        response = self.post(stored, self.body("I. Alfa", "note", n="1"))

        assert response.status_code == 200
        assert '<note n="1">I. Alfa</note>' in stored["page"].xml_content_corrected

    def test_a_note_without_a_number(self, stored):
        self.post(stored, self.body("I. Alfa", "note"))

        assert "<note>I. Alfa</note>" in stored["page"].xml_content_corrected

    def test_the_number_is_trimmed(self, stored):
        self.post(stored, self.body("I. Alfa", "note", n=" 2 "))

        assert '<note n="2">' in stored["page"].xml_content_corrected

    @pytest.mark.parametrize("n", ["", "   ", "x" * 21])
    def test_a_bad_number_is_refused(self, stored, n):
        response = self.post(stored, self.body("I. Alfa", "note", n=n))

        assert response.status_code == 422
        db.update_page.assert_not_called()

    def test_a_number_for_something_that_is_not_a_note_is_refused(self, stored):
        response = self.post(stored, self.body("84 KATEDRA", "header", n="1"))

        assert response.status_code == 422
        db.update_page.assert_not_called()

    def test_a_quotation_in_another_language(self, stored):
        response = self.post(stored, self.body("cle przekazał", "quote", lang="la"))

        assert response.status_code == 200
        assert (
            '<quote xml:lang="la">cle przekazał</quote>'
            in stored["page"].xml_content_corrected
        )
        assert tei_xml_to_text(stored["page"].xml_content_corrected) == self.TEXT

    @pytest.mark.parametrize(
        "extra", [{}, {"lang": ""}, {"lang": "pl"}, {"lang": "xx"}]
    )
    def test_a_quote_needs_one_of_the_languages(self, stored, extra):
        response = self.post(stored, self.body("cle przekazał", "quote", **extra))

        assert response.status_code == 422
        db.update_page.assert_not_called()

    def test_a_language_for_something_that_is_not_a_quote_is_refused(self, stored):
        response = self.post(stored, self.body("84 KATEDRA", "header", lang="la"))

        assert response.status_code == 422
        db.update_page.assert_not_called()

    def test_a_group_of_notes(self, stored):
        self.post(stored, self.body("I. Alfa", "note", n="1"))
        self.post(stored, self.body("II. Beta", "note", n="2"))

        response = self.post(stored, self.body("I. Alfa\nII. Beta", "note_group"))

        assert response.status_code == 200
        xml = stored["page"].xml_content_corrected
        assert '<noteGrp><note n="1">I. Alfa</note>' in xml
        assert tei_xml_to_text(xml) == self.TEXT

    def test_a_group_of_what_is_not_notes_is_refused_with_the_reason(self, stored):
        response = self.post(stored, self.body("I. Alfa", "note_group"))

        assert response.status_code == 422
        assert "only notes" in response.json()["detail"]
        db.update_page.assert_not_called()

    def test_the_table_of_contents_is_a_div_around_paragraphs(self, stored):
        response = self.post(stored, self.body("I. Alfa\nII. Beta", "toc"))

        assert response.status_code == 200
        xml = stored["page"].xml_content_corrected
        assert '<div type="toc"><p><lb/>I. Alfa<lb/>II. Beta</p></div>' in xml
        assert "<div><p><lb/>koniec</p><p>stopka 85</p></div>" in xml

    def test_saves_the_new_xml_as_the_xml_content_corrected_and_nothing_else(
        self, stored
    ):
        page = stored["page"]

        self.post(stored, self.body("84 KATEDRA", "header"))

        db.update_page.assert_called_once()
        args, kwargs = db.update_page.call_args
        assert args == (page.edition_id, 4)
        assert list(kwargs) == ["xml_content_corrected"]

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
        ("xml_content_corrected", "message"),
        [
            (None, "no TEI XML"),
            ("", "no TEI XML"),
            ("84 KATEDRA", "not well-formed"),
            ("<a/>", "root element must be <TEI>"),
        ],
    )
    def test_a_page_without_valid_tei_xml(self, stored, xml_content_corrected, message):
        stored["page"] = stored["page"].model_copy(
            update={"xml_content_corrected": xml_content_corrected}
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
            {"raw_ocr_content": "x"},  # not a field of this request
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

    def test_does_not_call_polona(self, polona, stored):

        response = self.post(stored, self.body("84 KATEDRA", "header"))

        assert response.status_code == 200
        polona.assert_not_called()

    def test_is_in_the_openapi_schema(self):
        schema = client.get("/openapi.json").json()

        path = "/editions/{edition_id}/pages/{number}/sections"
        assert "post" in schema["paths"][path]
        assert "SectionMark" in schema["components"]["schemas"]


class TestTeiEntities:
    def test_lists_the_entities_marked_in_the_xml(self):
        xml = str(text_to_tei_xml("Jan Kowalski mieszka w Paryżu."))
        xml = xml.replace("Jan Kowalski", "<persName>Jan Kowalski</persName>")
        xml = xml.replace("Paryżu", "<placeName>Paryżu</placeName>")

        response = client.post("/tei/entities", json={"xml": xml})

        assert response.status_code == 200
        assert response.json() == {
            "entities": [
                {"text": "Jan Kowalski", "label": "persName", "start": 0, "end": 12},
                {"text": "Paryżu", "label": "placeName", "start": 23, "end": 29},
            ]
        }

    def test_no_entities(self):
        xml = str(text_to_tei_xml("Ala"))

        assert client.post("/tei/entities", json={"xml": xml}).json() == {
            "entities": []
        }

    def test_xml_that_is_not_tei_is_422(self):
        response = client.post("/tei/entities", json={"xml": "<a/>"})

        assert response.status_code == 422
        assert "TEI" in response.json()["detail"]

    def test_stores_nothing(self, monkeypatch):
        update = mock.Mock()
        monkeypatch.setattr(db, "update_page", update)

        client.post("/tei/entities", json={"xml": str(text_to_tei_xml("Ala"))})

        update.assert_not_called()


class TestSpellcheckOfQuotations:
    def foreign_xml(self):
        xml = str(text_to_tei_xml("ala mens sana ma"))
        return xml.replace("mens sana", '<quote xml:lang="la">mens sana</quote>')

    @pytest.fixture(autouse=True)
    def dictionary(self, monkeypatch):
        checker = mock.Mock(**{"lookup.side_effect": lambda w: w in {"ala", "ma"}})
        monkeypatch.setattr(spelling, "get_checker", lambda: checker)

    def wrong(self, response):
        return [w["text"] for w in response.json()["words"] if not w["correct"]]

    def test_the_words_of_a_quotation_in_another_language_are_not_flagged(self):
        response = client.post(
            "/spellcheck", json={"text": "ala mens sana ma", "xml": self.foreign_xml()}
        )

        assert response.status_code == 200
        assert self.wrong(response) == []

    def test_without_the_xml_they_are_flagged(self):
        response = client.post("/spellcheck", json={"text": "ala mens sana ma"})

        assert self.wrong(response) == ["mens", "sana"]

    def test_text_that_is_not_the_text_of_the_xml_is_422(self):
        response = client.post(
            "/spellcheck", json={"text": "ala ma", "xml": self.foreign_xml()}
        )

        assert response.status_code == 422

    def test_xml_that_is_not_tei_is_422(self):
        response = client.post("/spellcheck", json={"text": "ala", "xml": "<a/>"})

        assert response.status_code == 422


class TestTeiSections:
    def test_lists_the_sections_marked_in_the_xml(self):
        xml = str(text_to_tei_xml("84 KATEDRA\n\ncle"))
        xml = xml.replace(
            "<p>84 KATEDRA</p>", '<p><fw type="header">84 KATEDRA</fw></p>'
        )

        response = client.post("/tei/sections", json={"xml": xml})

        assert response.status_code == 200
        assert response.json() == {
            "sections": [
                {"start": 0, "end": 10, "open": '<fw type="header">', "close": "</fw>"}
            ]
        }

    def test_no_sections(self):
        xml = str(text_to_tei_xml("Ala"))

        response = client.post("/tei/sections", json={"xml": xml})

        assert response.json() == {"sections": []}

    def test_xml_that_is_not_tei_is_422(self):
        response = client.post("/tei/sections", json={"xml": "<a/>"})

        assert response.status_code == 422
        assert "TEI" in response.json()["detail"]


class TestRubbishLines:
    def url(self, edition_id=None):
        return f"/editions/{edition_id or uuid4()}/rubbish-lines"

    @pytest.fixture(autouse=True)
    def dictionary(self, monkeypatch):
        known = {"ala", "ma", "kota", "szczerą"}
        checker = mock.Mock(**{"lookup.side_effect": lambda w: w.lower() in known})
        monkeypatch.setattr(spelling, "get_checker", lambda: checker)

    @pytest.fixture
    def stored(self, monkeypatch):
        edition_id = uuid4()
        monkeypatch.setattr(
            db,
            "get_edition",
            mock.Mock(return_value=Edition(id=edition_id, work_id=uuid4())),
        )
        marked = mark_section(
            str(text_to_tei_xml("Ala ma\n;;; ,,,\nkota")), 7, 14, "header", ";;; ,,,"
        )
        rows = [
            {"number": 0, "xml_content_corrected": str(text_to_tei_xml("Ala ma kota"))},
            {
                "number": 3,
                "xml_content_corrected": str(
                    text_to_tei_xml("Ala ma\n; . l-v ■ :\nkota\n\n***\n\n12")
                ),
            },
            {"number": 4, "xml_content_corrected": None},
            {"number": 5, "xml_content_corrected": marked},
            {"number": 6, "xml_content_corrected": "<not tei/>"},
        ]

        def execute(query, params=()):
            result = mock.Mock()
            result.fetchall.return_value = [] if "legal_spellings" in query else rows
            return result

        monkeypatch.setattr(db, "conn", mock.Mock(execute=execute))
        return edition_id

    def test_lists_the_rubbish_lines_page_by_page(self, stored):
        response = client.get(self.url(stored))

        assert response.status_code == 200
        assert response.json() == [
            {
                "page_number": 3,
                "lines": [
                    {"start": 7, "end": 18, "text": "; . l-v ■ :", "sure": True},
                    {"start": 25, "end": 28, "text": "***", "sure": True},
                ],
            }
        ]

    def test_offsets_are_those_of_the_text(self, stored):
        text = "Ala ma\n; . l-v ■ :\nkota\n\n***\n\n12"

        for line in client.get(self.url(stored)).json()[0]["lines"]:
            assert text[line["start"] : line["end"]] == line["text"]

    def test_a_marked_section_is_left_alone(self, stored):
        pages = [p["page_number"] for p in client.get(self.url(stored)).json()]

        assert 5 not in pages

    def test_edition_not_stored_is_404(self, monkeypatch):
        monkeypatch.setattr(db, "get_edition", mock.Mock(return_value=None))

        response = client.get(self.url())

        assert response.status_code == 404
        assert response.json()["detail"] == "Edition not found"

    def test_dictionary_unavailable_is_503(self, stored, monkeypatch):
        def unavailable():
            raise SpellcheckUnavailable("no dictionary")

        monkeypatch.setattr(spelling, "get_checker", unavailable)

        assert client.get(self.url(stored)).status_code == 503

    def test_is_in_the_openapi_schema(self):
        schema = client.get("/openapi.json").json()

        assert "get" in schema["paths"]["/editions/{edition_id}/rubbish-lines"]
        assert {"PageRubbish", "RubbishLine"} <= schema["components"]["schemas"].keys()


class TestRemoveLine:
    TEXT = "Ala ma\n; . :\nkota"
    XML = str(text_to_tei_xml(TEXT))

    def url(self, edition_id=None, number=4):
        return f"/editions/{edition_id or uuid4()}/pages/{number}/line-removals"

    @pytest.fixture
    def stored(self, monkeypatch):
        state = {
            "page": Page(
                edition_id=uuid4(),
                number=4,
                raw_ocr_content=self.TEXT,
                xml_content_corrected=self.XML,
            ),
            "recorded": [],
        }

        def apply(edition_id, number, replace, before, after, record=True):
            page = state["page"]
            if (edition_id, number) != (page.edition_id, page.number):
                return None
            new_xml = replace(page.xml_content_corrected or "")
            state["page"] = page.model_copy(update={"xml_content_corrected": new_xml})
            if record:
                state["recorded"].append((before, after))
            return state["page"]

        monkeypatch.setattr(db, "apply_correction", mock.Mock(side_effect=apply))
        return state

    BODY = {"start": 7, "end": 12, "text": "; . :"}

    def post(self, stored, body=None):
        return client.post(self.url(stored["page"].edition_id), json=body or self.BODY)

    def test_removes_the_line_from_the_xml(self, stored):
        response = self.post(stored)

        assert response.status_code == 200
        assert tei_xml_to_text(response.json()["page"]["content"]) == "Ala ma\nkota"
        assert tei_xml_to_text(stored["page"].xml_content_corrected) == "Ala ma\nkota"

    def test_says_how_many_characters_of_the_text_went(self, stored):
        assert self.post(stored).json()["removed"] == 6  # the line and a line break

    def test_a_line_that_is_a_paragraph_takes_two_line_breaks(self, stored):
        text = "Ala\n\n; . :\n\nkota"
        stored["page"] = stored["page"].model_copy(
            update={"xml_content_corrected": str(text_to_tei_xml(text))}
        )

        response = self.post(stored, {"start": 5, "end": 10, "text": "; . :"})

        assert response.json()["removed"] == 7

    def test_no_correction_is_recorded(self, stored):
        self.post(stored)

        assert stored["recorded"] == []

    def test_the_page_has_changed_is_409(self, stored):
        response = self.post(stored, {**self.BODY, "text": "; . ;"})

        assert response.status_code == 409
        assert stored["page"].xml_content_corrected == self.XML

    def test_page_not_stored_is_404(self, stored):
        response = client.post(self.url(), json=self.BODY)

        assert response.status_code == 404

    def test_page_without_xml_is_422(self, stored):
        stored["page"] = stored["page"].model_copy(
            update={"xml_content_corrected": None}
        )

        assert self.post(stored).status_code == 422

    def test_not_a_whole_line_is_422(self, stored):
        response = self.post(stored, {"start": 7, "end": 9, "text": "; "})

        assert response.status_code == 422

    @pytest.mark.parametrize(
        "body",
        [
            {"start": 7, "end": 7, "text": "x"},
            {"start": 7, "end": 12, "text": ""},
            {**BODY, "extra": 1},
        ],
    )
    def test_a_body_that_is_not_acceptable_is_422(self, stored, body):
        assert self.post(stored, body).status_code == 422

    def test_is_in_the_openapi_schema(self):
        paths = client.get("/openapi.json").json()["paths"]

        assert "post" in paths["/editions/{edition_id}/pages/{number}/line-removals"]


class TestSimilarEntities:
    def url(self, edition_id=None):
        return f"/editions/{edition_id or uuid4()}/similar-entities"

    @pytest.fixture
    def stored(self, monkeypatch):
        edition_id = uuid4()
        monkeypatch.setattr(
            db,
            "get_edition",
            mock.Mock(return_value=Edition(id=edition_id, work_id=uuid4())),
        )
        tagged = tag_text_range(
            str(text_to_tei_xml("Był Machnicki tu, i Machnicki tam")),
            4,
            13,
            "persName",
            "Machnicki",
        )
        rows = [
            {"number": 0, "xml_content_corrected": str(text_to_tei_xml("Ala ma kota"))},
            {
                "number": 3,
                "xml_content_corrected": str(
                    text_to_tei_xml("Z panem Machnickiego i Machnickiej, Machnickimi.")
                ),
            },
            {"number": 4, "xml_content_corrected": None},
            {"number": 5, "xml_content_corrected": tagged},
            {"number": 6, "xml_content_corrected": "<not tei/>"},
        ]
        monkeypatch.setattr(
            db,
            "conn",
            mock.Mock(**{"execute.return_value.fetchall.return_value": rows}),
        )

        # the NER model finds the genitive, and takes the instrumental for a place
        def recognize(text):
            found = []
            for word, label in (
                ("Machnickiego", "persName"),
                ("Machnickimi", "placeName"),
            ):
                at = text.find(word)
                if at >= 0:
                    found.append(
                        Entity(text=word, label=label, start=at, end=at + len(word))
                    )
            return found

        monkeypatch.setattr("app.persistence.db.find_entities", recognize)
        return edition_id

    def get(self, edition_id, text="Machnicki", label="persName"):
        return client.get(self.url(edition_id), params={"text": text, "label": label})

    def test_lists_the_places_by_page_with_their_context(self, stored):
        response = self.get(stored)

        assert response.status_code == 200
        pages = response.json()["pages"]
        # (page 5 has one place that is not marked yet; 0, 4 and 6 have none)
        assert [p["page_number"] for p in pages] == [3, 5]
        assert [(m["text"], m["before"], m["after"]) for m in pages[0]["matches"]] == [
            ("Machnickiego", "panem", "i"),
            ("Machnickiej", "i", ", Machnickimi"),
            ("Machnickimi", "Machnickiej,", "."),
        ]

    def test_offsets_are_those_of_the_text(self, stored):
        text = "Z panem Machnickiego i Machnickiej, Machnickimi."

        for match in self.get(stored).json()["pages"][0]["matches"]:
            assert text[match["start"] : match["end"]] == match["text"]

    def test_a_match_is_sure_if_the_model_finds_an_entity_with_the_same_label(
        self, stored
    ):
        matches = self.get(stored).json()["pages"][0]["matches"]

        assert [(m["text"], m["sure"]) for m in matches] == [
            ("Machnickiego", True),
            ("Machnickiej", False),  # the model does not find it
            ("Machnickimi", False),  # the model says it is a place
        ]

    def test_the_label_is_the_one_that_is_asked(self, stored):
        # the same token: the model says it is a person, not a place
        by_label = {
            label: self.get(stored, text="Machnickiego", label=label).json()["pages"]
            for label in ("persName", "placeName")
        }

        assert by_label["persName"][0]["matches"][0]["sure"] is True
        assert by_label["placeName"][0]["matches"][0]["sure"] is False

    def test_the_forms_that_were_looked_for_are_listed(self, stored):
        body = self.get(stored).json()

        assert body["label"] == "persName"
        assert body["forms"][0] == {"text": "Machnicki", "cases": ["nom.sg"]}
        by_text = {f["text"]: f["cases"] for f in body["forms"]}
        assert by_text["Machnickiego"] == ["acc.sg", "gen.sg"]
        assert "Machnickiej" in by_text

    def test_a_place_that_is_marked_already_is_left_out(self, stored):
        # on page 5 the first "Machnicki" is marked, the second is not
        pages = {p["page_number"]: p for p in self.get(stored).json()["pages"]}

        assert [(m["start"], m["end"]) for m in pages[5]["matches"]] == [(20, 29)]

    def test_a_name_of_several_words(self, stored, monkeypatch):
        rows = [
            {
                "number": 1,
                "xml_content_corrected": str(
                    text_to_tei_xml("Był Jan Machnicki, potem Jana Machnickiego.")
                ),
            }
        ]
        monkeypatch.setattr(
            db,
            "conn",
            mock.Mock(**{"execute.return_value.fetchall.return_value": rows}),
        )

        matches = self.get(stored, text="Jan Machnicki").json()["pages"][0]["matches"]

        assert [m["text"] for m in matches] == ["Jan Machnicki", "Jana Machnickiego"]

    def test_the_model_is_run_only_for_pages_with_matches(self, stored, monkeypatch):
        recognize = mock.Mock(return_value=[])
        monkeypatch.setattr("app.persistence.db.find_entities", recognize)

        self.get(stored)

        assert recognize.call_count == 2  # pages 3 and 5, not the others

    def test_edition_not_stored_is_404(self, monkeypatch):
        monkeypatch.setattr(db, "get_edition", mock.Mock(return_value=None))

        response = client.get(self.url(), params={"text": "Ala", "label": "persName"})

        assert response.status_code == 404
        assert response.json()["detail"] == "Edition not found"

    @pytest.mark.parametrize(
        "params",
        [
            {"text": "", "label": "persName"},
            {"text": "x" * 101, "label": "persName"},
            {"text": "a b c d e f", "label": "persName"},
            {"text": "123", "label": "persName"},
            {"text": "Ala", "label": "nonsense"},
            {"text": "Ala"},
            {"label": "persName"},
        ],
    )
    def test_a_request_that_is_not_acceptable_is_422(self, stored, params):
        response = client.get(self.url(stored), params=params)

        assert response.status_code == 422

    def test_model_unavailable_is_503(self, stored, monkeypatch):
        def unavailable(text):
            raise NerModelUnavailable("no model")

        monkeypatch.setattr("app.persistence.db.find_entities", unavailable)

        assert self.get(stored).status_code == 503

    def test_is_in_the_openapi_schema(self):
        schema = client.get("/openapi.json").json()

        assert "get" in schema["paths"]["/editions/{edition_id}/similar-entities"]
        assert {"SimilarEntities", "EntityMatch", "EntityForm"} <= schema["components"][
            "schemas"
        ].keys()


class TestSpellingChunkMatches:
    def url(self, edition_id=None):
        return f"/editions/{edition_id or uuid4()}/spelling-chunk-matches"

    @pytest.fixture(autouse=True)
    def dictionary(self, monkeypatch):
        known = {"ala", "ma", "kota", "konwencja"}
        checker = mock.Mock(**{"lookup.side_effect": lambda w: w.lower() in known})
        monkeypatch.setattr(spelling, "get_checker", lambda: checker)

    @pytest.fixture
    def stored(self, monkeypatch):
        edition_id = uuid4()
        monkeypatch.setattr(
            db,
            "get_edition",
            mock.Mock(return_value=Edition(id=edition_id, work_id=uuid4())),
        )
        rows = [
            {"number": 0, "xml_content_corrected": str(text_to_tei_xml("Ala ma kota"))},
            {
                "number": 3,
                "xml_content_corrected": str(
                    text_to_tei_xml("Ala ma Konwencya i Policya, Konwencya.")
                ),
            },
            {"number": 4, "xml_content_corrected": None},
            {
                "number": 5,
                "xml_content_corrected": str(text_to_tei_xml("Znów Konwencya tu.")),
            },
            {"number": 6, "xml_content_corrected": "<not tei/>"},
        ]

        def execute(query, params=()):
            result = mock.Mock()
            result.fetchall.return_value = (
                [{"spelling": "Policya"}] if "legal_spellings" in query else rows
            )
            return result

        monkeypatch.setattr(db, "conn", mock.Mock(execute=execute))
        return edition_id

    def pages(self, monkeypatch, rows, legal=()):
        def execute(query, params=()):
            result = mock.Mock()
            result.fetchall.return_value = (
                [{"spelling": spelling} for spelling in legal]
                if "legal_spellings" in query
                else rows
            )
            return result

        monkeypatch.setattr(db, "conn", mock.Mock(execute=execute))

    def get(self, edition_id, core="cy", endings=("a", "i")):
        return client.get(
            self.url(edition_id), params={"core": core, "ending": list(endings)}
        )

    def test_lists_each_flagged_word_once_with_its_places(self, stored):
        response = self.get(stored)

        assert response.status_code == 200
        assert response.json() == [
            {
                "text": "Konwencya",
                "count": 3,
                "occurrences": [
                    {
                        "page_number": 3,
                        "start": 7,
                        "end": 16,
                        "before": "ma",
                        "after": "i",
                    },
                    {
                        "page_number": 3,
                        "start": 28,
                        "end": 37,
                        "before": "Policya,",
                        "after": ".",
                    },
                    {
                        "page_number": 5,
                        "start": 5,
                        "end": 14,
                        "before": "Znów",
                        "after": "tu",
                    },
                ],
            }
        ]

    def test_a_legal_spelling_of_the_edition_is_not_listed(self, stored):
        words = [g["text"] for g in self.get(stored).json()]

        assert "Policya" not in words

    def test_a_word_is_found_by_any_of_the_endings(self, stored):
        words = {g["text"] for g in self.get(stored, endings=("i",)).json()}

        assert words == set()  # "Konwencya" has "cya", not "cyi"

    def test_offsets_are_those_of_the_text(self, stored):
        text = "Ala ma Konwencya i Policya, Konwencya."

        for place in self.get(stored).json()[0]["occurrences"][:2]:
            assert text[place["start"] : place["end"]] == "Konwencya"

    def test_only_a_few_places_are_given_but_all_are_counted(self, stored, monkeypatch):
        rows = [
            {
                "number": n,
                "xml_content_corrected": str(text_to_tei_xml("Konwencya Konwencya")),
            }
            for n in range(4)
        ]
        self.pages(monkeypatch, rows)

        (group,) = self.get(stored).json()

        assert group["count"] == 8
        assert len(group["occurrences"]) == 5

    def test_the_words_that_are_most_often_in_the_edition_come_first(
        self, stored, monkeypatch
    ):
        rows = [
            {
                "number": 0,
                "xml_content_corrected": str(
                    text_to_tei_xml("Policya Konwencya Konwencya")
                ),
            }
        ]
        self.pages(monkeypatch, rows)

        words = [g["text"] for g in self.get(stored).json()]

        assert words == ["Konwencya", "Policya"]

    def test_an_empty_ending_is_the_core_alone(self, stored):
        words = {g["text"] for g in self.get(stored, endings=("",)).json()}

        assert words == {"Konwencya"}

    def test_edition_not_stored_is_404(self, monkeypatch):
        monkeypatch.setattr(db, "get_edition", mock.Mock(return_value=None))

        response = client.get(self.url(), params={"core": "cy", "ending": ["a"]})

        assert response.status_code == 404
        assert response.json()["detail"] == "Edition not found"

    @pytest.mark.parametrize(
        "params",
        [
            {"core": "cy"},
            {"core": "", "ending": ["a"]},
            {"core": "  ", "ending": ["a"]},
            {"core": "c y", "ending": ["a"]},
            {"core": "cy", "ending": ["1"]},
            {
                "core": "cy",
                "ending": [a + b for a in "ab" for b in "abcdefghijklmnopqrstuvwxyz"][
                    :41
                ],
            },
            {"ending": ["a"]},
        ],
    )
    def test_a_request_that_is_not_acceptable_is_422(self, stored, params):
        assert client.get(self.url(stored), params=params).status_code == 422

    def test_the_reason_is_given(self, stored):
        response = client.get(self.url(stored), params={"core": "cy"})

        assert "at least one ending" in response.json()["detail"]

    def test_dictionary_unavailable_is_503(self, stored, monkeypatch):
        def unavailable():
            raise SpellcheckUnavailable("no dictionary")

        monkeypatch.setattr(spelling, "get_checker", unavailable)

        assert self.get(stored).status_code == 503

    def test_is_in_the_openapi_schema(self):
        schema = client.get("/openapi.json").json()

        assert "get" in schema["paths"]["/editions/{edition_id}/spelling-chunk-matches"]
        assert {"SpellingGroup", "SpellingOccurrence"} <= schema["components"][
            "schemas"
        ].keys()


class TestTokenOccurrences:
    def url(self, edition_id=None):
        return f"/editions/{edition_id or uuid4()}/token-occurrences"

    @pytest.fixture
    def stored(self, monkeypatch):
        edition_id = uuid4()
        monkeypatch.setattr(
            db,
            "get_edition",
            mock.Mock(return_value=Edition(id=edition_id, work_id=uuid4())),
        )
        rows = [
            {"number": 0, "xml_content_corrected": str(text_to_tei_xml("Ala ma kota"))},
            {
                "number": 3,
                "xml_content_corrected": str(
                    text_to_tei_xml("W tern dniu terne, tern.")
                ),
            },
            {"number": 4, "xml_content_corrected": None},
            {
                "number": 5,
                "xml_content_corrected": str(text_to_tei_xml("Znów\ntern tu.")),
            },
            {"number": 6, "xml_content_corrected": "<not tei/>"},
        ]
        monkeypatch.setattr(
            db,
            "conn",
            mock.Mock(**{"execute.return_value.fetchall.return_value": rows}),
        )
        return edition_id

    def get(self, edition_id, token="tern"):
        return client.get(self.url(edition_id), params={"token": token})

    def test_lists_every_place_page_by_page_with_its_context(self, stored):
        response = self.get(stored)

        assert response.status_code == 200
        assert response.json() == [
            {
                "page_number": 3,
                "occurrences": [
                    {
                        "start": 2,
                        "end": 6,
                        "text": "tern",
                        "before": "W",
                        "after": "dniu",
                    },
                    {
                        "start": 19,
                        "end": 23,
                        "text": "tern",
                        "before": "terne,",
                        "after": ".",
                    },
                ],
            },
            {
                "page_number": 5,
                "occurrences": [
                    {
                        "start": 5,
                        "end": 9,
                        "text": "tern",
                        "before": "Znów",
                        "after": "tu",
                    }
                ],
            },
        ]

    def test_the_offsets_are_those_of_the_text(self, stored):
        text = "W tern dniu terne, tern."

        for place in self.get(stored).json()[0]["occurrences"]:
            assert text[place["start"] : place["end"]] == "tern"

    def test_it_is_exact(self, stored):
        assert self.get(stored, "Tern").json() == []
        assert self.get(stored, "ter").json() == []

    def test_edition_not_stored_is_404(self, monkeypatch):
        monkeypatch.setattr(db, "get_edition", mock.Mock(return_value=None))

        response = client.get(self.url(), params={"token": "tern"})

        assert response.status_code == 404
        assert response.json()["detail"] == "Edition not found"

    @pytest.mark.parametrize(
        "token", ["", " ", "dwa słowa", "tern,", " tern", "x" * 101]
    )
    def test_a_token_that_is_not_one_is_422(self, stored, token):
        assert self.get(stored, token).status_code == 422

    def test_no_token_is_422(self, stored):
        assert client.get(self.url(stored)).status_code == 422

    def test_the_reason_is_given(self, stored):
        response = self.get(stored, "tern,")

        assert "not a single token" in response.json()["detail"]

    def test_is_in_the_openapi_schema(self):
        schema = client.get("/openapi.json").json()

        assert "get" in schema["paths"]["/editions/{edition_id}/token-occurrences"]
        assert {"PageTokenOccurrences", "TokenOccurrence"} <= schema["components"][
            "schemas"
        ].keys()

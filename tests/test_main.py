from unittest import mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.main as app_main
from app.main import app, db
from app.models import PageView
from app.ontology import Edition, Page
from app.polona_connector import PolonaError
from app.tei_xml import text_to_tei_xml

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


def test_create_correction_stub():
    body = {"page_id": str(uuid4()), "before": "Aia", "after": "Ala"}

    assert client.post("/corrections", json=body).status_code == 501


@pytest.mark.parametrize("field", ["before", "after"])
def test_create_correction_rejects_empty(field):
    body = {"page_id": str(uuid4()), "before": "Aia", "after": "Ala", field: ""}

    assert client.post("/corrections", json=body).status_code == 422


def test_openapi_uses_ontology_models():
    schemas = client.get("/openapi.json").json()["components"]["schemas"]

    assert {"PageView", "Correction"} <= schemas.keys()


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

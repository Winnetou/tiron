import urllib.error
from unittest import mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.main as app_main
from app.main import app, db
from app.ontology import Edition

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
    monkeypatch.setattr(db, "upsert_edition", mock.Mock())
    fetch = mock.Mock(
        return_value={
            "scan_page_url": "https://polona.pl/iiif/3/x/full/max/0/default.jpg",
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
    assert body["raw_content"] == "Ala ma kota"
    assert body["corrected_content"] is None
    assert body["scan_page_url"].endswith("default.jpg")
    polona.assert_called_once_with(edition_id, 0)


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (ValueError("out of range"), 404),
        (LookupError("no scans"), 404),
        (urllib.error.URLError("down"), 502),
        (urllib.error.HTTPError("u", 404, "Not Found", {}, None), 404),
        (urllib.error.HTTPError("u", 500, "Error", {}, None), 502),
    ],
)
def test_get_page_polona_errors(in_collection, polona, error, expected_status):
    polona.side_effect = error

    response = client.get(f"/editions/{uuid4()}/pages/1")

    assert response.status_code == expected_status


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
    app_main.fetch_edition_metadata.side_effect = urllib.error.URLError("down")

    assert client.get(f"/editions/{uuid4()}/pages/1").status_code == 502
    db.upsert_edition.assert_not_called()

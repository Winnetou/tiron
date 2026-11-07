import json
import urllib.error
from uuid import uuid4

import pytest

from app import polona_connector
from app.polona_connector import (
    PolonaError,
    alto_to_text,
    fetch_edition_metadata,
    fetch_page,
)

ALTO = b"""<alto xmlns="http://www.loc.gov/standards/alto/ns-v2#"><Layout><Page>
<PrintSpace>
<TextBlock>
  <TextLine VPOS="200" HPOS="10" HEIGHT="20">
    <String CONTENT="kota."/>
  </TextLine>
  <TextLine VPOS="100" HPOS="10" HEIGHT="20">
    <String CONTENT="Ala"/><SP/><String CONTENT="ma"/><HYP CONTENT="172"/>
  </TextLine>
</TextBlock>
<TextBlock>
  <TextLine VPOS="400" HPOS="10" HEIGHT="20"><String CONTENT="przypis"/></TextLine>
</TextBlock>
</PrintSpace></Page></Layout></alto>"""


def entry(n, *, with_ocr=True, iiif=True):
    e = {"fileName": f"{n}.tif", "alternativeStreams": []}
    if iiif:
        e["iiifImageAPIManifest"] = f"https://polona.pl/iiif/3/img{n}/info.json"
    else:
        e["alternativeStreams"].append(
            {"streamRole": "PYRAMIDAL_TIFF", "fileStorageAddress": f"/store/img{n}"}
        )
    if with_ocr:
        e["alternativeStreams"].append(
            {"streamRole": "OCR_ALTO", "fileStorageAddress": f"/store/alto{n}"}
        )
    return e


@pytest.fixture
def polona(monkeypatch):
    """Fake polona.pl: contents listing with 3 pages, and an ALTO file."""
    calls = []
    contents = {
        "pages": [
            {"isDefault": False, "content": []},
            {"isDefault": True, "content": [entry(1), entry(2), entry(3)]},
        ]
    }

    def fake_fetch(url):
        calls.append(url)
        if url.endswith("/contents"):
            return json.dumps(contents).encode()
        return ALTO

    monkeypatch.setattr(polona_connector, "_fetch", fake_fetch)
    return calls, contents


def test_alto_to_text_reading_order_and_blocks():
    assert alto_to_text(ALTO) == "Ala ma\nkota.\n\nprzypis\n"


def test_alto_to_text_empty():
    assert alto_to_text(b"<alto/>") == ""


def test_fetch_page_returns_scan_url_and_content(polona):
    calls, _ = polona
    edition_id = uuid4()

    result = fetch_page(edition_id, 1)

    assert result == {
        "scan_uri": "https://polona.pl/iiif/3/img2/full/max/0/default.jpg",
        "raw_content": "Ala ma\nkota.\n\nprzypis\n",
    }
    assert calls == [
        f"https://polona.pl/api/library-object-query/digital-objects/{edition_id}/contents",
        "https://polona.pl/api/store/alto2",
    ]


def test_fetch_page_is_zero_based(polona):
    first = fetch_page(uuid4(), 0)["scan_uri"]
    last = fetch_page(uuid4(), 2)["scan_uri"]

    assert first.endswith("img1/full/max/0/default.jpg")
    assert last.endswith("img3/full/max/0/default.jpg")


@pytest.mark.parametrize("page_number", [-1, 3])
def test_fetch_page_out_of_range(polona, page_number):
    with pytest.raises(PolonaError) as error:
        fetch_page(uuid4(), page_number)
    assert error.value.not_found


def test_fetch_page_without_ocr(polona):
    _, contents = polona
    contents["pages"][1]["content"] = [entry(1, with_ocr=False)]

    result = fetch_page(uuid4(), 0)

    assert result["raw_content"] == ""


def test_fetch_page_tiff_fallback(polona):
    _, contents = polona
    contents["pages"][1]["content"] = [entry(1, iiif=False)]

    assert fetch_page(uuid4(), 0)["scan_uri"] == (
        "https://polona.pl/iiif/3/img1/full/max/0/default.jpg"
    )


def test_fetch_page_no_scans(polona):
    _, contents = polona
    contents["pages"] = []

    with pytest.raises(PolonaError) as error:
        fetch_page(uuid4(), 0)
    assert error.value.not_found


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (  # real record: no creator, publisher only; place/year from imprint
            {
                "imprint": "[Warszawa] : Fundacja Nowoczesna Polska, [2008].",
                "date": "2008-01-01",
                "dateDescriptive": "[2008]",
                "contributorCorp": "Fundacja Nowoczesna Polska. Wydawca",
                "title": "Lalka. T. 2",
            },
            {"author": None, "title": "Lalka", "place": "Warszawa", "year": 2008},
        ),
        (  # creator + publishPlace; date beats the descriptive year
            {
                "creator": "Lasocki, Zygmunt (1867-1948)",
                "publishPlace": "Kraków",
                "imprint": "Kraków : nakł. aut., 1935 (Warszawa : Druk. L. Gronuś).",
                "date": "1934-01-01",
                "dateDescriptive": "1935",
            },
            {
                "author": "Lasocki, Zygmunt (1867-1948)",
                "title": None,
                "place": "Kraków",
                "year": 1934,
            },
        ),
        (  # contributorName, role suffix stripped, year from descriptive date
            {
                "contributorName": "Vrchlický, Jaroslav Autor",
                "dateDescriptive": "[1896]",
            },
            {
                "author": "Vrchlický, Jaroslav",
                "title": None,
                "place": None,
                "year": 1896,
            },
        ),
        (  # unknown place, year range -> first year
            {
                "imprint": '[Miejsce nieznane] : Wydawnictwo "Kraj", [1901-1905] (x).',
            },
            {"author": None, "title": None, "place": None, "year": 1901},
        ),
        (  # unparseable / out of range year
            {"creator": "Mickiewicz, Adam", "dateDescriptive": "[1 poł. 19 w.]"},
            {"author": "Mickiewicz, Adam", "title": None, "place": None, "year": None},
        ),
        (
            {"dateDescriptive": "1200"},
            {"author": None, "title": None, "place": None, "year": None},
        ),
    ],
)
def test_fetch_edition_metadata_parsing(monkeypatch, metadata, expected):
    monkeypatch.setattr(
        polona_connector,
        "_fetch",
        lambda url: json.dumps({"metadata": metadata}).encode(),
    )

    assert fetch_edition_metadata(uuid4()) == expected


def test_fetch_edition_metadata_url_and_missing_metadata(monkeypatch):
    calls = []

    def fake_fetch(url):
        calls.append(url)
        return b"{}"

    monkeypatch.setattr(polona_connector, "_fetch", fake_fetch)
    edition_id = uuid4()

    with pytest.raises(PolonaError) as error:
        fetch_edition_metadata(edition_id)
    assert error.value.not_found
    assert calls == [
        f"https://polona.pl/api/library-object-query/digital-objects/{edition_id}"
    ]


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Lalka. T. 2", "Lalka"),
        ("Lalka. T. 1", "Lalka"),
        ("Lalka", "Lalka"),
        ("Lalka. Tom II", "Lalka"),
        ("Pan Tadeusz : historia szlachecka z r. 1811", "Pan Tadeusz"),
        ("Pan Tadeusz / Adam Mickiewicz", "Pan Tadeusz"),
        (
            "Pan Tadeusz czyli Ostatni zajazd na Litwie",
            "Pan Tadeusz czyli Ostatni zajazd na Litwie",
        ),
        ("Quo vadis. Część 2.", "Quo vadis"),
        ("  ", None),
    ],
)
def test_title_normalisation(monkeypatch, title, expected):
    monkeypatch.setattr(
        polona_connector,
        "_fetch",
        lambda url: json.dumps({"metadata": {"title": title, "date": "1900"}}).encode(),
    )

    assert fetch_edition_metadata(uuid4())["title"] == expected


def http_error(code):
    return urllib.error.HTTPError("https://polona.pl/x", code, "msg", {}, None)


@pytest.mark.parametrize(
    ("raised", "not_found", "message"),
    [
        (http_error(404), True, "Not found on polona.pl"),
        (http_error(500), False, "polona.pl returned HTTP 500"),
        (urllib.error.URLError("down"), False, "Could not reach polona.pl"),
        (TimeoutError("slow"), False, "Could not reach polona.pl"),
        (ConnectionResetError(), False, "Could not reach polona.pl"),
    ],
)
@pytest.mark.parametrize(
    "call",
    [
        lambda: fetch_page(uuid4(), 0),
        lambda: fetch_edition_metadata(uuid4()),
    ],
    ids=["fetch_page", "fetch_edition_metadata"],
)
def test_network_errors_become_a_single_polona_error(
    monkeypatch, call, raised, not_found, message
):
    def failing_fetch(url):
        raise raised

    monkeypatch.setattr(polona_connector, "_fetch", failing_fetch)

    with pytest.raises(PolonaError, match=message) as error:
        call()

    assert error.value.not_found is not_found
    assert error.value.__cause__ is raised


@pytest.mark.parametrize("body", [b"<html>not json</html>", b""])
def test_invalid_json_is_not_reported_as_not_found(monkeypatch, body):
    monkeypatch.setattr(polona_connector, "_fetch", lambda url: body)

    with pytest.raises(PolonaError, match="invalid response") as error:
        fetch_page(uuid4(), 0)

    assert error.value.not_found is False


def test_invalid_ocr_layer_is_a_polona_error(polona, monkeypatch):
    def fetch(url):
        if url.endswith("/contents"):
            return json.dumps(
                {"pages": [{"isDefault": True, "content": [entry(1)]}]}
            ).encode()
        return b"<alto><unclosed>"

    monkeypatch.setattr(polona_connector, "_fetch", fetch)

    with pytest.raises(PolonaError, match="invalid OCR layer") as error:
        fetch_page(uuid4(), 0)

    assert error.value.not_found is False

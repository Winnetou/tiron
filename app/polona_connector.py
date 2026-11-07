"""Connector to polona.pl, the source of the scans and OCR output."""

import json
import re
import statistics
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TypedDict
from uuid import UUID

BASE = "https://polona.pl"
HEADERS = {"User-Agent": "tiron/0.1"}
TIMEOUT = 120


class PolonaError(Exception):
    """The only exception the public functions of this module raise.

    `not_found` is True when polona.pl has no such edition/page/metadata, and
    False when polona.pl could not be reached or answered unexpectedly.
    """

    def __init__(self, message: str, *, not_found: bool = False) -> None:
        super().__init__(message)
        self.not_found = not_found


@contextmanager
def _polona_errors() -> Iterator[None]:
    """Translate everything that can go wrong while talking to polona.pl (and
    reading its answers) into PolonaError. Also usable as a decorator."""
    try:
        yield
    except PolonaError:
        raise
    except (ValueError, LookupError) as exc:
        # raised by our own lookups: page out of range, no scans/image/metadata
        if isinstance(exc, json.JSONDecodeError):
            raise PolonaError("polona.pl sent an invalid response") from exc
        raise PolonaError(str(exc), not_found=True) from exc
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise PolonaError("Not found on polona.pl", not_found=True) from exc
        raise PolonaError(f"polona.pl returned HTTP {exc.code}") from exc
    except ET.ParseError as exc:
        raise PolonaError("polona.pl sent an invalid OCR layer") from exc
    except OSError as exc:  # URLError, timeouts, connection resets
        raise PolonaError("Could not reach polona.pl") from exc


class PolonaPage(TypedDict):
    scan_uri: str
    raw_content: str


class EditionMetadata(TypedDict):
    author: str | None
    title: str | None
    place: str | None
    year: int | None


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read()


def _get_page_entry(edition_id: UUID, index: int) -> dict:
    data = json.loads(
        _fetch(f"{BASE}/api/library-object-query/digital-objects/{edition_id}/contents")
    )
    groups = data.get("pages") or []
    if not groups:
        raise LookupError("This object has no paginated scans")
    group = next((g for g in groups if g.get("isDefault")), groups[0])
    pages = group["content"]
    if not 0 <= index < len(pages):
        raise ValueError(f"Page out of range: this edition has {len(pages)} pages")
    return pages[index]


def _stream(entry: dict, role: str) -> str | None:
    for stream in entry.get("alternativeStreams", []):
        if stream.get("streamRole") == role:
            return stream["fileStorageAddress"]
    return None


def _scan_url(entry: dict) -> str:
    """Full-resolution scan, via IIIF."""
    manifest = entry.get("iiifImageAPIManifest")
    if manifest:
        return manifest.rsplit("/info.json", 1)[0] + "/full/max/0/default.jpg"
    tiff = _stream(entry, "PYRAMIDAL_TIFF")
    if not tiff:
        raise LookupError("No image found for this page")
    return f"{BASE}/iiif/3/{tiff.rsplit('/', 1)[-1]}/full/max/0/default.jpg"


def _local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def alto_to_text(xml_bytes: bytes) -> str:
    """Convert an ALTO XML OCR layer to plain text in reading order."""
    root = ET.fromstring(xml_bytes)
    lines = []
    blocks = (el for el in root.iter() if _local_name(el) == "TextBlock")
    for block_no, block in enumerate(blocks):
        for text_line in block:
            if _local_name(text_line) != "TextLine":
                continue
            # <String> = word, <SP> = space. <HYP> is ABBYY's end-of-line
            # marker (CONTENT="172", i.e. "¬"); the viewer drops it, so do we.
            text = "".join(
                el.get("CONTENT", "") if _local_name(el) == "String" else " "
                for el in text_line
                if _local_name(el) in ("String", "SP")
            ).strip()
            if text:
                lines.append(
                    {
                        "b": block_no,
                        "y": int(float(text_line.get("VPOS", 0))),
                        "x": int(float(text_line.get("HPOS", 0))),
                        "h": int(float(text_line.get("HEIGHT", 0))),
                        "t": text,
                    }
                )
    if not lines:
        return ""

    # Reading order: top to bottom; lines on the same row (e.g. page number +
    # running head) are joined left to right. A new OCR text block (main text,
    # footnote...) starts after a blank line.
    lines.sort(key=lambda line: (line["y"], line["x"]))
    median_height = statistics.median(line["h"] for line in lines) or 1
    rows: list[list[dict]] = []
    for line in lines:
        if rows and abs(line["y"] - rows[-1][0]["y"]) < median_height * 0.5:
            rows[-1].append(line)
        else:
            rows.append([line])

    out: list[str] = []
    previous_blocks: set[int] | None = None
    for row in rows:
        row.sort(key=lambda line: line["x"])
        row_blocks = {line["b"] for line in row}
        if previous_blocks is not None and not row_blocks & previous_blocks:
            out.append("")
        out.append(" ".join(line["t"] for line in row))
        previous_blocks = row_blocks
    return "\n".join(out) + "\n"


@_polona_errors()
def fetch_page(edition_id: UUID, page_number: int) -> PolonaPage:
    """Fetch a page of an edition from polona.pl.

    `edition_id` is the Polona object UUID. `page_number` is 0-based, like
    Page.number and Polona's own ?page= URL parameter (page_number=99 is
    "Skan 100" in the viewer).

    Returns the full-resolution scan URL under "scan_uri" and the text of
    the OCR layer under "raw_content" ("" if the page has no OCR layer).

    Raises PolonaError, and nothing else: with `not_found` set if the edition
    has no scans or image, the page is out of range, or polona.pl answers 404;
    otherwise (network failure, HTTP error, invalid response) without it.
    """
    entry = _get_page_entry(edition_id, page_number)
    scan_uri = _scan_url(entry)
    alto = _stream(entry, "OCR_ALTO")
    raw_content = alto_to_text(_fetch(f"{BASE}/api{alto}")) if alto else ""
    return {"scan_uri": scan_uri, "raw_content": raw_content}


def _first(metadata: dict, *keys: str) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if value and value.strip():
            return value.strip()
    return None


def _parse_author(metadata: dict) -> str | None:
    author = _first(metadata, "creator", "contributorName")
    if author is None:
        return None
    return re.sub(r"\.?\s+Autor$", "", author)


def _parse_title(metadata: dict) -> str | None:
    """Title of the work: without subtitle ("Title : subtitle", "Title / ...")
    and without the volume ("Lalka. T. 2" -> "Lalka"), so that the volumes and
    editions of one work get the same title."""
    title = _first(metadata, "title")
    if title is None:
        return None
    title = re.split(r" [:/] ", title, maxsplit=1)[0]
    title = re.sub(
        r"[\s.,;]+(?:t|tom|cz|część)\.?\s*[\dIVX]+\b.*$", "", title, flags=re.I
    )
    return title.strip(" .,;:/") or None


def _parse_place(metadata: dict) -> str | None:
    place = _first(metadata, "publishPlace")
    if place is None:
        # imprint looks like "[Warszawa] : Wydawnictwo, 1925 (...)"
        imprint = _first(metadata, "imprint")
        if imprint is None or " : " not in imprint:
            return None
        place = imprint.split(" : ", 1)[0]
    place = place.strip("[] ?")
    if not place or place.lower().startswith("miejsce nieznane"):
        return None
    return place


def _parse_year(metadata: dict) -> int | None:
    for key in ("date", "dateDescriptive", "imprint"):
        value = metadata.get(key)
        match = value and re.search(r"(?<!\d)(\d{4})(?!\d)", value)
        if match:
            year = int(match.group(1))
            return year if 1500 <= year <= 2030 else None
    return None


@_polona_errors()
def fetch_edition_metadata(edition_id: UUID) -> EditionMetadata:
    """Fetch the bibliographic metadata of an edition from polona.pl.

    Polona's records are inconsistent, so every value is best effort and None
    when it can't be determined:
    - title: "title" without subtitle and volume number.
    - author: "creator" or "contributorName" (without the trailing "Autor");
      many records only name a publisher, so this is often None.
    - place: "publishPlace", else the part of "imprint" before " : ".
    - year: first 4-digit year in "date", "dateDescriptive" or "imprint"
      (the first year of a range), if within 1500-2030.

    Raises PolonaError, and nothing else, like fetch_page.
    """
    data = json.loads(
        _fetch(f"{BASE}/api/library-object-query/digital-objects/{edition_id}")
    )
    metadata = data.get("metadata")
    if not metadata:
        raise LookupError("This object has no metadata")
    return {
        "author": _parse_author(metadata),
        "title": _parse_title(metadata),
        "place": _parse_place(metadata),
        "year": _parse_year(metadata),
    }

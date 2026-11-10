import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

import psycopg.errors
from fastapi import FastAPI, HTTPException, Path, Response, status

from app.db import Database
from app.models import (
    EntityTag,
    NerRequest,
    NerResult,
    NewLegalSpelling,
    PageUpdate,
    PageView,
    SpellcheckRequest,
    SpellcheckResult,
    TeiText,
    TeiXml,
    WordCorrection,
)
from app.ner import MODEL_NAME, NerModelUnavailable, find_entities, get_nlp
from app.ontology import Edition, LegalSpelling, Page, Work
from app.polona_connector import PolonaError, fetch_edition_metadata, fetch_page
from app.spelling import SpellcheckUnavailable, check_words, get_checker
from app.tei_xml import (
    CannotReplace,
    CannotTag,
    InvalidTei,
    TextChanged,
    replace_text_range,
    tag_text_range,
    tei_xml_to_text,
    text_to_tei_xml,
)

db = Database()

logger = logging.getLogger("uvicorn.error")  # shown with uvicorn's own output


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Load the spaCy model and the Polish spelling dictionary when the backend
    starts, so that no request has to wait for them. The backend only starts
    serving requests once they are loaded, which takes several seconds.

    If one is not available (the model is not installed when running without
    Docker), the backend still starts, because everything else works without
    it: it logs a warning, and the endpoint that needs it answers 503.
    """
    logger.info("Loading the spaCy model %s ...", MODEL_NAME)
    try:
        get_nlp()
    except NerModelUnavailable as error:
        logger.warning("%s: /ner will answer 503", error)
    else:
        logger.info("The spaCy model %s is loaded", MODEL_NAME)
    logger.info("Loading the spelling dictionary ...")
    try:
        get_checker()
    except SpellcheckUnavailable as error:
        logger.warning("%s: /spellcheck will answer 503", error)
    else:
        logger.info("The spelling dictionary is loaded")
    yield


app = FastAPI(title="tiron", lifespan=lifespan)


@app.get("/editions/{edition_id}/pages/{number}", response_model=PageView)
def get_page(edition_id: UUID, number: Annotated[int, Path(ge=0)]) -> PageView:
    """Return a page of an edition, ready to display.

    1. The edition must be in the collection, otherwise 404. This check comes
       first, so it also guards pages that are already stored.
    2. If the page is already in the database, it is returned from there and
       Polona is not contacted.
    3. Otherwise the page is fetched from Polona. If the edition is not stored
       yet, its metadata is fetched too and a Work and an Edition are created
       (the page needs its edition). The page is then saved and returned, so
       the next request is served from the database. It is saved with the
       Polona text as `raw_content`, and that text converted to TEI XML as
       `corrected_content`, which the user then edits.

    Polona failures are turned into HTTP errors: 404 if Polona has no such
    page, 502 if it could not be reached or answered unexpectedly. Nothing is
    saved in that case.

    The response is a PageView: its `content` is the corrected text if the
    page has one, otherwise the raw OCR text.
    """
    if not db.check_edition_in_collection(edition_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Edition not found")
    stored = db.get_page_by_number(edition_id, number)
    if stored is not None:
        return PageView.from_page(stored)
    try:
        polona_page = fetch_page(edition_id, number)
        if db.get_edition(edition_id) is None:
            metadata = fetch_edition_metadata(edition_id)
            work = Work(author=metadata["author"], title=metadata["title"])
            db.upsert_edition(
                work,
                Edition(
                    id=edition_id,
                    work_id=work.id,
                    place=metadata["place"],
                    year=metadata["year"],
                ),
            )
    except PolonaError as exc:
        if exc.not_found:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found") from exc
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    raw_content = polona_page["raw_content"]
    page = db.save_fetched_page(
        Page(
            edition_id=edition_id,
            number=number,
            raw_content=raw_content,
            corrected_content=str(text_to_tei_xml(raw_content)),
            scan_uri=polona_page["scan_uri"],
        )
    )
    return PageView.from_page(page)


@app.patch("/editions/{edition_id}/pages/{number}", response_model=PageView)
def update_page(
    edition_id: UUID, number: Annotated[int, Path(ge=0)], update: PageUpdate
) -> PageView:
    """Edit a page: only `corrected_content` and `ready` can be changed.

    Fields left out of the request are not touched, and
    `"corrected_content": null` (or an empty string) removes the correction, so
    `content` falls back to the raw text. Any other field in the request, such
    as `raw_content` or `scan_uri`, is rejected with 422. The page must already
    be stored (open it with GET first); it is never fetched from Polona here.

    404 if the page is not stored. Returns the updated page as a PageView.
    """
    page = db.update_page(edition_id, number, **update.model_dump(exclude_unset=True))
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found")
    return PageView.from_page(page)


@app.post("/editions/{edition_id}/pages/{number}/entities", response_model=PageView)
def tag_entity(
    edition_id: UUID, number: Annotated[int, Path(ge=0)], entity: EntityTag
) -> PageView:
    """Mark a named entity in the TEI XML of a stored page: the characters
    `start` to `end` of the page's text are put inside an element named `label`
    (`persName`, `placeName`, `orgName`, `geogName`, `date` or `time`), and the
    changed XML is saved as the page's corrected content.

    The body is `{"start": 0, "end": 15, "text": "Adam Mickiewicz", "label":
    "persName"}`: what `POST /ner` returned, so the offsets are those of the
    text that `POST /tei/text` gives for this page. `text` is the entity as the
    client saw it, and the backend checks that it is what is at those offsets.
    The text of the page does not change, so the offsets of its other entities
    stay valid. Marking an entity that is already marked changes nothing.

    404 if the page is not stored. 409 if the text at the offsets is not
    `text`, because the page has changed since the entities were recognized.
    422 if the page has no valid TEI XML, or the entity cannot be marked in it
    (for example it spans paragraphs, or starts inside other markup). Returns
    the updated page as a PageView.
    """
    page = db.get_page_by_number(edition_id, number)
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found")
    if not page.corrected_content:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "The page has no TEI XML to mark entities in",
        )
    try:
        xml = tag_text_range(
            page.corrected_content,
            entity.start,
            entity.end,
            entity.label,
            entity.text,
        )
    except TextChanged as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (InvalidTei, CannotTag) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if xml != page.corrected_content:
        page = db.update_page(edition_id, number, corrected_content=xml) or page
    return PageView.from_page(page)


@app.post("/tei/text", response_model=TeiText)
def tei_to_text(tei: TeiXml) -> TeiText:
    """Validate a TEI XML document and return just the text extracted from it.

    The body is `{"xml": "<TEI ...>...</TEI>"}`. The XML must be well-formed,
    have no DOCTYPE/ENTITY declarations, and be a TEI document: a `TEI` root in
    the TEI namespace with a `teiHeader` and a `text`. Otherwise the response
    is 422 and `detail` says what is wrong. This checks the TEI skeleton; it
    is not a validation against the full TEI schema.

    The response is `{"text": "..."}`: the content of the `text` element
    without any markup. Blocks (`p`, `head`, ...) are separated by a blank
    line and line breaks (`lb`, ...) become newlines, so a page converted with
    `text_to_tei_xml` comes back as the same text. Nothing is stored.
    """
    try:
        return TeiText(text=tei_xml_to_text(tei.xml))
    except InvalidTei as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@app.post("/ner", response_model=NerResult)
def recognize_entities(request: NerRequest) -> NerResult:
    """Find the named entities (people, places, organisations, dates, ...) in
    a text, using spaCy and its large Polish model, `pl_core_news_lg`.

    The body is `{"text": "..."}`: the plain text of a page, not XML (get it
    from TEI with `POST /tei/text`). The response lists the entities in the
    order they appear, each with its `text`, its `label` and its `start` and
    `end` character offsets in the submitted text, so `text[start:end]` is the
    entity's text. A text without entities, or an empty one, gives an empty
    list. Nothing is stored.

    The model is loaded when the backend starts (see `lifespan`), so a request
    doesn't wait for it. If it is not installed the response is 503. The text
    may have up to 100,000 characters, otherwise the response is 422.
    """
    try:
        return NerResult(entities=find_entities(request.text))
    except NerModelUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@app.post("/spellcheck", response_model=SpellcheckResult)
def spellcheck(request: SpellcheckRequest) -> SpellcheckResult:
    """Check the spelling of a text, with phunspell and the Polish Hunspell
    dictionary (`pl_PL`).

    The body is `{"text": "..."}`: the plain text of a page. The response lists
    every word of it, in order, as `{"text", "start", "end", "correct"}`:
    `start` and `end` are character offsets in the submitted text, so
    `text[start:end]` is the word, and `correct` says whether it is spelled
    correctly. Everything between the words is not a word. A word is made of
    letters and digits, with a hyphen or apostrophe inside it; words with digits
    are not checked and count as correct. Listing all words rather than only the
    wrong ones means the backend alone decides what a word is.

    The body may also have an `edition_id`, the edition the text is from. The
    spellings that were made legal in it (`POST .../legal-spellings`) then count
    as correct: a legal "jey" is not reported as wrong, and nor are "Jey" and
    "JEY" (a sentence can start with it); a legal "Jey" does not make "jey"
    correct. An edition that is not stored just has none.

    The dictionary is loaded when the backend starts (see `lifespan`). If it is
    not available the response is 503. The text may have up to 100,000
    characters, otherwise the response is 422. Nothing is stored.
    """
    try:
        legal = db.get_legal_spellings(request.edition_id) if request.edition_id else ()
        return SpellcheckResult(words=check_words(request.text, legal))
    except SpellcheckUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@app.post("/editions/{edition_id}/pages/{number}/corrections", response_model=PageView)
def correct_word(
    edition_id: UUID, number: Annotated[int, Path(ge=0)], correction: WordCorrection
) -> PageView:
    """Correct a word of a stored page: the characters `start` to `end` of the
    page's text, which are `before`, are replaced with `after` in the page's
    TEI XML, and the Correction (`before` -> `after`) is recorded for the page.

    The body is `{"start": 25, "end": 37, "before": "dawnieyszych", "after":
    "dawniejszych"}`. The offsets are those of the text that `POST /tei/text`
    gives for this page (and `POST /spellcheck` returned). The backend finds the
    place in the XML and changes the text where it is, so the markup around it
    stays; the frontend never has to read the XML. `before` is the word as the
    client saw it, and the backend checks that it is what is at those offsets.

    The new XML is saved as the page's corrected content and the Correction is
    stored in the same database transaction, with the page locked: both happen
    or neither does. The text of the page changes, so the offsets of its
    entities and words from before are no longer valid.

    404 if the page is not stored. 409 if the text at the offsets is not
    `before`, because the page has changed since. 422 if the page has no valid
    TEI XML, or the replacement is not acceptable: `before` must not contain
    spaces or line breaks, and `after` must not be empty, start or end with a
    space, contain a line break, or be the same as `before`. Returns the
    updated page as a PageView.
    """

    def replace_in_xml(xml: str) -> str:
        if not xml:
            raise InvalidTei("The page has no TEI XML to correct")
        return replace_text_range(
            xml, correction.start, correction.end, correction.before, correction.after
        )

    try:
        page = db.apply_correction(
            edition_id, number, replace_in_xml, correction.before, correction.after
        )
    except TextChanged as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (InvalidTei, CannotReplace) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found")
    return PageView.from_page(page)


@app.post("/editions/{edition_id}/legal-spellings", response_model=LegalSpelling)
def add_legal_spelling(
    edition_id: UUID, new: NewLegalSpelling, response: Response
) -> LegalSpelling:
    """Say that a spelling is legal in an edition, although the dictionary does
    not know it: old print has spellings like "jey" and "tey", which are not
    mistakes there.

    The body is `{"spelling": "jey"}`: one word, as it is written in the
    edition (case matters: "Jey" is another spelling). The response is
    `{"edition_id": ..., "spelling": ...}`. A spelling is stored once per
    edition: 201 if it was added, and 200 if the edition already had it, so
    sending it again is harmless. Nothing else changes: this only records the
    spelling.

    404 if the edition is not stored, which it is once one of its pages has been
    opened. 422 if the spelling is empty, longer than 100 characters, or has
    spaces, line breaks or control characters in it.
    """
    if db.get_edition(edition_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Edition not found")
    legal = LegalSpelling(edition_id=edition_id, spelling=new.spelling)
    try:
        added = db.add_legal_spelling(legal)
    except psycopg.errors.ForeignKeyViolation as exc:  # removed in the meantime
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Edition not found") from exc
    response.status_code = status.HTTP_201_CREATED if added else status.HTTP_200_OK
    return legal

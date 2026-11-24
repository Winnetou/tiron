import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

import psycopg.errors
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)
from fastapi_pagination import add_pagination

from app import page_provider
from app.auth import (
    COOKIE_NAME,
    SESSION_SECONDS,
    create_token,
    is_https,
    require_login,
    session_secret,
)
from app.persistence.db import Database, WorkExists
from app.persistence.ontology import (
    EditionMetadata,
    LegalSpelling,
    MissingHyphen,
    PageMatches,
    PageRubbish,
    PageTokenOccurrences,
    SimilarEntities,
    SimilarPageNumbers,
    SpellingFamily,
    SpellingGroup,
)
from app.persistence.schema_creator import ensure_schema
from app.polona.polona_connector import EditionNotFoundError, PolonaError
from app.text_tools.ner import MODEL_NAME, NerModelUnavailable, find_entities, get_nlp
from app.text_tools.spelling import (
    NotAWord,
    SpellcheckUnavailable,
    check_words,
    get_checker,
    suggest_spelling,
)
from app.text_tools.spelling_chunks import InvalidChunk, chunk_substrings
from app.text_tools.tei_xml import (
    CannotInsert,
    CannotJoin,
    CannotJoinLines,
    CannotRemoveLine,
    CannotReplace,
    CannotSplit,
    CannotTag,
    InvalidTei,
    TextChanged,
    foreign_language_ranges,
    insert_line_at,
    insert_text_at,
    join_lines_at,
    join_text_range,
    mark_section,
    remove_line_at,
    replace_text_range,
    split_line_at,
    tag_text_range,
    tei_xml_entities,
    tei_xml_sections,
    tei_xml_to_text,
)
from app.text_tools.token_occurrences import InvalidToken
from app.view_models import (
    CollectionPage,
    ContinuedWord,
    ContinuedWordOnNextPage,
    EditionMetadataUpdate,
    EntityLabel,
    EntityTag,
    HyphenFixes,
    LineJoin,
    LineRemoval,
    LineRemoved,
    LineSplit,
    LoginRequest,
    LoginResult,
    NerRequest,
    NerResult,
    NewLegalSpelling,
    NewToken,
    PageUpdate,
    PageView,
    SectionMark,
    SpellcheckRequest,
    SpellcheckResult,
    SuggestionRequest,
    Suggestions,
    TeiEntities,
    TeiSections,
    TeiText,
    TeiXml,
    TokenJoin,
    WordCorrection,
)

db = Database()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Load the spaCy model and the Polish spelling dictionary when the backend
    starts, so that no request has to wait for them. The backend only starts
    serving requests once they are loaded, which takes several seconds.

    If one is not available (the model is not installed when running without
    Docker), the backend still starts, because everything else works without
    it: it logs a warning, and the endpoint that needs it answers 503.
    """
    # first: without the secret there is no login (production), and without the schema
    # nothing else is of any use (see `ensure_schema`)
    session_secret()
    ensure_schema(db)
    logger.info("Loading the spaCy model %s ...", MODEL_NAME)
    started = time.perf_counter()
    try:
        get_nlp()
    except NerModelUnavailable as error:
        logger.warning("%s: /ner will answer 503", error)
    else:
        logger.info(
            "The spaCy model %s is loaded (%.1f s)",
            MODEL_NAME,
            time.perf_counter() - started,
        )
    logger.info("Loading the spelling dictionary ...")
    started = time.perf_counter()
    try:
        get_checker()
    except SpellcheckUnavailable as error:
        logger.warning("%s: /spellcheck will answer 503", error)
    else:
        logger.info(
            "The spelling dictionary is loaded (%.1f s)", time.perf_counter() - started
        )
    yield


# Every endpoint needs a login, except those in `app.auth.PUBLIC` (the list of
# editions, /health, and the login itself).
app = FastAPI(title="tiron", lifespan=lifespan, dependencies=[Depends(require_login)])


@app.post("/auth/login", response_model=LoginResult)
def login(
    credentials: LoginRequest, request: Request, response: Response
) -> LoginResult:
    """Log in with a username and a password: the answer has the session cookie.

    The users are in the `users` table (added by hand: there is no registration and
    no way to change a password). The cookie is `HttpOnly` (the page's scripts cannot
    read it), `SameSite=Lax` (a request from another site does not carry it), and
    `Secure` when the browser is on HTTPS. It is a signed token that is good for 24
    hours (`app.auth`), and every other endpoint, except the list of editions and
    `/health`, answers 401 `Login required` without it.

    401 `Wrong username or password` for an unknown username or a wrong password
    (the answer is the same for both, and so is the time it takes).
    """
    if not db.check_user(credentials.username, credentials.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong username or password")
    response.set_cookie(
        COOKIE_NAME,
        create_token(credentials.username),
        max_age=SESSION_SECONDS,
        httponly=True,
        samesite="lax",
        secure=is_https(request),
        path="/",
    )
    return LoginResult(username=credentials.username)


@app.get("/health")
def health() -> dict[str, str]:
    """Whether the backend can do its work: the database is asked `SELECT 1`.

    `{"status": "ok", "database": "ok"}` if it answers, 503 if it cannot be
    reached. For a load balancer, an uptime check, or a look after a deploy. It
    does not wait for the spaCy model or the dictionary (those have their own
    503s), and a lost connection is opened again before the answer.
    """
    if not db.is_reachable():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "The database is not reachable"
        )
    return {"status": "ok", "database": "ok"}


@app.get("/editions", response_model=CollectionPage)
def list_editions() -> CollectionPage:
    """The main page: the editions in the collection, 100 on a page.

    Paginated with fastapi-pagination: `?page=1` (the first, the default) and
    `?size=` (at most 100, which is also the default). The response is
    `{"items": [{"id": uuid, "author": ..., "title": ...}, ...], "total": ...,
    "page": ..., "size": ..., "pages": ...}`. The `author` and `title` are those of
    the edition's work (the `collection` table is joined with `editions` and `works`
    on the edition id); each is null if it is not known, as for an edition that is
    not stored yet. The editions come in the order of the ids, so a page is the same
    every time. A page past the last one has no items. Each is an edition whose
    pages are at `GET /editions/{id}/pages/{number}`.
    """
    return db.list_collection()


@app.get("/editions/{edition_id}/metadata", response_model=EditionMetadata)
def get_edition_metadata(edition_id: UUID) -> EditionMetadata:
    """What is known of a stored edition: `{"id": ..., "work_id": ..., "work_editions":
    2, "author": ..., "title": ..., "place": ..., "year": ...}`. The `author` and
    `title` are those of the edition's work (`work_id`; `work_editions` is how many
    editions the work has), the `place` and `year` those of the edition. `null` is
    what is not known (the metadata that is fetched from Polona is not always
    complete). It is shown in the panel where it is edited (`PATCH` of the same
    address).

    404 if the edition is not stored. Polona is not called.
    """
    metadata = db.get_edition_metadata(edition_id)
    if metadata is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Edition not found")
    return metadata


@app.patch("/editions/{edition_id}/metadata", response_model=EditionMetadata)
def update_edition_metadata(
    edition_id: UUID, update: EditionMetadataUpdate
) -> EditionMetadata:
    """Edit what is known of a stored edition, for when what was fetched from Polona
    is not complete.

    The body has some of `author`, `title` (of the work), `place` and `year` (of the
    edition), e.g. `{"author": "Prus, Bolesław", "year": 1890}`. A field that is not
    in the body is left as it is; one that is `null` or only white space clears it.
    The text is trimmed, and white space in it becomes one space; `year` is a
    number from 1500 to 2030. Any other field is rejected, and so is a body without
    any of the four.

    `place` and `year` are changed in the edition. `author` and `title` are changed
    in the edition's work itself, so the other editions of the work have the change
    too (`work_editions` in the response says how many there are). The response is
    what is known now, the same as `GET`.

    404 if the edition is not stored, 409 if another work has the new author and title
    already (a work is one per author and title, case does not matter; nothing is
    changed), 422 if the body is not valid. Polona is not called.
    """
    try:
        metadata = db.update_edition_metadata(edition_id, update.changes())
    except WorkExists as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if metadata is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Edition not found")
    logger.info(
        "Metadata of edition %s edited: %s", edition_id, sorted(update.model_fields_set)
    )
    return metadata


@app.get("/editions/{edition_id}/pages/{number}", response_model=PageView)
def fetch_page(edition_id: UUID, number: Annotated[int, Path(ge=0)]) -> PageView:
    """Return a page of an edition, ready to display.

    1. If the page is already in the database, it is returned from there and
       Polona is not contacted.
    2. Otherwise (see `page_provider.idempotent_fetch_page`) the edition is saved
       first, if it is not stored yet: its metadata is fetched and a Work and an
       Edition are created (the page needs its edition). The page is then
       fetched from Polona, saved and returned, so the next request is served
       from the database. It is saved with the Polona text as
       `raw_ocr_content`, and that text converted to TEI XML as
       `xml_content_corrected`, which the user then edits.

    Polona failures are turned into HTTP errors: 404 ("Edition not found") if
    Polona has no such edition, 404 ("Page not found") if it has no such
    page, 502 if it could not be reached or answered unexpectedly. No page is
    saved in that case (the edition may be, if it was new).

    The response is a PageView: its `content` is the page's TEI XML
    (`xml_content_corrected`). The raw OCR text is never served.
    """
    try:
        page = page_provider.idempotent_fetch_page(edition_id, number, db=db)
    except PolonaError as exc:
        logger.warning(
            "Polona failed for page %s of edition %s: %s", number, edition_id, exc
        )
        if isinstance(exc, EditionNotFoundError):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Edition not found") from exc
        if exc.not_found:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found") from exc
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return PageView.from_page(page)


@app.patch("/editions/{edition_id}/pages/{number}", response_model=PageView)
def update_page(
    edition_id: UUID, number: Annotated[int, Path(ge=0)], update: PageUpdate
) -> PageView:
    """Edit a page: only `xml_content_corrected` and `ready` can be changed.

    Fields left out of the request are not touched. A page always keeps its
    XML, so `"xml_content_corrected"` cannot be null or an empty string (422).
    Any other field in the request, such as `raw_ocr_content` or `scan_uri`, is
    rejected with 422 too. The page must already be stored (open it with GET first);
    it is never fetched from Polona here.

    404 if the page is not stored. Returns the updated page as a PageView.
    """
    changes = update.model_dump(exclude_unset=True)
    page = db.update_page(edition_id, number, **changes)
    if page is None:
        logger.warning("Page %s of edition %s is not stored", number, edition_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found")
    logger.info(
        "Page %s of edition %s updated: %s", number, edition_id, ", ".join(changes)
    )
    return PageView.from_page(page)


@app.post("/editions/{edition_id}/pages/{number}/entities", response_model=PageView)
def tag_entity(
    edition_id: UUID, number: Annotated[int, Path(ge=0)], entity: EntityTag
) -> PageView:
    """Mark a named entity in the TEI XML of a stored page: the characters
    `start` to `end` of the page's text are put inside an element named `label`
    (`persName`, `placeName`, `orgName`, `geogName`, `date`, `time`, or `ref`, a
    reference in the text, `bibl`, bibliographic information, or `cit`, a cited
    quotation, which TEI wants in a `<quote>`: `<cit><quote>...</quote></cit>`),
    and the changed XML is saved as the page's corrected content.

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
    if not page.xml_content_corrected:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "The page has no TEI XML to mark entities in",
        )
    try:
        xml = tag_text_range(
            page.xml_content_corrected,
            entity.start,
            entity.end,
            entity.label,
            entity.text,
        )
    except TextChanged as exc:
        logger.warning(
            "Entity not tagged on page %s of %s: %s", number, edition_id, exc
        )
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (InvalidTei, CannotTag) as exc:
        logger.warning(
            "Entity not tagged on page %s of %s: %s", number, edition_id, exc
        )
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if xml != page.xml_content_corrected:
        page = db.update_page(edition_id, number, xml_content_corrected=xml) or page
    logger.info(
        "Entity %s tagged on page %s of %s (%s-%s)",
        entity.label,
        number,
        edition_id,
        entity.start,
        entity.end,
    )
    return PageView.from_page(page)


@app.post("/editions/{edition_id}/pages/{number}/sections", response_model=PageView)
def mark_page_section(
    edition_id: UUID, number: Annotated[int, Path(ge=0)], mark: SectionMark
) -> PageView:
    """Mark a section of a page in the TEI XML of a stored page: the characters
    `start` to `end` of the page's text become a running header, a footer, the
    printed page number, or the table of contents, and the changed XML is saved
    as the page's corrected content.

    The body is `{"start": 0, "end": 10, "text": "84 KATEDRA", "section":
    "header"}`. The offsets are those of the text that `POST /tei/text` gives
    for this page, and `text` is the selection as the client saw it, which the
    backend checks. The `section` is one of:

    - `header`: `<fw type="header">` around the text
    - `footer`: `<fw type="footer">` around the text
    - `page_number`: `<fw type="pageNum">` around the text (the printed number)
    - `toc`: `<div type="toc">` around whole paragraphs
    - `note`: `<note>` around the text, like an entity; with the optional `n`
      (e.g. `"n": "1"`, only for a note) it is `<note n="1">`
    - `quote`: `<quote xml:lang="la">` around the text, a quotation in another
      language; the body must also have `lang`, the code of the language: one of
      `ru`, `en`, `de`, `es`, `fr`, `it`, `la`, `grc` (Ancient Greek) (`lang`
      is only for a quote). The text in it is not Polish, so the spell check leaves it
      alone (see `POST /spellcheck`)
    - `note_group`: `<noteGrp>` around notes that are already marked and next to
      each other, and nothing else: TEI lets a `<noteGrp>` hold only notes, so
      anything else in the selection is a 422

    The `<fw>` elements go around the text, like an entity, and cannot span
    paragraphs or start inside other markup. A `<div>` cannot hold bare text or
    sit in a paragraph, so a paragraph that the selection starts or ends in the
    middle of is split there, the paragraphs of the selection go in the `<div>`,
    and the paragraphs after it each go in a plain `<div>` (TEI allows nothing
    else after a `<div>`). The text of the page then has a blank line where a
    paragraph was split, so for `toc` the offsets of the page's words, entities
    and so on are no longer valid. Marking a section that is already marked
    changes nothing.

    404 if the page is not stored. 409 if the text at the offsets is not `text`,
    because the page has changed since. 422 if the page has no valid TEI XML or
    the section cannot be marked in it, with the reason. Returns the updated page
    as a PageView.
    """
    page = db.get_page_by_number(edition_id, number)
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found")
    if not page.xml_content_corrected:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "The page has no TEI XML to mark sections in",
        )
    try:
        xml = mark_section(
            page.xml_content_corrected,
            mark.start,
            mark.end,
            mark.section,
            mark.text,
            mark.n,
            mark.lang,
        )
    except TextChanged as exc:
        logger.warning(
            "Section not marked on page %s of %s: %s", number, edition_id, exc
        )
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (InvalidTei, CannotTag) as exc:
        logger.warning(
            "Section not marked on page %s of %s: %s", number, edition_id, exc
        )
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if xml != page.xml_content_corrected:
        page = db.update_page(edition_id, number, xml_content_corrected=xml) or page
    logger.info(
        "Section %s marked on page %s of %s (%s-%s)",
        mark.section,
        number,
        edition_id,
        mark.start,
        mark.end,
    )
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


@app.post("/tei/sections", response_model=TeiSections)
def tei_sections(tei: TeiXml) -> TeiSections:
    """The sections of a page that are marked in a TEI XML document: the
    elements that `POST .../sections` makes (`<fw>`, `<div type="toc">`,
    `<noteGrp>`, `<note>`), to show where they are in the text.

    The body is `{"xml": "<TEI ...>...</TEI>"}`, as for `POST /tei/text`, and
    the answer is 422 in the same cases. The response is `{"sections": [...]}`,
    in the order of the document, outer ones first. Each section has `start`
    and `end`, the characters of the text that `POST /tei/text` gives for the
    same XML (counted the way Python does), and `open` and `close`, its tags as
    they are written in the XML, e.g. `<note n="1">` and `</note>`. Nothing is
    stored.
    """
    try:
        return TeiSections(sections=tei_xml_sections(tei.xml))
    except InvalidTei as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@app.post("/tei/entities", response_model=TeiEntities)
def tei_entities(tei: TeiXml) -> TeiEntities:
    """The named entities that are marked in a TEI XML document: the elements
    that `POST .../entities` makes (`<persName>`, `<placeName>`, `<orgName>`,
    `<geogName>`, `<date>`, `<time>`, `<ref>`, `<bibl>`, `<cit>`), so that a page
    that is opened again shows what was confirmed before. Every such element
    counts, whoever put it there.

    The body is `{"xml": "<TEI ...>...</TEI>"}`, as for `POST /tei/text`, and
    the answer is 422 in the same cases. The response is `{"entities": [...]}`,
    in the order of the document. Each entity has the same fields as one that
    `POST /ner` finds: its `text`, its `label` (the name of the element) and its
    `start` and `end`, the characters of the text that `POST /tei/text` gives
    for the same XML (counted the way Python does). Nothing is stored.
    """
    try:
        return TeiEntities(entities=tei_xml_entities(tei.xml))
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
        logger.error("NER requested, but the model is not available: %s", exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@app.post("/spellcheck", response_model=SpellcheckResult)
def spellcheck(request: SpellcheckRequest) -> SpellcheckResult:
    """Check the spelling of a text, with phunspell and the Polish Hunspell
    dictionary (`pl_PL`).

    The body is `{"text": "..."}`: the plain text of a page. The response lists
    every word of it, in order, as `{"text", "start", "end", "correct"}`:
    `start` and `end` are character offsets in the submitted text, so
    `text[start:end]` is the word, and `correct` says whether it is spelled
    correctly. Only white space is not a token. A run of signs ("$", "(|}", ",") is a
    token too, and counts as correct, so that it can be corrected. A word is made of
    letters and digits, with a hyphen or apostrophe inside it; words with digits
    are not checked and count as correct. Listing all words rather than only the
    wrong ones means the backend alone decides what a word is.

    The body may also have an `edition_id`, the edition the text is from. The
    spellings that were made legal in it (`POST .../legal-spellings`) then count
    as correct: a legal "jey" is not reported as wrong, and nor are "Jey" and
    "JEY" (a sentence can start with it); a legal "Jey" does not make "jey"
    correct. An edition that is not stored just has none.

    The body may also have the `xml`, the TEI XML that `text` is the text of (what
    `POST /tei/text` gave for it): the words in a quotation in another language
    (`<quote xml:lang="la">`, see `POST .../sections`) are then not checked, and are
    reported as correct, as the dictionary is a Polish one. If `text` is not the
    text of the `xml` the response is 422.

    The body may also have the `continuation`, what follows the text: the first line
    of the next page. A word that is split over the two pages (the text ends with
    "wszechświa-", the continuation starts with "towem") is then judged as one
    word, and both parts get its verdict; only the words of `text` are returned.

    The dictionary is loaded when the backend starts (see `lifespan`). If it is
    not available the response is 503. The text may have up to 100,000
    characters, otherwise the response is 422. Nothing is stored.
    """
    try:
        legal = db.get_legal_spellings(request.edition_id) if request.edition_id else ()
        ignore: list[tuple[int, int]] = []
        if request.xml is not None:
            try:
                if tei_xml_to_text(request.xml) != request.text:
                    raise HTTPException(
                        status.HTTP_422_UNPROCESSABLE_CONTENT,
                        "The text is not the text of the XML",
                    )
                ignore = foreign_language_ranges(request.xml)
            except InvalidTei as exc:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)
                ) from exc
        return SpellcheckResult(
            words=check_words(request.text, legal, ignore, request.continuation)
        )
    except SpellcheckUnavailable as exc:
        logger.error(
            "Spellcheck requested, but the dictionary is not available: %s", exc
        )
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@app.post("/spellcheck/suggestions", response_model=Suggestions)
def spelling_suggestions(request: SuggestionRequest) -> Suggestions:
    """The best replacements for a misspelled word, as an ordinary spell check
    suggests them: closest first, at most three (Hunspell's suggestions for the
    Polish dictionary), so that the user can click one instead of typing the word.

    The body is `{"text": "...", "start": 4, "end": 9}`: the plain text of the page
    and the word in it, `text[start:end]`, with offsets counted in code points as
    `POST /spellcheck` gives them (and the optional `edition_id`, whose legal
    spellings count as correct). The response is `{"suggestions": ["kotka", ...]}`.

    **Only a word in the middle of its line is given any**: the first and the last
    word of a line may be the parts of a word that is split over two lines, and a
    suggestion for them would be mostly wrong. The line is what is between two line
    breaks. A word that is spelled correctly, or is legal in the edition, has none
    either; the list is then empty. Each suggestion is one word, different from the
    word. Nothing is stored.

    422 if the characters at the offsets are not one word, 503 if the dictionary is
    not available.
    """
    try:
        legal = db.get_legal_spellings(request.edition_id) if request.edition_id else ()
        return Suggestions(
            suggestions=suggest_spelling(
                request.text, request.start, request.end, legal
            )
        )
    except NotAWord as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except SpellcheckUnavailable as exc:
        logger.error(
            "Suggestions requested, but the dictionary is not available: %s", exc
        )
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@app.get(
    "/editions/{edition_id}/similar-misspellings", response_model=list[PageMatches]
)
def get_similar_misspellings(
    edition_id: UUID,
    incorrect_spelling: Annotated[str, Query(min_length=1, max_length=100)],
    corrected_spelling: Annotated[str, Query(min_length=1, max_length=100)],
) -> list[PageMatches]:
    """List the places in the edition that have the same OCR mistake as a
    correction that was made, so that the correction can be applied to the whole
    edition: `incorrect_spelling` was corrected to `corrected_spelling`.

    The mistake is the cluster of letters from the first one that differs to the
    end of the word: "jćj" corrected to "jéj" is "ćj" (the OCR reads the old Polish
    "é" as "ć"), and every token with that cluster in it is a match, "jćj" and
    also "samćj" or "bardzićj" (case does not matter). The letters before the
    mistake are not part of it. A cluster is at least two letters long.

    The response is a list with an item for each page of the edition that has
    matches, in the order of the page numbers: `{"page_number": 8, "tokens":
    [{"text": "jćj", "start": 383, "end": 386, "suggestion": "jéj", "sure": true},
    ...]}`.
    `start` and `end` are offsets in the text of the page that `POST /tei/text`
    gives (counted in code points, like the offsets of a correction), so
    text[start:end] is the token. The `suggestion` is the token with the mistake
    corrected in it (`samćj` gives `saméj`), which is what a correction of it
    (`POST .../corrections`, with `before` the token and `after` the suggestion)
    would be.
    A correction is only suggested for a word that is wrong: a token is listed only if
    the spell check flags it (as `POST /spellcheck` does, in the text of its page, with
    the legal spellings of the edition as correct). A token that is spelled correctly
    is never offered, however much it looks like the mistake ("zasiedliśmy" is a word,
    and is not "zasiedliémy"). `sure` is true if the `suggestion` is a correct word or
    a legal spelling of the edition; if it is not (a wrong word to another wrong one,
    as the dictionary is a modern one and does not know "saméj"), the match is
    doubtful and `sure` is false.
    The pages are searched in their corrected content, as the user sees them, and
    a page without matches, or whose XML cannot be read, is left out. Nothing is
    changed, and Polona is not called.

    404 if the edition is not stored. 422 if a spelling is empty or longer than
    100 characters, or the two are the same. 503 if the dictionary is not available.
    """
    if db.get_edition(edition_id) is None:
        logger.warning("Edition %s is not stored", edition_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Edition not found")
    try:
        found = db.get_similar_misspellings(
            edition_id, incorrect_spelling, corrected_spelling
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except SpellcheckUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    logger.info(
        "%s pages of edition %s have misspellings like %r",
        len(found),
        edition_id,
        incorrect_spelling,
    )
    return found


@app.get(
    "/editions/{edition_id}/pages/{number}/similar-page-numbers",
    response_model=SimilarPageNumbers,
)
def get_similar_page_numbers(
    edition_id: UUID, number: Annotated[int, Path(ge=0)]
) -> SimilarPageNumbers:
    """List the pages of the edition that have a printed page number like the one
    that is marked on page `number` (`<fw type="pageNum">`, see `POST
    .../sections`), so that it can be marked on all of them at once.

    The page number marked on the page is the example: its line must be the first
    or the last of the page and have nothing on it but the number (one to four
    digits) and dashes around it, single or double (`- 12 -`, `— 12 —`, `-12-`),
    or none. The pattern is that: the same line of another page, a number with
    dashes if the example has them and without if it has none. Pages that do not
    fit it, such as a table of contents, are not listed, and nor are those that
    have a page number marked already.

    The response is `{"example": {...}, "where": "first", "offset": 3,
    "candidates": [...]}`. Each candidate is `{"page_number": 12, "start": 0,
    "end": 1, "text": "9", "number": 9, "context": "...", "fits_sequence":
    true}`: `start` and `end` are offsets in the text of the page that `POST
    /tei/text` gives (counted in code points), so text[start:end] is `text`, what
    `POST .../sections` is to mark (`"section": "page_number"`); it is the number
    with its dashes if the example was marked with them, and the digits alone if
    not. `context` is the start of the line next to it. `fits_sequence` says
    whether the printed number is the number of the page less the same `offset` as
    in the example (a numbering counts one by one): those that do not fit are
    probably something else. The pages are in the order of their numbers.

    Nothing is changed, and Polona is not called. 404 if the page is not stored;
    422 if no page number is marked on it, or it is not alone on the first or the
    last line (with the reason).
    """
    try:
        found = db.get_similar_page_numbers(edition_id, number)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if found is None:
        logger.warning("Page %s of edition %s is not stored", number, edition_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found")
    logger.info(
        "%s pages of edition %s have a page number like the one on page %s",
        len(found.candidates),
        edition_id,
        number,
    )
    return found


@app.get(
    "/editions/{edition_id}/token-occurrences",
    response_model=list[PageTokenOccurrences],
)
def get_token_occurrences(
    edition_id: UUID, token: Annotated[str, Query(min_length=1, max_length=100)]
) -> list[PageTokenOccurrences]:
    """List every place of a token in the edition, exactly, so that it can be
    corrected everywhere (`POST .../corrections`, a place at a time). The OCR makes
    the same mistake again and again, and the spell check does not flag a mistake
    that is a word too: "tem" is read as "tern". The person sees it on the scan and
    gives the token as it is wrong.

    `token` is one token as the spell check makes tokens: a word (a hyphen or an
    apostrophe inside it is part of it) or a run of signs. The match is exact: whole
    tokens only, and the case of the letters counts, so "tern" is neither "Tern", nor
    in "terne" or "intern". It is found whether the spell check flags it or not.

    The response is a list with an item for each page that has the token, in the
    order of the page numbers: `{"page_number": 8, "occurrences": [{"start": 383,
    "end": 387, "text": "tern", "before": "o", "after": "dniu"}]}`. `start` and `end`
    are offsets in the text of the page that `POST /tei/text` gives (counted in code
    points), so text[start:end] is the token, and the correction of it is `POST
    .../corrections` with `before` the token. `before` and `after` are its context: the
    nearest word before it and the nearest word after it, with the signs between them
    and the token, as they are written (`"dniu."` and `"raz"`), so that a full stop or a
    comma next to it does not leave nothing to read; empty at the ends of the text,
    where there is no word. The pages are
    searched in their corrected content, as the user sees them; one without the token,
    or whose XML cannot be read, is left out. Nothing is changed, and Polona is not
    called.

    404 if the edition is not stored. 422 if the token is empty, longer than 100
    characters, has spaces at its ends or inside, or is more than one token (with the
    reason).
    """
    if db.get_edition(edition_id) is None:
        logger.warning("Edition %s is not stored", edition_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Edition not found")
    try:
        found = db.get_token_occurrences(edition_id, token)
    except InvalidToken as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    logger.info(
        "%s places of the token on %s pages of edition %s",
        sum(len(page.occurrences) for page in found),
        len(found),
        edition_id,
    )
    return found


@app.get(
    "/editions/{edition_id}/spelling-chunk-matches",
    response_model=list[SpellingGroup],
)
def get_spelling_chunk_matches(
    edition_id: UUID,
    core: Annotated[str, Query(min_length=1)],
    ending: Annotated[list[str] | None, Query()] = None,
) -> list[SpellingGroup]:
    """List the words of the edition that have a substring in them and are flagged by
    the spell check, so that they can be accepted as legal spellings (`POST
    .../legal-spellings`), all at once. Nothing is corrected: old print has
    spellings that repeat, "Konwencya" and "tendencyę" have a "y" where we write
    "j", and the dictionary is a modern one. We call them "legal", not "correct".

    A substring is a `core` (`cy`) and `ending`, given once for each ending (`a`, `i`,
    `ami`): what is looked for is the core with each ending, `cya`, `cyi` and
    `cyami`, anywhere in the word, whatever the case of the letters. An empty
    ending is the core alone. At most forty endings (the frontend offers sets of
    them: the endings of the Polish cases); the core and each ending are at
    most 30 letters (a hyphen or an apostrophe inside is fine).

    The response is a list with an item for each word, the ones that are in the
    edition most often first: `{"text": "Konwencya", "count": 7, "occurrences":
    [{"page_number": 8, "start": 383, "end": 392, "before": "w", "after": "z"}]}`.
    `occurrences` are the first places of the word (at most five, in the order of
    the pages) and `count` is how many there are in all. `start` and `end` are
    offsets in the text of the page that `POST /tei/text` gives (counted in code
    points), so text[start:end] is the word. `before` and `after` are its context:
    the nearest word before it and the nearest word after it, with the signs between
    them and the word, as they are written (`"dom."` and `", kota"`), so that a full
    stop or a comma next to it does not leave nothing to read. Only
    words that the spell check flags are listed, in the way that `POST /spellcheck`
    does (a word split over two lines is judged as one word, and the legal
    spellings of the edition count as correct), so a word that is accepted already
    is not. A word is listed with the case it has: "Konwencya" and "konwencya" are
    two. The pages are searched in their corrected content, as the user sees them;
    one whose XML cannot be read is left out. Nothing is changed, and Polona is not
    called.

    404 if the edition is not stored. 422 if the core is empty, there are no
    endings or more than ten, or a part is too long or is not letters (with the
    reason). 503 if the dictionary is not available.
    """
    if db.get_edition(edition_id) is None:
        logger.warning("Edition %s is not stored", edition_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Edition not found")
    try:
        substrings = chunk_substrings(core, ending or [])
    except InvalidChunk as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    try:
        found = db.get_chunk_matches(edition_id, substrings)
    except SpellcheckUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    logger.info(
        "%s flagged words of edition %s have %s",
        len(found),
        edition_id,
        ", ".join(substrings),
    )
    return found


@app.get(
    "/editions/{edition_id}/bulk-spelling-check", response_model=list[SpellingFamily]
)
def get_bulk_spelling_check(edition_id: UUID) -> list[SpellingFamily]:
    """List the spellings of the whole edition that the spell check flags and that
    are there often, so that they can be accepted as legal spellings (`POST
    .../legal-spellings`), all at once. Nothing is corrected: old print has spellings
    that repeat ("Konwencya"), and the dictionary is a modern one. One flagged once is
    likely an OCR mistake, one flagged again and again is a spelling of the print.

    Every page is checked in the way that `POST /spellcheck` does (a word split over
    two lines is judged as one word, and the legal spellings of the edition count as
    correct, so a word that is accepted already is not listed), except that **the
    first and the last word of every line are left out**, so the first and the last
    of every page too: a word that the end of a line cuts is flagged for nothing.
    Signs do not count, `— Ala ma kota.` has `Ala` first and `kota` last. A spelling
    is listed only if it is in the edition **more than three times** (those words
    that are looked at), and with the case it has: "Konwencya" and "konwencya" are
    two.

    The response is a list of families, the ones that are in the edition most often
    (all their spellings together) first: `{"stem": "konwency", "count": 11,
    "spellings": [{"text": "Konwencya", "count": 7, "occurrences": [{"page_number":
    8, "start": 383, "end": 392, "before": "w", "after": "z"}]}, ...]}`. A family is
    spellings that are one word with different endings: they begin with the same
    letters, at least four whatever the case, and have at most four letters after
    them ("Konwencya", "Konwencyi" and "Konwencyami" are one, "Konstytucya" is
    another). `stem` is the letters they all begin with, in lower case. The
    `spellings` of a family are the most common first, and are like the items of
    `GET .../spelling-chunk-matches`: `occurrences` are the first places of the
    spelling (at most five, in the order of the pages), `count` is how many there
    are in all, and `start` and `end` are offsets in the text of the page that
    `POST /tei/text` gives (counted in code points), so text[start:end] is the
    spelling. `before` and `after` are its context, as they are written. The
    pages are searched in their corrected content, as the user sees them; one
    whose XML cannot be read is left out. Nothing is changed, and Polona is not
    called.

    404 if the edition is not stored. 503 if the dictionary is not available.
    """
    if db.get_edition(edition_id) is None:
        logger.warning("Edition %s is not stored", edition_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Edition not found")
    try:
        found = db.get_bulk_spelling_check(edition_id)
    except SpellcheckUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    logger.info(
        "%s families of frequent flagged spellings in edition %s",
        len(found),
        edition_id,
    )
    return found


@app.get("/editions/{edition_id}/similar-entities", response_model=SimilarEntities)
def get_similar_entities(
    edition_id: UUID,
    text: Annotated[str, Query(min_length=1, max_length=100)],
    label: EntityLabel,
) -> SimilarEntities:
    """List the places in the edition that have the named entity `text` (marked
    as `label`) in any of its forms in the cases, so that it can be marked there
    too, in bulk. A Polish name is not always in the nominative: "Machnicki" is
    also "Machnickiego", "Machnickiemu", "Machnickim", the feminine "Machnicka",
    "Machnickiej", and "Kraków" is "Krakowa", "Krakowie".

    The forms are made by rules from the word (see `app/text_tools/inflection.py`):
    a surname in -ski/-cki, a masculine, feminine or neuter noun, a name in -ń, -ek
    or -ec, with the 19th-century "-éj" and "-ém"; `label` says which families
    to try (a place is not a surname in -ski). A name of several words ("Jan
    Machnicki", at most five) is made of the forms of its words in the same case,
    and found where they are next to each other (one space or one line break
    between them). A match is the whole tokens, never a part of a word.

    The response is `{"label": "persName", "forms": [{"text": "Machnicki", "cases":
    ["nom.sg"]}, ...], "pages": [{"page_number": 8, "matches": [{"start": 383,
    "end": 395, "text": "Machnickiego", "before": "panem", "after": "wczoraj",
    "sure": true}]}]}`. The `forms` are what was looked for (the first is `text`),
    so that a human can see them, and a match is only one of them. `start` and `end`
    are offsets in the text of the page that `POST /tei/text` gives (counted in code
    points), so text[start:end] is `text`, which is what `POST .../entities` is to
    mark, with `label`. `before` and `after` are its context: the nearest
    word before the match and the nearest word after it, with the signs between them
    and the match, as they are written (`"dom."` and `", kota"`), so that a full stop
    or a comma next to it does not leave nothing to read. A match is `sure` if the NER
    model also finds an entity there, with the same label: else it may be a word that
    only looks like the name. The pages are searched in their corrected content, as the
    user sees them, in the order of the page numbers; a page without matches, or
    whose XML cannot be read, is left out, and so is a place that is inside a marked
    entity already. Nothing is changed, and Polona is not called.

    404 if the edition is not stored. 422 if `text` is not a name (it has no
    letters, or is more than five words) or `label` is not an entity label. 503 if
    the NER model is not available.
    """
    if db.get_edition(edition_id) is None:
        logger.warning("Edition %s is not stored", edition_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Edition not found")
    try:
        found = db.get_similar_entities(edition_id, text, label)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except NerModelUnavailable as exc:
        logger.error("Similar entities requested, but the model is not available")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    logger.info(
        "%s pages of edition %s have forms of an entity (%s forms)",
        len(found.pages),
        edition_id,
        len(found.forms),
    )
    return found


@app.get("/editions/{edition_id}/rubbish-lines", response_model=list[PageRubbish])
def get_rubbish_lines(edition_id: UUID) -> list[PageRubbish]:
    """List the lines of the edition that are OCR rubbish, not text: the OCR reads
    a fleuron, a printer's ornament, or the print that shows through from the other
    side of the page, as letters and signs (`I *>• ' -V. ^oSkv. ;-'. i M , . ypton`).
    They can then be removed (`POST .../line-removals`), in bulk. Only whole lines
    are found, never a token in the middle of a good line.

    A line is a candidate when signs outnumber letters in it (the letters are less
    than 60 % of the letters and signs together; digits do not count). It is
    `sure` if no correct word of three letters or more is in it: the spellings that
    are legal in the edition count as correct. If there is one, the line may be text
    with a lot of punctuation, and a human has to say. Never found: a line
    shorter than three characters, a line of one and the same sign, `?` `.` `-` or
    `!`, several times (`...`, `---`), a line with digits and no letters (a page
    number), a Roman numeral (`II.`), and a line in a section that is marked (a
    header, a footer, a page number, a table of contents, a note).

    The response is a list with an item for each page that has such lines, in the
    order of the page numbers: `{"page_number": 18, "lines": [{"start": 14, "end":
    51, "text": "I *>• ...", "sure": true}]}`. `start` and `end` are offsets in the
    text of the page that `POST /tei/text` gives (counted in code points), so
    text[start:end] is the line. The pages are searched in their corrected content,
    as the user sees them; one without lines like that, or whose XML cannot be
    read, is left out. Nothing is changed, and Polona is not called.

    404 if the edition is not stored, 503 if the dictionary is not available.
    """
    if db.get_edition(edition_id) is None:
        logger.warning("Edition %s is not stored", edition_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Edition not found")
    try:
        found = db.get_rubbish_lines(edition_id)
    except SpellcheckUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    logger.info(
        "%s pages of edition %s have rubbish lines (%s lines)",
        len(found),
        edition_id,
        sum(len(page.lines) for page in found),
    )
    return found


@app.get(
    "/editions/{edition_id}/pages/{number}/missing-hyphens",
    response_model=list[MissingHyphen],
)
def get_missing_hyphens(
    edition_id: UUID, number: Annotated[int, Path(ge=0)]
) -> list[MissingHyphen]:
    """List the line ends of page `number` where the OCR dropped the hyphen of a
    split word ("przeczy" / "tać" for "przeczy-" / "tać"), so that they can be put
    back at once (`POST .../hyphens`). Only this page is looked at.

    A line end is listed if the last word of the line and the first word of the
    next one (each of two letters at least) make a correct word together. The OCR
    may also have read the hyphen as another sign ("przeczy," or "przeczy'"): up to
    two signs glued to the word are taken as the hyphen. A line that has its
    hyphen already, or is followed by an empty line, is not listed. The
    spellings that are legal in the edition count as correct.

    Each item is `{"start": 9, "end": 16, "text": "przeczy", "suggestion":
    "przeczy-", "next_word": "tać", "joined": "przeczytać", "ambiguous":
    false}`: `start` and `end` are
    offsets in the text that `POST /tei/text` gives (counted in code points), so
    text[start:end] is `text`, which is to become `suggestion`. `ambiguous` is true
    when both parts are correct words on their own ("do" / "mu" makes "domu"): it
    is not sure that the word is split, so a human has to say. The items are in
    the order of the text.

    Nothing is changed. 404 if the page is not stored, 422 if its XML cannot be
    read, 503 if the dictionary is not available. A page without text has none.
    """
    try:
        found = db.get_missing_hyphens(edition_id, number)
    except InvalidTei as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except SpellcheckUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    if found is None:
        logger.warning("Page %s of edition %s is not stored", number, edition_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found")
    logger.info("%s missing hyphens on page %s of %s", len(found), number, edition_id)
    return found


@app.post("/editions/{edition_id}/pages/{number}/hyphens", response_model=PageView)
def put_hyphens_back(
    edition_id: UUID, number: Annotated[int, Path(ge=0)], hyphens: HyphenFixes
) -> PageView:
    """Put the hyphens that the OCR dropped back at the line ends of a stored
    page, all at once (the ones that `GET .../missing-hyphens` found, and the user
    approved).

    The body is `{"fixes": [{"start": 9, "end": 16, "before": "przeczy",
    "after": "przeczy-"}]}`. The offsets are those of the text that `POST
    /tei/text` gives for this page; `before` is a word, perhaps with the sign
    that the OCR made of the hyphen, and `after` is the word and a hyphen. The
    fixes must not overlap. They are made from the end of the page to its start,
    so that none of them moves the offsets of the others: all the offsets are
    those of the page as the client saw it.

    The new XML and a Correction for each fix are saved in one transaction: all
    of it happens, or none of it. Nothing is made a legal spelling: a word with
    a hyphen at the end of a line is not a spelling of its own.

    404 if the page is not stored. 409 if the text at an offset is not `before`,
    because the page has changed since. 422 if the page has no valid TEI XML, a
    fix is not acceptable, or the fixes overlap. Returns the updated page as a
    PageView.
    """
    fixes = sorted(hyphens.fixes, key=lambda fix: fix.start, reverse=True)

    def put_back(xml: str) -> str:
        if not xml:
            raise InvalidTei("The page has no TEI XML to correct")
        for fix in fixes:
            xml = replace_text_range(xml, fix.start, fix.end, fix.before, fix.after)
        return xml

    try:
        page = db.apply_corrections(
            edition_id,
            number,
            put_back,
            [(fix.before, fix.after) for fix in reversed(fixes)],
        )
    except TextChanged as exc:
        logger.warning(
            "Hyphens not put back on page %s of %s: %s", number, edition_id, exc
        )
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (InvalidTei, CannotReplace) as exc:
        logger.warning(
            "Hyphens not put back on page %s of %s: %s", number, edition_id, exc
        )
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if page is None:
        logger.warning("Page %s of edition %s is not stored", number, edition_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found")
    logger.info("%s hyphens put back on page %s of %s", len(fixes), number, edition_id)
    return PageView.from_page(page)


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
    spaces or line breaks, and `after` must not start or end with a
    space, contain a line break, or be the same as `before`. The exception is
    an empty `after`, which removes the token (a `$` in the middle of the text);
    if it is between two spaces, one of them goes too, so that there are not two
    together. No Correction is recorded for it: removing a token is not
    correcting a word. Returns the updated page as a PageView.
    """

    def replace_in_xml(xml: str) -> str:
        if not xml:
            raise InvalidTei("The page has no TEI XML to correct")
        return replace_text_range(
            xml, correction.start, correction.end, correction.before, correction.after
        )

    try:
        page = db.apply_correction(
            edition_id,
            number,
            replace_in_xml,
            correction.before,
            correction.after,
            # removing a token is not a correction of a word: nothing is recorded
            record=correction.after != "",
        )
    except TextChanged as exc:
        logger.warning(
            "Word not corrected on page %s of %s: %s", number, edition_id, exc
        )
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (InvalidTei, CannotReplace) as exc:
        logger.warning(
            "Word not corrected on page %s of %s: %s", number, edition_id, exc
        )
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if page is None:
        logger.warning("Page %s of edition %s is not stored", number, edition_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found")
    logger.info(
        "%s on page %s of %s (%s-%s)",
        "Correction recorded" if correction.after else "Token removed",
        number,
        edition_id,
        correction.start,
        correction.end,
    )
    return PageView.from_page(page)


@app.post(
    "/editions/{edition_id}/pages/{number}/continued-words", response_model=PageView
)
def correct_continued_word(
    edition_id: UUID, number: Annotated[int, Path(ge=0)], word: ContinuedWord
) -> PageView:
    """Correct a word that goes on in the next line, as one word: the last word
    of a line (`first`) and the first word of the next one (`second`).

    The body is `{"first": {"start": 9, "end": 19, "before": "wszechsiia",
    "after": "wszechświa-"}, "second": {"start": 20, "end": 26, "before":
    "iowymi", "after": "towymi"}}`. The offsets are those of the text that `POST
    /tei/text` gives for this page. Only a line break may be between the two
    words. `first.after` ends with the hyphen that marks the split (one hyphen);
    `second.after` may be the same as `second.before`, if that part was right.

    Both words are replaced in the XML, and one Correction is recorded for the
    word as a whole, without the hyphen: `wszechsiiaiowymi` -> `wszechświatowymi`
    (none if that is the same), in one transaction: all of it happens, or none.
    Nothing is made a legal spelling here.

    404 if the page is not stored. 409 if the text at an offset is not `before`,
    because the page has changed since. 422 if the page has no valid TEI XML, the
    words are not on two lines of the text, or a replacement is not acceptable.
    Returns the updated page as a PageView.
    """
    first, second = word.first, word.second
    recorded = (first.before + second.before, first.after[:-1] + second.after)

    def replace_in_xml(xml: str) -> str:
        if not xml:
            raise InvalidTei("The page has no TEI XML to correct")
        text = tei_xml_to_text(xml)
        if text[first.end : second.start] != "\n":
            raise CannotReplace("The words are not on two lines, one after the other")
        # the second first: the offsets of the first do not move
        if second.after != second.before:
            xml = replace_text_range(
                xml, second.start, second.end, second.before, second.after
            )
        return replace_text_range(
            xml, first.start, first.end, first.before, first.after
        )

    try:
        page = db.apply_corrections(
            edition_id,
            number,
            replace_in_xml,
            [recorded] if recorded[0] != recorded[1] else [],
        )
    except TextChanged as exc:
        logger.warning(
            "Continued word not corrected on page %s of %s: %s", number, edition_id, exc
        )
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (InvalidTei, CannotReplace) as exc:
        logger.warning(
            "Continued word not corrected on page %s of %s: %s", number, edition_id, exc
        )
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if page is None:
        logger.warning("Page %s of edition %s is not stored", number, edition_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found")
    logger.info(
        "Continued word corrected on page %s of %s (%s-%s)",
        number,
        edition_id,
        first.start,
        second.end,
    )
    return PageView.from_page(page)


@app.post(
    "/editions/{edition_id}/pages/{number}/continued-words-on-next-page",
    response_model=PageView,
)
def correct_word_continued_on_next_page(
    edition_id: UUID,
    number: Annotated[int, Path(ge=0)],
    word: ContinuedWordOnNextPage,
) -> PageView:
    """Correct a word that goes on in the next page, as one word: the last word of
    page `number` (`first`) and the first word of the page after it (`second`).

    The body is like that of `.../continued-words`: `{"first": {"start": 9,
    "end": 19, "before": "wszechsiia", "after": "wszechświa-"}, "second":
    {"start": 0, "end": 6, "before": "iowymi", "after": "towymi"}}`. The offsets of
    `first` are those of the text of page `number`, those of `second` are those of
    the text of the next page (`POST /tei/text`). The first word must be the last
    thing on its page, and the second the first word of its page: only white
    space may be after the first, and before the second only a page number or
    signs (nothing with a letter). `first.after` ends with one hyphen;
    `second.after` may be the same as `second.before`.

    Both pages are changed, and one Correction is recorded, for page `number`, for
    the word as a whole without the hyphen (`wszechsiiaiowymi` ->
    `wszechświatowymi`; none if that is the same), in one transaction: all of it
    happens, or none. Nothing is made a legal spelling here.

    404 if one of the two pages is not stored. 409 if the text at an offset is not
    `before`. 422 if a page has no valid TEI XML, the words are not at the end and
    the start of the pages, or a replacement is not acceptable. Returns the updated
    page `number` as a PageView.
    """
    first, second = word.first, word.second
    recorded = (first.before + second.before, first.after[:-1] + second.after)

    def change_first(xml: str) -> str:
        if not xml:
            raise InvalidTei("The page has no TEI XML to correct")
        if tei_xml_to_text(xml)[first.end :].strip():
            raise CannotReplace("The word is not at the end of the page")
        return replace_text_range(
            xml, first.start, first.end, first.before, first.after
        )

    def change_second(xml: str) -> str:
        if not xml:
            raise InvalidTei("The next page has no TEI XML to correct")
        # (above it there may be a page number or a sign, but no word)
        if any(c.isalpha() for c in tei_xml_to_text(xml)[: second.start]):
            raise CannotReplace("The word is not at the start of the next page")
        if second.after == second.before:
            return xml
        return replace_text_range(
            xml, second.start, second.end, second.before, second.after
        )

    try:
        page = db.apply_correction_over_pages(
            edition_id,
            number,
            change_first,
            change_second,
            recorded if recorded[0] != recorded[1] else None,
        )
    except TextChanged as exc:
        logger.warning(
            "Word continued on the next page not corrected on page %s of %s: %s",
            number,
            edition_id,
            exc,
        )
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (InvalidTei, CannotReplace) as exc:
        logger.warning(
            "Word continued on the next page not corrected on page %s of %s: %s",
            number,
            edition_id,
            exc,
        )
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if page is None:
        logger.warning(
            "Page %s or the next one of edition %s is not stored", number, edition_id
        )
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found")
    logger.info(
        "Word continued on the next page corrected on page %s of %s", number, edition_id
    )
    return PageView.from_page(page)


@app.post("/editions/{edition_id}/pages/{number}/tokens", response_model=PageView)
def add_token(
    edition_id: UUID, number: Annotated[int, Path(ge=0)], token: NewToken
) -> PageView:
    """Add a text token that the OCR missed to a stored page: `text` is put at
    `position` of the page's text, in the page's TEI XML.

    The body is `{"position": 25, "text": "jest"}`. `position` is an offset
    between two characters of the text that `POST /tei/text` gives for this page
    (0 is before the first character, the length of the text is after the last).
    The backend finds the place in the XML and puts the text into the text node
    there, so the markup around it stays; the frontend never has to read the XML.
    The token is a token of its own: a space is put before it unless there is a
    space or a line break there, and after it unless the text goes on with one.
    It may be several words, like `LIST SZÓSTY`, separated by spaces (a run of
    spaces counts as one).

    The new XML is saved as the page's corrected content. No Correction is
    recorded: adding a token is not correcting a word. The text of the page
    changes, so the offsets of its entities and words from before are no longer
    valid.

    404 if the page is not stored. 422 if the page has no valid TEI XML, or the
    token is not acceptable: it must not be empty, start or end with a space, or
    contain line breaks, tabs or other control characters, and the position must
    be in the text, and not on the empty line between two paragraphs. Returns the
    updated page as a PageView.

    A page without any text (the OCR found nothing) takes the token as its first
    line: the only position there is 0.

    By default the token goes into an existing line. With `"new_line": true` it is
    put on a line of its own, above the line that starts at `position` (0 for a new
    first line of the page): the body is `{"position": 0, "text": "LIST SZÓSTY",
    "new_line": true}`. The position must then be the start of a line (0, or after a
    line break), else 422; the token and a line break are put before the first
    character of that line.
    """

    def insert_in_xml(xml: str) -> str:
        if not xml:
            raise InvalidTei("The page has no TEI XML to add a token to")
        insert = insert_line_at if token.new_line else insert_text_at
        return insert(xml, token.position, token.text)

    try:
        page = db.apply_correction(
            edition_id,
            number,
            insert_in_xml,
            "",
            token.text,
            record=False,
        )
    except (InvalidTei, CannotInsert) as exc:
        logger.warning("Token not added on page %s of %s: %s", number, edition_id, exc)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if page is None:
        logger.warning("Page %s of edition %s is not stored", number, edition_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found")
    logger.info(
        "Token added on page %s of %s at %s", number, edition_id, token.position
    )
    return PageView.from_page(page)


@app.post("/editions/{edition_id}/pages/{number}/joins", response_model=PageView)
def join_tokens(
    edition_id: UUID, number: Annotated[int, Path(ge=0)], join: TokenJoin
) -> PageView:
    """Join tokens of a stored page that the OCR split ("abso lutely") into one:
    the spaces between them, in the characters `start` to `end` of the page's
    text, are removed from the page's TEI XML.

    The body is `{"start": 25, "end": 36, "text": "abso lutely"}`. The offsets are
    those of the text that `POST /tei/text` gives for this page. `text` is the
    tokens as the client saw them, two or more, separated by single spaces; the
    backend checks that it is what is at those offsets. The spaces are removed
    where they are, so the markup around stays; the frontend never has to read
    the XML. Tokens on different lines are not joined.

    The new XML is saved as the page's corrected content. No Correction is
    recorded: joining is not correcting a word. The text of the page changes, so
    the offsets of its entities and words from before are no longer valid.

    404 if the page is not stored. 409 if the text at the offsets is not `text`,
    because the page has changed since. 422 if the page has no valid TEI XML, or
    `text` is not tokens separated by single spaces. Returns the updated page as
    a PageView.
    """

    def join_in_xml(xml: str) -> str:
        if not xml:
            raise InvalidTei("The page has no TEI XML to join tokens in")
        return join_text_range(xml, join.start, join.end, join.text)

    try:
        page = db.apply_correction(
            edition_id, number, join_in_xml, join.text, "", record=False
        )
    except TextChanged as exc:
        logger.warning(
            "Tokens not joined on page %s of %s: %s", number, edition_id, exc
        )
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (InvalidTei, CannotJoin) as exc:
        logger.warning(
            "Tokens not joined on page %s of %s: %s", number, edition_id, exc
        )
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if page is None:
        logger.warning("Page %s of edition %s is not stored", number, edition_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found")
    logger.info(
        "Tokens joined on page %s of %s (%s-%s)",
        number,
        edition_id,
        join.start,
        join.end,
    )
    return PageView.from_page(page)


@app.post("/editions/{edition_id}/pages/{number}/line-splits", response_model=PageView)
def split_line(
    edition_id: UUID, number: Annotated[int, Path(ge=0)], split: LineSplit
) -> PageView:
    """Start a new line in a stored page: the space between two tokens of the
    same line, in the characters `start` to `end` of the page's text, becomes a
    line break (`<lb/>`) in the page's TEI XML.

    The body is `{"start": 25, "end": 36, "text": "kota Ala"}`. The offsets are
    those of the text that `POST /tei/text` gives for this page. `text` is the
    two tokens as the client saw them, separated by one space; the backend checks
    that it is what is at those offsets. The whitespace goes and the `<lb/>` is
    put where it was, so the markup around stays (a line may be split inside a
    `<persName>`); the frontend never has to read the XML. If the token before
    the break ends with a hyphen the break is `<lb break="no"/>`.

    To put a token on a new line, add it to a neighbouring line (`POST .../tokens`)
    and split the line before it. A page without any text takes the token as its
    first line.

    The new XML is saved as the page's corrected content. No Correction is
    recorded: splitting a line is not correcting a word. The text of the page
    changes, so the offsets of its entities and words from before are no longer
    valid.

    404 if the page is not stored. 409 if the text at the offsets is not `text`,
    because the page has changed since. 422 if the page has no valid TEI XML, or
    `text` is not two tokens separated by one space. Returns the updated page as
    a PageView.
    """

    def split_in_xml(xml: str) -> str:
        if not xml:
            raise InvalidTei("The page has no TEI XML to split a line in")
        return split_line_at(xml, split.start, split.end, split.text)

    try:
        page = db.apply_correction(
            edition_id, number, split_in_xml, split.text, "", record=False
        )
    except TextChanged as exc:
        logger.warning("Line not split on page %s of %s: %s", number, edition_id, exc)
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (InvalidTei, CannotSplit) as exc:
        logger.warning("Line not split on page %s of %s: %s", number, edition_id, exc)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if page is None:
        logger.warning("Page %s of edition %s is not stored", number, edition_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found")
    logger.info(
        "Line split on page %s of %s (%s-%s)",
        number,
        edition_id,
        split.start,
        split.end,
    )
    return PageView.from_page(page)


@app.post("/editions/{edition_id}/pages/{number}/line-joins", response_model=PageView)
def join_lines(
    edition_id: UUID, number: Annotated[int, Path(ge=0)], join: LineJoin
) -> PageView:
    """Remove a line break of a stored page: the line break (`<lb/>`) between the
    last token of a line and the first token of the next one, in the characters
    `start` to `end` of the page's text, is replaced with a space in the page's
    TEI XML, so that two lines become one. It undoes `POST .../line-splits`. Or
    the empty line between two paragraphs: the two `<p>` become one, with a line
    break between them, so one more removal joins the lines.

    The body is `{"start": 25, "end": 31, "text": "kota\nA"}`, or `{"start": 0,
    "end": 10, "text": "315\n\nbłędy"}` for an empty line. The offsets are those
    of the text that `POST /tei/text` gives for this page. `text` is the two
    tokens as the client saw them, separated by one line break or by an empty
    line; the backend checks that it is what is at those offsets. The markup
    around stays; the frontend never has to read the XML. Only a plain `<lb/>`
    goes: not the end of an `<l>` or an `<item>`. Empty lines are only removed
    between two `<p>` next to each other.

    The new XML is saved as the page's corrected content. No Correction is
    recorded: joining lines is not correcting a word. The text of the page
    changes, so the offsets of its entities and words from before are no longer
    valid.

    404 if the page is not stored. 409 if the text at the offsets is not `text`,
    because the page has changed since. 422 if the page has no valid TEI XML,
    `text` is not two tokens separated by one line break or an empty line, or the
    break is not a plain `<lb/>`, or the paragraphs cannot be merged. Returns the
    updated page as a PageView.
    """

    def join_in_xml(xml: str) -> str:
        if not xml:
            raise InvalidTei("The page has no TEI XML to join lines in")
        return join_lines_at(xml, join.start, join.end, join.text)

    try:
        page = db.apply_correction(
            edition_id, number, join_in_xml, join.text, "", record=False
        )
    except TextChanged as exc:
        logger.warning("Lines not joined on page %s of %s: %s", number, edition_id, exc)
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (InvalidTei, CannotJoinLines) as exc:
        logger.warning("Lines not joined on page %s of %s: %s", number, edition_id, exc)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if page is None:
        logger.warning("Page %s of edition %s is not stored", number, edition_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found")
    logger.info(
        "Lines joined on page %s of %s (%s-%s)",
        number,
        edition_id,
        join.start,
        join.end,
    )
    return PageView.from_page(page)


@app.post(
    "/editions/{edition_id}/pages/{number}/line-removals", response_model=LineRemoved
)
def remove_line(
    edition_id: UUID, number: Annotated[int, Path(ge=0)], removal: LineRemoval
) -> LineRemoved:
    """Remove a whole line of a stored page, with the line break that goes with it:
    OCR rubbish that `GET .../rubbish-lines` found, and the user approved.

    The body is `{"start": 14, "end": 51, "text": "I *>• ..."}`: the characters
    `start` to `end` of the text that `POST /tei/text` gives for this page, which
    are `text`, a whole line (the text before it ends with a line break, and the
    text after it starts with one, or it is at an end of the text). The backend
    finds the line in the XML and removes it and its `<lb/>`: the one before it, or
    after it if it is the first line of its paragraph. A line that is all of its
    paragraph takes the paragraph with it (and so the empty line next to it). The
    line must be plain text of one paragraph: a line with an element in it is not
    removed.

    The new XML is saved as the page's corrected content. No Correction is
    recorded: removing a line is not correcting a word. The text of the page
    changes, so the offsets of what follows the line are no longer valid.

    The response is `{"page": {...}, "removed": 38}`: the updated page, and how
    many characters of its text went (the line and its line break or breaks), so
    that the offsets after it can be moved by that.

    404 if the page is not stored. 409 if the text at the offsets is not `text`,
    because the page has changed since. 422 if the page has no valid TEI XML, or
    the line cannot be removed (not a whole line, markup in it).
    """
    removed = 0

    def remove_in_xml(xml: str) -> str:
        nonlocal removed
        if not xml:
            raise InvalidTei("The page has no TEI XML to remove a line from")
        new_xml = remove_line_at(xml, removal.start, removal.end, removal.text)
        removed = len(tei_xml_to_text(xml)) - len(tei_xml_to_text(new_xml))
        return new_xml

    try:
        page = db.apply_correction(
            edition_id, number, remove_in_xml, removal.text, "", record=False
        )
    except TextChanged as exc:
        logger.warning("Line not removed on page %s of %s: %s", number, edition_id, exc)
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (InvalidTei, CannotRemoveLine) as exc:
        logger.warning("Line not removed on page %s of %s: %s", number, edition_id, exc)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if page is None:
        logger.warning("Page %s of edition %s is not stored", number, edition_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found")
    logger.info(
        "Line removed on page %s of %s (%s-%s)",
        number,
        edition_id,
        removal.start,
        removal.end,
    )
    return LineRemoved(page=PageView.from_page(page), removed=removed)


@app.post("/editions/{edition_id}/legal-spellings", response_model=LegalSpelling)
def add_legal_spelling(
    edition_id: UUID, new: NewLegalSpelling, response: Response
) -> LegalSpelling:
    """Say that a spelling is legal in an edition, although the dictionary does
    not know it: old print has spellings like "jey" and "tey", which are not
    mistakes there.

    We call it "legal", not "correct": the dictionary is a modern one, and what
    was correct in the 19th century often is not anymore. We have no better word.

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
    logger.info(
        "Legal spelling %s for edition %s",
        "added" if added else "already known",
        edition_id,
    )
    return legal


# After all the routes: it adds the page and size parameters to the paginated ones.
add_pagination(app)

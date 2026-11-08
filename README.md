Simple app to correct OCR outputs from polona.pl

To run everything, edit .env with db creds
 Then run `make run` in one terminal and `make frontend` in another.

## How it works

The app shows a scan of a page next to its text, so the OCR can be corrected.
Scans and OCR text come from [polona.pl](https://polona.pl); the app fetches
each page once and keeps it in PostgreSQL.

### Requesting a page

`GET /editions/{edition_id}/pages/{number}`

- `edition_id` is the **Polona object UUID** (the one in a polona.pl item-view
  URL).
- `number` is the page number, **0-based**, the same as Polona's `?page=`
  parameter (`number=99` is "Skan 100" in the Polona viewer).

The response is a `PageView`: `id`, `edition_id`, `number`, `scan_uri`,
`ready` and `content`. `content` is the corrected text if the page has one,
otherwise the raw OCR text.

### Where the page comes from

1. **The edition must be in the collection.** Only editions whose UUID is in
   the `collection` table can be opened; anything else gets a 404. This check
   comes first, so it also applies to pages that are already stored.
2. **The database is asked first.** If the page (edition + number) is already
   stored, it is returned from there and Polona is not contacted. Stored pages
   are never refreshed automatically.
3. **Otherwise the page is fetched from Polona** (`app/polona_connector.py`),
   saved, and returned. The next request for it is served from the database.

### What is fetched from Polona

For a page that is not stored yet, two things are fetched.

**The page itself** (`fetch_page`):

- The list of the edition's page scans is read from Polona's
  `library-object-query/digital-objects/{uuid}/contents` endpoint, and the
  entry for the requested page is picked.
- `scan_uri` is the full-resolution IIIF image URL of that page. The image is
  **not downloaded**: only the URL is kept, and the browser loads the image
  from Polona.
- The text is Polona's OCR layer (ALTO XML), converted to plain text in
  reading order. Lines on the same row are joined, and separate text blocks
  (main text, footnotes) are separated by a blank line. A page without an OCR
  layer gets an empty text.

**The edition's metadata** (`fetch_edition_metadata`), only when the edition
is not stored yet. A `Work` and an `Edition` are created, because every page
belongs to an edition:

- Polona's records are inconsistent, so every value is best effort and may be
  empty: author (many records only name a publisher), title, place of
  publication and year (1500–2030).
- The title is cleaned so that volumes of one work match: `Lalka. T. 2` becomes
  `Lalka`.
- Editions of the same work share one `Work`: an existing work with the same
  author and title (ignoring case) is reused. When the author or the title is
  unknown, a new work is created every time.

### Saving, and corrections

A fetched page is saved with `raw_content` (the OCR text from Polona) and
`scan_uri`. The OCR text is also converted to TEI XML (`app/tei_xml.py`:
paragraphs at blank lines, `<lb/>` at line breaks) and saved as
`corrected_content`. This is the text the user edits, so the original OCR text
in `raw_content` is never overwritten. Because every fetched page starts with
a `corrected_content`, `content` in the response is that TEI XML until a
correction replaces it. If a page is ever fetched again, only the fetched data
(`raw_content`, `scan_uri`) is refreshed, and its corrected content, `ready`
flag and `id` are kept.

### Editing a page

`PATCH /editions/{edition_id}/pages/{number}` with a JSON body.

Only two things can be edited: `corrected_content` and `ready`. Fields left out
are not touched, and `"corrected_content": null` removes the correction, so
`content` falls back to the raw text. Any other field in the body (for example
`raw_content` or `scan_uri`) is rejected with 422, and so is an empty body.

The page must already be stored: open it with `GET` first. `PATCH` never
fetches from Polona, and answers 404 if the page is not stored. It does not
check the collection. It returns the updated `PageView`.

### Getting the text out of TEI XML

`POST /tei/text` with `{"xml": "<TEI ...>...</TEI>"}` returns `{"text": "..."}`:
just the text of the document, without markup, and nothing is stored.

The XML is validated first, and a bad document gets a 422 whose `detail` says
what is wrong. It must be well-formed, have no `DOCTYPE`/`ENTITY` declarations,
and be a TEI document: a `TEI` root in the TEI namespace with a `teiHeader` and
a `text`. This checks that skeleton only; it is not validation against the
full TEI schema.

The text is the content of `text` (not the header). Blocks (`p`, `head`, ...)
are separated by a blank line and line breaks (`lb`, ...) become newlines, so a
page converted by `text_to_tei_xml` comes back as the same text. Indentation in
hand-edited XML does not matter.

### What the frontend shows

A page's `content` is TEI XML, but the frontend never shows it, and it never
reads it either. As soon as it gets a page, it sends the `content` back to
`POST /tei/text` and shows the text that comes back (in a read-only text box).
The same happens for every page it opens afterwards (previous/next page) and
after "Mark as ready".

**The backend is the only source of truth for whether the XML is correct.**
The frontend has no XML parsing or validation of its own: if the backend
accepts the XML, the text it returns is shown; if the backend rejects it, the
reason it gives (the `detail` of the 422) is shown in place of the text, and
the scan and the buttons stay usable. This way there is one definition of
"correct TEI" and one way to read text out of it, and the frontend can't
disagree with it.

A switch above the text box ("Text" / "XML") changes what is shown. "Text" is
the default; "XML" shows the page's `content`, the TEI XML itself, as it is
(also read-only), with the markup (tags, comments, the XML declaration) on a
grey background in a smaller font, so that the content stands out. This is
only highlighting: the frontend splits the text at `<...>` to style it and
checks nothing, so it still leaves the judging of the XML to the backend. Switching makes no request, and the chosen view is kept when
moving to another page. The XML view also works when the backend rejects the
XML, which is when it is most useful for seeing what is wrong.

A page stored before pages were converted to TEI holds plain text, not XML, so
the backend rejects it and the frontend shows that error instead of its text.

### When Polona fails

`app/polona_connector.py` turns every failure into one exception,
`PolonaError`, and `get_page` maps it to an HTTP error. Nothing is saved when
Polona fails.

| Situation | Response |
|---|---|
| Edition not in the collection | 404 `Edition not found` |
| Polona has no such edition or page (out of range, no scans, HTTP 404) | 404 `Page not found` |
| Polona is unreachable, times out, returns an HTTP error, or sends an invalid response | 502 |

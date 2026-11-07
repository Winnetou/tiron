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

A fetched page is saved with `raw_content` (the OCR text) and `scan_uri`.
Corrections are stored separately, as `corrected_content`, so the original OCR
text is never overwritten. If a page is ever fetched again, only the fetched
data is refreshed, and its corrected content, `ready` flag and `id` are kept.

### When Polona fails

`app/polona_connector.py` turns every failure into one exception,
`PolonaError`, and `get_page` maps it to an HTTP error. Nothing is saved when
Polona fails.

| Situation | Response |
|---|---|
| Edition not in the collection | 404 `Edition not found` |
| Polona has no such edition or page (out of range, no scans, HTTP 404) | 404 `Page not found` |
| Polona is unreachable, times out, returns an HTTP error, or sends an invalid response | 502 |

Simple app to correct OCR outputs from polona.pl

To run everything, edit .env with db creds
 Then run `make run` in one terminal and `make frontend` in another.

## Running with Docker Compose

`docker compose up --build` (or `make up`) starts the backend together with its
own PostgreSQL 17 database (`docker-compose.yml`):

- **`db`** keeps its data in the named volume `db-data`, so it survives
  `docker compose down`, rebuilding the image, and removing the containers. Only
  `docker compose down -v` (or `docker volume rm tiron_db-data`) deletes the data.
  The database port is not published: only the backend, inside the compose
  network, can reach it.
- **`backend`** is built from the `Dockerfile` and waits until the database is
  healthy before it starts. It reaches the database as `db`; `POSTGRES_HOST` and
  `POSTGRES_PORT` from `.env` are not used here (they are for `make run`). It is
  published on port 8000, or `BACKEND_PORT` if you set it in `.env`.
- The database name, user and password come from `.env` (`POSTGRES_DB`,
  `POSTGRES_USER`, `POSTGRES_PASSWORD`; see `.env.example`). The password has no
  default, and compose refuses to start without it. They are only used when the
  volume is created: changing the password later does not change it in an
  existing volume.
- The backend loads the spaCy model and the spelling dictionary before it
  answers, which takes about 40 s with a warm disk cache and a few minutes on a
  cold start, so `docker compose ps` shows it as "starting" for that long, then
  "healthy".
- The frontend is not in the compose file: run `make frontend` (its dev server
  proxies to the backend on port 8000).
- A new database is empty. Pages can only be opened for editions in the
  `collection` table, so add one first, for example:

  ```
  docker compose exec db psql -U <user> -d <db> -c \
    "insert into collection (id) values ('c10409b4-4ae3-4393-91b2-c36d132cf741')"
  ```

`make down` stops everything and keeps the data.

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

When the user corrects a word (see "Correcting a word" below), the word is
changed in `corrected_content`, and the correction (`before` -> `after`) is also
recorded in the `corrections` table, for the page.

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

### Named entity recognition

`POST /ner` with `{"text": "..."}` returns the named entities found in the
text: people, places, organisations, dates and so on. The text is the plain
text of a page, not XML (`POST /tei/text` turns TEI into text). Nothing is
stored.

```json
{"entities": [
  {"text": "Adam Mickiewicz", "label": "persName", "start": 0, "end": 15},
  {"text": "Paryżu", "label": "placeName", "start": 57, "end": 63}
]}
```

- Entities come in the order they appear in the text. `start` and `end` are
  character offsets into the submitted text, so `text[start:end]` is the
  entity's `text`. A text without entities gives an empty list.
- Recognition is done by [spaCy](https://spacy.io) with its large Polish
  model, `pl_core_news_lg`. The labels are the model's (for example
  `persName`, `placeName`, `orgName`, `geogName`, `date`).
- The model is loaded when the backend starts, so no request has to wait for
  it: the first one takes about as long as any other (a fraction of a second
  for a page). The price is the startup: the backend does not accept requests
  until the model is loaded, which takes about 20 s in the Docker image (the
  log says "Loading the spaCy model ..." and then "... is loaded"). Once loaded
  it uses about 1 GB of memory. The spelling dictionary (below) is loaded at
  startup as well, so the whole startup takes about 40 s.
- The model is installed in the Docker image. When running the backend
  without Docker it has to be installed separately. Without it the backend
  still starts (everything else works), logs a warning, and `/ner` answers 503;
  the model is tried again on the next request.
- The text can have up to 100,000 characters; longer text gets a 422.

### Marking an entity in the XML

`POST /editions/{edition_id}/pages/{number}/entities` puts a named entity in
the TEI XML of a stored page inside an element named after its label, and saves
the changed XML as the page's `corrected_content`. The body is what `POST /ner`
found:

```json
{"start": 0, "end": 15, "text": "Adam Mickiewicz", "label": "persName"}
```

so that `<p>Adam Mickiewicz mieszkał ...</p>` becomes
`<p><persName>Adam Mickiewicz</persName> mieszkał ...</p>`. The response is the
updated page, like `GET`. `label` is one of the six that the model produces,
which are also TEI elements: `persName`, `placeName`, `orgName`, `geogName`,
`date`, `time`.

- **The backend finds the place in the XML.** `start`/`end` are offsets into the
  text that `POST /tei/text` gives for this page, and the backend maps them to
  the XML, so the frontend never has to read it. `text` is the entity as the
  client saw it; if it is not what is at those offsets, the page has changed
  since the entities were recognized, and the answer is 409.
- **The text does not change**, so the offsets of the other entities of the
  page stay valid, and several can be marked one after the other without
  recognizing again. Marking an entity that is already marked does nothing.
- **How the XML is changed.** A text node is cut where the entity starts and
  ends, and the nodes in between move inside the new element. So an entity can
  span a line break (`<persName>Piotr<lb/>Tomicki</persName>`), and spaces or
  line breaks at its ends stay outside it. It cannot start or end in the middle
  of other markup (e.g. begin inside a `<hi>` and end outside it), span
  paragraphs, lines of verse or list items, or sit in an element that contains a
  comment or processing instruction (they cannot be moved with it); all of those
  answer 422 with the reason.
- 404 if the page is not stored, 422 if it has no valid TEI XML. Like `PATCH`,
  it does not check the collection or fetch from Polona.

### Spell check

`POST /spellcheck` with `{"text": "..."}` checks the spelling of a text, with
[phunspell](https://pypi.org/project/phunspell/) and its Polish Hunspell
dictionary (`pl_PL`), and returns **every word** of the text, in order:

```json
{"words": [
  {"text": "Ala", "start": 0, "end": 3, "correct": true},
  {"text": "jey", "start": 7, "end": 10, "correct": false}
]}
```

`start` and `end` are character offsets (counted the way Python does), so
`text[start:end]` is the word. The text is the plain text of a page, as
`POST /tei/text` gives it. Nothing is stored.

The body may also have an `edition_id`, the edition the text is from. The
spellings that were made **legal** in that edition (see "Legal spellings") then
count as correct. A legal `jey` makes `Jey` and `JEY` correct too (a sentence can
start with it), as the dictionary does for its own words, but a legal `Jey` does
not make `jey` correct. An edition that is not stored just has none. The
frontend sends the edition with every check.

- **The backend decides what a word is**, which is why all words are listed and
  not only the wrong ones: the frontend does not tokenize anything. A word is
  made of letters and digits, with a single hyphen or apostrophe inside it
  (`biało-czerwony`). A run of signs (`$`, `(|}`, a comma), alone or glued to a word, is a token
  too: it is never wrong, but it can be clicked and corrected like a word. Only
  white space is not a token. A hyphen at the end of a line is not part of a
  word, so the two halves of a word split over two lines (`o-` / `wych`) are two
  words.
- A word with digits in it is not checked and counts as correct. A hyphenated
  word is correct if it is in the dictionary, or if all its parts are. Capital
  letters do not matter.
- There are no suggestions: phunspell takes about 2.6 s to suggest for one
  word, which is too slow to do for all the wrong words of a page. (A page of
  about 200 words is checked in about 0.3 s.)
- The dictionary is loaded when the backend starts, like the spaCy model. If it
  is not available the backend still starts, logs a warning, and the endpoint
  answers 503. The text can have up to 100,000 characters (422 if longer).

### Correcting a word

`POST /editions/{edition_id}/pages/{number}/corrections` replaces a word of the
text of a stored page with another one, in the page's TEI XML, and records the
correction:

```json
{"start": 25, "end": 37, "before": "dawnieyszych", "after": "dawniejszych"}
```

- **The backend finds the place in the XML.** The offsets are those of the text
  that `POST /tei/text` gives for the page (and `POST /spellcheck` returned).
  The text is changed where it is, in the text nodes that hold it, so the markup
  around it stays: a word inside `<persName>...</persName>` is still inside it.
  A word that inline markup cuts in two is replaced in its first part and
  removed from the others. `before` is the word as the client saw it; if it is
  not what is at those offsets, the page has changed since, and the answer is
  409.
- **The correction is recorded.** The new XML is saved as the page's
  `corrected_content`, and the `Correction` (`before`, `after`) is stored for the
  page, in **one transaction with the page's row locked**: both happen or
  neither does, and two corrections of the same page cannot overwrite each
  other. The response is the updated page, like `GET`.
- `before` must not contain spaces or line breaks (it is a word), and `after`
  must not be empty, start or end with a space, contain a line break or other
  control characters, or be the same as `before`. It may contain spaces inside
  (`wcałey` -> `w całej`). 422 otherwise, and for a page without valid TEI XML.
  404 if the page is not stored. Like `PATCH`, it does not check the collection
  or fetch from Polona.
- The text of the page changes, so the offsets of its entities and words from
  before are no longer valid: check the spelling again (the frontend does).
- This replaces the earlier `POST /corrections` stub (which returned 501).

### Legal spellings

Old print has spellings that the dictionary does not know and that are not
mistakes there (`jey`, `tey`, `raczey`). A **legal spelling** says so, for one
edition. They are kept in the table `legal_spellings`, with two columns: the
`edition_id` (an edition that is stored) and the `spelling`. The two are unique
together (they are the table's primary key), so an edition has each spelling
once, and the same spelling can be legal in many editions.

`POST /editions/{edition_id}/legal-spellings` adds one:

```json
{"spelling": "jey"}
```

The response is `{"edition_id": "...", "spelling": "jey"}`, with status **201**
if it was added and **200** if the edition already had that spelling, so sending
it again is harmless (`Database.add_legal_spelling` returns whether it was
added). Nothing else is changed: this only records the spelling.

- A spelling is one word: not empty, at most 100 characters, and without spaces,
  line breaks or control characters (422 otherwise). It is kept as it is, and
  case matters: `Jey` and `jey` are two spellings.
- 404 if the edition is not stored, which it is once one of its pages has been
  opened. Like the other editing endpoints it does not check the collection or
  call Polona.
- The table is created when the backend starts, like the others, so an existing
  database gets it on the next start.
- `POST /spellcheck` uses them: given the `edition_id`, a legal spelling is
  reported as correct (`Database.get_legal_spellings` reads them). That is what
  keeps a word that was made legal from being red again on the next page of the
  edition, or after a reload. Nothing lists them yet.

### The sections marked in a page

`POST /tei/sections` takes `{"xml": "..."}` like `POST /tei/text` (422 in the same
cases) and lists the sections that are marked in it: the elements that
`POST .../sections` makes (`<fw>`, `<div type="toc">`, `<noteGrp>`, `<note>`),
outer ones first. Each has `start` and `end` (offsets into the text that
`/tei/text` gives, counted the way Python does) and `open` and `close`, its tags
as written in the XML, e.g. `<note n="1">` and `</note>`. Entities and the plain
`<div>`s that follow a table of contents are not sections. Nothing is stored.

### Marking the sections of a page

`POST /editions/{edition_id}/pages/{number}/sections` marks a stretch of the
text of a stored page as a section of the page, in its TEI XML, and saves the
changed XML as the page's `corrected_content`:

```json
{"start": 0, "end": 10, "text": "84 KATEDRA", "section": "header"}
```

`start`/`end` are offsets into the text that `POST /tei/text` gives for the page
(counted the way Python does), and `text` is the selection as the client saw it:
if it is not what is at those offsets the page has changed, and the answer is
409. The response is the updated page, like `GET`. The `section` is one of:

| `section`     | In the XML                          | What it is                      |
|---------------|-------------------------------------|---------------------------------|
| `header`      | `<fw type="header">…</fw>`          | the running head of the page    |
| `footer`      | `<fw type="footer">…</fw>`          | the foot of the page            |
| `page_number` | `<fw type="pageNum">…</fw>`         | the printed page number         |
| `toc`         | `<div type="toc"><p>…</p></div>`    | the table of contents           |
| `note`        | `<note>…</note>`, `<note n="1">…</note>` | one note (a footnote, say) |
| `note_group`  | `<noteGrp><note>…</note>…</noteGrp>` | a group of notes               |

`fw` (forme work) is the TEI element for the furniture of a printed page.

- **`header`, `footer` and `page_number` go around the text itself**, the way an
  entity does (see "Marking an entity in the XML"), so the text does not change.
  The same limits apply: they can span a line break, but cannot span paragraphs
  or start or end inside other markup, and marking the same thing again changes
  nothing. A page number can be inside a header (`<fw type="header"><fw
  type="pageNum">84</fw> KATEDRA</fw>`).
- **`toc` goes around whole paragraphs.** TEI does not allow a `<div>` to hold
  bare text or to sit inside a paragraph, so the selection cannot just be wrapped
  in one. Instead a paragraph that the selection starts or ends in the middle of
  is split there (the `<lb/>` that starts a line goes with that line), the
  paragraphs of the selection are put in `<div type="toc">`, and, because TEI
  allows nothing but `<div>`s after a `<div>`, the paragraphs that follow it are
  each put in a plain `<div>`. Paragraphs before it stay as they are. The text
  has a blank line where a paragraph was split, so for `toc` the offsets of the
  page's words, entities and so on are no longer valid. The selection must start
  and end in `<p>` paragraphs that are in the same division of the page, not
  inside other markup, and a paragraph that has to be split must not contain a
  comment or processing instruction (they cannot be moved).
- **`note` goes around the text itself**, like the `<fw>` elements. The body can
  have an optional `n`, the number of the note (`{"…": "…", "section": "note",
  "n": "1"}`, at most 20 characters, trimmed, not empty); any other section with
  an `n` is a 422.
- **`note_group` goes around notes that are already marked.** TEI lets a
  `<noteGrp>` hold only notes (no text, not even an `<lb/>`), so mark each note
  first, then select all of them, and nothing else: anything else in the
  selection is a 422 ("A note group holds only notes…"). A line break between the
  notes cannot stay an `<lb/>` inside the group, so it becomes a newline in the
  XML, which the text extraction reads as a line break again: the text of the
  page does not change.
- The documents this produces were checked against the official TEI schema
  (`tei_all`), including random pages with several marks one after the other.
- 404 if the page is not stored, 422 if it has no valid TEI XML or the section
  cannot be marked (with the reason). Like `PATCH`, it does not check the
  collection or fetch from Polona.

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

The "Named entity recognition" button in the sidebar sends the text on the
screen (the plain text, not the XML) to `POST /ner`, and the entities the
backend finds are highlighted in red in the text view, with how many were found
next to the switch. The highlighting follows the `start`/`end` offsets from the
backend rather than searching for the words, so a word is only marked where the
model found an entity, not everywhere it appears. The offsets count characters
the way Python does, which the frontend converts for strings with emoji and the
like. An entity that doesn't fit the text is skipped, not guessed at. Each click
sends a new request, and clicking the button while the XML is displayed switches
to the text. The first click is as fast as any other, because the backend loads
its model when it starts; the button pulses while a request is running.

**Confirming or rejecting an entity.** When the mouse is over a red entity (or
it has the keyboard focus), a small dialog appears next to it. It lists every
label the model can give (`persName`, `placeName`, `orgName`, `geogName`,
`date`, `time`) and "Not NER". The label the model recognized is already
selected; if the recognition is wrong, choose the right label instead, or
"Not NER". Click **Confirm** to apply the choice; Escape closes the dialog.

- With a label, the frontend asks the backend to mark the entity in the XML
  with that label (`POST .../entities`, above), whether or not it is the one
  the model gave. It does not touch the XML itself: it sends the entity's
  offsets and the chosen label, and shows what the backend returns. The entity
  turns green, because it is now in the XML as `<persName>...</persName>` (see
  it in the XML view; hovering the green entity shows the label it was marked
  with), and has no dialog any more. The text stays the same.
- With "Not NER", the highlight is removed and the count goes down. The XML is
  not changed and no request is made. This is not saved: recognizing again for
  the same page in the same session remembers it, but after reloading the
  entity is found again.
- While a request is running nothing else can be confirmed, and a failure
  (for example the 409 when the page has changed) is shown above the text, with
  the entity still there to be confirmed again.
- What was found and decided belongs to a text: it is not shown for another
  page, and is back if you return to the page, until you reload.

**Spelling.** As soon as the text of a page is shown, the frontend sends it to
`POST /spellcheck`, with no click, and every word it gets back can be clicked.
The misspelled ones are in red with a wavy underline, and how many there are is
shown next to the switch ("41 misspelled"). The frontend does not decide what a
word is or whether it is right: it shows what the backend says. If the spelling
cannot be checked (for example the dictionary is missing) the text is shown as
it is, with the reason above it.

**Correcting a word.** Click any word, wrong or not, and it turns into a text
field in place, with the word selected. Type the new word and press **Enter**
to confirm; **Escape**, or clicking elsewhere, cancels. Nothing is sent if the
word is unchanged or empty, and spaces around it are removed. The frontend asks
the backend to correct the word (`POST .../corrections`, above), sending the
word's offsets and the new text, and never changes the XML itself. When the
backend answers, the page is shown with its new text, and the spelling is
checked again. While the request runs the field is disabled; if it fails, the
reason is shown and the field is kept so it can be tried again. The misspelled
words can also be reached with the Tab key, and Enter on one starts editing.

**Legal spellings.** A misspelled (red) word may be a spelling that is right in
this edition, as old print has many. Click the word, and next to its field there
is a **Legal spelling** button (only for misspelled words). The word has two ways
out of red:

1. **The button.** It says that the word, as it is in the text, is legal: the
   frontend sends it to `POST /editions/{id}/legal-spellings` and checks the
   text again, and the word is no longer red, in this page and in any other page
   of the edition (the backend counts it as correct when it checks). The text
   does not change, and no correction is sent.
2. **Editing it and pressing Enter.** This is the correction as before (the word
   is replaced in the XML and the correction recorded), and then the new spelling
   is also sent to legal-spellings, so it is not red afterwards even if the
   dictionary does not know it either. The text is checked once, after both
   requests. Only a misspelled word gets this: editing a word that was spelled
   correctly is just a correction. And an edit into several words (`w całej`)
   sends no legal spelling, because a spelling is one word. If the correction
   works but the legal spelling does not, the corrected word is shown, with a
   message about what failed.

Clicking the button does not leave the field first (that would cancel the
editing). While a request is running both the field and the button are disabled.
Either way, the frontend does not decide what is red: after the request it asks
the backend again, and shows what the backend says.

The words and the entities are shown together: a word inside an entity is
still a word (it is red if misspelled and can be corrected in place, without
the entity's dialog getting in the way), and an entity that starts or ends in
the middle of a word cuts it in two, and either part edits the whole word. A
correction changes the text, so the entities recognized in the old text are not
shown for the new one; click the NER button again.

**The structure view.** The **Structure** button (the first in the sidebar) turns
a third view on, next to Text and XML: the text of the page, in which the sections
of the page can be marked. It is a toggle (it is dark while it is on; press it
again to go back to the text), and the Text, XML and named entity recognition
buttons also leave it. In this view, **select some text with the mouse** (or
Shift and the arrows) and a small dialog appears right next to the end of the
selection, with a **dropdown** of the sections (Page header, Page footer, Page
number, Table of contents, Note group, Note) and a **Confirm** button. For a
Note, a small field for its **number** (optional) appears next to the dropdown;
Enter in it confirms. Nothing is chosen at first,
and Confirm waits until a section is. Escape, or a click that clears the
selection, closes the dialog.

**The marked sections are shown in the text.** The structure view asks
`POST /tei/sections` (above) for the XML of the page and shows what is marked
with its tags in blue, before and after it (`<fw type="header">84 KATEDRA</fw>`,
`<noteGrp>` around its `<note n="1">`s, ...), nested where the XML is. The tags
are drawn by the stylesheet (generated content), so they are not text: they
cannot be selected or edited, and a selection's offsets are still those of the
text alone. The frontend does not read the XML; the backend says where the
sections are, and they are asked for again after each marking. If that fails the
view just shows the text.

On Confirm the frontend sends the selection to `POST .../sections` (above): the
offsets of the selected text (without the spaces and line breaks at its ends,
and counted the way the backend counts, which the frontend converts for text with
emoji and the like), the text, and the section. The backend puts the text inside
the right element in the XML; the frontend never touches the XML. The page is then
shown again with the text the backend returns, which for a table of contents has
a blank line where a paragraph was split, the view stays on the structure view,
and a short note says what was marked ("Marked as page header."). While the
request runs nothing else can be confirmed, and if the backend refuses (for
example a header cannot span paragraphs) its reason is shown and the text stays
as it was. The result is visible in the XML view. The Correction button is still a
stub.

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

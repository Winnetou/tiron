Simple app to correct OCR outputs from polona.pl

To run everything, edit .env with db creds, then run `make up`: the backend, the
database and the frontend start together, and the frontend is at
http://localhost:5173. (Or run `make run` in one terminal and `make frontend` in
another, to run the backend alone in a container and the frontend on the host.)

## Tests on GitHub

`.github/workflows/tests.yml` runs on every pull request to `main` (and on every
push to it):

- **Backend tests**: `uv sync --locked`, `ruff check`, `ruff format --check`, and
  `pytest` with a PostgreSQL 17 service, so the tests of the database layer run
  too (they empty the tables of that throwaway database, so never set
  `INTEGRATION_DB=1` against a database that has data).
- **Frontend tests**: Node 22, `npm ci`, `tsc -b`, `oxlint` and `vitest`.

A failing run only blocks a merge if the checks are required: in the repository's
Settings, Branches, protect `main` and require the status checks **Backend tests**
and **Frontend tests** (and that branches are up to date before merging).

### Dependabot

`.github/dependabot.yml` makes Dependabot look for new versions once a week, on Monday,
and open a pull request for them. It watches seven places: the backend (`pyproject.toml`
and `uv.lock`, the `uv` ecosystem), the frontend (`frontend/package.json` and its lock
file), the actions of the workflows, the base images of the two Dockerfiles, the images of
`docker-compose.yml`, and the hooks of `.pre-commit-config.yaml`. The minor and patch
updates of the backend, of the frontend and of the actions are each in one pull request
(a group); a new major version, which may need a change in the code, is in a pull request
of its own, and so is every update of an image or a hook.

These pull requests go through the workflow "Tests" like any other, so an update that
breaks something is a red check. Merge them like the others, once the checks are green
(and look at the changelog of a major version first). If you require the checks of the
workflow in the branch protection (see above), Dependabot's pull requests need them too.
Dependabot's own page is in the repository's Insights, Dependency graph, Dependabot: it
shows the last checks, and tells if `dependabot.yml` has a mistake.

## Running with Docker Compose

`docker compose up --build` (or `make up`) starts the backend together with its
own PostgreSQL 17 database, the frontend, and a RabbitMQ broker with a Celery
worker (`docker-compose.yml`):

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
- **`frontend`** is built from `frontend/Dockerfile` (Node 22) and runs the Vite
  dev server on port 5173, published on 5173 or `FRONTEND_PORT` if you set it in
  `.env`. The dev server proxies the backend's routes to `http://backend:8000`
  inside the compose network (`BACKEND_URL`), and an address a browser asks for
  as a page (`/editions/{uuid}/pages/{n}`) gets the app. `frontend/` is mounted
  into the container, so an edit of the sources shows in the browser at once
  (the container's own `node_modules` are kept: the dependencies are installed
  in the image, so after changing `package.json` run `docker compose up --build`).
  It only waits for the backend to be started, not healthy, so for the first
  minutes the main page shows an error until the backend has loaded its models.
  `make frontend` still runs the dev server on the host instead.
- **`rabbitmq`** (RabbitMQ 4) is the message broker for the Celery worker. It keeps
  its data in the named volume `rabbitmq-data` (and has a fixed hostname, because
  RabbitMQ stores it under the node name), and waits for nobody. Its port is not
  published: only the containers of the compose network reach it, as
  `amqp://<user>:<password>@rabbitmq:5672//`. The user and password are
  `RABBITMQ_USER` and `RABBITMQ_PASSWORD` from `.env`, `tiron` and `tiron` if not
  set.
- **`celery-worker`** is built from the same `Dockerfile` as the backend (the tasks
  are part of the app) and runs `celery -A app.celery_app worker`. It waits until
  RabbitMQ and the database are healthy, gets the broker address as
  `CELERY_BROKER_URL` and reaches the database like the backend does. `app/` is
  mounted, so restart the container after editing the tasks
  (`docker compose restart celery-worker`).
- A new database is empty. Pages can only be opened for editions in the
  `collection` table, so add one first, for example:

  ```
  docker compose exec db psql -U <user> -d <db> -c \
    "insert into collection (id) values ('c10409b4-4ae3-4393-91b2-c36d132cf741')"
  ```

`make down` stops everything and keeps the data.

## Production, on a Compute Engine VM

`docker-compose.prod.yml` runs the backend, the Celery worker, the RabbitMQ broker
and Caddy. **There is no database in it**: it is PostgreSQL in Google Cloud (Cloud
SQL, on a private IP in the VM's VPC), reached through `POSTGRES_HOST`.

```
cp .env.prod.example .env.prod      # fill it in; it is not committed (chmod 600)
make prod-up                        # docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
make prod-config                    # what compose makes of it; fails if a setting is missing
make prod-down
```

What differs from `docker-compose.yml`, which is for development:

- **No `db`, and no bind mounts**: the code is in the image, and the database is
  Cloud SQL (`POSTGRES_HOST`, `POSTGRES_DB`, `POSTGRES_USER` and `POSTGRES_PASSWORD`
  have no defaults, and compose refuses to start without them).
- **`PROD` is `true` and is not read from the environment** (it is fixed in the file).
  The backend does not create the schema, since the `tiron` user may not change it,
  and it **does not start if the tables are missing** (`SchemaMissing`). Create them
  by hand first, with an administrator, from `app/persistence/schema.py` (the `SCHEMA`
  string is plain SQL), and give `tiron` `SELECT, INSERT, UPDATE, DELETE` on them.
- **The frontend is built and served by Caddy** (`frontend/Dockerfile.prod`,
  `frontend/Caddyfile`), not by the Vite dev server. Caddy does what the dev server's
  proxy does: it serves the files, and passes `/editions`, `/auth`, `/tei`,
  `/ner`, `/spellcheck` and `/health` to the backend. Under `/editions`, a request that asks
  for HTML (a browser opening the address of a page) gets the app, and any other gets
  the API. Any other address is the app, except a missing file under `/assets/`, which
  is a 404. The backend's own docs (`/openapi.json`) are not published. Caddy looks the
  backend up when a request comes, so it starts even while the backend is still loading
  its models (a few minutes); until then the API answers 502. A request body over 5 MB
  is refused.
- **HTTPS is Caddy's**, and it is set by `SITE_ADDRESS`. With a domain
  (`SITE_ADDRESS=tiron.example.com` in `.env.prod`) Caddy gets a Let's Encrypt
  certificate for it by itself when it starts, renews it before it runs out (there is
  no certbot and no cron job), and sends HTTP to HTTPS (HTTP/3 too). For that the
  domain must have an A record pointing at the VM's static IP, and the ports 80 and
  443 must be open to the internet in the VPC firewall: the certificate is asked for
  over them. The certificates are kept in the `caddy-data` volume, so keep it: a new
  volume asks for new certificates, and Let's Encrypt limits how many it gives. Without
  `SITE_ADDRESS` (the default, `:80`) it is plain HTTP with no certificate, for
  before there is a domain. Tried locally with a name that Caddy makes its own
  certificate for (`SITE_ADDRESS=localhost`): HTTP is redirected (308) and HTTPS
  answers. Let's Encrypt itself cannot be tried without a real domain.
- **Only Caddy is published**, on the ports 80 and 443: the backend, the broker and
  their ports are inside the compose network.
- **`SESSION_SECRET` has no default** and the backend does not start without it: it
  signs the login sessions (see "Login"). Make it with `openssl rand -hex 32`. The
  `users` table is part of the schema that is created by hand in Cloud SQL, and the
  users are added to it by hand (the script is in "Login").
- **Passwords have no defaults** (`RABBITMQ_PASSWORD` too), `restart: always`, and
  the logs of each container are limited to 5 files of 10 MB, so that they do not
  fill the disk.
- The backend's health check is `GET /health`, which asks the database `SELECT 1`:
  the container is unhealthy while the database cannot be reached.

TLS to the database is optional (`POSTGRES_SSLMODE`, `POSTGRES_SSLROOTCERT`, see
"The database connection"). **The app has no login**: as it is, anyone who can reach
the port can read and edit everything, and HTTPS does not change that (it only
encrypts). Restrict it (the firewall to your address, Identity-Aware Proxy, or a login
in front of the app) before you rely on it.

## How it works

The app shows a scan of a page next to its text, so the OCR can be corrected.
Scans and OCR text come from [polona.pl](https://polona.pl); the app fetches
each page once and keeps it in PostgreSQL.

### The main page: the editions of the collection

`GET /editions` lists the editions in the `collection` table, each with its id
(a Polona object UUID) and the author and title of its work, 100 on a page. It is paginated with
[fastapi-pagination](https://uriyyo-fastapi-pagination.netlify.app/):

- `?page=` is the page, from 1 (the default);
- `?size=` is how many editions are on a page: 100 by default, and 100 at most (more
  is 422).

The response is `{"items": [{"id": "<uuid>", "author": "Kraszewski", "title":
"Stara baśń"}, ...], "total": 187597, "page": 1, "size": 100, "pages": 1876}`.
The `collection` table is joined with `editions` and `works` on the edition id: an
edition that is not stored yet, or whose work has no author or no title, has
`null` for what is missing. The editions are in the order of the ids, so a page is
the same on every request. A page past the last one has no items. Each is an
edition whose first page is at `GET /editions/{id}/pages/0`. The main page links
each one by what is known of its book: "Kraszewski J.; Stara baśń", the author as
the last name and the first letter of the first name (the catalogue writes "Last
name, First names (years)"), `NN` if the author is not known, and the title cut to
its first five words with `...` after them if it is longer (`Bez tytułu` if the
title is not known). An edition whose book is not known at all, neither author nor
title, is linked by its id. (This is done by the frontend, in
`editionLabel.ts`; the endpoint gives the author and title as they are stored.)

### Editing the metadata of an edition

The metadata that is fetched from Polona is not always complete, so it can be
edited. It is of two kinds: the **work** (the book) has an `author` and a `title`,
and the **edition** has a `place` and a `year`. `GET /editions/{edition_id}/metadata`
gives both:

```json
{"id": "<uuid>", "work_id": "<uuid>", "work_editions": 2,
 "author": "Prus, Bolesław (1847-1912)", "title": "Lalka",
 "place": null, "year": 1890}
```

`null` is what is not known; `work_editions` is how many editions the work has, this
one included. `PATCH /editions/{edition_id}/metadata` changes it; the body has some of
`author`, `title`, `place` and `year`, and the response is what is known now, like
`GET`:

```json
{"author": "Prus, Bolesław (1847-1912)", "year": 1890}
```

- **`author` and `title` are changed in the work itself**, in place, so the other
  editions of the work have the change too (`work_editions` says how many there are).
  **`place` and `year` are changed in the edition** alone.
- **What is not in the body is left as it is**; a field that is `null`, or only white
  space, is cleared. The text is trimmed and white space in it becomes one space.
  The author is written the way the catalogue writes it, "Last name, First names
  (years)", which is what the main page shortens to "Prus B.".
- **Checks**: `year` is a whole number from 1500 to 2030 (the panel picks it from a list that opens at 1900 when none is known); `author` and `place` have at
  most 300 characters, `title` 1000. Any other field (`id`, `work_id`, ...) and an empty
  body are a 422, and nothing is changed. 404 if the edition is not stored. A work is
  one per author and title (case does not matter): if another work has the new author
  and title already, the answer is 409 and nothing is changed (the place and year
  too). Polona is not called.
- **In the frontend**, a line at the very top of a page, above the page number, says
  what the edition is, the way the main page does and with the place and the year:
  "Prus B.; Lalka · Warszawa, 1890" (the short author, the short title, then the place
  and the year; what is not known is left out, a full stop after the place is dropped).
  It is asked for once for an edition (`GET .../metadata`) and shows what is saved at
  once. Right next to it is a pencil button. It opens a panel with two parts, "Work" (author and
  title, with a note of how many other editions are changed too) and "This edition"
  (place and year), filled in, and a Save button (disabled until something changes).
  Save sends only what was changed. The year must be a number; if the backend refuses,
  its reason is shown. Escape or Close closes the panel.

### Requesting a page

`GET /editions/{edition_id}/pages/{number}`

- `edition_id` is the **Polona object UUID** (the one in a polona.pl item-view
  URL).
- `number` is the page number, **0-based**, the same as Polona's `?page=`
  parameter (`number=99` is "Skan 100" in the Polona viewer).

The response is a `PageView`: `id`, `edition_id`, `number`, `scan_uri`,
`ready` and `content`. `content` is the page's TEI XML (`xml_content_corrected`); the
raw OCR text is never served.

### Where the page comes from

1. **The database is asked first.** If the page (edition + number) is already
   stored, it is returned from there and Polona is not contacted. Stored pages
   are never refreshed automatically.
2. **Otherwise the page is fetched from Polona and saved**
   (`idempotent_fetch_page` in `app/page_provider.py`). The edition comes first,
   because the page belongs to it (`idempotent_fetch_edition`): if it is not
   stored yet, its metadata is fetched and a work and the edition are created
   (`create_work`, `create_edition`). Then the page is requested from Polona
   (`request_polona_edition_page` in `app/polona/polona_connector.py`), saved
   (`fetch_page_from_known_edition`) and returned. The next request for it is
   served from the database. A page that Polona does not have therefore leaves
   its (new) edition stored.

   **The first fetch of a page is safe against concurrent requests.** Requests for
   the same page that is not stored yet take turns (a lock for each page, in the
   backend process); the ones that wait look in the database again when their
   turn comes, find the page that the first one saved and return it. Polona is
   asked once, the page is saved once, and a stored page is never overwritten.
   Other pages do not wait for it. The lock is not shared between backend
   processes (the backend runs as one, and its threads share one database
   connection, which is why a database lock would not do): with several
   processes the page could still be fetched twice, but it is stored once, with
   the text of the first.

### What is fetched from Polona

For a page that is not stored yet, two things are fetched: the edition's
metadata first (if the edition is new), then the page itself.

**The page itself** (`request_polona_edition_page`):

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

A fetched page is saved with `raw_ocr_content` (the OCR text as fetched from
Polona) and `scan_uri`. `raw_ocr_content` is written once, when the page is
first fetched, and never changes after that. The OCR text is also converted to
TEI XML (`app/text_tools/tei_xml.py`: paragraphs at blank lines, `<lb/>` at line breaks)
and saved as `xml_content_corrected`. This is the XML the user edits. Because
every fetched page starts with an `xml_content_corrected`, `content` in the
response is that TEI XML until a correction replaces it. If a page is ever
fetched again, only `scan_uri` is refreshed; `raw_ocr_content`,
`xml_content_corrected`, `ready` and `id` are kept.

When the user corrects a word (see "Correcting a word" below), the word is
changed in `xml_content_corrected`, and the correction (`before` -> `after`) is also
recorded in the `corrections` table, for the page.

### Editing a page

`PATCH /editions/{edition_id}/pages/{number}` with a JSON body.

Only two things can be edited: `xml_content_corrected` and `ready`. Fields left out
are not touched. A page always keeps its XML, so `xml_content_corrected` cannot
be null or an empty string. Any other field in the body (for example
`raw_ocr_content` or `scan_uri`) is rejected with 422, and so is an empty body.

The page must already be stored: open it with `GET` first. `PATCH` never
fetches from Polona, and answers 404 if the page is not stored. It returns the
updated `PageView`.

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
the changed XML as the page's `xml_content_corrected`. The body is what `POST /ner`
found:

```json
{"start": 0, "end": 15, "text": "Adam Mickiewicz", "label": "persName"}
```

so that `<p>Adam Mickiewicz mieszkał ...</p>` becomes
`<p><persName>Adam Mickiewicz</persName> mieszkał ...</p>`. The response is the
updated page, like `GET`. `label` is one of the six that the model produces,
which are also TEI elements: `persName`, `placeName`, `orgName`, `geogName`,
`date`, `time`; or one of three that the model cannot find, which the user marks by
hand: `ref` (a reference in the text), `bibl` (bibliographic information) and `cit`
(a cited quotation). The first two are wrapped like the others: `<ref>Jan
Kowalski</ref>`. TEI wants the text of a `<cit>` in a `<quote>`, so it becomes
`<cit><quote>Jan Kowalski</quote></cit>`. Any other label is 422.

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
  it does not fetch from Polona.

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
  white space is not a token.
- **A word split over two lines is checked as one word.** When a word is followed,
  with nothing between, by a hyphen, then one line break and another word
  (`przeczy-` / `tać`), the two halves are one word. Each half stays a token of its
  own, with its own offsets, so it can still be corrected on its own, but both get
  the verdict of the whole word: correct if the word joined (`przeczytać`) is
  correct, or if the word with the hyphen kept (`biało-czerwony`) is, because a
  hyphen at the end of a line may be one of the word or only the sign of the split.
  The legal spellings of the edition are matched against the joined word too. The
  hyphen itself is a sign, so it is correct. A word split over three lines or four
  is checked the same way; an empty line (a new paragraph) or a space after the
  hyphen is not a split. A word that is split over two *pages* is not found: only
  the text of one page is known to the check.
- A word with digits in it is not checked and counts as correct. A hyphenated
  word is correct if it is in the dictionary, or if all its parts are. Capital
  letters do not matter.
- There are no suggestions: phunspell takes about 2.6 s to suggest for one
  word, which is too slow to do for all the wrong words of a page. (A page of
  about 200 words is checked in about 0.3 s.)
- The dictionary is loaded when the backend starts, like the spaCy model. If it
  is not available the backend still starts, logs a warning, and the endpoint
  answers 503. The text can have up to 100,000 characters (422 if longer).

### Suggesting the spelling of a word

For a misspelled word the correction view offers the replacements that an ordinary
spell check would: the closest ones, each a button that corrects the word to it, so
that the user clicks instead of typing. `POST /spellcheck/suggestions` gives them:

```json
{"text": "Ala ma kótka tu", "start": 7, "end": 12}
```

`text` is the plain text of the page and `start`/`end` the word in it, counted in code
points like the offsets of `POST /spellcheck` (the optional `edition_id` makes the legal
spellings of the edition count as correct). The response is `{"suggestions": ["kotka",
"kotką", "krótka"]}`: Hunspell's suggestions for the Polish dictionary, closest first,
**at most three**, each one word and different from the word.

- **Only a word in the middle of its line gets any.** The first and the last word of a
  line (a line is what is between two line breaks) get none, and so does a word that is
  alone on its line: they may be the parts of a word that is split over two lines
  ("kó-" / "tka"), and a suggestion for them would be mostly wrong. A sign after the last
  word does not make it mid-line (`"Ala ma kótka."`: `kótka` is the last word).
- **A word that is spelled correctly, or is a legal spelling of the edition, gets none**
  either; the list is then empty, and so it is when the dictionary has nothing to offer.
- 422 if the characters at the offsets are not one word (a sign, part of a word, or
  several words), 503 if the dictionary is not available. Nothing is stored.
- **In the frontend**, the field that opens when a misspelled word is clicked asks for
  the suggestions and shows them as green buttons next to "Legal spelling". A click
  corrects the word exactly as if it was typed (the correction is recorded, and the
  new spelling is made a legal one, as always). If there are none, or they cannot be
  had, nothing is shown and the word can be typed as before.

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
  `xml_content_corrected`, and the `Correction` (`before`, `after`) is stored for the
  page, in **one transaction with the page's row locked**: both happen or
  neither does, and two corrections of the same page cannot overwrite each
  other. The response is the updated page, like `GET`.
- `before` must not contain spaces or line breaks (it is a word), and `after`
  must not be empty, start or end with a space, contain a line break or other
  control characters, or be the same as `before`. It may contain spaces inside
  (`wcałey` -> `w całej`). 422 otherwise, and for a page without valid TEI XML.
  404 if the page is not stored. Like `PATCH`, it does not fetch from
  Polona.
- The text of the page changes, so the offsets of its entities and words from
  before are no longer valid: check the spelling again (the frontend does).
- This replaces the earlier `POST /corrections` stub (which returned 501).

### A word that goes on in the next line

A word split over two lines needs both parts corrected before the correction makes
sense (`wszechsiia` / `iowymi` is `wszechświa-` / `towymi`, not
`wszechświaiowymi`), and the frontend cannot know whether the second part is to be
corrected too. So the editor of the last word of a line has a **"Continued on next
line -"** button: it opens two fields, the two parts, with a hyphen between them,
and a "Save" button. Nothing is sent before Save. A hyphen typed at the end of the
first part counts once.

`POST /editions/{edition_id}/pages/{number}/continued-words` saves it:

```json
{"first": {"start": 9, "end": 19, "before": "wszechsiia", "after": "wszechświa-"},
 "second": {"start": 20, "end": 26, "before": "iowymi", "after": "towymi"}}
```

- Only one line break may be between the words, and `first.after` ends with one
  hyphen. `second.after` may be the same as `second.before`, if that part was right.
- Both words are replaced in the XML and **one** correction is recorded for the
  whole word, without the hyphen: `wszechsiiaiowymi` -> `wszechświatowymi` (none if
  that is the same), in one transaction. 409 if the page has changed, 422 if the
  words are not on two lines or a part is not acceptable, 404 if the page is not
  stored.
- The frontend then makes the whole new word a legal spelling, if either part was
  misspelled. A word split over pages is not handled.
- "Legal spelling" on a part of a word that is already split by a hyphen at the
  end of a line saves the whole word (the parts put together), not the part.

### The same mistake in the rest of the edition

The OCR makes the same mistake again and again: it reads the old Polish "é" as "ć",
so `jéj` comes out as `jćj`, `samćj` and `bardzićj` for `saméj` and `bardziéj`.
`GET /editions/{edition_id}/similar-misspellings?incorrect_spelling=jćj&corrected_spelling=jéj`
lists where the rest of the edition has the same mistake, so that a correction can be
offered for the whole edition. It changes nothing.

- **The mistake is a cluster of letters**: from the first letter that differs between
  the two spellings to the end of the word. `jćj` corrected to `jéj` is the cluster
  `ćj`; `tćgo` corrected to `tégo` is `ćgo`. The letters before it are not part of the
  mistake, and differ from word to word (`jćj`, `samćj`). It is at least two letters
  long: a single wrong letter at the end is taken with the letter before it, because
  one letter (`ć`) is in far too many words that are right. If the wrong spelling is
  only the beginning of the corrected one (letters are missing), only that spelling
  itself is looked for.
- **A match is any word token that contains the cluster**, whatever its case:
  `jćj`, `samćj`, `bardzićj`, `tćjże`. Every page of the edition is looked at, in its
  corrected content (`xml_content_corrected`, the text that the user sees, as
  `POST /tei/text` gives it). A page without corrected content, or whose XML cannot
  be read, is left out.
- **Only wrong words, sure or doubtful**: a token is listed only if the spell check
  flags it (as `POST /spellcheck` does, with the legal spellings of the edition counted as
  correct, and a word split over two lines judged as one word). A token that is spelled
  correctly is never offered, however much it looks like the mistake (`zasiedliśmy` is a
  word, it is not `zasiedliémy`). `sure` says whether the `suggestion` is a correct word
  or a legal spelling of the edition; if not (the dictionary is a modern one, so `saméj`
  is not known until it is legal in the edition) the match is doubtful, `sure: false`.
  The panel shows the doubtful ones in a separate group, below the sure ones.
- **The response** is a list with an item for each page that has matches, in the
  order of the page numbers: `{"page_number": 8, "tokens": [{"text": "jćj", "start":
  383, "end": 386, "suggestion": "jéj", "sure": true, "before": "To", "after": "i"}, ...]}`. `start` and `end` are offsets in
  that text, counted in code points like the offsets of a correction, so
  `text[start:end]` is the token. The `suggestion` is the token with the mistake
  corrected in it, every occurrence of the cluster and with the capital letters kept
  (`samćj` gives `saméj`, `Ćj` gives `Éj`): it is the `after` of the correction
  (`POST .../corrections`) that applies it, with the token as `before` and these
  offsets. A page without matches is not in the list, and an edition without matches
  gets `[]`.
- **Context**: each token has `before` and `after`, the nearest word before it and the
  nearest word after it with the signs between them and the token, as they are written
  (`"dniu."`, `", raz"`), so that a full stop or a comma next to it does not leave nothing to
  read (the same rule as in the other edition-wide tools). The panel shows each place as
  `before misspelling → suggestion after`.
- **404** if the edition is not stored, **422** if a spelling is empty or longer than
  100 characters, if one is missing, or if the two are the same, **503** if the dictionary
  is not available. Polona is not called. The search is `Database.get_similar_misspellings`.

### Adding a token that the OCR missed

`POST /editions/{edition_id}/pages/{number}/tokens` with `{"position": 25, "text":
"jest"}` puts a token at `position` of the text that `POST /tei/text` gives for the
page (an offset between two characters, counted the way Python does: 0 is before the
first character, and the length of the text is after the last). The backend finds
the place in the XML and puts the token into the text node there, so the markup
around it stays (a token after the last word of a `<persName>` goes into it). The
token is a token of its own: a space is put before it unless there is a space or a
line break there already, and after it unless the text goes on with one, so a token
`X` put in the middle of `Alama` makes `Ala X ma`. It may be several words, like `LIST SZÓSTY` (a run of spaces
counts as one). It must not be empty, start or end with a space, or contain line
breaks, tabs or other control characters, and the position must be in the text, and not on the empty line between two paragraphs (422 for those, and when the
page has no valid TEI XML; 404 if the page is not stored). The new XML is saved as
the page's `xml_content_corrected`, in one transaction with the page locked. No
Correction is recorded: adding a token is not correcting a word. The raw OCR text is
not touched. The response is the updated `PageView`.

By default the token goes into an existing line. With `"new_line": true` it is put on
a line of its own instead, **above** the line that starts at `position`: `{"position":
12, "text": "LIST SZÓSTY", "new_line": true}` makes a new line before the line that
starts at 12 (position 0 gives a new first line of the page; there is no word before
it to split a line at, so this is the only way to get one). The position must be the
start of a line (0, or after a line break), else 422. The token and a line break
(`<lb/>`) go before the first character of that line, in the same block, and outside
the markup that the line starts with (a `<persName>`). In the frontend, the group of
buttons of a clicked word has **"↑ Add line above"**, for the first word of a line (a
line of one word too): it opens a field next to the word, and the text typed is sent
with the word's start as `position`.

A page without any text (the OCR found
nothing, which happens) takes the token as its first line: the only position there
is 0, and the token goes into the first `<p>` of the page (which is made, with the
`<body>`, if the XML has none).

### Joining tokens that the OCR split

Sometimes the OCR cuts one word in two ("abso lutely").
`POST /editions/{edition_id}/pages/{number}/joins` with `{"start": 25, "end": 36,
"text": "abso lutely"}` removes the spaces between the tokens in the characters
`start` to `end` of the text that `POST /tei/text` gives for the page. `text` is
two or more tokens separated by single spaces, as the client saw them.

- The spaces are removed where they are in the XML (all the whitespace of the run,
  also when it is a text node of its own between two inline elements), so the
  markup around stays. Joining the last word of one `<persName>` and the first of
  the next one puts the two elements next to each other, without a space.
- Tokens on different lines are not joined: a line break is not a space.
- `text` is checked against what is at the offsets: 409 if the page has changed
  since. 422 if `text` is not tokens with single spaces between them, or the page
  has no valid TEI XML; 404 if the page is not stored.

### Starting a new line

`POST /editions/{edition_id}/pages/{number}/line-splits` with `{"start": 25,
"end": 33, "text": "kota Ala"}` turns the space between two tokens of the same
line into a line break. `start` to `end` are characters of the text that
`POST /tei/text` gives for the page, and `text` is the two tokens, separated by one
space, as the client saw them. It is the counterpart of adding a token: a token is
added to a line, and the line is then split before it.

- The whitespace of the space goes, and `<lb/>` is put where it was, so the markup
  around stays: a line can be split inside a `<persName>`
  (`<persName>Jan<lb/>Kowalski</persName>`), and a space between two elements
  becomes the line break between them.
- If the token before the break ends with a hyphen, it continues a word on the next
  line, and the break is `<lb break="no"/>`, as in a page made from the OCR text.
- Two tokens on one line are needed, so no line is ever empty: the start or the end
  of a line, a line break and a paragraph break are not places for a split.
- `text` is checked against what is at the offsets: 409 if the page has changed
  since. 422 if `text` is not two tokens with one space between them, or the page
  has no valid TEI XML; 404 if the page is not stored.
- The new XML is saved as the page's `xml_content_corrected`. No Correction is
  recorded: splitting a line is not correcting a word. The response is the updated
  `PageView`.

A split is undone with the next section.

### Removing a line break

`POST /editions/{edition_id}/pages/{number}/line-joins` with `{"start": 25,
"end": 31, "text": "kota\nA"}` removes the line break between the last token of a
line and the first token of the next one, so the two lines become one. It undoes a
line split. `start` to `end` are characters of the text that `POST /tei/text` gives
for the page, and `text` is the two tokens, separated by one line break or by an
empty line, as the client saw them.

- The `<lb/>` between them is replaced with a space, and the markup around stays:
  a break inside a `<persName>` goes and the element stays
  (`<persName>Jan Kowalski</persName>`). A break inside a word
  (`<lb break="no"/>`, after a hyphen) becomes a space too; the hyphen stays in the
  text, and "Join with next" takes the space away if it is wanted.
- **An empty line** (`"text": "315\n\nbłędy"`) is the end of one `<p>` and the start
  of the next one. The two paragraphs become one, with a line break (`<lb/>`) between
  them: the empty line is gone and the two lines stay lines, one line break less
  (`315⏎⏎błędy` becomes `315⏎błędy`). A second removal joins the lines. The two must
  be `<p>` next to each other, with nothing but empty elements between them (422
  otherwise, for example for a `<head>` and a `<p>`, or paragraphs in different
  `<div>`s). A hyphen at the end of the first one makes the break
  `<lb break="no"/>`.
- Only a plain `<lb/>` goes. The end of an `<l>` or an `<item>` is not removed (422).
- `text` is checked against what is at the offsets: 409 if the page has changed
  since. 422 if `text` is not two tokens with one line break between them, or the
  page has no valid TEI XML; 404 if the page is not stored.
- The new XML is saved as the page's `xml_content_corrected`. No Correction is
  recorded. The response is the updated `PageView`.
- Like adding a token, no Correction is recorded (it is not the correction of a
  word), the raw OCR text is not touched, and the response is the updated
  `PageView`. The text changes, so check the spelling again (the frontend does).

### Legal spellings

Old print has spellings that the dictionary does not know and that are not
mistakes there (`jey`, `tey`, `raczey`). A **legal spelling** says so, for one
edition.

**Why "legal" and not "correct".** The dictionary is a modern one: "correct"
there means correct today. What was correct in the 19th century often is not
anymore, and the other way round, so calling these spellings "correct" would
be wrong, and "incorrect" for the dictionary's verdict on them would be wrong
too. We have no better word, so we say **legal**: a spelling that the edition
itself uses and that is not to be corrected. In the API, the code and these
docs, "correct" and the `correct` flag of a word are the dictionary's verdict
(a legal spelling only makes the verdict come out as correct), and "legal" is
what a person decides for an edition. The decision is for an edition, not for
the language.

They are kept in the table `legal_spellings`, with two columns: the
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
  opened. Like the other editing endpoints it does not call
  Polona.
- The table is created when the backend starts, like the others, so an existing
  database gets it on the next start.
- `POST /spellcheck` uses them: given the `edition_id`, a legal spelling is
  reported as correct (`Database.get_legal_spellings` reads them). That is what
  keeps a word that was made legal from being red again on the next page of the
  edition, or after a reload. Nothing lists them yet.

### The entities marked in a page

`POST /tei/entities` takes `{"xml": "..."}` like `POST /tei/text` (422 in the same
cases) and lists the named entities that are marked in it: every `<persName>`,
`<placeName>`, `<orgName>`, `<geogName>`, `<date>` and `<time>` element, whoever
put it there, in the order of the document. Each is like an entity that
`POST /ner` finds: `text`, `label` (the name of the element), and `start` and
`end` (offsets into the text that `/tei/text` gives, counted the way Python
does). Spaces and line breaks at its ends are not part of it. Nothing is stored.
This is how a page that is opened again shows what was confirmed before.

### The sections marked in a page

`POST /tei/sections` takes `{"xml": "..."}` like `POST /tei/text` (422 in the same
cases) and lists the sections that are marked in it: the elements that
`POST .../sections` makes (`<fw>`, `<div type="toc">`, `<noteGrp>`, `<note>`,
`<quote xml:lang="…">`), outer ones first. Each has `start` and `end` (offsets into the text that
`/tei/text` gives, counted the way Python does) and `open` and `close`, its tags
as written in the XML, e.g. `<note n="1">` and `</note>`. Entities and the plain
`<div>`s that follow a table of contents are not sections. Nothing is stored.

### Marking the sections of a page

`POST /editions/{edition_id}/pages/{number}/sections` marks a stretch of the
text of a stored page as a section of the page, in its TEI XML, and saves the
changed XML as the page's `xml_content_corrected`:

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
| `quote`       | `<quote xml:lang="la">…</quote>`    | a quotation in another language |

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
- **`quote` goes around the text itself**, like the `<fw>` elements, and needs the
  body's `lang`, the code of the language it is in: `ru`, `en`, `de`, `es`, `fr`, `it`
  `la` (Latin) or `grc` (Ancient Greek), which becomes `xml:lang` (`{"…": "…", "section": "quote", "lang":
  "la"}`). A quote without `lang`, or with another one, is a 422, and so is a `lang`
  on any other section. The frontend offers the languages in a dropdown. **What is
  in such a quotation is not Polish, so the Polish spell check leaves it alone**: its
  words are reported as correct by `POST /spellcheck` (when the request has the `xml`
  the text is of; 422 if the `text` is not that XML's text) and are not found by the
  edition-wide tools (the same mistake, the substring spellings, the missing hyphens
  and the OCR rubbish). Spell-checking the other languages is not done (yet).
- The documents this produces were checked against the official TEI schema
  (`tei_all`), including random pages with several marks one after the other.
- 404 if the page is not stored, 422 if it has no valid TEI XML or the section
  cannot be marked (with the reason). Like `PATCH`, it does not fetch from
  Polona.

### The same page number on the other pages

In some editions the page number is printed at the top of every page, as the first
line, and it should be marked on all of them as `<fw type="pageNum">` (see "Marking
the sections of a page"): nobody should have to do that page by page.
`GET /editions/{edition_id}/pages/{number}/similar-page-numbers` takes the page
number that is marked on that page as an example and lists the other pages that
have one like it, so that a human can check them and approve them. It changes
nothing.

- **The example** is the first page number marked on the page (`<fw
  type="pageNum">`). Its line must be the first or the last line of the page and
  have nothing on it but the number and dashes around it: a number of one to four
  digits, with single or double dashes before and after it (`- 12 -`, `— 12 —`,
  `-12-`, any of `-`, `–`, `—`) or with none. If not, the answer is a 422 with the
  reason (`The page number must be alone on its line…`).
- **The pattern** is that: the same line of another page (the first, or the last),
  the whole line a number, with dashes if the example has them and without if it
  has none (single or double does not matter). So a page that starts with a table
  of contents, a title or a chapter heading (`12 Rozdział pierwszy`) is not a
  candidate. A page that has a page number marked already is left out too, and a
  page without XML, or with XML that cannot be read.
- **What is marked** is what the example has: the number with its dashes if the
  example was marked with them, the digits alone if it was not.
- **The response**: `{"example": {...}, "where": "first", "offset": 3,
  "candidates": [{"page_number": 12, "start": 0, "end": 1, "text": "9",
  "number": 9, "context": "Ciekawość względem …", "fits_sequence": true}, ...]}`,
  the candidates in the order of the pages. `start` and `end` are offsets in the
  text of the page (counted in code points, as `POST /tei/text` gives it), so
  `text[start:end]` is `text`: what `POST .../sections` is to mark, with
  `"section": "page_number"`. `context` is the start of the nearest line with text
  in it, to see where the number is.
- **`fits_sequence`** says whether the number follows the numbering of the example.
  A numbering counts one by one, so the page less its printed number is the same
  all through (`offset`: page 12 shows 9, offset 3, and so does page 123 with 120).
  A candidate that does not fit (`9`, whose page less 9 is not 3, or the `99` of a
  table of contents) is probably something else; it is listed all the same.
- 404 if the page is not stored, 422 if no page number is marked on it or it is not
  alone on the first or last line. Polona is not called. The search is
  `Database.get_similar_page_numbers`, on `app/text_tools/page_numbers.py`.

### The hyphens that the OCR dropped at the end of a line

A word that is split over two lines has a hyphen at the end of the first line
(`przeczy-` / `tać`). The OCR very often leaves it out, typically a few times on a
page, and the spell check then sees two wrong words. These are put back in bulk,
for one page at a time (not the edition).

`GET /editions/{edition_id}/pages/{number}/missing-hyphens` lists the line ends of
the page where a hyphen is missing. It changes nothing.

- **What is found**: the last word of a line and the first word of the next line,
  each of two letters at least and without digits, that make a correct word
  together (`przeczy` + `tać` = `przeczytać`). The spellings that are legal in the
  edition count as correct (see "Legal spellings"). A line that has its hyphen
  already, a line that is followed by an empty line (a paragraph break) and a space
  before the line break are not found. A word that has a hyphen of its own
  (`biało-czerwony`) is not found either: any two correct words are correct with a
  hyphen between them, so that would find every line end.
- **A stray sign**: the OCR may have read the hyphen as another sign (`przeczy,`,
  `przeczy'`, `przeczy~`). One or two signs glued to the word at the end of the line
  are taken as the hyphen (dashes are not: that is a hyphen of another kind), and
  they are replaced by it.
- **The response**: a list of `{"start": 9, "end": 16, "text": "przeczy",
  "suggestion": "przeczy-", "next_word": "tać", "joined": "przeczytać",
  "ambiguous": false}` in the order of the text. `joined` is the word that the two
  parts make together, the one that was found correct. `start` and `end` are offsets in the text of the page (as `POST
  /tei/text` gives it), so `text[start:end]` is `text` (the word with the stray
  sign, if any), which is to become `suggestion`.
- **`ambiguous`** is true when both parts are correct words on their own (`do` /
  `mu` makes `domu`): it is not sure that the word is split, so a human has to say.
- 404 if the page is not stored, 422 if its XML cannot be read, 503 if the
  dictionary is not available. A page without text has none.

`POST /editions/{edition_id}/pages/{number}/hyphens` puts the hyphens back, all at
once, so there is a single request however many were approved. The body is
`{"fixes": [{"start": 9, "end": 16, "before": "przeczy", "after": "przeczy-"}]}`:
`before` is a word, perhaps with the stray sign, and `after` is the same word and a
hyphen (anything else is a 422), the fixes must not overlap, and there is at least
one. All the offsets are those of the page as the client saw it: the backend makes
the fixes from the end of the page to its start, so that none moves the others. The
new XML and a Correction for each fix are saved in one transaction (all or none);
nothing is made a legal spelling, as a hyphen at the end of a line is not a
spelling. 404 if the page is not stored, 409 if the text at an offset is not
`before` (the page has changed), 422 if the page has no valid XML.

The search is `Database.get_missing_hyphens`, on `app/text_tools/hyphens.py`;
saving is `Database.apply_corrections`, the `apply_correction` for several.

### Every place of a token

The OCR makes the same mistake again and again, and the spell check does not flag it when
the wrong word is a word too: `tem` is read as `tern`, and `tern` passes. A person who has
seen it on the scan gives the token as it is wrong, and every place of it in the edition is
found, each in its context, to be corrected one by one or all at once.

`GET /editions/{edition_id}/token-occurrences?token=tern` changes nothing.

- **The token** is one token as the spell check makes tokens: a word (a hyphen or an
  apostrophe inside it is part of it: `biało-czerwony`) or a run of signs (`(|}`). Spaces
  at its ends, spaces inside it, or a word with signs glued to it (`tern,`) is not one: 422
  with the reason. It is at most 100 characters.
- **The match is exact**: whole tokens only, and the case of the letters counts, so `tern` is
  not `Tern`, and it is not in `terne` or `intern`. No dictionary is used, so it is found
  whether the spell check flags it or not.
- **The response**: a list with an item for each page that has the token, in the order of the
  pages: `{"page_number": 8, "occurrences": [{"start": 383, "end": 387, "text": "tern",
  "before": "o", "after": "dniu"}]}`. `start` and `end` are offsets in the text of the page (as
  `POST /tei/text` gives it), so `text[start:end]` is the token. `before` and `after` are its
  **context**: the nearest *word* before it and the nearest word after it, with the signs between
  them and the token, as they are written. A full stop or a comma next to the token does not use
  up the context: `Ala ma dom. tern, kota` gives `dom.` and `, kota`, so a person always has a word
  to read on each side. A line break is a space. Where there is no word (the end of the text, or
  only signs) it is the signs, or empty.
- **The correction** of a place is `POST /editions/{id}/pages/{n}/corrections` with `before` the
  token and `after` the replacement (see "Correcting a word"), the same as for a word.
- 404 if the edition is not stored, 422 if the token is not one. The search is
  `Database.get_token_occurrences`, on `app/text_tools/token_occurrences.py`.

### Accepting the spellings that have a substring in common

Old print has spellings that repeat: `Konwencya` and `tendencyę` have a `y` where we
write `j`. The dictionary is a modern one and flags them, and making each a legal
spelling by hand (the "legal spelling" button of a word, see "Legal spellings") is no
work for a human. We call them "legal", not "correct", and **nothing is corrected or
replaced** here: the words are only accepted, as they are. A person gives the substring that
the words have in common, and the backend finds the flagged words that have it.

`GET /editions/{edition_id}/spelling-chunk-matches?core=cy&ending=a&ending=i&ending=ami`
changes nothing.

- **The substring** is a `core` (`cy`) and an `ending` for each ending (`a`, `i`, `ami`). What
  is looked for is the core with each ending: `cya`, `cyi` and `cyami`, **anywhere in the
  word** (`Konwencya`, and also `tendencyalny`), whatever the case of the letters. An empty
  ending is the core alone. There are at most forty endings, and the core and each ending are
  at most 30 letters (a hyphen or an apostrophe inside is fine); spaces around them do not
  count.
- **What is listed**: the words that the spell check flags, as `POST /spellcheck` judges
  them: a word split over two lines is judged as one word, and the legal spellings of the
  edition count as correct, so a word that is accepted already is not listed. Signs are not
  words. A word is listed with the case it has (`Konwencya` and `konwencya` are two).
- **The response**: a list with an item for each word, the ones that are in the edition
  most often first: `{"text": "Konwencya", "count": 7, "occurrences": [{"page_number": 8,
  "start": 383, "end": 392, "before": "w", "after": "z"}]}`. `occurrences` are the first
  places of the word (at most five, in the order of the pages); `count` is how many there
  are in all. `start` and `end` are offsets in the text of the page (as `POST /tei/text`
  gives it), so `text[start:end]` is the word. `before` and `after` are its
  **context**: the nearest *word* before it and the nearest word after it, with the signs between
  them and the word, as they are written (`dom.` and `, kota`), so a full stop or a semicolon next to
  it does not leave nothing to read. A line break is a space; the same rule as in "Every place of a
  token".
- **Accepting** is the request that the "legal spelling" button sends: `POST
  /editions/{id}/legal-spellings` with `{"spelling": "Konwencya"}`. It is of the word,
  wherever it is, so one request is for all its places.
- 404 if the edition is not stored, 422 if the core is empty, there is no ending or more
  than ten, or a part is too long or is not letters (with the reason), 503 if the
  dictionary is not available. The substring is in `app/text_tools/spelling_chunks.py`, the
  search is `Database.get_chunk_matches`.

### Bulk spelling check of the edition

The spellings of the print that repeat (`Konwencya`) are flagged again and again, and one
that is flagged once is probably an OCR mistake. `GET /editions/{edition_id}/bulk-spelling-check`
(no other parameter) checks **all the pages** and lists the flagged spellings that are in the
edition often, so that a person accepts them as legal spellings at once. It changes nothing.

- **What is checked**: as `POST /spellcheck` judges (a word split over two lines is one word;
  the legal spellings of the edition count as correct), except that **the first and the last
  word of every line are left out**, so those of every page too: a word that the end of a line
  cuts is flagged for nothing. Signs do not count as words (`— Ala ma kota.` has `Ala` first
  and `kota` last). The words in other languages are not checked.
- **What is listed**: a spelling is listed only if it is in the edition **more than three
  times** (`MORE_THAN` in `app/text_tools/bulk_spelling.py`; counted over the words that are
  looked at). It has the case it has: `Konwencya` and `konwencya` are two.
- **Families**: spellings that are one word with different endings are grouped: they begin
  with the same letters, at least four (whatever the case), and have at most four letters
  after them (`Konwencya`, `Konwencyi` and `Konwencyami` are one family, `Konstytucya` is
  another). A word joins the first family whose most common word it fits.
- **The response**: a list of families, the most common first (all their spellings
  together): `{"stem": "konwency", "count": 11, "spellings": [...]}`; the `spellings` of a
  family, the most common first, are the items of `spelling-chunk-matches` (`text`, `count`,
  the first five `occurrences` with `page_number`, `start`, `end`, `before`, `after`).
- 404 if the edition is not stored, 503 if the dictionary is not available. The search is
  `Database.get_bulk_spelling_check`.

In the frontend it is **Bulk spelling check** in the cog wheel of the correction view. The box
asks the backend when it opens (there is nothing to type) and works like the box that accepts
spellings by their substring: the families with their spellings, each with its count and
places in their context, a check box to take it out of **Accept all**, **Accept** (the request
of the "legal spelling" button), **Include all** and **Include none**.

### The same named entity in all its forms

A Polish name is not always in the nominative. A surname that a person confirmed as
`<persName>` (`Machnicki`) is `Machnickiego`, `Machnickiemu`, `Machnickim` in the rest
of the edition, the feminine is `Machnicka`, `Machnickiej`, and a place (`Kraków`) is
`Krakowa`, `Krakowie`. `GET /editions/{edition_id}/similar-entities?text=Machnicki&label=persName`
finds all of them in the whole edition, so that they can be marked in bulk. It changes
nothing.

- **The forms are made by rules** from the word that was marked
  (`app/text_tools/inflection.py`), not taken from the spaCy model: its lemma of a name
  that it does not know is no use (it makes `machnickiego` of `Machnickiego`). The word
  is taken apart by its ending, in each family it fits: a surname in `-ski`/`-cki`/`-dzki`
  (and `-ki`, `-gi`), a hard adjective in `-y`, a masculine noun (`Jan`, with `ó` of the
  nominative as `o` in the others: `Kraków`, `Krakowa`; after `k` and `g` the instrumental
  is `-iem`), the masculines in `-ek`, `-ec` (`Marek`, `Marka`) and `-ń` (`Poznań`,
  `Poznania`, `Poznaniu`), a neuter in `-o` (`Zamczysko`, `Zamczyska`), and a feminine in
  `-a` (`Warszawa`, `Warszawie`; `Polska`, `Polsce`). The same forms are made from any
  form of the word, so it does not matter which case was marked. The 19th-century
  `-éj` and `-ém` are covered. Only the singular is made for nouns; a surname in `-ski`
  has its plural too (`Machnickich`, `Machniccy`).
- **`label` says which families to try**: a `persName` that fits `-ski`/`-cki` is that
  and nothing else (`Machnickiego` is not a noun), a `placeName` or `geogName` is a noun
  (never a surname in `-ski`), another label tries them all. A word that does not
  start with a capital, or has a sign other than a letter, a hyphen or an apostrophe in
  it, is left as it is.
- **Several words** (`Jan Machnicki`, at most five) are made of the forms of each word in
  the same case (`Jana Machnickiego`, `Janowi Machnickiemu`), and a word without a capital
  (`de`) stays. They are found where the words are next to each other, with one space or
  one line break between them.
- **What is found**: the whole tokens, never a part of a word, with the same case of the
  letters. A place that is inside an entity that is marked already (of any label) is left
  out; where two forms overlap, the longest is taken.
- **The response**: `{"label": "persName", "forms": [{"text": "Machnicki", "cases":
  ["nom.sg"]}, ...], "pages": [{"page_number": 8, "matches": [{"start": 383, "end": 395,
  "text": "Machnickiego", "before": "panem", "after": "wczoraj", "sure": true}]}]}`. The
  `forms` are what was looked for, so that a human can see them. `start` and `end` are
  offsets in the text of the page (as `POST /tei/text` gives it), so `text[start:end]` is
  `text`, which is what `POST .../entities` is to mark, with the `label`. `before` and
  `after` are its **context**: the nearest *word* before it and the nearest word after it, with the
  signs between them and the match, as they are written (`dom.` and `, kota`), so a full stop or a
  comma next to it does not leave nothing to read; the same rule as in "Every place of a token".
- **`sure`** is true if the NER model also finds an entity at that place with the same
  label. The model is run only on the pages that have a match, which takes a few seconds
  for a whole edition. A match that is not sure may be a word that only looks like the
  name (the model took `Zamczyska` for a person, so for a place it is not sure).
- 404 if the edition is not stored, 422 if `text` is empty, has no letters, is longer than
  100 characters or five words, or `label` is not an entity label, 503 if the model is
  not available. The search is `Database.get_similar_entities`.

### The OCR rubbish

The OCR reads a fleuron, a printer's ornament, or the print that shows through from
the other side of the page, as text, and produces nonsense, whole lines of it
(`I *>• ' -V. ^oSkv. ;-'. i M , . ypton`; a line of `■`, `*` or `\\`). These are
found in the whole edition and removed in bulk. Only whole lines are dealt with,
never a damaged token at the start of a good line (`£r~*>e szczerą ...`): that is
closer to a correction than to a removal.

`GET /editions/{edition_id}/rubbish-lines` lists them, page by page, in the order of
the pages. It changes nothing.

- **A candidate** is a line in which signs outnumber letters: the letters are less
  than 60 % of the letters and signs together (digits are not counted). It is
  **`sure`** if there is no correct word of three letters or more in it. If there is
  one, the line may be text with a lot of punctuation (`zamku!....`, a list of
  references), so it is `sure: false` and a human has to say. The spellings that are
  legal in the edition count as correct words.
- **Never found**: a line shorter than three characters (`*`, `.`, `\`, `♦`: too little to
  say it is rubbish), a line of one and the same sign, `?` `.` `-` or `!`, several times
  (`...`, `---`, `???`, `!!!`, `. . .`; a mix such as `.-.` does count), a line with
  digits and no letters (`0`, `16`, `7 (`: a page number or what stands for it), a Roman
  numeral (`II.`: the number of a chapter), and a line
  that is in a marked section (a header, a footer, a page number, a table of
  contents, a note).
- **The response**: `[{"page_number": 18, "lines": [{"start": 14, "end": 51, "text":
  "I *>• ...", "sure": true}]}]`. `start` and `end` are offsets in the text of the page
  (counted in code points, as `POST /tei/text` gives it), so `text[start:end]` is the
  line. A page whose XML cannot be read, or that has no such line, is left out.
- 404 if the edition is not stored, 503 if the dictionary is not available. The
  search is `Database.get_rubbish_lines`, on `app/text_tools/rubbish.py`.

`POST /editions/{edition_id}/pages/{number}/line-removals` removes one line, with the
body `{"start": 14, "end": 51, "text": "I *>• ..."}`. The characters `start` to `end`
must be `text` and a whole line (a line break before it and after it, or an end of
the text), else it is a 409 (the page has changed) or a 422. The backend removes the
line and its `<lb/>`: the one before it, or after it if it is the first line of its
paragraph. A line that is all of its paragraph takes the paragraph with it, and so
the empty line next to it, unless it is the only paragraph of the page, which stays,
empty. A line with an element inside it (a `<persName>`), or that is not plain text of
a paragraph, is not removed (422). No Correction is recorded: it is not the
correction of a word. The answer is `{"page": {...}, "removed": 6}`: the updated page,
and how many characters of its text went (the line and its line break or breaks), so
the client can move the offsets of what follows on the page by that. The function is
`remove_line_at` in `app/text_tools/tei_xml.py`.

### What the frontend shows

While the scan of a page is loading (or has not started), a spinner with "Loading the
scan …" is shown in the left half, so that the user knows to wait. It goes when the
image has loaded, and comes back for the scan of the next page. If the image cannot
be loaded, a message says so.

The two halves scroll together: scrolling the scan (the left half) scrolls the text
(the right half), and scrolling the text scrolls the scan, by the same part of the
way: half way down one is half way down the other. It works in every view of the
text.

The main page (`/`) lists the editions of the collection as links, 100 on a page
(`GET /editions`), with Previous and Next to move between the pages of the list
(`/?page=2`, ...). There is no field to type a uuid in. Each link goes to the
first page of its edition, `/editions/{uuid}/pages/0`: the address of a page in
the browser is the same as the endpoint's, and opening it shows that page. If the
page cannot be loaded (for example, the number is past the last page), the reason is
shown, and the address stays as it is, with a link "Back to the edition" to the
first page of the edition that is in the address (not to the main page). If that
edition is not known either (the backend has no metadata for it), the link is "Back
to the editions", to the main page. The same goes for an address whose edition is not
a uuid at all (`/editions/abc/pages/0`): the error is shown, not the main page.
The error page is a skeleton pointing its finger at the message
(the animated `frontend/public/skeleton-wave.gif`), with the message in the type of the edition titles
on the main page and the way back as a button.

On a page, a round **house** button ("Back to the list of editions", at the left of the
Previous button) goes back to the main page: the frontend shows the list again
and the address becomes `/`, with no reload.

A page's `content` is TEI XML, but the frontend never shows it, and it never
reads it either. As soon as it gets a page, it sends the `content` back to
`POST /tei/text` and shows the text that comes back (in a read-only text box).
The same happens for every page it opens afterwards (previous/next page).

**The backend is the only source of truth for whether the XML is correct.**
The frontend has no XML parsing or validation of its own: if the backend
accepts the XML, the text it returns is shown; if the backend rejects it, the
reason it gives (the `detail` of the 422) is shown in place of the text, and
the scan and the buttons stay usable. This way there is one definition of
"correct TEI" and one way to read text out of it, and the frontend can't
disagree with it.

The font size of the text follows the page (`frontend/src/fitFont.ts`): it is set so that
the longest line just fits the width of the box, without wrapping, smaller if it
would not fit and bigger if there is room to spare, between 12px and 30px (a longer
line wraps then). It is measured in the browser, and done again when the text or the
width of the box changes.

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

The entities that are marked in the XML are shown as confirmed (green) in the NER
view, also after the page is opened again: when the view is opened (the NER button)
and after each confirmation, the frontend asks `POST /tei/entities` for the XML of
the page, next to the recognition. Every marked entity is shown, whoever marked it,
and where one overlaps a recognized entity, the marked one wins. They are not shown
in the correction view.

In the correction view a double click on a place in the text (not on a word, which is
edited with a click) opens a small box next to it, with a field and an Add button, to
add text that the OCR did not recognize as text (a token, or several words). The frontend finds the place
between two characters that was clicked and sends it with the token to
`POST .../tokens`; the page is then shown again with its new text and its spelling
checked again. Escape closes the box. The text view, the XML view and the NER view
ignore a double click.

Also in the correction view, when a word is clicked to be edited and there is
another word right before or after it with only a space between them (not across a
line break or punctuation), the editor has "← Join with previous" and "Join with
next →" buttons, for a word that the OCR split in two ("abso lutely"). They send
the two words to `POST .../joins`, which removes the space; the page is shown
again with its new text, and the spelling is checked again.

The same editor has a "↵ New line here" button for a word that has another one right
before it with only a space between (the same rule as "Join with previous"): it
starts a new line before the word. It sends the word before and the word to
`POST .../line-splits`, which turns the space into a line break; the page is shown
again with its new text, and the spelling is checked again. Together with a
double click that adds a token, it puts a token on a line of its own: add the
token at the end of a line, click it and choose "New line here".

"↑ Remove line break" and "Remove line break ↓" are the way back: for the first word
of a line, the first one joins the line to the one above; for the last word of a
line, the second joins the line below to it. Where there is an empty line between two
paragraphs (a page number alone on the first line, for example), the buttons say
"Remove empty line" and take it away, leaving a line break; a second click joins the
lines. They send the two words, with what is between them, to `POST .../line-joins`;
the page is shown again with its new text, and the spelling is checked again.

A page that has no text at all (the OCR found nothing) has nothing to double-click.
Instead of the hint, the correction view shows a field and an "Add text" button:
the text typed there is sent as a token at position 0 (`POST .../tokens`) and
becomes the first line of the page. It is not shown when the page's XML could not be
read (then the reason is shown instead).

In the NER view the user can also mark an entity that the recognition missed:
select some of the text, as in the Structure view, and a dialog appears next to
the selection with a dropdown of the labels (`persName`, `placeName`, `orgName`,
`geogName`, `date`, `time`, and `ref` for a reference, `bibl` for bibliographic
information, `cit` for a cited quotation; no "Not NER", which makes no sense here)
and a Confirm button. The recognized entities keep the dialog of the six labels of
the model, as the three others are not something it finds. Confirm sends it to `POST /editions/{id}/pages/{n}/entities`, like a
recognized entity that was confirmed, and the backend marks it in the XML; it is
then read back from the XML and shown in green. The dialog of a recognized entity
may be open meanwhile: its text is not counted in the offsets of the selection.

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
word is unchanged, and spaces around it are removed. An empty field removes the
token (a stray `$`): it is sent as an empty text, which the backend puts in its place (and if
the token was between two spaces, one of them goes too, so that no two are left
together); no Correction is recorded for it.
The frontend asks
the backend to correct the word (`POST .../corrections`, above), sending the
word's offsets and the new text, and never changes the XML itself. When the
backend answers, the page is shown with its new text, and the spelling is
checked again. While the request runs the field is disabled; if it fails, the
reason is shown and the field is kept so it can be tried again. The misspelled
words can also be reached with the Tab key, and Enter on one starts editing.

**The buttons of the word editor float over the text.** The buttons of a word
that is being edited (Legal spelling, Join with previous / next, New line here,
Remove line break / empty line) are one group, `role="group"`, that is out of the
line: it is positioned (`position: absolute`) under the field, with a white
background, a border and a shadow, over the text below it. Only the field is in the
line, so the rest of the line stays where it is and does not jump to the next one.
Near the right edge of the text box the group lines up with the right edge of the
field instead of the left one, and near the bottom edge it goes above the field,
when there is room there. The buttons keep the focus in the field (as before), so
clicking one does not cancel the edit.

**Applying a correction to the entire edition.** The OCR makes the same mistake
again and again, so when a word was corrected (the backend has answered, and the
page is shown), a small box comes up at the bottom right: "You corrected `jćj` to
`jéj`", with two buttons: **Apply it to the entire edition** and **Nein Danke**.
It does not come up for a removed word (an empty field), for a word left as it
was, for a correction that failed, or for a spelling of more than 100 characters,
nor when the string that was replaced, or the one that replaces it, is a single
character (`l` to `I`, `,` to `.`) or only signs, with no letter or digit (`—` to `-`):
those are everywhere, and mostly right as they are; and not when the correction only adds a hyphen to the last word of a line
(`abso` to `abso-`, the sign that the word goes on in the next line, which is
nothing the rest of the edition has to be told); the last word of the page ends a
line too. A hyphen added in the middle of a line, or together with another change,
is a correction like any other. Nor is that hyphen made a legal spelling of the
edition, as the new spelling of a misspelled word is otherwise (see "Legal
spellings"): `abso-` is not a spelling. A new correction replaces the box, and so does
leaving for the editions. If nobody clicks in the box, it goes away by itself after
15 seconds (`OFFER_SECONDS`); once it was clicked in, it stays until it is answered. Nothing is
asked of the backend until the user answers; "Nein Danke" (or Escape) closes the
box.

"Apply it to the entire edition" opens a big box over the page, which asks
`GET /editions/{id}/similar-misspellings` (see "The same mistake in the rest of
the edition") and lists what it finds, by page: the misspelling, the correction
the backend suggests, and its offsets, with an **Approve** button for each. The
button carries what it is about as `data-edition-id`, `data-page`, `data-start`
and `data-end`. Approving sends that place alone to `POST .../corrections`
(`before` the token, `after` its suggestion, with the offsets), like the correction
of a word, and the place is marked "✓ Approved". Nothing is applied by opening the
list. Some details:

- One approval is made at a time (the other buttons are disabled meanwhile),
  because a correction that changes the length of the text moves the places that
  follow it on its page: the offsets of those are moved by the difference, so
  they stay right. Other pages are not touched.
- If the place is on the page on the screen, that page is shown again with its
  new text, and its spelling is checked again. A place on another page changes
  that page in the backend only.
- If the backend refuses a place (for example the 409 when the page has changed
  since), the reason is shown in its row, the place stays, and it can be approved
  again. The other places are not affected.
- The box says "No other place in the edition has this mistake" when there is
  none, shows the backend's reason if the search fails, and is closed with
  **Close** or Escape.
- Approving also makes the corrected spelling a legal spelling of the edition
  (`POST /editions/{id}/legal-spellings`, after the correction), as editing a
  misspelled word does, so that `saméj` is not red afterwards even if the
  dictionary does not know it. Each spelling is sent once, however many places
  have it, and none is sent for a suggestion of several words (a spelling is one
  word). The page on the screen is checked again after that. If only the legal
  spelling is refused, the place stays corrected and is marked approved, with the
  reason as a warning in its row.

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

**Replacing a token in the edition.** The **Replace a token in the edition** tool of the correction
view (in the cog wheel of the sidebar) opens a big box over the page, in the look of the
substring box (see "The look of the tools"), with two boxes: the **incorrect token**, as it is
wrong (`tern`), and the **replacement token** (`tem`). **Find** is inactive until both are
written and they differ (spaces at the ends do not count). It asks `GET .../token-occurrences`
for the incorrect token only (see "Every place of a token"), and the box lists every place by
page, each **in its context**: the nearest word before it (with the signs between, `dom.`), the
token with the change (`tern → tem`, the replacement marked in yellow), and the nearest word after
it (`, kota`): a full stop or a comma next to the token does not leave nothing to read. If the backend says that what was written
is not a single token, its reason is shown. The list does not change if a box is changed
afterwards: it shows what was looked for and what it is to be replaced with; **Find** again.

- Each place has a **Replace** button (`data-edition-id`, `data-page`, `data-start`, `data-end`)
  and a box to take it out of **Replace all (N)**. All are in by default, as the match is exact;
  **Include all** and **Include none** set the boxes.
- It is applied the way the same mistake in the edition is: **Replace** corrects that token alone
  (`POST .../corrections`, `before` the token, `after` the replacement) and makes the replacement a
  legal spelling of the edition (once, however many places). **Replace all** does it for the places
  that are in, one request at a time, in the order of the pages, with "Replacing x of y …". A place
  that the backend refuses is marked with the reason and the others go on. A correction changes the
  length of the page, so the offsets of the places after it on its page are moved by the difference.
- If a place is on the page on the screen, that page is shown again with its new text.
- The box says "The token “tern” is nowhere in the edition" when there is none, and is closed with
  **Close** or Escape.

**Accepting the spellings that have a substring in common.** The **Apply spelling rule to the
edition** tool of the correction view (in the cog wheel of the sidebar) opens a big box over
the page, which accepts spellings as legal without changing the text (see "Accepting the
spellings that have a substring in common"). It has a box for the **core of the substring** (`cy`)
and, below it, a box for an **ending** (`a`); the ending boxes are inactive (greyed, with a tooltip) until the core is written; the boxes are big and rounded, their label is inside
them in grey (a placeholder), and a box has a yellowish frame while it is written in; a **+** sign adds a box for another ending (`i`,
`ami`; up to ten), and **×** drops one that was added. So that the predictable endings of a
Polish noun need not be typed each time, four buttons below the core (**Masculine**,
**Feminine**, **Neuter**, **Adjectival**; inactive until the core is written) put the endings
of every case, singular and plural, into a **frozen field of pills**. One set is chosen at a
time (another takes its place, the same again takes it away) and a pill can be removed with
its **×**, but not edited. They are kept in the frontend (`frontend/src/nounEndings.ts`), as
they are written after the stem, with the declension classes of a gender put together, so a
set has some endings that a noun does not use, and without the zero ending (the core alone is in
every word that has the core). Masculine: `a u owi em iem e ie y i owie ów om ami mi ach`.
Feminine: `a y i ie e ę ą o om ami ach`. Neuter: `o e ę um a u em iem ie i y ów om ami ach`.
Adjectival (adjectives and the surnames in `-ski`, `-cki`, `-dzki`): `i iego iemu im ich imi a iej ą`.
The pills and the boxes are searched together, each ending once, and Find is active when there is
one ending from either (up to forty are sent). The line that says what is looked for names the
first six substrings and "and N more". A line says what is looked for ("Looking
for words that have cya, cyi, cyami in them"); a box left empty is left out. **Find** (a big, round button in the middle) is inactive until the core is written and at least
one ending is given, and, like **Close** (also round), it gets the same yellowish frame as a box that is written in when the pointer is over it; it asks `GET .../spelling-chunk-matches`; if the
backend cannot look for the substring, its reason is shown. The substring can be changed and sent again.

- Each flagged word is listed **once**, with how many times it is in the edition (`×7`) and
  its first places, each **in its context**: the nearest word before it (with the signs between,
  `dom.`), the word (marked in yellow) and the nearest word after it (`; kota`), with its page; "and 5 more" says how many places are not shown. No
  correction or replacement is shown, as none is made.
- Each has an **Accept** button, which makes the word a legal spelling of the edition (`POST
  .../legal-spellings`, the request of the word's own "legal spelling" button), and a box to
  take it out of **Accept all (N)**, which accepts the words that are in, one request at a
  time, with "Accepting x of y …". All are in by default. **Include all** and **Include none**
  set the boxes. A word that the backend refuses is marked with the reason and the others go
  on.
- Accepting does not change any text. The page on the screen is checked again, so the word is
  not red there any more.
- The box says "No flagged word of the edition has this substring" when there is none, and is
  closed with **Close** or Escape.

**The same named entity in all its forms.** The **Find this NER on other pages** tool of
the NER view (in the cog wheel of the sidebar) is enabled when an entity is confirmed on
the page (marked in the XML: confirmed here, or by a person earlier); until then its
tooltip says "Confirm a named entity on this page first". It opens a big box over the page,
which asks `GET /editions/{id}/similar-entities` (see "The same named entity in all its
forms") for the entity and its label, and lists the places of the edition by page.

- If several entities are confirmed, a dropdown "Named entity" chooses which one is looked
  for; the list is made again for the other one.
- Each place is shown **in its context**: the nearest word before it (with the signs between,
  `dom.`), the name (marked in yellow), and the nearest word after it (`, kota`), so that it can be
  judged without the page. It has an
  **Approve** button (it carries `data-edition-id`, `data-page`, `data-start` and
  `data-end`) and a box to take it out of "Approve all". The sure ones are included by
  default; the others say "the model does not take it for this entity". **Include all**,
  **Include none** and **Include only the sure ones** set the boxes.
- "Forms that were looked for (N), M of them found" opens the list of the forms, each with
  its cases (`genitive sg, accusative sg`) and a box. Dropping a form hides its places and
  keeps them out of "Approve all".
- **Approve** marks that place alone: the same request as confirming an entity in the NER
  view (`POST .../entities`, with the label of the example). **Approve all (N)** does it for
  the places that are in, one request at a time, in the order of the pages, with
  "Approving x of y …". A place that the backend refuses is marked with the reason, and the
  others go on. Marking does not change the text, so the offsets of the other places stay
  right. If the place is on the page on the screen, it shows as confirmed at once.
- The box says "No other place in the edition has this name" when there is none, shows the
  backend's reason if the search fails, and is closed with **Close** or Escape.

**The OCR rubbish of the edition.** The **Find OCR rubbish in the edition** tool of the
correction view (in the cog wheel of the sidebar) opens a big box over the page, which
asks `GET /editions/{id}/rubbish-lines` (see "The OCR rubbish") and lists the lines by
page, each as it is (in a monospace face), with an **Approve** button (it carries
`data-edition-id`, `data-page`, `data-start` and `data-end`) and a box to take it out of
"Approve all". It is like the box for the same mistake in the edition, but the unit is
a whole line, and approving *removes* it.

- The sure lines are included by default; those with a correct word in them say "has a
  correct word: may be text" and are not. **Include all**, **Include none** and
  **Include only the sure ones** set the boxes.
- **Approve** removes that line alone (`POST .../line-removals`) and marks it
  "✓ Removed". **Approve all (N)** removes the included lines one request at a time, in
  the order of the pages, and shows "Removing x of y …". A line that the backend refuses
  is marked with the reason, and the others go on.
- One removal is made at a time, as it makes the page shorter: the offsets of the lines
  that follow it on its page are moved by `removed`, the number of characters that went
  (the backend says it, as it depends on the XML). Other pages are not touched.
- If the line is on the page on the screen, that page is shown again with its new text.
- The box says "No OCR rubbish found in the edition" when there is none, shows the
  backend's reason if the search fails, and is closed with **Close** or Escape.

**The look of the tools.** The boxes of the tools that find things in the edition share one
look (the class `review-styled`): the **substring** box, the **missing hyphens** panel, the
**OCR rubbish** box, the **named entity** box and the **page number** box ("Find this on other
pages"). Their buttons are **round** (Close, Fold, Approve, Approve all, Include …), and get a
**yellowish effect** (a gold border, a soft glow and a pale yellow fill) when the pointer is over
them or the keyboard is on them, as a box that is written in does; a disabled button does not.
The dropdown that chooses the named entity is a big rounded box with the same yellowish frame while
it is in use, and the check boxes are yellow-gold. The small round **+** and **×** buttons and the
**×** on a pill keep their own shape, and the buttons of a row are smaller than those of the header
and the toolbar. The styles are in `frontend/src/index.css`; they use `:where`, so they do not
outweigh a more particular rule. The boxes that find the same mistake in the edition (corrections)
are not changed.

**The tools of the sidebar.** At the bottom of the sidebar, set apart from the
buttons above it by a horizontal bar, a **cog wheel** button (tooltip "Tools")
reveals the tools of the view that is shown: each view has its own. The list closes
when a tool is chosen, with Escape, on a click anywhere else, on a second click on
the cog wheel and when the view changes. A view that has no tools (Text, XML) has
the cog wheel disabled.

| View | Tool | |
|---|---|---|
| Correction | **Find missing hyphens** | puts back the hyphens the OCR dropped at line ends (below) |
| Correction | **Bulk spelling check** | checks the spelling of the whole edition and accepts, as legal spellings, the flagged ones that are there more than three times, in families (above) |
| Correction | **Apply spelling rule to the edition** | accepts, as legal spellings, the flagged words of the whole edition that have a substring in common, such as `cy` with the endings `a`, `i`, `ami` (above) |
| Correction | **Replace a token in the edition** | finds every place of a token as it is wrong, such as `tern`, and replaces it with another, such as `tem`, place by place or all at once (above) |
| Correction | **Find OCR rubbish in the edition** | finds the lines of the whole edition that are not text, to remove them (above) |
| Structure | **Find this on other pages** | looks for the page number marked on this page on the other pages; disabled until a page number is marked (its tooltip says "Mark the page number on this page first") |
| Named entity recognition | **Find this NER on other pages** | finds a confirmed entity, in all its forms in the cases, in the whole edition; disabled until an entity is confirmed on the page (above) |

**The missing hyphens of a page.** The OCR very often drops the hyphen at the end of
a line where a word is split (`przeczy` / `tać`), a few times on a page. In the
correction view the **Find missing hyphens** tool (in the cog wheel of the sidebar, see
"The tools of the sidebar"; disabled while a request runs and on a page without
text) opens a panel docked under the text of the
page (not a box over it: the text and the scan stay in sight, and the text can be
corrected meanwhile), which asks `GET .../pages/{number}/missing-hyphens` (see "The hyphens that the OCR dropped at
the end of a line") for the page on the screen only, never the edition. It lists
each line end: the word, a `↵`, the word that starts the next line, the word
with its hyphen and the word that both halves make together (`joined: przeczytać`),
so that the human can decide, with an **Approve** button (carrying `data-edition-id`,
`data-page`, `data-start`, `data-end`) and a box to take it out of "Approve all".

- The panel is not modal. Its height is changed by dragging its lower right corner,
  **Fold** shrinks it to its header (and **Show the list** brings it back), and
  **Close** closes it (not Escape, which belongs to the text being edited). It goes
  away when another page is opened.
- A place that the mouse is over, or that has the keyboard focus, is shown in the
  text: its two words (and the sign taken for the hyphen) get a yellow marker, and
  the text scrolls to it if it is out of view; the scan follows the text.
- The places where both parts are words on their own (`do` / `mu`) say "both parts
  are words: not sure it is split" and are not included by default; the sure ones
  are. **Include all**, **Include none** and **Include only the sure ones** set the
  boxes.
- **Approve all (N)** sends the included places in one request,
  `POST .../pages/{number}/hyphens` (all or none). **Approve** on a row sends that
  place alone. The places approved are marked "✓ Approved", the page on the screen
  is shown again with its new text, and its spelling is checked again.
- After an approval the offsets of the places that follow it are moved by the
  difference in length, so they stay right for the next one.
- If the backend refuses (for example the 409 when the page has changed), the reason
  is shown: for "Approve all" no place is changed, and it can be tried again.
- The panel says "No missing hyphen found on this page" when there is none, and shows
  the backend's reason if the search fails.

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

### Login

Everything needs a login except the **main page** (the list of editions, `GET
/editions`), `GET /health` and the login itself. It is for a handful of people: the
users are put into the database by hand, and there is no registration, no password
reset and no reminder.

**The `users` table** has a `username` (the key) and a `password_hash`, and nothing
else. Only a hash of the password is stored (scrypt from the standard library, with a
salt of its own for each password: `app/persistence/passwords.py`), never the password.
To add a user, get the statement from the script (it asks for the username and the
password twice, and does not touch the database), and run what it prints in `psql`:

```
docker compose exec backend python -m app.persistence.passwords      # in Docker
python -m app.persistence.passwords                                  # or on the host
```

```
INSERT INTO users (username, password_hash) VALUES ('anna', 'scrypt$16384$8$1$...$...');
```

In **development** there is a shortcut that adds the user itself, with the connection
that the app has (the `POSTGRES_*` variables), so there is nothing to paste:

```
docker compose exec backend python -m app.persistence.passwords --insert
```

It asks the same questions, and prints `Added the user 'anna'`. It **refuses while
`PROD` is true** (the default), before it asks anything: in production run the
statement yourself. It says so if the username is taken, if there is no `users` table
(start the backend once with `PROD=false`), or if the database cannot be reached.

(`make into_db` opens `psql` in the development database.) To change a password, run
the script again and `UPDATE users SET password_hash = '...' WHERE username = 'anna'`;
to remove a user, `DELETE FROM users WHERE username = 'anna'` (a session that exists
goes on until it runs out, at most 24 hours). The table is part of the schema
(`schema.py`): a development database that has the other tables gets it when the
backend starts (`PROD=false`); in production create it by hand, and give the `tiron`
user `SELECT` on it.

**`POST /auth/login`** takes `{"username": ..., "password": ...}`. If they match
(`Database.check_user`) the answer is `{"username": ...}` and a cookie `tiron_session`:
`HttpOnly` (the scripts of the page cannot read it), `SameSite=Lax` (a request from
another site does not carry it, which is what stops a forged `POST`), `Secure` when the
browser is on HTTPS (it is, behind a proxy that sends `X-Forwarded-Proto: https`), and
good for 24 hours. Otherwise the answer is 401 `Wrong username or password`: the same
for an unknown username and for a wrong password, and it takes as long for both (an
unknown user is checked against a dummy hash). A request with any other field is 422.

The session is a token that the backend signs (`app/auth.py`): `payload.signature`,
the payload being the username and when the session ends, and the signature an
HMAC-SHA256 of it with `SESSION_SECRET`. Nothing is kept on the server, so there is no
logout on the server either (the cookie runs out; there is no logout button yet).
**`SESSION_SECRET`** must be set in production (`openssl rand -hex 32`): with
`PROD` true the backend does not start without it (`AuthNotConfigured`), because with
a guessable secret anybody could make a session. While `PROD` is false it has a fixed
default, so local development needs nothing. Changing it logs everybody out.

**Every endpoint but those three answers 401 `Login required` without a valid
session.** It is a dependency of the whole app (`require_login`), not of each
endpoint, so an endpoint that is added later is protected too; a test goes through all
the routes of the app to check it, and fails if one that is not in `PUBLIC` is open.

**The frontend**: when a request is answered with 401 (opening the address of a page
without a session, or a session that has run out while the page is open) the app shows
the **login page**: the main page's Dürer background, a card with a username, a
password and a "Log in" button, and one message if they are wrong. The address stays
as it was, so after the login the same page is opened. The main page needs no login.
In development (`npm run dev` or `docker compose up`) `/auth` is proxied to the
backend like the other routes, so the cookie works there too.

Not done: there is no limit on how often a password may be tried (scrypt makes each
try cost the attacker about 50 ms, but that is all), so use long passwords and, in
production, keep the VM behind a firewall as well.

### The schema of the database

The SQL of the schema is in `app/persistence/schema.py` (`SCHEMA`, and `TABLES`, the
names of the tables). `Database` does not run it.

**`PROD`** is a flag for the whole app (`app/config.py`, `is_prod()`), read from the
environment, and **true if it is not set**. Only `false`, `0`, `no` or `off` make it
false; any other value, an empty or a misspelled one too, is true, so that a mistake
leaves the database protected. While `PROD` is true the app does not change the
schema of the database: in production the database user (`tiron`) has no right to
do that, and the schema is created beforehand, by hand, from `schema.py`.

**`SchemaCreator`** (`app/persistence/schema_creator.py`) has two methods:

- `schema_exists()` is true if every table of the schema is in the database (the
  tables only, not their columns);
- `create_schema()` runs the schema (`CREATE ... IF NOT EXISTS`, so what is there is
  left as it is). If `PROD` is true it raises `SchemaCreationForbidden` and does
  nothing.

**When the backend starts** it is the first thing it does, before the models are
loaded (`ensure_schema` in the lifespan of `app/main.py`):

| the schema is there | `PROD` | what happens |
|---|---|---|
| yes | any | nothing is changed |
| no | false | it is created (and checked), and the backend starts |
| no | true | the backend stops with `SchemaMissing`, and says why: it may not create it, and cannot work without it |

The Celery worker does not look at the schema: its tasks are sent by the backend,
which has started by then. `docker compose up` sets `PROD=false` for the backend and
the worker (their own database is empty at first); a `PROD` in `.env` overrides it.
For `make run` against an empty local database set `PROD=false` in `.env`. The tests
that need the tables (`INTEGRATION_DB=1`) create them with `PROD=false`. They also
empty the tables, so they **refuse to run (fail, not skip) unless `POSTGRES_HOST` is
local** (`localhost`, `127.0.0.1`, `::1`, or a socket directory) **and `POSTGRES_DB`
has "test" in its name** (`refuse_remote_database` in `tests/conftest.py`). The
second keeps them off the development database, which is local too (it is `tiron`).
CI's database is `test`.

### The database connection

The backend and the Celery worker read the connection from the environment
(`app/persistence/db.py`): `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`,
`POSTGRES_HOST` (default `localhost`) and `POSTGRES_PORT` (default `5432`). For a
database that is not in Docker, such as Cloud SQL, there are two optional ones:
`POSTGRES_SSLMODE` (`require`, `verify-ca` or `verify-full`; unset, libpq's default
`prefer` is used) and `POSTGRES_SSLROOTCERT` (the CA certificate for the last two).
They are only in the connection string if they are set. The Cloud SQL Auth Proxy
encrypts by itself, so it needs neither.

A connection attempt gives up after 10 seconds, and TCP keepalives are on, so that a
connection that a firewall dropped silently is noticed.

**The connection is opened again when it is lost.** A database in the cloud drops
connections: maintenance, a failover, an idle one that the network let go. The
backend has one long-lived connection, so without this every request would fail
until it was restarted. `ReconnectingConnection` is the `db.conn` of `Database`:

- a connection that is found closed or broken before a statement is opened again
  first;
- a statement that finds the connection lost is sent again on a new one (once), but
  not inside a transaction, where the rest of the transaction went with the
  connection: the error is raised, and the next statement opens a new connection;
- an error of a connection that is alive (a constraint, a cancelled query) is raised
  as it is. A warning is logged each time the connection is opened again.

A statement that is sent again may have been done already, if the connection went
down after the database did it and before the answer came back. That is rare, and
the statements are single ones.

**`GET /health`** asks the database `SELECT 1` (`Database.is_reachable`) and answers
`{"status": "ok", "database": "ok"}`, or 503 `The database is not reachable` (the
reason is in the log). It is for a load balancer, an uptime check, or a look after a
deploy. It does not wait for the spaCy model or the dictionary.

### Logging

Every module logs with `logging.getLogger(__name__)` (`app.main`, `app.text_tools.ner`,
...), and `setup_logging()` in [app/logs.py](app/logs.py) puts one handler on
the `app` logger, writing to stderr, so these records show next to uvicorn's
own. The level is `INFO`, or the one in the `LOG_LEVEL` environment variable
(`DEBUG`, `INFO`, `WARNING`, `ERROR`; see `.env.example`; `docker compose`
passes it on). `INFO` has the start-up (how long the model and the dictionary
took to load), the pages fetched from Polona, and each change made to a page
(a correction, a removed token, a tagged entity, a marked section, the ready
flag, a legal spelling). `WARNING` has what the backend refused (a page that
changed, XML that does not allow the change, Polona failures) and `ERROR` the
model or dictionary that is not available. `DEBUG` adds the requests to Polona
and the sizes of the spelling and NER results. The text of the pages is never
logged: only ids, offsets and counts.

### When Polona fails

`app/polona/polona_connector.py` turns every failure into one exception,
`PolonaError`, and the page endpoint (`fetch_page` in `app/main.py`) maps it to an
HTTP error. No page is saved when
Polona fails (a new edition may be: it is saved before its page is fetched).

| Situation | Response |
|---|---|
| Polona has no such edition (its metadata is HTTP 404) | 404 `Edition not found` |
| Polona has no such page of a known edition (out of range, no scans, HTTP 404) | 404 `Page not found` |
| Polona is unreachable, times out, returns an HTTP error, or sends an invalid response | 502 |

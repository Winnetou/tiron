"""The SQL of the database schema, and the names of its tables.

It is only ever run by `SchemaCreator` (`schema_creator.py`), and that only when the
`PROD` flag is false: the database of a production deployment is created by hand,
and its user is not allowed to change it.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS works (
    id uuid PRIMARY KEY,
    author text,
    title text
);

CREATE TABLE IF NOT EXISTS editions (
    id uuid PRIMARY KEY,
    work_id uuid NOT NULL REFERENCES works (id),
    place text,
    year integer CHECK (year BETWEEN 1500 AND 2030),
    fetched_completed boolean NOT NULL DEFAULT false,
    page_count integer CHECK (page_count >= 1)
);


CREATE TABLE IF NOT EXISTS pages (
    id uuid PRIMARY KEY,
    edition_id uuid NOT NULL REFERENCES editions (id),
    number integer NOT NULL CHECK (number >= 0),
    raw_ocr_content text NOT NULL,
    xml_content_corrected text,
    scan_uri text,
    ready boolean NOT NULL DEFAULT false,
    UNIQUE (edition_id, number)
);

CREATE TABLE IF NOT EXISTS corrections (
    id uuid PRIMARY KEY,
    page_id uuid NOT NULL REFERENCES pages (id),
    "before" text NOT NULL CHECK ("before" <> ''),
    "after" text NOT NULL CHECK ("after" <> '')
);

CREATE TABLE IF NOT EXISTS collection (
    id uuid PRIMARY KEY
);

-- spellings that are legal in an edition; each edition has each spelling once
CREATE TABLE IF NOT EXISTS legal_spellings (
    edition_id uuid NOT NULL REFERENCES editions (id),
    spelling text NOT NULL CHECK (spelling <> ''),
    PRIMARY KEY (edition_id, spelling)
);

-- a ready edition from outside (a public-domain classic from wolnelektury.pl): it is
-- not corrected here, it is only kept for the spellings it gives
CREATE TABLE IF NOT EXISTS external_edition (
    id uuid PRIMARY KEY,
    isbn text CHECK (isbn <> ''),
    author text NOT NULL CHECK (author <> ''),
    title text NOT NULL CHECK (title <> ''),
    year integer NOT NULL CHECK (year BETWEEN 1500 AND 2030)
);

-- spellings that are legal in an external edition, like legal_spellings; each
-- external edition has each spelling once
CREATE TABLE IF NOT EXISTS external_edition_spelling (
    external_edition_id uuid NOT NULL REFERENCES external_edition (id),
    spelling text NOT NULL CHECK (spelling <> ''),
    PRIMARY KEY (external_edition_id, spelling)
);

-- the people who may log in; they are added by hand (see passwords.py), there is no
-- registration. Only a hash of the password is kept.
CREATE TABLE IF NOT EXISTS users (
    username text PRIMARY KEY CHECK (username <> ''),
    password_hash text NOT NULL CHECK (password_hash <> '')
);

-- one work per (author, title), case-insensitive; unknown values never match
CREATE UNIQUE INDEX IF NOT EXISTS works_author_title_key
    ON works (lower(author), lower(title))
    WHERE author IS NOT NULL AND title IS NOT NULL;
"""

# The tables that the schema creates: a database has the schema if it has them all.
TABLES = (
    "works",
    "editions",
    "pages",
    "corrections",
    "collection",
    "legal_spellings",
    "external_edition",
    "external_edition_spelling",
    "users",
)

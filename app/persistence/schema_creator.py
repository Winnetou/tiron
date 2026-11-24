"""Creating the schema of the database, where that is allowed."""

import logging
from typing import TYPE_CHECKING

from app.config import is_prod
from app.persistence.schema import SCHEMA, TABLES

if TYPE_CHECKING:
    from app.persistence.db import Database

logger = logging.getLogger(__name__)


class SchemaCreationForbidden(RuntimeError):
    """The schema was to be created while `PROD` is true."""


class SchemaMissing(RuntimeError):
    """The database does not have the schema, and (`PROD` is true) it is not
    created by the app."""


class SchemaCreator:
    """Looks whether the database has the schema, and creates it when it is not a
    production one (`PROD` is false).

    In production the database user has no right to change the schema, so the
    schema is made there beforehand, by hand, and this class only looks.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    def schema_exists(self) -> bool:
        """Whether every table of the schema is in the database. Only the tables
        are looked for, not their columns."""
        for table in TABLES:
            row = self.db.conn.execute(
                "SELECT to_regclass(%s) IS NOT NULL AS found", (table,)
            ).fetchone()
            if not row or not row["found"]:
                logger.info("The table %s is not in the database", table)
                return False
        return True

    def create_schema(self) -> None:
        """Create the schema (what is there already is left as it is).

        Raises SchemaCreationForbidden, and does nothing, if `PROD` is true.
        """
        if is_prod():
            raise SchemaCreationForbidden(
                "The schema is not created while PROD is true "
                "(set PROD=false for a local database)"
            )
        self.db.conn.execute(SCHEMA)


def ensure_schema(db: Database) -> None:
    """What the app does when it starts: look for the schema, and create it if it
    is not there and `PROD` is false.

    Raises SchemaMissing if it is not there and `PROD` is true (the app cannot work
    without it, and is not allowed to make it: better to stop at once, with the
    reason, than to answer every request with an error).
    """
    creator = SchemaCreator(db)
    if creator.schema_exists():
        logger.info("The database has the schema")
        return
    if is_prod():
        raise SchemaMissing(
            "The database does not have the schema, and PROD is true, so it is not "
            "created: create it first (app/persistence/schema.py)"
        )
    logger.info("The database does not have the schema: creating it (PROD is false)")
    creator.create_schema()
    if not creator.schema_exists():
        raise SchemaMissing("The schema was created, but its tables are not there")
    logger.info("The schema is created")

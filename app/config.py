"""Settings that are read from the environment, for the whole app."""

import os

_FALSE = {"false", "0", "no", "off"}


def is_prod() -> bool:
    """Whether this is a production deployment: the `PROD` environment variable,
    true if it is not set.

    Only `false`, `0`, `no` or `off` (in any case) make it false; anything else,
    including an empty or a misspelled value, is true, so that a mistake leaves the
    database protected and not open to changes. The app does not change the schema
    of the database in production.
    """
    return os.environ.get("PROD", "true").strip().lower() not in _FALSE

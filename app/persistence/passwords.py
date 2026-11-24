"""Passwords: how they are stored, and how one is checked against what is stored.

Only a hash of a password is in the database (`users.password_hash`), made with scrypt
from the standard library (a salt of its own for each password), never the password.

The users are put in the database by hand, there is no registration. To get the line
for one, run (in the backend container, or wherever the app is installed; it does not
touch the database):

    python -m app.persistence.passwords

It asks for the username and the password, and prints the INSERT statement to run in
psql (it does not touch the database: that is how it is used for production, where
someone with the right to do it runs the statement).

For a development database there is a shortcut, which adds the user itself, with the
connection that the app has (the POSTGRES_* variables):

    python -m app.persistence.passwords --insert

It refuses to do that while PROD is true.
"""

import argparse
import base64
import getpass
import hashlib
import hmac
import secrets
import sys

import psycopg

from app.config import is_prod

_N, _R, _P = 2**14, 8, 1  # scrypt's cost: about 50 ms and 16 MB for a check
_SALT_BYTES = 16
_KEY_BYTES = 32
_PREFIX = "scrypt"


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode(), salt=salt, n=n, r=r, p=p, dklen=_KEY_BYTES, maxmem=2**26
    )


def hash_password(password: str) -> str:
    """The text that is stored for a password: `scrypt$n$r$p$salt$key` (the salt and
    the key in base64). Each call has a new salt, so the same password never has the
    same hash twice."""
    salt = secrets.token_bytes(_SALT_BYTES)
    key = _scrypt(password, salt, _N, _R, _P)
    return "$".join(
        (
            _PREFIX,
            str(_N),
            str(_R),
            str(_P),
            base64.b64encode(salt).decode(),
            base64.b64encode(key).decode(),
        )
    )


def verify_password(password: str, stored: str) -> bool:
    """Whether the password is the one that `stored` (from `hash_password`) was made
    from. A stored text that is not a hash of ours is False, not an error. The keys
    are compared in constant time."""
    try:
        prefix, n, r, p, salt, key = stored.split("$")
        if prefix != _PREFIX:
            return False
        expected = base64.b64decode(key, validate=True)
        actual = _scrypt(
            password,
            base64.b64decode(salt, validate=True),
            int(n),
            int(r),
            int(p),
        )
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


# A hash to check a password against when the user does not exist, so that an unknown
# username takes as long to answer as a wrong password does.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def insert_statement(username: str, password: str) -> str:
    """The INSERT statement that adds a user (the quotes of the username doubled)."""
    quoted = username.replace("'", "''")
    return (
        "INSERT INTO users (username, password_hash) "
        f"VALUES ('{quoted}', '{hash_password(password)}');"
    )


def insert_user(username: str, password: str) -> None:
    """Add the user to the database that the POSTGRES_* variables name. Exits with a
    message if that is not allowed (PROD is true), the username is taken, or the
    database cannot be used."""
    from app.persistence.db import Database, UserExists  # not at the top: db imports us

    refuse_in_production()
    try:
        db = Database()
    except psycopg.Error as exc:
        raise SystemExit(f"Cannot connect to the database: {exc}") from exc
    try:
        db.create_user(username, password)
    except UserExists as exc:
        raise SystemExit(
            f"{exc}. To change the password, run the script without --insert and "
            "UPDATE users SET password_hash = ... WHERE username = ..."
        ) from exc
    except psycopg.errors.UndefinedTable as exc:
        raise SystemExit(
            "There is no users table: start the backend once with PROD=false, "
            "which creates it"
        ) from exc
    finally:
        db.close()


def refuse_in_production() -> None:
    if is_prod():
        raise SystemExit(
            "Refusing to change the database: PROD is true (it is, unless PROD=false "
            "is set). Run without --insert and run the statement it prints yourself."
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.persistence.passwords",
        description="Make the INSERT statement that adds a user.",
    )
    parser.add_argument(
        "--insert",
        action="store_true",
        help="add the user to the database itself (only while PROD is false)",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.insert:
        refuse_in_production()  # before asking anything
    username = input("Username: ").strip()
    if not username:
        raise SystemExit("The username is empty")
    password = getpass.getpass("Password: ")
    if not password:
        raise SystemExit("The password is empty")
    if getpass.getpass("Password again: ") != password:
        raise SystemExit("The passwords differ")
    if args.insert:
        insert_user(username, password)
        print(f"Added the user {username!r}")
        return
    print()
    print(insert_statement(username, password))


if __name__ == "__main__":
    main()

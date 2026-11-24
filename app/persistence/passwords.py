"""Passwords: how they are stored, and how one is checked against what is stored.

Only a hash of a password is in the database (`users.password_hash`), made with scrypt
from the standard library (a salt of its own for each password), never the password.

The users are put in the database by hand, there is no registration. To get the line
for one, run (in the backend container, or wherever the app is installed; it does not
touch the database):

    python -m app.persistence.passwords

It asks for the username and the password, and prints the INSERT statement to run in
psql.
"""

import base64
import getpass
import hashlib
import hmac
import secrets

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


def main() -> None:
    username = input("Username: ").strip()
    if not username:
        raise SystemExit("The username is empty")
    password = getpass.getpass("Password: ")
    if not password:
        raise SystemExit("The password is empty")
    if getpass.getpass("Password again: ") != password:
        raise SystemExit("The passwords differ")
    print()
    print(insert_statement(username, password))


if __name__ == "__main__":
    main()

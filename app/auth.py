"""Login: the session that a user has after they gave the right username and password.

The session is a cookie that the backend signs, so nothing is kept on the server (and
nothing in the database): `payload.signature`, where the payload is the username and
when the session ends, and the signature is an HMAC-SHA256 of it with the secret
`SESSION_SECRET`. A cookie that was changed, was signed with another secret, or has
run out is no session.

Every endpoint needs a session (`require_login`, a dependency of the whole app: an
endpoint that is added later is protected too), except the ones in `PUBLIC`.
"""

import base64
import hashlib
import hmac
import json
import os
import time

from fastapi import HTTPException, Request, status

from app.config import is_prod

COOKIE_NAME = "tiron_session"
SESSION_SECONDS = 24 * 60 * 60

# The endpoints that need no login: the list of the editions (the main page) and its
# search, the check that the backend works, and the login itself.
PUBLIC = {
    ("GET", "/editions"),
    ("GET", "/editions/search"),
    ("GET", "/health"),
    ("POST", "/auth/login"),
}

# What the secret is when SESSION_SECRET is not set and PROD is false (development,
# tests). Anyone can read it here: never in production, where the backend does not
# start without a SESSION_SECRET.
_DEV_SECRET = "development-only-secret"


class AuthNotConfigured(RuntimeError):
    """`SESSION_SECRET` is not set while `PROD` is true."""


def session_secret() -> bytes:
    """The secret that the sessions are signed with: `SESSION_SECRET`. If it is not
    set, a fixed one in development (`PROD` false), and AuthNotConfigured in
    production, where the sessions would be forgeable."""
    secret = os.environ.get("SESSION_SECRET", "").strip()
    if secret:
        return secret.encode()
    if is_prod():
        raise AuthNotConfigured(
            "SESSION_SECRET is not set (needed while PROD is true): "
            "make one with `openssl rand -hex 32`"
        )
    return _DEV_SECRET.encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: str) -> str:
    return _b64(hmac.new(session_secret(), payload.encode(), hashlib.sha256).digest())


def create_token(username: str, *, now: float | None = None) -> str:
    """The value of the session cookie of a user who has just logged in."""
    ends = int((time.time() if now is None else now) + SESSION_SECONDS)
    payload = _b64(json.dumps({"u": username, "e": ends}).encode())
    return f"{payload}.{_sign(payload)}"


def read_token(token: str, *, now: float | None = None) -> str | None:
    """The username of a session cookie, or None if it is not a valid one: not
    signed with our secret, changed, malformed, or run out."""
    payload, dot, signature = token.partition(".")
    # compared as bytes: compare_digest refuses a str that is not ASCII (a cookie can
    # be anything)
    if not dot or not hmac.compare_digest(signature.encode(), _sign(payload).encode()):
        return None
    try:
        data = json.loads(_unb64(payload))
        username, ends = data["u"], data["e"]
    except ValueError, KeyError, TypeError:
        return None
    if not isinstance(username, str) or not isinstance(ends, int | float):
        return None
    if ends <= (time.time() if now is None else now):
        return None
    return username


def is_https(request: Request) -> bool:
    """Whether the browser is on HTTPS: the request, or the proxy in front of the
    backend (Caddy) says so. It decides the `Secure` flag of the cookie."""
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    return request.url.scheme == "https" or forwarded == "https"


def require_login(request: Request) -> str:
    """A dependency of the whole app: the username of the session, or 401 (`Login
    required`) if there is none. The endpoints in `PUBLIC` need no session ("" is
    then the user)."""
    route = request.scope.get("route")
    path = getattr(route, "path", request.url.path)
    if (request.method, path) in PUBLIC:
        return ""
    token = request.cookies.get(COOKIE_NAME)
    username = read_token(token) if token else None
    if username is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Login required")
    return username

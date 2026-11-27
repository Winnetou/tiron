from unittest import mock

import pytest
from fastapi.testclient import TestClient
from fastapi_pagination import paginate

from app import auth
from app.auth import (
    COOKIE_NAME,
    PUBLIC,
    SESSION_SECONDS,
    AuthNotConfigured,
    create_token,
    read_token,
    session_secret,
)
from app.main import app, db

client = TestClient(app, base_url="http://testserver")


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "a-secret-for-the-tests")


# --- the secret ---


def test_the_secret_is_the_environment_variable(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "  abc  ")

    assert session_secret() == b"abc"


def test_without_a_secret_development_has_a_fixed_one(monkeypatch):
    monkeypatch.delenv("SESSION_SECRET")
    monkeypatch.setenv("PROD", "false")

    assert session_secret() == b"development-only-secret"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_without_a_secret_production_has_none(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("SESSION_SECRET")
    else:
        monkeypatch.setenv("SESSION_SECRET", value)
    monkeypatch.setenv("PROD", "true")

    with pytest.raises(AuthNotConfigured, match="SESSION_SECRET"):
        session_secret()


def test_production_is_the_default(monkeypatch):
    monkeypatch.delenv("SESSION_SECRET")
    monkeypatch.delenv("PROD", raising=False)

    with pytest.raises(AuthNotConfigured):
        session_secret()


# --- the token ---


def test_a_token_gives_back_the_username():
    assert read_token(create_token("anna")) == "anna"


def test_a_username_with_any_characters_survives():
    assert read_token(create_token('zażółć.o\'brien "x"')) == 'zażółć.o\'brien "x"'


def test_a_token_runs_out():
    token = create_token("anna", now=1_000)

    assert read_token(token, now=1_000 + SESSION_SECONDS - 1) == "anna"
    assert read_token(token, now=1_000 + SESSION_SECONDS) is None
    assert read_token(token, now=1_000 + SESSION_SECONDS + 1) is None


def test_a_token_of_another_secret_is_refused(monkeypatch):
    token = create_token("anna")
    monkeypatch.setenv("SESSION_SECRET", "another-secret")

    assert read_token(token) is None


def test_a_changed_token_is_refused():
    payload, signature = create_token("anna").split(".")
    forged = auth._b64(b'{"u": "root", "e": 99999999999}')

    assert read_token(f"{forged}.{signature}") is None
    assert read_token(f"{payload}.{signature[:-2]}AA") is None


@pytest.mark.parametrize(
    "token", ["", ".", "abc", "abc.def", "a.b.c", "....", "e30.e30", "ü.ü"]
)
def test_a_malformed_token_is_refused(token):
    assert read_token(token) is None


def test_a_signed_payload_that_is_not_a_session_is_refused():
    for payload in (b"[]", b'{"u": 1, "e": 99999999999}', b'{"u": "a"}', b"null"):
        encoded = auth._b64(payload)
        assert read_token(f"{encoded}.{auth._sign(encoded)}") is None


# --- the login ---


def login(username="anna", password="secret", **kwargs):
    return client.post(
        "/auth/login", json={"username": username, "password": password}, **kwargs
    )


@pytest.fixture
def users(monkeypatch):
    check = mock.Mock(side_effect=lambda name, pw: (name, pw) == ("anna", "secret"))
    monkeypatch.setattr(db, "check_user", check)
    return check


class TestLogin:
    def test_the_right_username_and_password_log_in(self, users):
        response = login()

        assert response.status_code == 200
        assert response.json() == {"username": "anna"}
        users.assert_called_once_with("anna", "secret")

    def test_the_cookie_is_a_session_that_is_kept_from_scripts(self, users):
        cookie = login().headers["set-cookie"]

        assert read_token(cookie.split(";")[0].split("=", 1)[1]) == "anna"
        assert cookie.startswith(f"{COOKIE_NAME}=")
        assert "HttpOnly" in cookie
        assert "SameSite=lax" in cookie
        assert f"Max-Age={SESSION_SECONDS}" in cookie
        assert "Path=/" in cookie
        assert "Secure" not in cookie  # plain HTTP: the browser would not send it

    def test_the_cookie_is_secure_behind_a_proxy_that_does_https(self, users):
        cookie = login(headers={"X-Forwarded-Proto": "https"}).headers["set-cookie"]

        assert "Secure" in cookie

    def test_the_cookie_is_secure_on_https(self, users):
        https = TestClient(app, base_url="https://testserver")

        response = https.post(
            "/auth/login", json={"username": "anna", "password": "secret"}
        )

        assert "Secure" in response.headers["set-cookie"]

    def test_a_wrong_password_is_401_without_a_cookie(self, users):
        response = login(password="wrong")

        assert response.status_code == 401
        assert response.json()["detail"] == "Wrong username or password"
        assert "set-cookie" not in response.headers

    def test_an_unknown_user_gets_the_same_answer(self, users):
        unknown = login(username="nobody")
        wrong = login(password="wrong")

        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json() == wrong.json()

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"username": "anna"},
            {"password": "secret"},
            {"username": "", "password": "secret"},
            {"username": "anna", "password": ""},
            {"username": "anna", "password": "secret", "admin": True},
            {"username": 1, "password": "secret"},
        ],
    )
    def test_a_bad_request_is_422_and_checks_nothing(self, users, body):
        assert client.post("/auth/login", json=body).status_code == 422
        users.assert_not_called()

    def test_the_session_opens_the_endpoints(self, users, monkeypatch):
        monkeypatch.setattr(db, "get_edition_metadata", lambda _id: None)
        assert client.get(f"/editions/{auth_id()}/metadata").status_code == 401

        login()  # the client keeps the cookie

        # 404, not 401: it got through the login, to the endpoint
        assert client.get(f"/editions/{auth_id()}/metadata").status_code == 404
        client.cookies.clear()


def auth_id():
    return "0003374b-70d9-406c-aae3-570acb20ee1d"


# --- what needs a login ---


def endpoints():
    for route in app.routes:
        for method in getattr(route, "methods", None) or ():
            path = getattr(route, "path", "")
            if path in ("/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"):
                continue
            yield method, path


def concrete(path):
    return (
        path.replace("{edition_id}", auth_id())
        .replace("{number}", "0")
        .replace("{page_id}", auth_id())
        .replace("{spelling}", "x")
    )


def test_there_are_endpoints_to_check():
    assert len(list(endpoints())) > 25


def test_every_endpoint_but_the_public_ones_needs_a_login():
    protected = [(m, p) for m, p in endpoints() if (m, p) not in PUBLIC]

    for method, path in protected:
        response = client.request(method, concrete(path), json={})
        assert response.status_code == 401, (method, path)
        assert response.json()["detail"] == "Login required", (method, path)


def test_the_public_endpoints_are_only_the_main_page_its_search_health_and_login():
    assert {
        ("GET", "/editions"),
        ("GET", "/editions/search"),
        ("GET", "/health"),
        ("POST", "/auth/login"),
    } == PUBLIC
    assert set(endpoints()) >= PUBLIC  # and they exist (no stale entry)


def test_the_list_of_editions_needs_no_login(monkeypatch):
    monkeypatch.setattr(db, "list_collection", lambda: paginate([]))

    assert client.get("/editions").status_code == 200


def test_the_search_of_editions_needs_no_login(monkeypatch):
    monkeypatch.setattr(
        db, "search_collection", lambda author=None, title=None: paginate([])
    )

    assert client.get("/editions/search", params={"author": "Prus"}).status_code == 200


def test_health_needs_no_login(monkeypatch):
    monkeypatch.setattr(db, "is_reachable", lambda: True)

    assert client.get("/health").status_code == 200


def with_cookie(token):
    """A client of its own that has this session cookie."""
    own = TestClient(app, base_url="http://testserver")
    own.cookies.set(COOKIE_NAME, token)
    return own


@pytest.mark.parametrize(
    "token",
    [
        "garbage",
        "",
        "run out long ago",
    ],
)
def test_a_bad_or_old_session_is_no_login(token):
    if token == "run out long ago":
        token = create_token("anna", now=0)

    response = with_cookie(token).get(f"/editions/{auth_id()}/metadata")

    assert response.status_code == 401


def test_a_session_of_another_secret_is_no_login(monkeypatch):
    token = create_token("anna")
    monkeypatch.setenv("SESSION_SECRET", "another")

    response = with_cookie(token).get(f"/editions/{auth_id()}/metadata")

    assert response.status_code == 401


def test_a_login_in_a_header_is_not_a_session():
    response = client.get(
        f"/editions/{auth_id()}/metadata", headers={"Authorization": "Bearer x"}
    )

    assert response.status_code == 401


# --- the start of the backend ---


def test_the_backend_does_not_start_in_production_without_a_secret(monkeypatch):
    monkeypatch.delenv("SESSION_SECRET")
    monkeypatch.setenv("PROD", "true")
    monkeypatch.setattr("app.main.ensure_schema", mock.Mock())
    monkeypatch.setattr("app.main.get_nlp", mock.Mock())
    monkeypatch.setattr("app.main.get_checker", mock.Mock())

    with pytest.raises(AuthNotConfigured), TestClient(app):
        pass

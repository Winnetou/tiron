from unittest import mock

import psycopg
import pytest

from app.persistence import passwords
from app.persistence.db import Database
from app.persistence.passwords import hash_password, insert_statement, verify_password


def test_a_password_matches_its_hash():
    assert verify_password("correct horse", hash_password("correct horse")) is True


def test_another_password_does_not():
    assert verify_password("battery staple", hash_password("correct horse")) is False
    assert verify_password("", hash_password("correct horse")) is False


def test_the_hash_does_not_contain_the_password_and_has_a_salt_of_its_own():
    first, second = hash_password("correct horse"), hash_password("correct horse")

    assert "correct horse" not in first
    assert first != second
    assert verify_password("correct horse", second)
    assert first.startswith("scrypt$")


def test_unicode_passwords_work():
    assert verify_password("zażółć gęślą jaźń", hash_password("zażółć gęślą jaźń"))


@pytest.mark.parametrize(
    "stored",
    ["", "plain text", "scrypt$1$2", "bcrypt$16384$8$1$AA==$AA==", "a$b$c$d$e$f"],
)
def test_something_that_is_not_our_hash_matches_nothing(stored):
    assert verify_password("anything", stored) is False


def test_a_stored_hash_that_is_changed_does_not_match():
    stored = hash_password("correct horse")
    prefix, n, r, p, salt, key = stored.split("$")
    changed = "$".join((prefix, n, r, p, salt, "A" + key[1:]))

    assert verify_password("correct horse", changed) is False


def test_the_insert_statement_has_the_username_and_a_hash_that_matches():
    statement = insert_statement("anna", "secret")

    assert statement.startswith(
        "INSERT INTO users (username, password_hash) VALUES ('anna', 'scrypt$"
    )
    stored = statement.split("'")[3]
    assert verify_password("secret", stored)
    assert "secret" not in statement


def test_the_insert_statement_doubles_the_quotes_of_the_username():
    statement = insert_statement("o'brien", "secret")

    assert "VALUES ('o''brien', " in statement


def test_the_script_asks_and_prints_the_statement(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt: " anna ")
    monkeypatch.setattr(passwords.getpass, "getpass", lambda prompt: "secret")

    passwords.main([])

    assert "VALUES ('anna', 'scrypt$" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("username", "answers", "message"),
    [
        ("", ["x", "x"], "username is empty"),
        ("anna", ["", ""], "password is empty"),
        ("anna", ["one", "two"], "differ"),
    ],
)
def test_the_script_refuses_bad_input(monkeypatch, username, answers, message):
    monkeypatch.setattr("builtins.input", lambda prompt: username)
    given = iter(answers)
    monkeypatch.setattr(passwords.getpass, "getpass", lambda prompt: next(given))

    with pytest.raises(SystemExit, match=message):
        passwords.main([])


class TestCheckUser:
    @pytest.fixture
    def db(self, monkeypatch):
        monkeypatch.setattr("app.persistence.db.psycopg.connect", mock.MagicMock())
        return Database()

    def stored(self, db, row):
        db.conn.execute = mock.Mock(
            return_value=mock.Mock(**{"fetchone.return_value": row})
        )

    def test_the_right_password(self, db):
        self.stored(db, {"password_hash": hash_password("secret")})

        assert db.check_user("anna", "secret") is True
        query, params = db.conn.execute.call_args.args
        assert "FROM users WHERE username = %s" in query
        assert params == ("anna",)

    def test_a_wrong_password(self, db):
        self.stored(db, {"password_hash": hash_password("secret")})

        assert db.check_user("anna", "other") is False

    def test_an_unknown_user_is_checked_against_a_dummy_hash(self, db, monkeypatch):
        self.stored(db, None)
        checked = []
        real = verify_password

        def verify(password, stored):
            checked.append(stored)
            return real(password, stored)

        monkeypatch.setattr("app.persistence.db.verify_password", verify)

        assert db.check_user("nobody", "secret") is False
        assert checked == [passwords.DUMMY_HASH]  # the same work as for a user

    def test_an_unknown_user_never_passes_even_with_the_dummy_password(self, db):
        self.stored(db, None)

        assert db.check_user("nobody", "") is False


# --- --insert ---


@pytest.fixture
def typed(monkeypatch):
    """The user types this username and password (twice)."""
    monkeypatch.setattr("builtins.input", lambda prompt: "anna")
    monkeypatch.setattr(passwords.getpass, "getpass", lambda prompt: "secret")


@pytest.fixture
def database(monkeypatch):
    """The Database that --insert uses: a mock, made once and handed out."""
    made = mock.MagicMock()
    monkeypatch.setattr("app.persistence.db.Database", mock.Mock(return_value=made))
    return made


def test_insert_adds_the_user_to_the_database(monkeypatch, typed, database, capsys):
    monkeypatch.setenv("PROD", "false")

    passwords.main(["--insert"])

    database.create_user.assert_called_once_with("anna", "secret")
    database.close.assert_called_once()
    out = capsys.readouterr().out
    assert "Added the user 'anna'" in out
    assert "INSERT" not in out and "secret" not in out


@pytest.mark.parametrize("prod", [None, "true", "yes"])
def test_insert_is_refused_in_production_before_anything_is_asked(
    monkeypatch, database, prod
):
    if prod is None:
        monkeypatch.delenv("PROD", raising=False)
    else:
        monkeypatch.setenv("PROD", prod)
    asked = mock.Mock()
    monkeypatch.setattr("builtins.input", asked)

    with pytest.raises(SystemExit, match="PROD is true"):
        passwords.main(["--insert"])

    asked.assert_not_called()
    database.create_user.assert_not_called()


def test_insert_says_so_when_the_username_is_taken(monkeypatch, typed, database):
    from app.persistence.db import UserExists

    monkeypatch.setenv("PROD", "false")
    database.create_user.side_effect = UserExists("There is a user 'anna' already")

    with pytest.raises(SystemExit, match="already.*UPDATE users"):
        passwords.main(["--insert"])

    database.close.assert_called_once()


def test_insert_says_so_when_there_is_no_users_table(monkeypatch, typed, database):
    monkeypatch.setenv("PROD", "false")
    database.create_user.side_effect = psycopg.errors.UndefinedTable("no table")

    with pytest.raises(SystemExit, match="no users table"):
        passwords.main(["--insert"])


def test_insert_says_so_when_the_database_cannot_be_reached(monkeypatch, typed):
    monkeypatch.setenv("PROD", "false")
    monkeypatch.setattr(
        "app.persistence.db.Database",
        mock.Mock(side_effect=psycopg.OperationalError("connection refused")),
    )

    with pytest.raises(SystemExit, match="Cannot connect.*refused"):
        passwords.main(["--insert"])


def test_insert_checks_the_input_like_the_statement_does(monkeypatch, database):
    monkeypatch.setenv("PROD", "false")
    monkeypatch.setattr("builtins.input", lambda prompt: "anna")
    given = iter(["one", "two"])
    monkeypatch.setattr(passwords.getpass, "getpass", lambda prompt: next(given))

    with pytest.raises(SystemExit, match="differ"):
        passwords.main(["--insert"])

    database.create_user.assert_not_called()


def test_without_insert_nothing_is_written(monkeypatch, typed, database, capsys):
    monkeypatch.setenv("PROD", "false")

    passwords.main([])

    database.create_user.assert_not_called()
    assert "INSERT INTO users" in capsys.readouterr().out


def test_create_user_stores_a_hash_of_the_password(monkeypatch):
    monkeypatch.setattr("app.persistence.db.psycopg.connect", mock.MagicMock())
    db = Database()
    db.conn.execute = mock.Mock()

    db.create_user("anna", "secret")

    query, params = db.conn.execute.call_args.args
    assert query.startswith("INSERT INTO users")
    assert params[0] == "anna"
    assert params[1] != "secret"
    assert verify_password("secret", params[1])


def test_create_user_raises_user_exists_for_a_taken_username(monkeypatch):
    from app.persistence.db import UserExists

    monkeypatch.setattr("app.persistence.db.psycopg.connect", mock.MagicMock())
    db = Database()
    db.conn.execute = mock.Mock(side_effect=psycopg.errors.UniqueViolation("dup"))

    with pytest.raises(UserExists, match="anna"):
        db.create_user("anna", "secret")

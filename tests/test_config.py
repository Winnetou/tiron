import pytest

from app.config import is_prod


def test_prod_is_true_by_default(monkeypatch):
    monkeypatch.delenv("PROD", raising=False)

    assert is_prod() is True


@pytest.mark.parametrize(
    "value", ["false", "False", "FALSE", " false ", "0", "no", "off"]
)
def test_prod_is_false_only_for_a_false_value(monkeypatch, value):
    monkeypatch.setenv("PROD", value)

    assert is_prod() is False


@pytest.mark.parametrize("value", ["true", "True", "1", "yes", "", "flase", "dev"])
def test_prod_is_true_for_anything_else(monkeypatch, value):
    monkeypatch.setenv("PROD", value)

    assert is_prod() is True

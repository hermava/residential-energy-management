import pytest

from energy_manager.config import (
    get_home_assistant_token,
    get_home_assistant_url,
)


def test_get_home_assistant_url(monkeypatch):
    monkeypatch.setenv("HOME_ASSISTANT_URL", "http://example.local:8123")

    assert get_home_assistant_url() == "http://example.local:8123"


def test_get_home_assistant_token(monkeypatch):
    monkeypatch.setenv("HOME_ASSISTANT_TOKEN", "test-token")

    assert get_home_assistant_token() == "test-token"


def test_get_home_assistant_url_missing(monkeypatch):
    monkeypatch.delenv("HOME_ASSISTANT_URL", raising=False)

    with pytest.raises(RuntimeError):
        get_home_assistant_url()


def test_get_home_assistant_token_missing(monkeypatch):
    monkeypatch.delenv("HOME_ASSISTANT_TOKEN", raising=False)

    with pytest.raises(RuntimeError):
        get_home_assistant_token()
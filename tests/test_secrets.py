"""Tests for scq.config.secrets — env var + keyring fallback chain."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scq.config import secrets  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Strip any SCQ_ env vars and any installed keyring module from each test."""
    for k in list(__import__("os").environ.keys()):
        if k.startswith("SCQ_"):
            monkeypatch.delenv(k, raising=False)
    # Ensure no real keyring leaks across tests
    monkeypatch.delitem(sys.modules, "keyring", raising=False)
    monkeypatch.delitem(sys.modules, "keyring.errors", raising=False)
    yield


def test_env_var_name_normalizes():
    assert secrets.env_var_name("foo") == "SCQ_FOO"
    assert secrets.env_var_name("email_app_password") == "SCQ_EMAIL_APP_PASSWORD"
    assert secrets.env_var_name("smtp-password") == "SCQ_SMTP_PASSWORD"
    assert secrets.env_var_name("ns.key") == "SCQ_NS_KEY"


def test_get_reads_env_var_first(monkeypatch):
    monkeypatch.setenv("SCQ_FOO", "from-env")
    assert secrets.get("foo") == "from-env"


def test_get_returns_None_when_nothing_resolves():
    assert secrets.get("nope") is None


def test_get_falls_back_to_keyring(monkeypatch):
    """Inject a fake keyring module; env var unset → keyring wins."""
    fake = types.ModuleType("keyring")

    def get_password(service, name):
        if service == "scq" and name == "smtp_pw":
            return "from-keyring"
        return None

    fake.get_password = get_password  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "keyring", fake)
    assert secrets.get("smtp_pw") == "from-keyring"
    # And missing names still return None
    assert secrets.get("other") is None


def test_env_var_wins_over_keyring(monkeypatch):
    fake = types.ModuleType("keyring")
    fake.get_password = lambda s, n: "from-keyring"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "keyring", fake)
    monkeypatch.setenv("SCQ_PW", "from-env")
    assert secrets.get("pw") == "from-env"


def test_keyring_backend_failure_treated_as_missing(monkeypatch):
    fake = types.ModuleType("keyring")

    def boom(*_a, **_k):
        raise RuntimeError("DBus locked")

    fake.get_password = boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "keyring", fake)
    assert secrets.get("anything") is None


def test_has_returns_bool():
    assert secrets.has("nothing") is False


def test_has_does_not_leak_value(monkeypatch):
    monkeypatch.setenv("SCQ_LEAK", "private")
    # has() should return True without side-effects exposing the value
    assert secrets.has("leak") is True


def test_set_raises_KeyringUnavailable_without_keyring(monkeypatch):
    # Make `import keyring` fail by removing it from finder candidates
    def _fail(name, *_a, **_k):
        if name == "keyring":
            raise ImportError("no keyring")
        return _orig(name, *_a, **_k)

    _orig = __import__
    monkeypatch.setattr("builtins.__import__", _fail)
    with pytest.raises(secrets.KeyringUnavailable):
        secrets.set("name", "value")


def test_set_writes_to_keyring(monkeypatch):
    captured: dict = {}
    fake = types.ModuleType("keyring")

    def set_password(service, name, value):
        captured["call"] = (service, name, value)

    fake.set_password = set_password  # type: ignore[attr-defined]
    fake.get_password = lambda s, n: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "keyring", fake)
    secrets.set("smtp_pw", "abc123")
    assert captured["call"] == ("scq", "smtp_pw", "abc123")


def test_delete_returns_False_without_keyring(monkeypatch):
    def _fail(name, *_a, **_k):
        if name == "keyring":
            raise ImportError("no keyring")
        return _orig(name, *_a, **_k)

    _orig = __import__
    monkeypatch.setattr("builtins.__import__", _fail)
    deleted = secrets.delete("anything")
    assert deleted is False


def test_delete_calls_keyring(monkeypatch):
    deleted: list = []
    fake_kr = types.ModuleType("keyring")
    fake_errors = types.ModuleType("keyring.errors")

    class PasswordDeleteError(Exception):
        pass

    fake_errors.PasswordDeleteError = PasswordDeleteError  # type: ignore[attr-defined]

    def delete_password(service, name):
        deleted.append((service, name))

    fake_kr.delete_password = delete_password  # type: ignore[attr-defined]
    fake_kr.errors = fake_errors  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "keyring", fake_kr)
    monkeypatch.setitem(sys.modules, "keyring.errors", fake_errors)
    ok = secrets.delete("pw")
    assert ok is True
    assert deleted == [("scq", "pw")]


def test_keyring_available_reflects_import_state(monkeypatch):
    # Without
    def _fail(name, *_a, **_k):
        if name == "keyring":
            raise ImportError("no")
        return _orig(name, *_a, **_k)

    _orig = __import__
    monkeypatch.setattr("builtins.__import__", _fail)
    assert secrets.keyring_available() is False


def test_input_validation():
    with pytest.raises(ValueError):
        secrets.get("")
    with pytest.raises(ValueError):
        secrets.get(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        secrets.set("name", 12345)  # type: ignore[arg-type]

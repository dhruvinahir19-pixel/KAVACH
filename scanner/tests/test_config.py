import pytest

from kcore import config


def _clear_env(monkeypatch):
    for k in config.REQUIRED:
        monkeypatch.delenv(k, raising=False)


def test_missing_all_named(monkeypatch):
    _clear_env(monkeypatch)
    with pytest.raises(config.ConfigError) as e:
        config.load()
    for k in config.REQUIRED:
        assert k in str(e.value)                 # every missing name is listed


def test_partial_missing_named(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("NEON_DATABASE_URL", "postgres://x")
    monkeypatch.setenv("TRIGGER_SECRET", "s")
    with pytest.raises(config.ConfigError) as e:
        config.load()
    assert "TELEGRAM_CHAT_ID" in str(e.value)
    assert "NEON_DATABASE_URL" not in str(e.value)   # present ones are not named


def test_full_env(monkeypatch):
    _clear_env(monkeypatch)
    for k in config.REQUIRED:
        monkeypatch.setenv(k, "val-" + k)
    cfg = config.load()
    assert cfg["TRIGGER_SECRET"] == "val-TRIGGER_SECRET"


def test_non_strict_tolerates_missing(monkeypatch):
    _clear_env(monkeypatch)
    cfg = load = config.load(strict=False)
    assert cfg["NEON_DATABASE_URL"] == ""

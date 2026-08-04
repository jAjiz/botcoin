import importlib
import os

import core.config as config


def _reload_with_env(monkeypatch, name: str, raw: str | None) -> None:
    if raw is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, raw)
    importlib.reload(config)


def _reload_with_pairs(monkeypatch, raw: str | None) -> None:
    _reload_with_env(monkeypatch, "PAIRS", raw)


def test_pairs_strips_whitespace_and_drops_empty_entries(monkeypatch) -> None:
    """A space after the comma (`PAIRS=XBTEUR, ETHEUR`) must not produce a
    bogus " ETHEUR" key."""
    original = os.environ.get("PAIRS")
    try:
        _reload_with_pairs(monkeypatch, "XBTEUR, ETHEUR")
        assert set(config.PAIRS.keys()) == {"XBTEUR", "ETHEUR"}
    finally:
        _reload_with_pairs(monkeypatch, original)


def test_pairs_empty_env_yields_empty_dict(monkeypatch) -> None:
    original = os.environ.get("PAIRS")
    try:
        _reload_with_pairs(monkeypatch, "")
        assert config.PAIRS == {}
    finally:
        _reload_with_pairs(monkeypatch, original)


def test_telegram_poll_interval_tolerates_empty_string(monkeypatch) -> None:
    """Docker Compose passes an unset ${VAR} through as an empty string, so
    int("") must not raise at import time."""
    original = os.environ.get("TELEGRAM_POLL_INTERVAL")
    try:
        _reload_with_env(monkeypatch, "TELEGRAM_POLL_INTERVAL", "")
        assert config.TELEGRAM_POLL_INTERVAL == 0
        _reload_with_env(monkeypatch, "TELEGRAM_POLL_INTERVAL", "10")
        assert config.TELEGRAM_POLL_INTERVAL == 10
    finally:
        _reload_with_env(monkeypatch, "TELEGRAM_POLL_INTERVAL", original)

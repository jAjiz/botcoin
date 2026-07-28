"""Unit tests for core.config parsing helpers."""

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
    """B6: `PAIRS=XBTEUR, ETHEUR` (a space after the comma, as an operator would
    naturally write it) previously created a bogus " ETHEUR" key that
    build_pairs_map silently dropped, leaving the bot trading only XBTEUR with
    no clear error."""
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
    """The telegram service gets this var through a docker-compose `environment:`
    allowlist, and compose passes an unset ${VAR} through as an empty string —
    present, so the getenv default never applies. int("") would raise here, at
    import time, before the service's own config validation could report it."""
    original = os.environ.get("TELEGRAM_POLL_INTERVAL")
    try:
        _reload_with_env(monkeypatch, "TELEGRAM_POLL_INTERVAL", "")
        assert config.TELEGRAM_POLL_INTERVAL == 0
        _reload_with_env(monkeypatch, "TELEGRAM_POLL_INTERVAL", "10")
        assert config.TELEGRAM_POLL_INTERVAL == 10
    finally:
        _reload_with_env(monkeypatch, "TELEGRAM_POLL_INTERVAL", original)

"""Unit tests for core.config parsing helpers."""

import importlib
import os

import core.config as config


def _reload_with_pairs(monkeypatch, raw: str | None) -> None:
    if raw is None:
        monkeypatch.delenv("PAIRS", raising=False)
    else:
        monkeypatch.setenv("PAIRS", raw)
    importlib.reload(config)


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

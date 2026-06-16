"""Single owner of dynamic per-pair configuration.

The live module-level dicts in ``core.config`` (TRADING_PARAMS, ASSET_ALLOCATION,
STOP_PERCENTILES) remain the read path for the trading loop. This module keeps
them current: it seeds them from the DB (or seeds the DB from them) at startup,
and applies validated runtime patches. A module lock makes each multi-field patch
atomic against the scheduler's reads.
"""

import threading
from typing import Any

import core.config as config
import core.database as db
import core.runtime as runtime
from core.config import PAIRS, VOLATILITY_LEVELS
from core.validation import normalize_pair_config, target_sum_error

_lock = threading.Lock()

# The flat config keys persisted in pair_config and exchanged with the API.
FLAT_KEYS = (
    "target_pct",
    "hodl_pct",
    "k_act",
    "min_margin",
    *[f"stop_pct_{lvl.lower()}" for lvl in VOLATILITY_LEVELS],
)


class UnknownPairError(Exception):
    """Raised when a config operation targets a pair not in PAIRS."""


class ConfigValidationError(Exception):
    """Raised when a patch fails validation. Carries the list of error strings."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def _flat_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in FLAT_KEYS}


def get_pair(pair: str) -> dict[str, Any]:
    """Current typed flat config for one pair, read from the live dicts."""
    return config.get_pair_config(pair)


def get_all() -> dict[str, dict[str, Any]]:
    return {pair: config.get_pair_config(pair) for pair in PAIRS}


def load_or_seed() -> None:
    """Startup sync. For each pair: load the DB row into the live dicts if present,
    otherwise seed a row from the (already env-validated) live dict values."""
    rows = db.load_all_pair_config()
    for pair in PAIRS:
        if pair in rows:
            config.set_pair_config(pair, _flat_from_row(rows[pair]))
        else:
            db.upsert_pair_config(pair, config.get_pair_config(pair), updated_by="seed")


def apply_patch(pair: str, fields: dict[str, Any], updated_by: str | None = None) -> dict[str, Any]:
    """Validate, persist, and apply a partial config change for one pair.

    Returns the new typed flat config. Raises UnknownPairError for an unknown
    pair, ConfigValidationError on validation failure (nothing persisted or
    applied). Persist-then-apply: if the DB write fails the live dicts are
    untouched (the exception propagates)."""
    with _lock:
        if pair not in PAIRS:
            raise UnknownPairError(pair)

        current = config.get_pair_config(pair)
        merged = {**current, **fields}
        typed, errors = normalize_pair_config(pair, merged)

        targets = {p: config.get_pair_config(p)["target_pct"] for p in PAIRS if p != pair}
        targets[pair] = typed["target_pct"]
        sum_err = target_sum_error(targets)
        if sum_err:
            errors.append(sum_err)

        if errors:
            raise ConfigValidationError(errors)

        stop_changed = any(
            typed[f"stop_pct_{lvl.lower()}"] != current[f"stop_pct_{lvl.lower()}"] for lvl in VOLATILITY_LEVELS
        )

        db.upsert_pair_config(pair, typed, updated_by=updated_by)
        config.set_pair_config(pair, typed)
        if stop_changed:
            runtime.mark_config_dirty(pair)

        return typed

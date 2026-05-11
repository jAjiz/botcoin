"""Load legacy CSV and JSON data into the new PostgreSQL schema.

This is a small, guarded ETL useful for one-off migration runs.

- Loads CSV files matching `*_ohlc_data_*min.csv` from the `data/` directory
  and saves rows into `ohlc_data` using `core.database.save_ohlc_data`.
- Loads `closed_positions.json` and inserts rows into `closed_positions`.
  Existing `closing_order_id` values are skipped (idempotent re-runs).

Design choices:
- Reuses existing `core.database` helpers so behavior and validation match application logic.
- Provides a `--dry-run` mode for safety.
- Avoids embedding long-running IO inside Alembic migrations.

Run example:
    python scripts/load_legacy_data.py --data-dir data
"""

from __future__ import annotations

import argparse
import json
import logging as stdlib_logging
import os
import re
import sys
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.database import (
    ClosedPosition,
    TrailingState,
    check_database_connection,
    get_session,
    save_ohlc_data,
)

logger = stdlib_logging.getLogger("load_legacy_data")

CSV_PATTERN = re.compile(r"(?P<pair>.+)_ohlc_data_(?P<timeframe>\d+)min\.csv$")
CSV_COLUMNS = ["time", "open", "high", "low", "close", "vwap", "volume", "count", "atr"]
# Stay under PostgreSQL's 65535-parameter limit: ~14 cols per OHLC row → 4000 rows ≈ 56k params.
OHLC_BATCH_SIZE = 4000


def _parse_datetime(value: str) -> datetime:
    """Parse an ISO-format datetime string, assuming UTC if no timezone is present."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def find_csv_files(data_dir: str) -> list[str]:
    files = []
    for name in os.listdir(data_dir):
        if name.endswith(".csv") and "ohlc_data" in name:
            m = CSV_PATTERN.match(name)
            if m:
                files.append(os.path.join(data_dir, name))
    return sorted(files)


def load_csv_file(path: str, dry_run: bool = False) -> int:
    name = os.path.basename(path)
    m = CSV_PATTERN.match(name)
    if not m:
        logger.warning("Skipping unknown CSV format: %s", name)
        return 0
    pair = m.group("pair")
    timeframe = int(m.group("timeframe"))

    logger.info("Reading CSV %s (pair=%s timeframe=%d)", name, pair, timeframe)
    df = pd.read_csv(path)
    df.columns = df.columns.str.lower()

    # Ensure there is an integer `time` column (epoch seconds). If only `dtime` present,
    # convert it.
    if "time" not in df.columns and "dtime" in df.columns:
        try:
            df["time"] = pd.to_datetime(df["dtime"]).astype(int) // 10**9
        except Exception:
            # Fallback: try parsing with pd.to_datetime without astype
            df["time"] = (pd.to_datetime(df["dtime"]) - pd.Timestamp("1970-01-01")) // pd.Timedelta("1s")

    # Keep only relevant columns; missing optional columns are OK.
    selected = [c for c in CSV_COLUMNS if c in df.columns]
    df_selected = df[selected].copy()

    logger.info("Prepared %d rows for %s", len(df_selected), name)

    if dry_run:
        return len(df_selected)

    total = len(df_selected)
    for start in range(0, total, OHLC_BATCH_SIZE):
        chunk = df_selected.iloc[start : start + OHLC_BATCH_SIZE]
        save_ohlc_data(pair, timeframe, chunk)
        logger.info("  %s: saved %d/%d rows", pair, min(start + OHLC_BATCH_SIZE, total), total)
    return total


def _closed_position_row(pair: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Map a legacy closed-position JSON entry to a `closed_positions` row dict."""
    return {
        "pair": pair,
        "side": entry["side"],
        "volume": entry["volume"],
        "entry_price": entry["entry_price"],
        "activation_atr": entry.get("activation_atr"),
        "activation_price": entry.get("activation_price"),
        "created_at": _parse_datetime(entry["creation_time"]),
        "activated_at": (_parse_datetime(entry["activation_time"]) if entry.get("activation_time") else None),
        "trailing_price": entry.get("trailing_price"),
        "stop_price": entry.get("stop_price"),
        "stop_atr": entry.get("stop_atr"),
        "closing_price": entry["closing_price"],
        "closing_order_id": entry["closing_order"],
        "closed_at": _parse_datetime(entry["closing_time"]),
        "pnl_percent": entry["pnl"],
    }


def clean_trailing_state(dry_run: bool = False) -> None:
    # Wipe stale state so the bot rebuilds it from the freshly migrated data on next start.
    if dry_run:
        return
    with get_session() as session:
        session.query(TrailingState).delete()
    logger.info("Cleared trailing_state table")


def load_closed_positions(path: str, dry_run: bool = False) -> int:
    if not os.path.exists(path):
        logger.info("Closed positions JSON not found: %s", path)
        return 0
    logger.info("Loading closed positions JSON: %s", path)
    with open(path, encoding="utf-8") as fh:
        data: dict[str, list[dict[str, Any]]] = json.load(fh)

    rows: list[dict[str, Any]] = []
    for pair, entries in data.items():
        if not isinstance(entries, list):
            logger.warning("Skipping %s: expected list of positions", pair)
            continue
        for entry in entries:
            try:
                rows.append(_closed_position_row(pair, entry))
            except KeyError as e:
                logger.warning(
                    "Skipping closed position for %s (missing field %s): %s",
                    pair,
                    e,
                    entry.get("closing_order"),
                )

    logger.info("Prepared %d closed-position rows", len(rows))
    if dry_run or not rows:
        return len(rows)

    # Bulk insert with conflict skip on unique closing_order_id so re-runs are safe.
    stmt = pg_insert(ClosedPosition.__table__).values(rows).on_conflict_do_nothing(index_elements=["closing_order_id"])
    with get_session() as session:
        result = session.execute(stmt)
    inserted = result.rowcount if result.rowcount is not None else len(rows)
    logger.info("Inserted %d new closed positions (skipped %d duplicates)", inserted, len(rows) - inserted)
    return inserted


def confirm(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load legacy CSV/JSON data into the database")
    parser.add_argument("--data-dir", default="data", help="Directory containing legacy CSV files")
    parser.add_argument(
        "--closed-positions-file",
        default="data/closed_positions.json",
        help="Closed positions JSON file",
    )
    parser.add_argument("--dry-run", action="store_true", help="Don't write to the database; just report counts")
    parser.add_argument(
        "--clean-trailing-state", action="store_true", help="Delete all rows from trailing_state before importing"
    )
    parser.add_argument("--yes", action="store_true", help="Don't prompt for confirmation")

    args = parser.parse_args(argv)

    stdlib_logging.basicConfig(
        level=stdlib_logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not os.path.isdir(args.data_dir):
        logger.error("Data directory not found: %s", args.data_dir)
        return 2

    logger.info("Checking database connection...")
    if not check_database_connection():
        logger.error("Database connection failed. Set environment variables appropriately and try again.")
        return 3

    csv_files = find_csv_files(args.data_dir)
    if not csv_files and not os.path.exists(args.closed_positions_file):
        logger.info("No CSV or JSON files found to import in %s", args.data_dir)
        return 0

    # Estimate rows for confirmation
    total_rows = 0
    for p in csv_files:
        try:
            # cheap row count by streaming header + len of file lines (not ideal for huge files)
            with open(p, encoding="utf-8") as fh:
                # subtract header
                rows = sum(1 for _ in fh) - 1
            total_rows += max(rows, 0)
        except Exception:
            logger.debug("Could not count rows for %s", p)

    closed_count = 0
    if os.path.exists(args.closed_positions_file):
        try:
            with open(args.closed_positions_file, encoding="utf-8") as fh:
                j = json.load(fh)
            if isinstance(j, dict):
                closed_count = sum(len(v) for v in j.values() if isinstance(v, list))
        except Exception:
            closed_count = 0

    logger.info("Planned import: %d CSV rows, %d closed-position entries", total_rows, closed_count)

    if args.dry_run:
        logger.info("Dry run requested; no writes will be performed")
    elif not args.yes and not confirm("Proceed with import? [y/N]: "):
        logger.info("Aborted by user")
        return 1

    if args.clean_trailing_state:
        try:
            clean_trailing_state(dry_run=args.dry_run)
        except Exception as e:
            logger.exception("Error cleaning trailing state: %s", e)

    imported = 0
    for p in csv_files:
        try:
            n = load_csv_file(p, dry_run=args.dry_run)
            imported += n
            logger.info("Processed %s: %d rows", os.path.basename(p), n)
        except Exception as e:
            logger.exception("Error processing %s: %s", p, e)

    try:
        n = load_closed_positions(args.closed_positions_file, dry_run=args.dry_run)
        logger.info("Processed closed-position entries: %d", n)
        imported += n
    except Exception as e:
        logger.exception("Error loading closed positions: %s", e)

    logger.info("Import complete. Total rows/entries processed: %d", imported)
    return 0


if __name__ == "__main__":
    sys.exit(main())

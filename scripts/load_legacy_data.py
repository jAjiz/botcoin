"""Load legacy CSV and JSON data into the new PostgreSQL schema.

This is a small, guarded ETL useful for one-off migration runs.

- Loads CSV files matching `*_ohlc_data_*min.csv` from the `data/` directory
  and saves rows into `ohlc_data` using `core.database.save_ohlc_data`.
- Loads `trailing_state.json` and upserts rows using `core.database.save_trailing_state`.

Design choices:
- Reuses existing `core.database` helpers so behavior and validation match application logic.
- Provides a `--dry-run` mode for safety.
- Avoids embedding long-running IO inside Alembic migrations.

Run example:
    python scripts/load_legacy_data.py --data-dir data --json-file data/trailing_state.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Dict

import pandas as pd

import core.logging as project_logging
from core.database import save_ohlc_data, save_trailing_state, check_database_connection

logger = project_logging.logging.getLogger("load_legacy_data")

CSV_PATTERN = re.compile(r"(?P<pair>.+)_ohlc_data_(?P<timeframe>\d+)min\.csv$")
CSV_COLUMNS = ["time", "open", "high", "low", "close", "vwap", "volume", "count", "atr"]
_TRAILING_STATE_DATETIME_FIELDS = ("created_at", "activated_at", "closing_requested_at")


def _parse_datetime(value: str) -> datetime:
    """Parse an ISO-format datetime string, assuming UTC if no timezone is present."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
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
            df["time"] = pd.to_datetime(df["dtime"]).astype(int) // 10 ** 9
        except Exception:
            # Fallback: try parsing with pd.to_datetime without astype
            df["time"] = (pd.to_datetime(df["dtime"]) - pd.Timestamp("1970-01-01")) // pd.Timedelta("1s")

    # Keep only relevant columns; missing optional columns are OK.
    selected = [c for c in CSV_COLUMNS if c in df.columns]
    df_selected = df[selected].copy()

    logger.info("Prepared %d rows for %s", len(df_selected), name)

    if dry_run:
        return len(df_selected)

    # Use existing application helper to save rows (per-row ORM insert).
    save_ohlc_data(pair, timeframe, df_selected)
    return len(df_selected)


def load_trailing_state(path: str, dry_run: bool = False) -> int:
    if not os.path.exists(path):
        logger.info("Trailing state JSON not found: %s", path)
        return 0
    logger.info("Loading trailing state JSON: %s", path)
    with open(path, "r", encoding="utf-8") as fh:
        data: Dict[str, Dict] = json.load(fh)

    count = 0
    for pair, state in data.items():
        count += 1
        logger.info("Found trailing state for %s", pair)
        if not dry_run:
            parsed = dict(state)
            for field in _TRAILING_STATE_DATETIME_FIELDS:
                if field in parsed and parsed[field] is not None:
                    parsed[field] = _parse_datetime(parsed[field])
            save_trailing_state(pair, parsed)
    return count


def confirm(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load legacy CSV/JSON data into the database")
    parser.add_argument("--data-dir", default="data", help="Directory containing legacy CSV files")
    parser.add_argument("--json-file", default="data/trailing_state.json", help="Trailing state JSON file")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to the database; just report counts")
    parser.add_argument("--yes", action="store_true", help="Don't prompt for confirmation")

    args = parser.parse_args(argv)

    if not os.path.isdir(args.data_dir):
        logger.error("Data directory not found: %s", args.data_dir)
        return 2

    logger.info("Checking database connection...")
    if not check_database_connection():
        logger.error("Database connection failed. Set environment variables appropriately and try again.")
        return 3

    csv_files = find_csv_files(args.data_dir)
    if not csv_files and not os.path.exists(args.json_file):
        logger.info("No CSV or JSON files found to import in %s", args.data_dir)
        return 0

    # Estimate rows for confirmation
    total_rows = 0
    for p in csv_files:
        try:
            # cheap row count by streaming header + len of file lines (not ideal for huge files)
            with open(p, "r", encoding="utf-8") as fh:
                # subtract header
                rows = sum(1 for _ in fh) - 1
            total_rows += max(rows, 0)
        except Exception:
            logger.debug("Could not count rows for %s", p)

    trailing_count = 0
    if os.path.exists(args.json_file):
        try:
            with open(args.json_file, "r", encoding="utf-8") as fh:
                j = json.load(fh)
            trailing_count = len(j.keys()) if isinstance(j, dict) else 0
        except Exception:
            trailing_count = 0

    logger.info("Planned import: %d CSV rows, %d trailing_state entries", total_rows, trailing_count)

    if args.dry_run:
        logger.info("Dry run requested; no writes will be performed")
    elif not args.yes:
        if not confirm("Proceed with import? [y/N]: "):
            logger.info("Aborted by user")
            return 1

    # Process CSVs
    imported = 0
    for p in csv_files:
        try:
            n = load_csv_file(p, dry_run=args.dry_run)
            imported += n
            logger.info("Processed %s: %d rows", os.path.basename(p), n)
        except Exception as e:
            logger.exception("Error processing %s: %s", p, e)

    # Process trailing_state.json
    try:
        n = load_trailing_state(args.json_file, dry_run=args.dry_run)
        logger.info("Processed trailing state entries: %d", n)
        imported += n
    except Exception as e:
        logger.exception("Error loading trailing state: %s", e)

    logger.info("Import complete. Total rows/entries processed: %d", imported)
    return 0


if __name__ == "__main__":
    sys.exit(main())

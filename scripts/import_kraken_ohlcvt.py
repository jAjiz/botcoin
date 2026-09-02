"""Import Kraken's downloadable OHLCVT history files into `ohlc_data`.

Kraken's public REST endpoint only returns the last ~720 candles, so deep history
has to come from the CSV archives it publishes. Those files carry no header and no
VWAP; the columns are:

    timestamp,open,high,low,close,volume,trades

ATR is not in the file either, and the trading code reads it straight from the
column, so it is computed here with the bot's own Wilder implementation over the
whole imported series. The first ``ATR_PERIOD`` rows have no ATR by construction
and are stored with NULL, exactly as the live ingestion leaves them.

Writes through `db.save_ohlc_data`, which upserts, so re-importing the same file
is safe. The script refuses to run unless `--yes` is given, and it reports any gap
between what it is about to write and what the table already holds: a hole in the
series silently corrupts any analysis that indexes bars by position.

Usage (PYTHONPATH=. and DB env vars required):
  PYTHONPATH=. python scripts/import_kraken_ohlcvt.py XBTEUR 15 path/to/*.csv --yes
"""

import argparse
import sys
from datetime import UTC, datetime

import pandas as pd

import core.database as db
from core.config import ATR_PERIOD
from trading.market_analyzer import _wilder_atr_from_scratch

COLUMNS = ["time", "open", "high", "low", "close", "volume", "count"]
CHUNK = 2000


def read_files(paths: list[str]) -> pd.DataFrame:
    """Concatenate the CSVs into one frame, sorted by time with duplicates dropped."""
    frames = []
    for path in paths:
        df = pd.read_csv(path, header=None, names=COLUMNS)
        print(f"  {path}: {len(df)} filas  {_stamp(df['time'].min())}..{_stamp(df['time'].max())}")
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
    return out


def _stamp(unix: int) -> str:
    return datetime.fromtimestamp(int(unix), UTC).strftime("%Y-%m-%d %H:%M")


def report_continuity(df: pd.DataFrame, timeframe: int, pair: str) -> None:
    """Print any hole inside the file series, and any gap against what the table holds."""
    step = timeframe * 60
    deltas = df["time"].diff().dropna()
    holes = deltas[deltas != step]
    if len(holes):
        print(f"\n  AVISO: {len(holes)} saltos dentro de los ficheros (mayor: {int(holes.max()) // step} velas)")
    else:
        print(f"\n  serie continua: {len(df)} velas seguidas de {timeframe} minutos")

    existing = db.load_ohlc_data(pair, timeframe)
    if existing.empty:
        print("  la tabla no tiene datos de este par todavia")
        return
    lo, hi = int(existing["time"].min()), int(existing["time"].max())
    print(f"  la tabla ya tiene {len(existing)} velas  {_stamp(lo)}..{_stamp(hi)}")
    new_hi = int(df["time"].max())
    if new_hi < lo:
        missing = (lo - new_hi) // step - 1
        print(
            f"  AVISO: quedan {missing} velas sin cubrir entre los ficheros y la tabla ({(lo - new_hi) / 86400:.1f} dias)"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="Importa ficheros OHLCVT de Kraken a la tabla ohlc_data.")
    ap.add_argument("pair")
    ap.add_argument("timeframe", type=int)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--yes", action="store_true", help="Escribe de verdad; sin esto solo informa.")
    args = ap.parse_args()

    print(f"[importar] {args.pair} {args.timeframe}m desde {len(args.files)} ficheros\n")
    df = read_files(args.files)
    report_continuity(df, args.timeframe, args.pair)

    df["atr"] = _wilder_atr_from_scratch(df, ATR_PERIOD)
    with_atr = int(df["atr"].notna().sum())
    print(f"\n  ATR de Wilder ({ATR_PERIOD} periodos): {with_atr} velas con valor, {len(df) - with_atr} sin el")

    if not args.yes:
        print("\nEn seco. Repite con --yes para escribir.")
        return 0

    for start in range(0, len(df), CHUNK):
        db.save_ohlc_data(args.pair, args.timeframe, df.iloc[start : start + CHUNK])
        print(f"  escritas {min(start + CHUNK, len(df))}/{len(df)}", end="\r")

    after = db.load_ohlc_data(args.pair, args.timeframe)
    print(f"\n\nhecho: la tabla tiene ahora {len(after)} velas de {args.pair}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

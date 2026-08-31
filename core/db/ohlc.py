"""OHLC market-data access (``ohlc_data``)."""

import logging as stdlib_logging
from decimal import Decimal

import pandas as pd
from sqlalchemy import and_, desc, func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.db.models import OHLCData, _to_decimal_required
from core.db.session import open_session

logger = stdlib_logging.getLogger(__name__)


def load_ohlc_data(
    pair: str,
    timeframe: int,
    since_time: int | None = None,
    before_time: int | None = None,
    limit: int | None = None,
) -> pd.DataFrame:
    """Load OHLC data from the database.

    Args:
        pair: Trading pair.
        timeframe: Candle timeframe in minutes.
        since_time: Optional inclusive lower bound on `time` (Unix timestamp).
        before_time: Optional exclusive upper bound on `time` (Unix timestamp).
        limit: Optional maximum number of rows to return.

    Returns:
        A DataFrame with OHLC data and a datetime column, ordered newest first.
    """
    try:
        with open_session() as session:
            query = session.query(OHLCData).filter(and_(OHLCData.pair == pair, OHLCData.timeframe_minutes == timeframe))
            if since_time is not None:
                query = query.filter(OHLCData.time >= since_time)
            if before_time is not None:
                query = query.filter(OHLCData.time < before_time)
            query = query.order_by(desc(OHLCData.time))
            if limit is not None:
                query = query.limit(limit)
            records = query.all()
            if not records:
                return pd.DataFrame()
            df = pd.DataFrame([r.to_dict() for r in records])
            df["dtime"] = pd.to_datetime(pd.to_numeric(df["time"]), unit="s")
            logger.debug(f"Fetched {len(df)} OHLC records for {pair}")
            return df
    except Exception as e:
        logger.error(f"Error fetching OHLC data for {pair}: {e}")
        return pd.DataFrame()


def save_ohlc_data(pair: str, timeframe: int, df: pd.DataFrame) -> None:
    """Save OHLC data to the database.

    Args:
        pair: Trading pair.
        timeframe: Candle timeframe in minutes.
        df: DataFrame containing OHLC columns.
    """
    try:
        if df.empty:
            logger.warning(f"Empty DataFrame provided for {pair}")
            return
        records = df.to_dict("records")
        rows = [
            {
                "pair": pair,
                "timeframe_minutes": timeframe,
                "time": int(r["time"]),
                "open": _to_decimal_required(r["open"]),
                "high": _to_decimal_required(r["high"]),
                "low": _to_decimal_required(r["low"]),
                "close": _to_decimal_required(r["close"]),
                "vwap": Decimal(str(r["vwap"])) if "vwap" in r and pd.notna(r["vwap"]) else None,
                "volume": Decimal(str(r["volume"])) if "volume" in r and pd.notna(r["volume"]) else None,
                "count": int(r["count"]) if "count" in r and pd.notna(r["count"]) else None,
                "atr": Decimal(str(r["atr"])) if "atr" in r and pd.notna(r["atr"]) else None,
            }
            for r in records
        ]
        with open_session() as session:
            stmt = (
                pg_insert(OHLCData)
                .values(rows)
                .on_conflict_do_update(
                    index_elements=["pair", "timeframe_minutes", "time"],
                    set_={
                        "open": pg_insert(OHLCData).excluded.open,
                        "high": pg_insert(OHLCData).excluded.high,
                        "low": pg_insert(OHLCData).excluded.low,
                        "close": pg_insert(OHLCData).excluded.close,
                        "vwap": pg_insert(OHLCData).excluded.vwap,
                        "volume": pg_insert(OHLCData).excluded.volume,
                        "count": pg_insert(OHLCData).excluded.count,
                        "atr": pg_insert(OHLCData).excluded.atr,
                        "updated_at": func.now(),
                    },
                )
            )
            session.execute(stmt)
            logger.debug(f"Saved {len(rows)} OHLC records for {pair}")
    except Exception as e:
        logger.error(f"Error saving OHLC data for {pair}: {e}")
        raise

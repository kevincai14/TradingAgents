"""Serve Yahoo's daily OHLCV from a frozen CSV instead of the network.

**This is the one place where the authors' data path is intercepted, and it is
an environment workaround, not a change to the method.** It exists because from
this network Yahoo Finance answers the default egress with HTTP 429, the local
proxy succeeds only intermittently, and a 122-cell sweep cannot afford a data
fetch that fails one cell in five. ``get_indicators`` already reads a local CSV
through the authors' own cache; ``get_stock_data`` does not — it calls
``yf.Ticker(...).history`` directly — so the two analysts' views of price drift
apart exactly when the network is unhealthy, which is the worst possible failure
mode for a reproduction.

The frozen file is the authors' own cache artefact
(``data/cache/<TICKER>-YFin-data.csv``, a five-year download), copied to
``data/frozen/<TICKER>-OHLCV.csv`` and committed. Anything not covered by a
frozen file falls through to the real call, so this cannot silently fabricate
data: a missing ticker raises the same error the network path would.

History: the mtime of Yahoo's cache file is what makes the authors' cache "fresh",
and it is only fresh for the calendar day it was written. That makes their
backtest non-reproducible across days without any code change — recorded as an
obstacle in the README.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def install(frozen_dir: Path, verbose: bool = True) -> list[str]:
    """Patch ``yfinance.Ticker.history`` to read frozen CSVs when available.

    Returns the list of tickers that are frozen, for the run log.
    """
    import yfinance as yf

    frozen_dir = Path(frozen_dir)
    frozen = {p.stem.replace("-OHLCV", "") for p in frozen_dir.glob("*-OHLCV.csv")}
    if not frozen:
        if verbose:
            print("[freeze] no frozen series found; using the network")
        return []

    original = yf.Ticker.history

    def history(self, *args, **kwargs):
        path = frozen_dir / f"{self.ticker}-OHLCV.csv"
        if not path.is_file():
            return original(self, *args, **kwargs)

        # The cached rows carry a fixed UTC offset that switches between -05:00
        # and -04:00 across a DST boundary, which pandas cannot parse into one
        # DatetimeIndex directly; go through UTC and convert.
        frame = pd.read_csv(path)
        date_col = frame.columns[0]
        stamp = pd.to_datetime(frame[date_col], utc=True).dt.tz_convert("America/New_York")
        frame = frame.drop(columns=[date_col])
        frame.index = pd.DatetimeIndex(stamp, name=date_col)

        start = kwargs.get("start") or (args[0] if args else None)
        end = kwargs.get("end") or (args[1] if len(args) > 1 else None)
        if start is not None:
            frame = frame[frame.index >= pd.Timestamp(start, tz=frame.index.tz)]
        if end is not None:
            # yfinance treats ``end`` as exclusive; match that.
            frame = frame[frame.index < pd.Timestamp(end, tz=frame.index.tz)]
        if verbose:
            print(f"[freeze] {self.ticker}: {len(frame)} rows from {path.name}")
        return frame.copy()

    yf.Ticker.history = history
    if verbose:
        print(f"[freeze] serving {sorted(frozen)} from {frozen_dir}")
    return sorted(frozen)

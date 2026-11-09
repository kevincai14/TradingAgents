"""Download the benchmark price series once so scoring stays offline.

Writes ``data/benchmark/<TICKER>.csv`` with columns date, close. The buy-and-hold
arm of the paper's Table 1 uses a plain close-to-close series, so adjusted prices
are deliberately *not* used: over a single quarter AAPL pays one dividend, and
mixing adjustment conventions between the strategy arm and the benchmark would
show up as a spurious return.

Usage:
    .venv\\Scripts\\python.exe scripts/fetch_benchmark.py --ticker AAPL \\
        --start 2023-12-01 --end 2024-04-15
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _net import apply_proxy, configure_yfinance  # noqa: E402

apply_proxy()
yf = configure_yfinance()

import argparse  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default="AAPL")
    parser.add_argument("--start", default="2023-12-01")
    parser.add_argument("--end", default="2024-04-15")
    args = parser.parse_args()

    frame = yf.Ticker(args.ticker).history(
        start=args.start, end=args.end, auto_adjust=False, actions=False
    )
    if frame.empty:
        raise SystemExit(
            f"no rows returned for {args.ticker} {args.start}..{args.end}; "
            "check the proxy (RESEARCH_PROXY) before retrying"
        )

    out_dir = REPO_ROOT / "data" / "benchmark"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{args.ticker}.csv"

    frame = frame.reset_index()
    date_col = "Date" if "Date" in frame.columns else frame.columns[0]
    frame[date_col] = frame[date_col].dt.strftime("%Y-%m-%d")
    frame[["Date", "Close"]].rename(
        columns={date_col: "date", "Close": "close"}
    ).to_csv(path, index=False)

    print(f"{path}  rows={len(frame)}  {frame[date_col].iloc[0]}..{frame[date_col].iloc[-1]}")


if __name__ == "__main__":
    main()

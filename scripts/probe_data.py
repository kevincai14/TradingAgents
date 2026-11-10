"""Probe every data route the four analysts depend on, and record what answers.

This costs no LLM tokens, so it can be run before spending anything. It exists
because "the market analyst had no data" is the kind of failure that a pipeline
hides: the decision still comes back with a confident rating, and the loss is
only visible if you look at the tool output. The table this prints is the
evidence behind the data-availability section of the report.

Usage:
    .venv\\Scripts\\python.exe scripts/probe_data.py --ticker AAPL --date 2024-01-04
    .venv\\Scripts\\python.exe scripts/probe_data.py --ticker AAPL --date 2024-01-04 --live
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _net import apply_proxy, configure_yfinance  # noqa: E402
from _offline_yf import install as install_frozen  # noqa: E402

_PROXY = apply_proxy()
configure_yfinance(_PROXY)

import argparse  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402

from tradingagents.dataflows.config import set_config  # noqa: E402
from tradingagents.dataflows.router import route_to_vendor  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]

FAILURE_MARKERS = ("DATA_UNAVAILABLE", "NO_DATA", "unavailable", "Error")


def classify(text: str) -> str:
    head = text.strip()[:400]
    for marker in FAILURE_MARKERS:
        if head.startswith(marker) or marker in head[:120]:
            return "UNAVAILABLE"
    return "OK"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default="AAPL")
    parser.add_argument("--date", default="2024-01-04")
    parser.add_argument("--start", default="2023-12-01")
    parser.add_argument("--live", action="store_true", help="skip the frozen price file")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    frozen = [] if args.live else install_frozen(REPO_ROOT / "data" / "frozen", verbose=False)

    config = {
        "data_cache_dir": str(REPO_ROOT / "data" / "cache"),
    }
    set_config(config)

    calls = [
        ("price / get_stock_data", "get_stock_data", (args.ticker, args.start, args.date)),
        ("price / get_indicators(close_50_sma)", "get_indicators",
         (args.ticker, "close_50_sma", args.date, 120)),
        ("price / get_indicators(rsi)", "get_indicators", (args.ticker, "rsi", args.date, 120)),
        ("fundamentals / get_fundamentals", "get_fundamentals", (args.ticker, args.date)),
        ("fundamentals / balance_sheet", "get_balance_sheet", (args.ticker, "quarterly", args.date)),
        ("fundamentals / cashflow", "get_cashflow", (args.ticker, "quarterly", args.date)),
        ("fundamentals / income_statement", "get_income_statement",
         (args.ticker, "quarterly", args.date)),
        ("news / get_news", "get_news", (args.ticker, args.start, args.date)),
        ("news / get_global_news", "get_global_news", (args.date, 7, 10)),
        ("news / insider_transactions", "get_insider_transactions", (args.ticker,)),
        ("macro / get_macro_indicators", "get_macro_indicators", (args.date, ("cpi", "fed_rate"))),
    ]

    rows = []
    for label, tool, tool_args in calls:
        started = time.time()
        try:
            out = str(route_to_vendor(tool, *tool_args))
            status = classify(out)
            detail = out.strip().replace("\n", " ")[:120]
        except Exception as exc:  # noqa: BLE001 - the point is to record any failure
            status = "ERROR"
            detail = f"{type(exc).__name__}: {exc}"[:120]
        elapsed = time.time() - started
        rows.append(
            {"tool": label, "status": status, "seconds": round(elapsed, 2), "detail": detail}
        )
        print(f"{status:<12} {label:<40} {elapsed:6.2f}s  {detail[:70]}")

    summary = {
        "ticker": args.ticker,
        "date": args.date,
        "frozen_prices": frozen,
        "proxy": _PROXY,
        "available": sum(1 for r in rows if r["status"] == "OK"),
        "total": len(rows),
        "routes": rows,
    }
    out_path = Path(args.out) if args.out else REPO_ROOT / "data" / "probes" / f"{args.ticker}_{args.date}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{summary['available']}/{summary['total']} routes answered -> {out_path}")


if __name__ == "__main__":
    main()

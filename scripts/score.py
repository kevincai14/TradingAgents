"""Rebuild the paper's four Table 1 metrics from the collected decisions.

The upstream ``tradingagents/backtest.py`` scores rating hit-rate and alpha
against a benchmark; it does not compute cumulative return, annualised return,
Sharpe or max drawdown, so those are reconstructed here under a convention that
has to be stated explicitly because the paper does not state one:

* **Position mapping.** ``Buy = +1``, ``Overweight = +0.5``, ``Hold = 0``,
  ``Underweight = -0.5``, ``Sell = -1``. The paper describes a directional
  long/short scale without giving the weights; this is the natural reading of a
  five-tier rating. Every weight is reported alongside the metrics so a reader
  can rescale.
* **No look-ahead.** The decision for trading day *t* is applied to the return
  from the close of *t* to the close of the next trading day. The pipeline only
  sees data up to *t*, so anything faster would be fiction.
* **Flat annualisation.** 252 trading days, risk-free rate 0.

Usage:
    .venv\\Scripts\\python.exe scripts/score.py --ticker AAPL --debate-rounds 1
    .venv\\Scripts\\python.exe scripts/score.py --ticker AAPL --compare 1 2
"""

from __future__ import annotations

import argparse
import glob
import json
import math
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

WEIGHTS = {
    "buy": 1.0,
    "overweight": 0.5,
    "hold": 0.0,
    "underweight": -0.5,
    "sell": -1.0,
}


def load_prices(ticker: str) -> dict[str, float]:
    path = REPO_ROOT / "data" / "benchmark" / f"{ticker}.csv"
    if not path.exists():
        raise SystemExit(f"missing {path}; run scripts/fetch_benchmark.py first")
    closes: dict[str, float] = {}
    with path.open(encoding="utf-8") as handle:
        next(handle)
        for line in handle:
            date, close = line.strip().split(",")
            closes[date] = float(close)
    return dict(sorted(closes.items()))


def load_cells(ticker: str, debate_rounds: int) -> list[dict]:
    pattern = REPO_ROOT / "data" / "cells" / f"{ticker}_*_r{debate_rounds}.json"
    cells = []
    for path in sorted(glob.glob(str(pattern))):
        cells.append(json.loads(Path(path).read_text(encoding="utf-8")))
    if not cells:
        raise SystemExit(f"no cells matched {pattern}")
    return sorted(cells, key=lambda c: c["date"])


def position_of(decision: str) -> float | None:
    key = (decision or "").strip().lower()
    return WEIGHTS.get(key)


def drawdown(equity: list[float]) -> float:
    peak = equity[0]
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1.0)
    return worst


def metrics(returns: list[float], periods_per_year: int = 252) -> dict:
    if not returns:
        return {}
    equity = [1.0]
    for r in returns:
        equity.append(equity[-1] * (1.0 + r))
    cumulative = equity[-1] - 1.0
    n = len(returns)
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / (n - 1) if n > 1 else 0.0
    std = math.sqrt(variance)
    return {
        "n_periods": n,
        "cumulative_return": cumulative,
        # Guard the annualisation against a total wipe-out, where (1+CR) <= 0.
        "annualised_return": (1.0 + cumulative) ** (periods_per_year / n) - 1.0
        if cumulative > -1.0
        else -1.0,
        "sharpe": (mean / std * math.sqrt(periods_per_year)) if std > 0 else float("nan"),
        "max_drawdown": drawdown(equity),
        "mean_daily": mean,
        "std_daily": std,
    }


def evaluate(ticker: str, debate_rounds: int) -> dict:
    prices = load_prices(ticker)
    dates = list(prices)
    cells = load_cells(ticker, debate_rounds)

    strategy_returns: list[float] = []
    benchmark_returns: list[float] = []
    rows = []
    skipped: list[str] = []

    for cell in cells:
        date = cell["date"]
        weight = position_of(cell.get("decision", ""))
        if weight is None:
            skipped.append(f"{date}: unparseable decision {cell.get('decision')!r}")
            continue
        # First trading day on or after the decision date.
        idx = next((i for i, d in enumerate(dates) if d >= date), None)
        if idx is None or idx + 1 >= len(dates):
            skipped.append(f"{date}: no following session in the price series")
            continue
        forward = prices[dates[idx + 1]] / prices[dates[idx]] - 1.0
        strategy_returns.append(weight * forward)
        benchmark_returns.append(forward)
        rows.append(
            {
                "decision_date": date,
                "executed_session": dates[idx],
                "exit_session": dates[idx + 1],
                "decision": cell.get("decision"),
                "weight": weight,
                "benchmark_return": forward,
                "strategy_return": weight * forward,
                "elapsed_seconds": cell.get("elapsed_seconds"),
                "llm_calls": (cell.get("usage") or {}).get("llm_calls"),
                "sections_missing_data": cell.get("sections_missing_data") or [],
            }
        )

    result = {
        "ticker": ticker,
        "debate_rounds": debate_rounds,
        "decisions": len(cells),
        "scored": len(rows),
        "first_decision": rows[0]["decision_date"] if rows else None,
        "last_decision": rows[-1]["decision_date"] if rows else None,
        "strategy": metrics(strategy_returns),
        "buy_and_hold": metrics(benchmark_returns),
        "rating_counts": _counts([c.get("decision") for c in cells]),
        "total_llm_calls": sum((c.get("usage") or {}).get("llm_calls") or 0 for c in cells),
        "total_elapsed_minutes": round(
            sum(c.get("elapsed_seconds") or 0 for c in cells) / 60.0, 1
        ),
        "cells_missing_any_data": sum(1 for c in cells if c.get("sections_missing_data")),
        "skipped": skipped,
        "per_decision": rows,
    }
    return result


def _counts(values: list) -> dict:
    out: dict[str, int] = {}
    for value in values:
        key = str(value)
        out[key] = out.get(key, 0) + 1
    return out


def fmt(result: dict) -> str:
    s, b = result["strategy"], result["buy_and_hold"]
    if not s:
        return f"r{result['debate_rounds']}: nothing scored"
    return (
        f"r{result['debate_rounds']}  n={result['scored']:<3} "
        f"CR={s['cumulative_return']*100:+7.2f}%  "
        f"ARR={s['annualised_return']*100:+8.2f}%  "
        f"SR={s['sharpe']:6.2f}  MDD={s['max_drawdown']*100:6.2f}%   "
        f"| B&H CR={b['cumulative_return']*100:+7.2f}% SR={b['sharpe']:6.2f} "
        f"MDD={b['max_drawdown']*100:6.2f}%"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default="AAPL")
    parser.add_argument("--debate-rounds", type=int, default=None)
    parser.add_argument(
        "--compare",
        type=int,
        nargs="+",
        default=None,
        help="score several arms side by side, e.g. --compare 1 2",
    )
    parser.add_argument("--out", default=None, help="write the full result as JSON")
    parser.add_argument("--dump-rows", action="store_true", help="print per-decision rows")
    args = parser.parse_args()

    arms = args.compare or ([args.debate_rounds] if args.debate_rounds is not None else [1])

    payload = {}
    for arm in arms:
        try:
            result = evaluate(args.ticker, arm)
        except SystemExit as exc:
            print(f"r{arm}: {exc}")
            continue
        payload[f"r{arm}"] = result
        print(fmt(result))
        if args.dump_rows:
            for row in result["per_decision"]:
                print(
                    f"  {row['decision_date']}  {str(row['decision']):<12} "
                    f"w={row['weight']:+.1f}  bench={row['benchmark_return']*100:+6.2f}%  "
                    f"strat={row['strategy_return']*100:+6.2f}%"
                )

    if args.out and payload:
        out_path = REPO_ROOT / args.out if not Path(args.out).is_absolute() else Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nwritten: {out_path}")


if __name__ == "__main__":
    main()

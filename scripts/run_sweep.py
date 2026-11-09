"""Run a date range for one arm, resumably.

Each decision is executed in its own subprocess (``run_cell.py``) rather than
in-process, for two reasons: a crashed or rate-limited decision cannot take the
sweep down with it, and the pipeline keeps module-level state across calls, so
reusing one interpreter would make cell *k* depend on cells *1..k-1* in ways that
are hard to audit.

The sweep is resumable: a cell whose summary already exists in ``data/cells/`` is
skipped, so an interrupted run is restarted with the same command.

Usage:
    # baseline arm, every trading day of 2024Q1
    .venv\\Scripts\\python.exe scripts/run_sweep.py --debate-rounds 1 \\
        --start 2024-01-02 --end 2024-03-28

    # resume after a crash / top-up
    .venv\\Scripts\\python.exe scripts/run_sweep.py --debate-rounds 1 \\
        --start 2024-01-02 --end 2024-03-28 --resume
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"


def trading_days(ticker: str, start: str, end: str) -> list[str]:
    path = REPO_ROOT / "data" / "benchmark" / f"{ticker}.csv"
    if not path.exists():
        raise SystemExit(
            f"missing {path}; run scripts/fetch_benchmark.py --ticker {ticker} first"
        )
    days = []
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if start <= row["date"] <= end:
                days.append(row["date"])
    if not days:
        raise SystemExit(f"no trading days between {start} and {end} in {path}")
    return days


def cell_path(ticker: str, date: str, rounds: int, tag: str) -> Path:
    suffix = f"_{tag}" if tag else ""
    return REPO_ROOT / "data" / "cells" / f"{ticker}_{date}_r{rounds}{suffix}.json"


def run_one(
    ticker: str, date: str, rounds: int, tag: str, memory_key: str | None, reset: bool
) -> tuple[str, bool, str]:
    cmd = [
        str(PYTHON),
        str(REPO_ROOT / "scripts" / "run_cell.py"),
        "--ticker", ticker,
        "--date", date,
        "--debate-rounds", str(rounds),
    ]
    if tag:
        cmd += ["--tag", tag]
    if memory_key:
        cmd += ["--memory-key", memory_key]
    if reset:
        cmd += ["--reset-memory"]
    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))
    elapsed = time.time() - started
    ok = proc.returncode == 0 and cell_path(ticker, date, rounds, tag).exists()
    tail = (proc.stdout.strip().splitlines() or [""])[-1] if ok else proc.stderr.strip()[-400:]
    return date, ok, f"{elapsed:5.1f}s  {tail}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default="AAPL")
    parser.add_argument("--start", default="2024-01-02")
    parser.add_argument("--end", default="2024-03-28")
    parser.add_argument("--debate-rounds", type=int, default=1)
    parser.add_argument("--tag", default="")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--resume", action="store_true", help="skip cells already on disk")
    parser.add_argument("--force", action="store_true", help="re-run cells already on disk")
    parser.add_argument(
        "--fresh-memory",
        action="store_true",
        help="delete the arm's memory log before the first cell",
    )
    args = parser.parse_args()

    if not PYTHON.exists():
        raise SystemExit(f"no interpreter at {PYTHON}")

    days = trading_days(args.ticker, args.start, args.end)
    pending = [
        d for d in days
        if args.force or not cell_path(args.ticker, d, args.debate_rounds, args.tag).exists()
    ]
    print(
        f"{args.ticker}  r{args.debate_rounds}  {args.start}..{args.end}  "
        f"{len(days)} trading days, {len(pending)} to run, workers={args.workers}"
    )
    if not pending:
        print("nothing to do")
        return

    started = time.time()
    failures: list[str] = []
    done = 0
    errors: dict = {}

    if args.workers <= 1:
        for index, date in enumerate(pending):
            reset = args.fresh_memory and index == 0
            date, ok, note = run_one(args.ticker, date, args.debate_rounds, args.tag, None, reset)
            done += 1
            rate = (time.time() - started) / done
            remaining = rate * (len(pending) - done) / 60
            print(f"[{done:>3}/{len(pending)}] {date} {'OK ' if ok else 'FAIL'} {note} "
                  f"| eta {remaining:.0f} min", flush=True)
            if not ok:
                failures.append(date)
    else:
        # Disjoint memory namespaces: concurrent writers on one log file would
        # interleave reflections from decisions made on different dates.
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(
                    run_one, args.ticker, date, args.debate_rounds, args.tag,
                    f"r{args.debate_rounds}_w{index % args.workers}", False,
                ): date
                for index, date in enumerate(pending)
            }
            for future in as_completed(futures):
                date, ok, note = future.result()
                done += 1
                print(f"[{done:>3}/{len(pending)}] {date} {'OK ' if ok else 'FAIL'} {note}",
                      flush=True)
                if not ok:
                    failures.append(date)

    elapsed_min = (time.time() - started) / 60
    summary = {
        "ticker": args.ticker,
        "debate_rounds": args.debate_rounds,
        "window": [args.start, args.end],
        "requested": len(pending),
        "succeeded": len(pending) - len(failures),
        "failed": failures,
        "workers": args.workers,
        "wall_clock_minutes": round(elapsed_min, 1),
        "models": {"deep": "deepseek-v4-pro", "quick": "deepseek-v4-flash"},
    }
    out = REPO_ROOT / "data" / "sweeps"
    out.mkdir(parents=True, exist_ok=True)
    suffix = f"_{args.tag}" if args.tag else ""
    path = out / f"{args.ticker}_{args.start}_{args.end}_r{args.debate_rounds}{suffix}.json"
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\n{len(pending) - len(failures)}/{len(pending)} ok in {elapsed_min:.0f} min -> {path}")
    if failures:
        print(f"failed: {', '.join(failures)}")
        print("re-run the same command with --resume to retry only the missing cells")
        sys.exit(1)


if __name__ == "__main__":
    main()

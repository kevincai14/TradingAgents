"""Run one (ticker, date) decision of the reproduction and record what it cost.

Four things this wrapper does that the upstream CLI does not, each for a
reproducibility reason:

1. **Network.** The host injects ``http_proxy=http://127.0.0.1:3768`` (its own
   egress), which Yahoo Finance answers with HTTP 429, and ``load_dotenv`` never
   overrides an already-exported variable — so the switch to the working proxy
   has to happen in-process, before ``tradingagents`` is imported.
2. **yfinance.** It builds its own ``curl_cffi`` session and ignores
   ``HTTP_PROXY``/``HTTPS_PROXY`` entirely; ``yf.config.network.proxy`` is the
   only thing that works.
3. **Memory isolation.** The pipeline appends every decision to a shared
   ``~/.tradingagents/memory/trading_memory.md`` and feeds it back into later
   decisions (and settles earlier ones against realised prices). Left alone, the
   baseline arm would bleed into the changed arm and neither arm would be
   reproducible. Each arm therefore gets its own log file.
4. **Cost accounting.** A LangChain callback counts LLM calls and tokens per
   decision, so the paper's "~11 LLM calls per prediction" claim can be checked
   instead of assumed.

Usage:
    .venv\\Scripts\\python.exe scripts/run_cell.py --ticker AAPL --date 2024-01-02
    .venv\\Scripts\\python.exe scripts/run_cell.py --ticker AAPL --date 2024-01-02 \\
        --debate-rounds 2 --reset-memory
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _net import apply_proxy, configure_yfinance  # noqa: E402

_PROXY = apply_proxy()
configure_yfinance(_PROXY)

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402

from langchain_core.callbacks import BaseCallbackHandler  # noqa: E402

from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402
from tradingagents.graph.trading_graph import TradingAgentsGraph  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


class UsageCounter(BaseCallbackHandler):
    """Count LLM calls and tokens for one decision."""

    def __init__(self) -> None:
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def on_llm_end(self, response, **kwargs) -> None:  # noqa: D401
        self.calls += 1
        for generation_list in getattr(response, "generations", None) or []:
            for generation in generation_list:
                message = getattr(generation, "message", None)
                usage = getattr(message, "usage_metadata", None) or {}
                self.input_tokens += usage.get("input_tokens", 0) or 0
                self.output_tokens += usage.get("output_tokens", 0) or 0

    def as_dict(self) -> dict:
        return {
            "llm_calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


def memory_path(debate_rounds: int, key: str | None = None) -> Path:
    """One memory log per arm — see module docstring, point 3."""
    return REPO_ROOT / "data" / "memory" / (key or f"r{debate_rounds}") / "trading_memory.md"


def build_config(debate_rounds: int, memory_key: str | None = None) -> dict:
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = "deepseek"
    config["deep_think_llm"] = os.getenv("DEEP_THINK_MODEL", "deepseek-v4-pro")
    config["quick_think_llm"] = os.getenv("QUICK_THINK_MODEL", "deepseek-v4-flash")
    config["max_debate_rounds"] = debate_rounds
    config["max_risk_discuss_rounds"] = 1
    config["temperature"] = 0.0
    config["results_dir"] = str(REPO_ROOT / "data" / "results")
    config["data_cache_dir"] = str(REPO_ROOT / "data" / "cache")
    config["memory_log_path"] = str(memory_path(debate_rounds, memory_key))
    return config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default="AAPL")
    parser.add_argument("--date", default="2024-01-02")
    parser.add_argument("--debate-rounds", type=int, default=1)
    parser.add_argument("--analysts", default="market,social,news,fundamentals")
    parser.add_argument(
        "--reset-memory",
        action="store_true",
        help="delete this arm's memory log before running (first cell of a sweep)",
    )
    parser.add_argument("--tag", default="", help="suffix for repeated runs, e.g. seed2")
    parser.add_argument(
        "--memory-key",
        default=None,
        help="memory namespace; defaults to r<N>. Used to give concurrent "
        "workers disjoint memory logs instead of racing on one file.",
    )
    args = parser.parse_args()

    log = memory_path(args.debate_rounds, args.memory_key)
    log.parent.mkdir(parents=True, exist_ok=True)
    if args.reset_memory and log.exists():
        log.unlink()

    analysts = tuple(a.strip() for a in args.analysts.split(",") if a.strip())
    config = build_config(args.debate_rounds, args.memory_key)
    counter = UsageCounter()
    graph = TradingAgentsGraph(
        selected_analysts=analysts, debug=False, config=config, callbacks=[counter]
    )

    started = time.time()
    state, decision = graph.propagate(args.ticker, args.date)
    elapsed = time.time() - started

    out = {
        "ticker": args.ticker,
        "date": args.date,
        "debate_rounds": args.debate_rounds,
        "analysts": list(analysts),
        "decision": decision,
        "elapsed_seconds": round(elapsed, 1),
        "models": {"deep": config["deep_think_llm"], "quick": config["quick_think_llm"]},
        "usage": counter.as_dict(),
        "proxy": _PROXY,
        "tag": args.tag,
    }

    # Cheap data-integrity flag: if any analyst received no data at all, the
    # decision is not comparable and the cell must be reported as such.
    reports = {k: v for k, v in (state or {}).items() if isinstance(v, str)}
    unavailable = [
        k for k, v in reports.items() if "DATA_UNAVAILABLE" in v or "NO_DATA" in v
    ]
    out["sections_missing_data"] = unavailable

    out_dir = REPO_ROOT / "data" / "cells"
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"_{args.tag}" if args.tag else ""
    path = out_dir / f"{args.ticker}_{args.date}_r{args.debate_rounds}{suffix}.json"
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")

    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()

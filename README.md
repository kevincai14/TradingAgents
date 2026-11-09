# Reproduction study: TradingAgents (FTEC5660)

Reproduction of the backtest results reported in **TradingAgents: Multi-Agents LLM
Financial Trading Framework** (Xiao et al., [arXiv:2412.20138](https://arxiv.org/abs/2412.20138)),
submitted for FTEC5660 Reproducibility Work.

This repository is a fork of the authors' code ([TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents),
pinned at **v0.7.1**) with the reproduction harness, run logs and findings added on
top. The authors' own README is kept as [UPSTREAM_README.md](UPSTREAM_README.md).

---

## 1. What is being reproduced

The paper's **Table 1** is a per-ticker panel: three tickers (AAPL, GOOGL, AMZN)
over 2024-01-01 to 2024-03-29, four metrics — cumulative return (CR), annualised
return (ARR), Sharpe ratio (SR) and maximum drawdown (MDD) — scored against four
rule-based baselines plus buy-and-hold.

The full AAPL panel, as reported in the paper:

| Arm | CR % | ARR % | SR | MDD % | In this study |
|---|---|---|---|---|---|
| Buy & hold | −5.23 | −5.09 | −1.29 | 11.90 | recomputed as the yardstick |
| MACD | −1.49 | −1.48 | −0.81 | 4.53 | not reproduced |
| KDJ & RSI | 2.05 | 2.07 | 1.64 | 1.09 | not reproduced |
| ZMR | 0.57 | 0.57 | 0.17 | 0.86 | not reproduced |
| SMA | −3.20 | −2.97 | −1.72 | 3.67 | not reproduced |
| **TradingAgents** | **+26.62** | **+30.50** | **8.21** | **0.91** | **the target** |

**Target.** One cell of that panel: the TradingAgents row for AAPL, one quarter,
one decision per trading day. Everything in this repository is scored against
that single result vector. The four rule-based baselines are intentionally left
out — they are cheap to recompute but reproducing them is a separate question
(do the authors' baselines themselves replicate) that this study does not ask.

**The single change.** `max_debate_rounds` is switched from `1` to `2`, i.e. one
extra bull/bear exchange. The paper states that the number of debate rounds *n*
is set dynamically by a debate-facilitator agent, but never reports what *n*
actually took, so the value cannot be recovered from the paper; v0.7.1 replaces
that facilitator with a fixed counter, which has to be given a number. The change
therefore measures the sensitivity of the reported result to the one quantity a
reader cannot reconstruct. See [§5](#5-the-change).

---

## 2. Environment

| Item | Value |
|---|---|
| OS | Windows 11 |
| Python | 3.13.14 (`uv`-managed venv in `.venv/`) |
| Upstream code | `TauricResearch/TradingAgents` @ v0.7.1 (2026-11 shallow clone) |
| LLM provider | DeepSeek, OpenAI-compatible endpoint `https://api.deepseek.com` |
| Deep-think model | `deepseek-v4-pro` (paper: `o1-preview`) |
| Quick-think model | `deepseek-v4-flash` (paper: `gpt-4o` / `gpt-4o-mini`) |
| Temperature | `0.0` |
| Analysts | `market, social, news, fundamentals` (all four) |
| Decision frequency | one decision per trading day |

The paper's backbones (`o1-preview`, `gpt-4o`) were not available for this study.
The model substitution is recorded here in full because **nothing in this
repository is directly comparable to the paper's numbers**: a different LLM
family, a different price/vendor path and a different framework version all
intervene between the paper's reported figures and the ones measured here.

### Installation

```bash
uv venv .venv --python 3.13
uv pip install --python .venv/Scripts/python.exe -e .   # editable: the harness
                                                        # must see the local tree
```

### Configuration

Create `.env` at the repository root (it is git-ignored; never commit keys):

```ini
DEEPSEEK_API_KEY=<key>

# SEC EDGAR throttles non-browser user agents from this network.
SEC_EDGAR_USER_AGENT=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 \
(KHTML, like Gecko) Chrome/126.0 Safari/537.36

TRADINGAGENTS_LLM_PROVIDER=deepseek
TRADINGAGENTS_DEEP_THINK_LLM=deepseek-v4-pro
TRADINGAGENTS_QUICK_THINK_LLM=deepseek-v4-flash
TRADINGAGENTS_TEMPERATURE=0.0
TRADINGAGENTS_MAX_DEBATE_ROUNDS=1
TRADINGAGENTS_MAX_RISK_ROUNDS=1
```

### Running one decision

```bash
.venv/Scripts/python.exe scripts/run_cell.py --ticker AAPL --date 2024-01-02 --debate-rounds 1
```

Each call writes a one-line summary to `data/cells/<TICKER>_<DATE>_r<N>.json`
(decision, wall-clock, model names) and the full pipeline state to
`data/results/`, which the upstream code produces on its own.

### Network note (this is a hard requirement, not a convenience)

The host machine routes traffic through an egress proxy that Yahoo Finance
answers with **HTTP 429**. Every market-data call in the authors' code path goes
to Yahoo, so out of the box the market analyst receives `DATA_UNAVAILABLE` and
the pipeline still returns a confident-looking decision. Two things are needed:

1. Route the process through a working HTTP proxy (`RESEARCH_PROXY`, default
   `http://127.0.0.1:4780`).
2. Set `yfinance.config.network.proxy` explicitly — `yfinance` builds its own
   `curl_cffi` session and **ignores `HTTP_PROXY`/`HTTPS_PROXY` entirely**.

Both are done at the top of `scripts/run_cell.py`, before `tradingagents` is
imported. No file of the authors' code is modified to achieve this.

### Price data is frozen

Even with a working proxy, Yahoo answered this IP with 429 on roughly a third of
attempts, and the authors' own cache only counts as fresh on the calendar day it
was written — so the same command can produce a different result tomorrow purely
because the cache expired. A 122-cell sweep cannot tolerate either behaviour.

`data/frozen/<TICKER>-OHLCV.csv` therefore holds the authors' own five-year
download, committed to the repository, and `scripts/_offline_yf.py` serves
`yf.Ticker.history` from it. This is an environment workaround, not a change to
the method: `get_indicators` already reads the same CSV through the authors'
cache, so the two price views stay identical instead of diverging whenever the
network is unhealthy. Tickers without a frozen file fall through to the network,
so nothing is fabricated. Pass `--no-freeze` to use the live feed.

Consequence for the report: the price series is identical across runs by
construction, so any difference between the two arms is attributable to the
debate-round change and to LLM sampling — not to the data.

---

## 3. Metrics

The upstream `tradingagents/backtest.py` scores decisions by rating hit-rate and
alpha against a benchmark. It does **not** compute the paper's CR / ARR / SR /
MDD, so `scripts/score.py` rebuilds those four from the collected decisions
under an explicitly documented convention:

- position mapping: `Buy = +1`, `Overweight = +0.5`, `Hold = 0`,
  `Underweight = −0.5`, `Sell = −1` (the same directional scale as the paper);
- execution: the decision for trading day *t* is applied to the close-to-close
  return of day *t+1* (no look-ahead — the pipeline only sees data up to *t*);
- CR: compounded sum over the quarter; ARR: CR annualised over 252/n days;
- SR: mean/std of daily strategy returns × √252, risk-free rate 0;
- MDD: minimum of the running drawdown of the compounded equity curve;
- buy & hold: same window, long-only, same price series.

`scripts/fetch_benchmark.py` pulls the benchmark price series once so scoring is
offline and deterministic.

---

## 4. Results

_Pending — the sweep is running. This section will report, for the baseline arm
and the changed arm, the four metrics plus their dispersion over repeated runs
(the pipeline is stochastic even at `temperature=0`), the token cost, and the
per-cell agreement rate._

---

## 5. The change

`max_debate_rounds: 1 → 2` — one extra round of the bull/bear researcher debate.

Why this one:

- **Isolated.** It is a single configuration value. No code, prompt or data path
  is touched, so any delta is attributable to the debate depth alone.
- **Measurable.** It changes the number of LLM calls per decision and therefore
  both the decision itself and the cost. Both are recorded per cell.
- **Substantive.** The paper treats *n* as a hyper-parameter but never states its
  value — a genuine reproducibility gap. Measuring the sensitivity around it
  says whether the reported result is robust to the one number a reader cannot
  recover from the paper.

Re-run with:

```bash
.venv/Scripts/python.exe scripts/run_cell.py --ticker AAPL --date 2024-01-02 --debate-rounds 2
```

---

## 6. Reproduction obstacles found so far

| Obstacle | Effect | Status |
|---|---|---|
| Yahoo Finance returns 429 through the default egress | Market analyst sees no data; decisions are made blind | Worked around (proxy + `yf.config.network.proxy`) |
| `yfinance` ignores `HTTP_PROXY` | Same as above, even with a working proxy exported | Worked around (explicit config) |
| Yahoo's own cache is only valid on the day it was written (`_cache_is_fresh` compares mtime) | The same backtest is not reproducible across days with no code change | Worked around (frozen CSV in `data/frozen/`) |
| Yahoo still returns 429 intermittently through the proxy | One cell in three lost its price data mid-sweep | Worked around (frozen CSV) |
| StockTwits API and Reddit JSON return 403 from this network | Social analyst degrades to "all sources unavailable" | **Not fixed** — recorded as a comparability loss |
| SEC EDGAR rejects non-browser UA | Fundamentals analyst gets nothing | Worked around (browser-style UA) |
| Paper uses `o1-preview` / `gpt-4o`; neither accessible | Model substitution | Recorded, not comparable |
| Paper's repo version (v0.1.x) differs from v0.7.1 used here | Pipeline shape changed between releases | Recorded |
| `max_debate_rounds` value undisclosed in the paper | Baseline had to be assumed | This is the object of study |

---

## 7. Repository layout

```
scripts/
  _net.py              proxy resolution (the port moves between restarts)
  _offline_yf.py       serve yf.Ticker.history from data/frozen/
  run_cell.py          one (ticker, date) decision; the unit of the experiment
  run_sweep.py         run a date range for one arm, resumable
  score.py             CR / ARR / SR / MDD from collected decisions
  fetch_benchmark.py   benchmark price series for scoring
data/
  cells/               per-decision summaries (committed)
  frozen/              frozen OHLCV, five years (committed)
  benchmark/           benchmark close series (committed)
  pilot/               two throwaway cells used to measure cost (committed)
  cache/               market-data cache (git-ignored, regenerated)
  results/             full pipeline state logs (git-ignored)
report/                written report and figures
tradingagents/         upstream code (unmodified)
```

---

## 8. Citation

```bibtex
@article{xiao2024tradingagents,
  title  = {TradingAgents: Multi-Agents LLM Financial Trading Framework},
  author = {Xiao, Yijia and Sun, Edward and Luo, Di and Wang, Wei},
  journal= {arXiv preprint arXiv:2412.20138},
  year   = {2024}
}
```

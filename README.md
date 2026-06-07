# opponent-dominance-gate-suite

Opponent/price-dominance gate scanners with backtest, validation, and attribution for Polymarket entry timing.

## Why it exists

In the Polymarket 5-minute crypto up/down markets, the order book often reveals which side the crowd has already committed to. Firing the *losing* side after the market has tipped is the single largest source of avoidable loss. This suite discovers, validates, and attributes **entry gates** — rules that block a fire when the opposing side has shown sustained dominance (e.g. "block DN when UP_ask ≥ $0.50 for 2 sustained ticks before cd≤210"). It is the research-and-evidence layer that produced the DN/UP opponent gates wired into the live engine.

## What's inside

All scripts are **offline, read-only analyzers** over recorded market history — no live trading, no order placement.

| Module | Purpose |
| --- | --- |
| `exhaustive_gate_scanner.py` | Brute-forces ~10k+ opponent-threshold × duration × entry-zone × dwell combinations, Bonferroni-corrected. The discovery workhorse. |
| `extended_gate_scanner.py` | Scans indicators the exhaustive run missed: BN (Binance) delta, own-side lows, combined gates, staircase momentum, trajectory peaks. |
| `interval_scanner.py` | Sweeps the sampling interval (3–60s) × threshold × consec to find how fast vs. how selectively dominance should be detected. |
| `price_gate_analysis.py` | Studies early opening price (cd≈290–250 up_ask / spread) as a noise-free conviction signal; full Bayesian + permutation + Kelly battery. |
| `entry_price_gate_sweep.py` | Real-time entry-price filter sweep (block UP if entry_price < X) using the `entry_price` field already on every fire — no tick replay. |
| `dn_gate_floor.py` | Tests an entry-price floor on the DN gate (only block DN above $0.10–$0.30, since cheap DN entries stay profitable in UP markets). |
| `reversal_reducer.py` | Fire-time-only loss reducers: UP price cap, max-fires-per-market, side lock, cheap-only UP. |
| `no_gate_decision.py` | Decides the optimal default action when *neither* gate triggers (release / block all / one-side / entry-limit). |
| `gate_backtest.py` | Full reconstruction backtest from T-200→T-30 snapshots with no future bias; auto-finds data dirs or takes `--mh/--recap`. |
| `gate_comparison.py` | Head-to-head of current vs. two-stage gate policies under realistic PnL. |
| `gate_validate.py` | Look-ahead-bias guard: confirms a backtest edge isn't leaking future info (e.g. BN@END vs BN@fire-time agreement). |
| `gate_attribution.py` | Leave-one-out marginal PnL contribution per gate, and per-(strategy, gate). |
| `gate_analysis_vps.py` | argparse CLI to find the statistically optimal `dn_live` gate policy on the full history (with progress bar / ETA). |
| `investigate_improvement.py` | Sanity check that gate-driven gains are genuine recent improvement, not stale early-market data. |

## Requirements

- Python 3.8+
- `scipy` (binomial / permutation / Bayesian stats), `numpy` (used by the price/entry sweeps)
- Recorded market data from the private **polymarket-data** repo (see Data). No wallet, key, or network access is required — nothing here touches funds.

```bash
pip install scipy numpy
```

## Usage

Each script is a standalone CLI. Most take the recorded JSONL files as positional args or auto-discover them.

```bash
# Discovery: exhaustive opponent-gate scan (redirect — verbose)
python3 exhaustive_gate_scanner.py market_history.jsonl > exhaustive_results.txt 2>&1

# Sampling-interval and extended-indicator scans
python3 interval_scanner.py market_history.jsonl
python3 extended_gate_scanner.py market_history.jsonl

# Entry-price / DN-floor sweeps (need recap + history)
python3 dn_gate_floor.py market_recap_history.jsonl market_history.jsonl
python3 reversal_reducer.py market_recap_history.jsonl market_history.jsonl

# Full no-future-bias backtest (auto-finds data dir, or pass paths)
python3 gate_backtest.py /path/to/polymarket-data
python3 gate_backtest.py --mh market_history.jsonl --recap market_recap_history.jsonl

# Validate against look-ahead bias, then attribute PnL per gate
python3 gate_validate.py market_history.jsonl market_recap_history.jsonl
python3 gate_attribution.py

# Optimal dn_live policy on the full VPS history
python3 gate_analysis_vps.py \
    --history market_history.jsonl \
    --gate    opp_gate_history.jsonl \
    --out     gate_report.json
```

## Data

These scripts load recorded market data — `market_history.jsonl` (per-tick `[cd, up_bid, up_ask, dn_bid, dn_ask, ... bn_delta]` arrays + winners), `market_recap_history.jsonl` (realized fires with `entry_price`, `hypo_pnl`, gate flags), and `opp_gate_history.jsonl` — from the **private `polymarket-data` repo**. Several default to `~/polymarket-bot/` paths; point them at your local checkout via the positional args, `--mh/--recap/--history`, or by running inside the data dir. No data is committed here (see `.gitignore`).

> Private research software. No warranty; trades/handles real funds at your own risk.

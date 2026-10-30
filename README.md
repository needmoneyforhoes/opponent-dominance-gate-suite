# opponent-dominance-gate-suite

Offline scanners and backtests for Polymarket entry gates: rules that block a fire when the opposing side shows sustained order-book dominance. Read-only over recorded market history; no live trading, no order placement, no credentials.

## Scripts

| File | What it does |
| --- | --- |
| `exhaustive_gate_scanner.py` | Brute-forces ~10k+ opponent-threshold x duration x entry-zone x dwell combos, Bonferroni-corrected. |
| `extended_gate_scanner.py` | Scans indicators the exhaustive run skips: BN (Binance) delta, own-side lows, combined gates, staircase momentum, trajectory peaks. |
| `interval_scanner.py` | Sweeps sampling interval (3-60s) x threshold x consec to trade detection speed against selectivity. |
| `price_gate_analysis.py` | Tests opening price (up_ask / spread at cd 290-250) as a conviction signal; Bayesian + permutation + Kelly. |
| `entry_price_gate_sweep.py` | Sweeps an entry-price filter (block UP if entry_price < X) using the `entry_price` field on each fire; no tick replay. |
| `dn_gate_floor.py` | Sweeps an entry-price floor on the DN gate ($0.10-$0.30); cheap DN entries stay profitable in UP markets. |
| `reversal_reducer.py` | Fire-time loss reducers: UP price cap, max-fires-per-market, side lock, cheap-only UP. |
| `no_gate_decision.py` | Picks the default action when neither gate triggers: release, block all, one-side, or entry-limit. |
| `gate_backtest.py` | Reconstruction backtest from T-200 to T-30 snapshots, no future bias; auto-finds data dirs or takes `--mh/--recap`. |
| `gate_comparison.py` | Compares current vs two-stage gate policies under realized PnL. |
| `gate_validate.py` | Look-ahead-bias check: confirms an edge is not leaking future info (BN@END vs BN@fire-time). |
| `gate_attribution.py` | Leave-one-out marginal PnL per gate and per (strategy, gate). |
| `gate_analysis_vps.py` | argparse CLI to find the optimal `dn_live` gate policy on full history, with progress/ETA. |
| `investigate_improvement.py` | Checks that gate gains are recent improvement, not stale early-market data. |

## Usage

Python 3.8+, `scipy`, `numpy`. Each script is a standalone CLI taking JSONL files as positional args or auto-discovering them.

```bash
# Exhaustive opponent-gate scan (verbose)
python3 exhaustive_gate_scanner.py market_history.jsonl > exhaustive_results.txt 2>&1

# Interval and extended-indicator scans
python3 interval_scanner.py market_history.jsonl
python3 extended_gate_scanner.py market_history.jsonl

# Entry-price / DN-floor sweeps (recap + history)
python3 dn_gate_floor.py market_recap_history.jsonl market_history.jsonl
python3 reversal_reducer.py market_recap_history.jsonl market_history.jsonl

# Full no-future-bias backtest (auto-finds data dir, or pass paths)
python3 gate_backtest.py $DATA_DIR
python3 gate_backtest.py --mh market_history.jsonl --recap market_recap_history.jsonl

# Validate against look-ahead bias, then attribute PnL per gate
python3 gate_validate.py market_history.jsonl market_recap_history.jsonl
python3 gate_attribution.py

# Optimal dn_live policy on full history
python3 gate_analysis_vps.py \
    --history market_history.jsonl \
    --gate    opp_gate_history.jsonl \
    --out     gate_report.json
```

## Data

Loads `market_history.jsonl` (per-tick `[cd, up_bid, up_ask, dn_bid, dn_ask, ... bn_delta]` arrays + winners), `market_recap_history.jsonl` (realized fires with `entry_price`, `hypo_pnl`, gate flags), and `opp_gate_history.jsonl` from the private polymarket-data repo. Several scripts default to `$DATA_DIR`; override via positional args or `--mh/--recap/--history`. No data is committed (see `.gitignore`).

## Feedback

Suggestions, improvements, or something not working? Please
[open an issue](https://github.com/needmoneyforhoes/opponent-dominance-gate-suite/issues/new). Pull requests are welcome too.

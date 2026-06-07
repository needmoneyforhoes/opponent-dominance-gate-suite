#!/usr/bin/env python3
"""
DN Gate Entry Floor — only block DN fires above $X
=====================================================
The DN gate blocks ALL DN fires when UP dominant.
But cheap DN entries ($0.05-$0.15) are profitable even in UP markets.
Test: block DN only when entry > $0.10, $0.15, $0.20, $0.25, $0.30

Run: python3 dn_gate_floor.py market_recap_history.jsonl market_history.jsonl
"""
import json, sys
from collections import defaultdict

CD,UP_BID,UP_ASK,DN_BID,DN_ASK = 0,1,2,3,4

def sample_Ns(ticks, idx, interval):
    s = []; prev = 999
    for t in ticks:
        if t[CD] < prev - (interval - 0.5):
            s.append((t[CD], t[idx] or 0))
            prev = t[CD]
    return s

def check_dn_gate(ticks):
    up_s = sample_Ns(ticks, UP_ASK, 10)
    consec = 0
    for cd, p in up_s:
        if p >= 0.50: consec += 1
        else: consec = 0
        if consec >= 2 and cd <= 210:
            return True
    return False

def check_up_gate(ticks):
    dn_s = sample_Ns(ticks, DN_ASK, 3)
    n = len(dn_s)
    for i in range(3, n+1):
        subset = dn_s[:i]
        cd = subset[-1][0]
        if cd > 250: continue
        above = sum(1 for _, p in subset if p >= 0.70)
        if (above / i * 100) >= 40:
            return True
    return False

def main():
    recap_fp = sys.argv[1] if len(sys.argv) > 1 else "market_recap_history.jsonl"
    hist_fp = sys.argv[2] if len(sys.argv) > 2 else "market_history.jsonl"

    live_set = {
        'cl_gated_cheap|UP', 'cl_tight|UP',
        't4_late|DN', 't4_late|UP', 't4_sr_relaxed|DN',
        'deep_dip_scout|DN', 'deep_dip_scout|UP',
        'depth_collapse_mid|DN', 'depth_collapse_mid|UP',
        'depth_surge|DN', 'depth_surge|UP',
        'dn_deep_ask_collapse|DN', 'dn_deep_spread_collapse|DN', 'dn_mid_gap_count_spike|DN',
        'fake_train_detector|DN', 'fake_train_detector|UP',
        'up_cheap_low_flow|UP', 'up_deep_ask_collapse|UP', 'up_deep_spread_collapse|UP',
        'up_depth_collapse_mid_high|UP', 'up_mid_gap_count_spike|UP',
    }

    # Load gate data
    print(f"Loading {hist_fp}...")
    gate_data = {}
    with open(hist_fp) as f:
        for line in f:
            if not line.strip(): continue
            try:
                m = json.loads(line)
                slug = m.get('slug', '')
                if m.get('ticks') and m.get('winner'):
                    gate_data[slug] = {
                        'dn_gate': check_dn_gate(m['ticks']),
                        'up_gate': check_up_gate(m['ticks']),
                    }
            except: pass

    # Load recap
    print(f"Loading {recap_fp}...")
    with open(recap_fp) as f:
        markets = [json.loads(l) for l in f if l.strip()]
    print(f"  {len(markets)} markets")

    # Test configs
    floors = [0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 999]
    # 0 = block ALL, 999 = disabled (block nothing)

    print(f"\n{'='*110}")
    print(f"DN GATE ENTRY FLOOR — Scenario B (current + promoted) + UP gate")
    print(f"{'='*110}")
    print(f"  {'Floor':<20}{'Fires':>6}{'Wins':>6}{'WR':>6}{'PnL':>10}{'$/mkt':>8}{'DN_blk':>8}{'DN_allow':>9}")
    print(f"  {'─'*75}")

    for floor in floors:
        total_pnl = 0; total_fires = 0; total_wins = 0
        dn_blocked = 0; dn_allowed = 0
        per_strat = defaultdict(float)

        for market in markets:
            slug = market.get('slug', '')
            fires = market.get('fires', [])
            gates = gate_data.get(slug, {})
            dn_gate = gates.get('dn_gate', False)
            up_gate = gates.get('up_gate', False)

            for fire in fires:
                strat = fire.get('strategy', '?')
                side = fire.get('side', '?')
                key = f"{strat}|{side}"
                hypo = fire.get('hypo_pnl', 0) or 0
                price = fire.get('entry_price', 0) or 0

                if key not in live_set: continue

                # UP gate: block all UP when DN≥$0.70
                if side == 'UP' and up_gate: continue

                # DN gate with floor: block DN only when price > floor
                if side == 'DN' and dn_gate:
                    if price > floor:
                        dn_blocked += 1
                        continue
                    else:
                        dn_allowed += 1

                total_pnl += hypo
                total_fires += 1
                if hypo > 0: total_wins += 1
                per_strat[key] += hypo

        n = len(markets)
        wr = total_wins / total_fires if total_fires > 0 else 0
        label = f"DISABLED" if floor >= 999 else f"BLOCK ALL" if floor == 0 else f"block > ${floor:.2f}"
        print(f"  {label:<20}{total_fires:>6}{total_wins:>6}{wr:>5.0%}${total_pnl:>+9.2f}${total_pnl/n:>+7.2f}"
              f"  {dn_blocked:>6}  {dn_allowed:>7}")

    # Detailed breakdown at key floors
    for floor in [0, 0.15, 0.25, 999]:
        label = "BLOCK ALL" if floor == 0 else "DISABLED" if floor >= 999 else f"block > ${floor:.2f}"
        print(f"\n  ── Per-strategy at {label} ──")
        per_strat = defaultdict(lambda: {'fires': 0, 'pnl': 0})

        for market in markets:
            slug = market.get('slug', '')
            fires = market.get('fires', [])
            gates = gate_data.get(slug, {})
            dn_gate = gates.get('dn_gate', False)
            up_gate = gates.get('up_gate', False)

            for fire in fires:
                strat = fire.get('strategy', '?')
                side = fire.get('side', '?')
                key = f"{strat}|{side}"
                hypo = fire.get('hypo_pnl', 0) or 0
                price = fire.get('entry_price', 0) or 0

                if key not in live_set: continue
                if side == 'UP' and up_gate: continue
                if side == 'DN' and dn_gate and price > floor: continue

                per_strat[key]['fires'] += 1
                per_strat[key]['pnl'] += hypo

        print(f"  {'Strategy':<35}{'Fires':>6}{'PnL':>9}")
        for key in sorted(per_strat.keys(), key=lambda k: -per_strat[k]['pnl']):
            v = per_strat[key]
            if v['fires'] > 0:
                print(f"  {key:<35}{v['fires']:>6}${v['pnl']:>+8.2f}")

if __name__ == "__main__":
    main()

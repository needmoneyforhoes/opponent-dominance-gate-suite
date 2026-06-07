#!/usr/bin/env python3
"""
Reversal Loss Reducer — REAL-TIME patterns only
=================================================
Every pattern uses ONLY info available at fire time:
  1. UP price cap — entry_price known at fire time
  2. Max UP fires per market — count known at fire time
  3. Side lock — if already fired DN, block UP (and vice versa)
  4. Cheap-only UP — only fire UP ≤$0.20
  5. Combined: price cap + max fires

Run: python3 reversal_reducer.py market_recap_history.jsonl market_history.jsonl
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
        if consec >= 2 and cd <= 210: return True
    return False

def check_up_gate(ticks):
    dn_s = sample_Ns(ticks, DN_ASK, 3)
    n = len(dn_s)
    for i in range(3, n+1):
        subset = dn_s[:i]
        cd = subset[-1][0]
        if cd > 250: continue
        above = sum(1 for _, p in subset if p >= 0.70)
        if (above / i * 100) >= 40: return True
    return False

def realistic_pnl(entry_price, side, winner, base_shares=5):
    cost_per = entry_price + 0.01
    shares = base_shares
    total_cost = shares * cost_per
    if total_cost < 1.0 and cost_per > 0:
        shares = max(shares, int(1.0 / cost_per) + 1)
        total_cost = shares * cost_per
    settle = 1.00 if winner == side else 0.00
    return round(shares * settle - total_cost, 3), shares

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
    with open(recap_fp) as f:
        markets = [json.loads(l) for l in f if l.strip()]
    n = len(markets)
    split = int(n * 0.68)
    train = markets[:split]
    test = markets[split:]

    # Pattern configs to test
    patterns = {
        'BASELINE (no pattern)':     {'up_cap': 9.0, 'max_up': 999, 'max_dn': 999, 'side_lock': False, 'dn_cap': 9.0},
        # UP price caps
        'UP cap $0.45':              {'up_cap': 0.45, 'max_up': 999, 'max_dn': 999, 'side_lock': False, 'dn_cap': 9.0},
        'UP cap $0.40':              {'up_cap': 0.40, 'max_up': 999, 'max_dn': 999, 'side_lock': False, 'dn_cap': 9.0},
        'UP cap $0.35':              {'up_cap': 0.35, 'max_up': 999, 'max_dn': 999, 'side_lock': False, 'dn_cap': 9.0},
        'UP cap $0.30':              {'up_cap': 0.30, 'max_up': 999, 'max_dn': 999, 'side_lock': False, 'dn_cap': 9.0},
        'UP cap $0.25':              {'up_cap': 0.25, 'max_up': 999, 'max_dn': 999, 'side_lock': False, 'dn_cap': 9.0},
        # Max UP fires
        'Max 3 UP fires':            {'up_cap': 9.0, 'max_up': 3, 'max_dn': 999, 'side_lock': False, 'dn_cap': 9.0},
        'Max 5 UP fires':            {'up_cap': 9.0, 'max_up': 5, 'max_dn': 999, 'side_lock': False, 'dn_cap': 9.0},
        'Max 7 UP fires':            {'up_cap': 9.0, 'max_up': 7, 'max_dn': 999, 'side_lock': False, 'dn_cap': 9.0},
        # Max DN fires  
        'Max 3 DN fires':            {'up_cap': 9.0, 'max_up': 999, 'max_dn': 3, 'side_lock': False, 'dn_cap': 9.0},
        'Max 5 DN fires':            {'up_cap': 9.0, 'max_up': 999, 'max_dn': 5, 'side_lock': False, 'dn_cap': 9.0},
        # DN price caps
        'DN cap $0.45':              {'up_cap': 9.0, 'max_up': 999, 'max_dn': 999, 'side_lock': False, 'dn_cap': 0.45},
        'DN cap $0.40':              {'up_cap': 9.0, 'max_up': 999, 'max_dn': 999, 'side_lock': False, 'dn_cap': 0.40},
        'DN cap $0.35':              {'up_cap': 9.0, 'max_up': 999, 'max_dn': 999, 'side_lock': False, 'dn_cap': 0.35},
        # Side lock
        'Side lock (first side only)': {'up_cap': 9.0, 'max_up': 999, 'max_dn': 999, 'side_lock': True, 'dn_cap': 9.0},
        # Combos
        'UP cap $0.40 + max 5':      {'up_cap': 0.40, 'max_up': 5, 'max_dn': 999, 'side_lock': False, 'dn_cap': 9.0},
        'UP cap $0.35 + max 3':      {'up_cap': 0.35, 'max_up': 3, 'max_dn': 999, 'side_lock': False, 'dn_cap': 9.0},
        'UP cap $0.40 + DN cap $0.40': {'up_cap': 0.40, 'max_up': 999, 'max_dn': 999, 'side_lock': False, 'dn_cap': 0.40},
        'Both cap $0.35':            {'up_cap': 0.35, 'max_up': 999, 'max_dn': 999, 'side_lock': False, 'dn_cap': 0.35},
        'Both cap $0.35 + max 5':    {'up_cap': 0.35, 'max_up': 5, 'max_dn': 5, 'side_lock': False, 'dn_cap': 0.35},
    }

    for ds_name, dataset in [("OOS", test), ("ALL", markets)]:
        print(f"\n{'='*110}")
        print(f"  PATTERN COMPARISON — {ds_name} ({len(dataset)} markets) + DN gate $0.25 floor + UP gate $0.70")
        print(f"{'='*110}")
        print(f"  {'Pattern':<35}{'Fires':>6}{'Wins':>6}{'WR':>6}{'PnL':>10}{'$/mkt':>8}{'W_mkt':>6}{'L_mkt':>6}{'MktWR':>6}")
        print(f"  {'─'*90}")

        results = []
        for pname, cfg in patterns.items():
            total_pnl = 0; total_fires = 0; total_wins = 0
            win_mkts = 0; loss_mkts = 0

            for market in dataset:
                slug = market.get('slug', '')
                fires = market.get('fires', [])
                gates = gate_data.get(slug, {})
                dn_gate = gates.get('dn_gate', False)
                up_gate = gates.get('up_gate', False)
                winner = market.get('winner', '?')

                # Sort fires by cd descending (earliest first) to simulate real-time
                sorted_fires = sorted(fires, key=lambda f: -(f.get('cd', 0) or 0))

                up_count = 0; dn_count = 0
                first_side = None
                mkt_pnl = 0

                for fire in sorted_fires:
                    strat = fire.get('strategy', '?')
                    side = fire.get('side', '?')
                    key = f"{strat}|{side}"
                    entry = fire.get('entry_price', 0) or 0
                    cd = fire.get('cd', 0) or 0

                    if key not in live_set: continue

                    # DN gate with $0.25 floor
                    if side == 'DN' and dn_gate and entry > 0.25: continue
                    # UP gate
                    if side == 'UP' and up_gate: continue

                    # Pattern filters (all real-time — only use info at fire time)
                    if side == 'UP':
                        if entry > cfg['up_cap']: continue
                        if up_count >= cfg['max_up']: continue
                    if side == 'DN':
                        if entry > cfg['dn_cap']: continue
                        if dn_count >= cfg['max_dn']: continue

                    # Side lock
                    if cfg['side_lock']:
                        if first_side is None:
                            first_side = side
                        elif side != first_side:
                            continue

                    pnl, shares = realistic_pnl(entry, side, winner)
                    total_pnl += pnl
                    total_fires += 1
                    if pnl > 0: total_wins += 1
                    mkt_pnl += pnl

                    if side == 'UP': up_count += 1
                    elif side == 'DN': dn_count += 1

                if mkt_pnl > 0: win_mkts += 1
                elif mkt_pnl < 0: loss_mkts += 1

            n_ds = len(dataset)
            wr = total_wins / total_fires if total_fires > 0 else 0
            mkt_wr = win_mkts / (win_mkts + loss_mkts) if (win_mkts + loss_mkts) > 0 else 0
            per_mkt = total_pnl / n_ds if n_ds > 0 else 0

            results.append((pname, total_fires, total_wins, wr, total_pnl, per_mkt, win_mkts, loss_mkts, mkt_wr))
            print(f"  {pname:<35}{total_fires:>6}{total_wins:>6}{wr:>5.0%}${total_pnl:>+9.2f}${per_mkt:>+7.2f}{win_mkts:>6}{loss_mkts:>6}{mkt_wr:>5.0%}")

        # Sort by PnL
        results.sort(key=lambda x: -x[4])
        print(f"\n  RANKED BY PnL:")
        for i, (pn, tf, tw, wr, tp, pm, wm, lm, mwr) in enumerate(results[:5], 1):
            print(f"    #{i} {pn:<35} ${tp:>+9.2f} (${pm:>+.2f}/mkt, {mwr:.0%} mkt WR)")

if __name__ == "__main__":
    main()

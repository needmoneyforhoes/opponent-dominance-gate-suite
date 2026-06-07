#!/usr/bin/env python3
"""
Current UP Gate vs Two-Stage — which performs better?
========================================================
Tests:
  1. Current UP gate only (DN≥$0.70 p40%)
  2. Two-stage only (cd210 std≤0.04 → cd190 drop≥0.20)
  3. BOTH (either blocks → OR logic)
  4. Two-stage REPLACES current (disable old, enable new)
  5. Current + Config 2 (recovery) as shadow comparison

All with DN gate $0.25 floor always active.

Run: python3 gate_comparison.py market_recap_history.jsonl market_history.jsonl
"""
import json, sys

CD,UP_BID,UP_ASK,DN_BID,DN_ASK = 0,1,2,3,4

def get_samples(ticks, cp):
    s = []; prev = 999
    for t in ticks:
        cd = t[CD]
        if cd >= cp and cd < prev - 2.5:
            s.append((cd, t[UP_ASK] or 0, t[DN_ASK] or 0))
            prev = cd
    return s

def realistic_pnl(entry_price, side, winner, base_shares=5):
    cost_per = entry_price + 0.01
    shares = base_shares
    total_cost = shares * cost_per
    if total_cost < 1.0 and cost_per > 0:
        shares = max(shares, int(1.0 / cost_per) + 1)
        total_cost = shares * cost_per
    settle = 1.00 if winner == side else 0.00
    return round(shares * settle - total_cost, 3)

def check_dn_gate(ticks):
    prev = 999; consec = 0
    for t in ticks:
        cd = t[CD]; up = t[UP_ASK] or 0
        if cd < prev - 9.5:
            if up >= 0.50: consec += 1
            else: consec = 0
            if consec >= 2 and cd <= 210: return True
            prev = cd
    return False

def check_up_gate_070(ticks):
    """Current UP gate: DN≥$0.70 p40% (3s)"""
    prev = 999; samples = []
    for t in ticks:
        cd = t[CD]; dn = t[DN_ASK] or 0
        if cd < prev - 2.5:
            samples.append((cd, dn)); prev = cd
            n = len(samples)
            if n >= 3 and cd <= 250:
                above = sum(1 for _, d in samples if d >= 0.70)
                if (above / n * 100) >= 40: return True
    return False

def check_twostage_drop(ticks):
    """Config 1: cd210 std≤0.04 → cd190 up_drop≥0.20"""
    s1 = get_samples(ticks, 210)
    dn1 = [s[2] for s in s1]
    if len(dn1) < 1: return False  # no data
    if len(dn1) == 1: std = 0
    else:
        avg = sum(dn1) / len(dn1)
        std = (sum((d - avg)**2 for d in dn1) / len(dn1)) ** 0.5
    if std > 0.04: return False  # Stage 1 fails
    
    s2 = get_samples(ticks, 190)
    up2 = [s[1] for s in s2]
    if len(up2) < 2: return False
    drop = max(up2) - up2[-1]
    return drop >= 0.20

def check_twostage_recovery(ticks):
    """Config 2: cd220 std≤0.05 → cd190 recovery≥0.20"""
    s1 = get_samples(ticks, 220)
    dn1 = [s[2] for s in s1]
    if len(dn1) < 1: std = 0
    elif len(dn1) == 1: std = 0
    else:
        avg = sum(dn1) / len(dn1)
        std = (sum((d - avg)**2 for d in dn1) / len(dn1)) ** 0.5
    if std > 0.05: return False
    
    s2 = get_samples(ticks, 190)
    dn2 = [s[2] for s in s2]
    if len(dn2) < 2: return False
    rec = dn2[-1] - min(dn2)
    return rec >= 0.20

def main():
    recap_fp = sys.argv[1] if len(sys.argv) > 1 else "market_recap_history.jsonl"
    hist_fp = sys.argv[2] if len(sys.argv) > 2 else "market_history.jsonl"

    live_set = {
        'cl_gated_cheap|UP', 'cl_tight|UP',
        't4_late|DN', 't4_late|UP', 't4_sr_relaxed|DN',
        'depth_collapse_mid|DN', 'depth_surge|DN', 'fake_train_detector|DN',
        'dn_deep_ask_collapse|DN', 'dn_deep_spread_collapse|DN', 'dn_mid_gap_count_spike|DN',
        'up_cheap_low_flow|UP', 'up_depth_collapse_mid_high|UP',
        'up_deep_spread_collapse|UP', 'up_deep_ask_collapse|UP', 'up_mid_gap_count_spike|UP',
        'deep_dip_scout|DN', 'deep_dip_scout|UP',
    }

    # Load tick data
    print(f"Loading {hist_fp}...")
    tick_data = {}
    with open(hist_fp) as f:
        for line in f:
            if not line.strip(): continue
            try:
                m = json.loads(line)
                slug = m.get('slug', '')
                if m.get('ticks') and m.get('winner'):
                    tick_data[slug] = {
                        'ticks': m['ticks'],
                        'dn_gate': check_dn_gate(m['ticks']),
                        'up070': check_up_gate_070(m['ticks']),
                        'ts_drop': check_twostage_drop(m['ticks']),
                        'ts_rec': check_twostage_recovery(m['ticks']),
                        'winner': m['winner'],
                    }
            except: pass

    print(f"Loading {recap_fp}...")
    with open(recap_fp) as f:
        markets = [json.loads(l) for l in f if l.strip()]
    n = len(markets)
    split = int(n * 0.68)

    # Overlap analysis first
    print(f"\n{'='*100}")
    print(f"OVERLAP ANALYSIS — do they catch the SAME or DIFFERENT markets?")
    print(f"{'='*100}")
    both = 0; only_070 = 0; only_ts = 0; neither = 0
    for market in markets:
        slug = market.get('slug', '')
        td = tick_data.get(slug)
        if not td: continue
        u = td['up070']; t = td['ts_drop']
        if u and t: both += 1
        elif u: only_070 += 1
        elif t: only_ts += 1
        else: neither += 1
    print(f"  Both trigger: {both}")
    print(f"  Only current (DN≥$0.70): {only_070}")
    print(f"  Only two-stage (drop): {only_ts}")
    print(f"  Neither: {neither}")
    print(f"  → {'COMPLEMENTARY' if only_070 > 0 and only_ts > 0 else 'REDUNDANT'}")

    # Same for recovery
    both_r = 0; only_070_r = 0; only_ts_r = 0
    for market in markets:
        slug = market.get('slug', '')
        td = tick_data.get(slug)
        if not td: continue
        u = td['up070']; t = td['ts_rec']
        if u and t: both_r += 1
        elif u: only_070_r += 1
        elif t: only_ts_r += 1
    print(f"\n  vs Recovery gate:")
    print(f"  Both trigger: {both_r}")
    print(f"  Only current: {only_070_r}")
    print(f"  Only recovery: {only_ts_r}")

    configs = {
        'A) No UP gate (baseline)':      lambda td: False,
        'B) Current only (DN≥0.70)':     lambda td: td['up070'],
        'C) Two-stage drop only':        lambda td: td['ts_drop'],
        'D) Two-stage recovery only':    lambda td: td['ts_rec'],
        'E) Current OR drop':            lambda td: td['up070'] or td['ts_drop'],
        'F) Current OR recovery':        lambda td: td['up070'] or td['ts_rec'],
        'G) Current OR drop OR recovery': lambda td: td['up070'] or td['ts_drop'] or td['ts_rec'],
        'H) Drop OR recovery (no current)': lambda td: td['ts_drop'] or td['ts_rec'],
    }

    for ds_name, dataset in [("TRAIN", markets[:split]), ("OOS", markets[split:]), ("ALL", markets)]:
        print(f"\n{'='*100}")
        print(f"  {ds_name} ({len(dataset)} markets)")
        print(f"{'='*100}")
        print(f"  {'Config':<40}{'PnL':>10}{'$/mkt':>7}{'Sharpe':>7}{'MktWR':>6}{'MaxDD':>7}{'Blk':>5}{'FP%':>5}")
        print(f"  {'─'*90}")

        for cname, block_fn in configs.items():
            market_pnls = []
            total_blocked = 0; blocked_won = 0

            for market in dataset:
                slug = market.get('slug', '')
                td = tick_data.get(slug)
                if not td: continue

                up_blocked = block_fn(td)
                mkt_pnl = 0

                for fire in market.get('fires', []):
                    key = f"{fire.get('strategy','?')}|{fire.get('side','?')}"
                    if key not in live_set: continue
                    side = fire.get('side', '?')
                    entry = fire.get('entry_price', 0) or 0

                    # DN gate always active
                    if side == 'DN' and td['dn_gate'] and entry > 0.25: continue
                    # UP gate per config
                    if side == 'UP' and up_blocked:
                        total_blocked += 1
                        if td['winner'] == 'UP': blocked_won += 1
                        continue

                    mkt_pnl += realistic_pnl(entry, side, td['winner'])

                market_pnls.append(mkt_pnl)

            # Stats
            total = sum(market_pnls)
            n_ds = len(market_pnls)
            mean = total / n_ds if n_ds > 0 else 0
            var = sum((p - mean)**2 for p in market_pnls) / n_ds if n_ds > 1 else 0
            std = var ** 0.5
            sharpe = mean / std if std > 0 else 0
            wins = sum(1 for p in market_pnls if p > 0)
            losses = sum(1 for p in market_pnls if p < 0)
            wr = wins / (wins + losses) if wins + losses > 0 else 0
            cumsum = 0; peak = 0; max_dd = 0
            for p in market_pnls:
                cumsum += p; peak = max(peak, cumsum); max_dd = max(max_dd, peak - cumsum)
            fp = blocked_won / total_blocked if total_blocked > 0 else 0

            marker = '✅' if total > 0 and fp < 0.20 else '·'
            print(f"  {cname:<40}${total:>+9.2f}${mean:>+6.2f}  {sharpe:>5.3f} {wr:>4.0%}  ${max_dd:>5.2f} {total_blocked:>4} {fp:>4.0%} {marker}")

if __name__ == "__main__":
    main()

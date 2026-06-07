#!/usr/bin/env python3
"""
Exhaustive Sampling Interval Scanner
=======================================
Tests intervals: 3, 5, 7, 10, 15, 20, 30, 50, 60 seconds
For EACH interval, tests ALL threshold × consec combos.

At 10s interval: consec=7 → 70s sustained
At 5s interval:  consec=7 → 35s sustained  
At 30s interval: consec=7 → 210s sustained (most of market)
At 60s interval: consec=3 → 180s sustained

Maybe shorter intervals catch dominance FASTER (more profitable).
Maybe longer intervals are more SELECTIVE (fewer false positives).

Tests DN gate (price-based) and UP gate (BN-based) separately.

Run: python3 interval_scanner.py market_history.jsonl > interval_results.txt 2>&1
"""
import json, sys

CD,UP_BID,UP_ASK,DN_BID,DN_ASK = 0,1,2,3,4
BN_DELTA = 12

def load(fp):
    mkts = []
    with open(fp) as f:
        for line in f:
            if not line.strip(): continue
            m = json.loads(line)
            if m.get('ticks') and m.get('winner') in ('UP','DN'):
                mkts.append(m)
    mkts.sort(key=lambda m: m.get('ts',''))
    return mkts

def sample_Ns(ticks, idx, interval):
    s = []; prev = 999
    for t in ticks:
        if t[CD] < prev - (interval - 0.5):
            s.append((t[CD], t[idx] or 0))
            prev = t[CD]
    return s

def simulate_dn_gate(markets, interval, thresh, consec, fire_after_cd):
    total = 0; blocked = 0; blocked_won = 0; fires = 0; allowed_won = 0; allowed_lost = 0

    for m in markets:
        ticks = m['ticks']
        winner = m['winner']
        up_s = sample_Ns(ticks, UP_ASK, interval)
        dn_s = sample_Ns(ticks, DN_ASK, interval)

        gate = False; cur = 0
        for cd, p in up_s:
            if cd >= fire_after_cd:
                if p >= thresh:
                    cur += 1
                    if cur >= consec: gate = True
                else:
                    cur = 0

        fired = set()
        for cd, price in dn_s:
            if cd < 30 or price <= 0: continue
            if cd >= fire_after_cd: continue
            zone = round(price * 20) / 20
            if zone in fired: continue
            fired.add(zone)
            won = (winner == 'DN')
            pnl = ((1.0 - price) * 5) if won else (-price * 5)
            fires += 1
            if gate:
                blocked += 1
                if won: blocked_won += 1
            else:
                total += pnl
                if won: allowed_won += 1
                else: allowed_lost += 1

    return {'pnl': total, 'blocked': blocked, 'blocked_won': blocked_won,
            'fires': fires, 'allowed_won': allowed_won, 'allowed_lost': allowed_lost}

def simulate_up_gate_bn(markets, interval, bn_thresh, bn_pct, fire_after_cd):
    total = 0; blocked = 0; blocked_won = 0; fires = 0; allowed_won = 0; allowed_lost = 0

    for m in markets:
        ticks = m['ticks']
        winner = m['winner']
        up_s = sample_Ns(ticks, UP_ASK, interval)
        bn_s = sample_Ns(ticks, BN_DELTA, interval)

        gate = False
        before_bn = [p for cd, p in bn_s if cd >= fire_after_cd]
        if len(before_bn) >= 3:
            neg = sum(1 for b in before_bn if b < bn_thresh)
            if (neg / len(before_bn) * 100) >= bn_pct:
                gate = True

        fired = set()
        for cd, price in up_s:
            if cd < 30 or price <= 0: continue
            if cd >= fire_after_cd: continue
            zone = round(price * 20) / 20
            if zone in fired: continue
            fired.add(zone)
            won = (winner == 'UP')
            pnl = ((1.0 - price) * 5) if won else (-price * 5)
            fires += 1
            if gate:
                blocked += 1
                if won: blocked_won += 1
            else:
                total += pnl
                if won: allowed_won += 1
                else: allowed_lost += 1

    return {'pnl': total, 'blocked': blocked, 'blocked_won': blocked_won,
            'fires': fires, 'allowed_won': allowed_won, 'allowed_lost': allowed_lost}

def simulate_up_gate_dn(markets, interval, dn_thresh, dn_pct, fire_after_cd):
    total = 0; blocked = 0; blocked_won = 0; fires = 0

    for m in markets:
        ticks = m['ticks']
        winner = m['winner']
        up_s = sample_Ns(ticks, UP_ASK, interval)
        dn_s = sample_Ns(ticks, DN_ASK, interval)

        gate = False
        before_dn = [p for cd, p in dn_s if cd >= fire_after_cd]
        if len(before_dn) >= 3:
            above = sum(1 for d in before_dn if d >= dn_thresh)
            if (above / len(before_dn) * 100) >= dn_pct:
                gate = True

        fired = set()
        for cd, price in up_s:
            if cd < 30 or price <= 0: continue
            if cd >= fire_after_cd: continue
            zone = round(price * 20) / 20
            if zone in fired: continue
            fired.add(zone)
            won = (winner == 'UP')
            pnl = ((1.0 - price) * 5) if won else (-price * 5)
            fires += 1
            if gate:
                blocked += 1
                if won: blocked_won += 1
            else:
                total += pnl

    return {'pnl': total, 'blocked': blocked, 'blocked_won': blocked_won, 'fires': fires}

def main():
    fp = sys.argv[1] if len(sys.argv) > 1 else "market_history.jsonl"
    print(f"Loading {fp}...")
    raw = load(fp)
    n = len(raw)
    split = int(n * 0.68)
    train = raw[:split]
    test = raw[split:]
    print(f"Loaded {n} markets (Train={len(train)} Test={len(test)})")

    intervals = [3, 5, 7, 10, 15, 20, 30, 50, 60]

    # ══════════════════════════════════════════════════════════════
    # DN GATE: Find optimal (interval, threshold, consec, checkpoint)
    # ══════════════════════════════════════════════════════════════
    for ds_name, dataset in [("OOS", test), ("TRAIN", train)]:
        # Get baseline
        base = simulate_dn_gate(dataset, 10, 99, 999, 999)
        # Actually need baseline with no gate
        base_pnl = 0
        for m in dataset:
            dn_s = sample_Ns(m['ticks'], DN_ASK, 10)
            fired = set()
            for cd, price in dn_s:
                if cd < 30 or price <= 0: continue
                zone = round(price * 20) / 20
                if zone in fired: continue
                fired.add(zone)
                won = (m['winner'] == 'DN')
                base_pnl += ((1.0 - price) * 5) if won else (-price * 5)

        print(f"\n{'='*130}")
        print(f"DN GATE — {ds_name} ({len(dataset)} markets) — Baseline DN PnL: ${base_pnl:+.0f}")
        print(f"{'='*130}")

        results = []
        for interval in intervals:
            for thresh_int in range(50, 86, 5):
                thresh = thresh_int / 100
                # Calculate consec values that give ~30s to ~210s sustained
                for duration_sec in [21, 30, 42, 50, 60, 70, 90, 105, 120, 150, 180, 210]:
                    consec = max(2, round(duration_sec / interval))
                    # Skip duplicates (different durations can give same consec)
                    combo_key = (interval, thresh_int, consec)
                    for fire_cd in [250, 230, 210, 180, 150]:
                        r = simulate_dn_gate(dataset, interval, thresh, consec, fire_cd)
                        if r['blocked'] < 3: continue
                        r['vs'] = r['pnl'] - base_pnl
                        r['name'] = f"int={interval}s thr=${thresh:.2f} c{consec} ({consec*interval}s) cd={fire_cd}"
                        r['interval'] = interval
                        r['duration'] = consec * interval
                        r['ratio'] = f"{r['blocked']-r['blocked_won']}:{r['blocked_won']}"
                        results.append(r)

        results.sort(key=lambda r: -r['vs'])
        print(f"  Tested {len(results)} combos")
        print(f"\n  TOP 30:")
        print(f"  {'#':<4}{'Config':<55}{'PnL':>8}{'Blk':>6}{'BlkW':>6}{'Ratio':>8}{'vs':>8}{'Int':>5}{'Dur':>5}")
        print(f"  {'─'*110}")
        for i, r in enumerate(results[:30]):
            print(f"  {i+1:<4}{r['name']:<55}${r['pnl']:>+6.0f}  {r['blocked']:>4}  "
                  f"{r['blocked_won']:>4}  {r['ratio']:>6}  ${r['vs']:>+6.0f}  {r['interval']:>3}s  {r['duration']:>3}s")

        # Best per interval
        print(f"\n  BEST PER INTERVAL:")
        print(f"  {'Interval':<10}{'Best config':<50}{'PnL':>8}{'vs':>8}{'Blk':>6}{'Ratio':>8}")
        print(f"  {'─'*95}")
        for iv in intervals:
            iv_results = [r for r in results if r['interval'] == iv]
            if iv_results:
                best = max(iv_results, key=lambda r: r['vs'])
                print(f"  {iv:>3}s      {best['name']:<50}${best['pnl']:>+6.0f}${best['vs']:>+6.0f}  "
                      f"{best['blocked']:>4}  {best['ratio']:>6}")

    # ══════════════════════════════════════════════════════════════
    # UP GATE (BN-based): Find optimal interval
    # ══════════════════════════════════════════════════════════════
    for ds_name, dataset in [("OOS", test), ("TRAIN", train)]:
        base_pnl = 0
        for m in dataset:
            up_s = sample_Ns(m['ticks'], UP_ASK, 10)
            fired = set()
            for cd, price in up_s:
                if cd < 30 or price <= 0: continue
                zone = round(price * 20) / 20
                if zone in fired: continue
                fired.add(zone)
                won = (m['winner'] == 'UP')
                base_pnl += ((1.0 - price) * 5) if won else (-price * 5)

        print(f"\n{'='*130}")
        print(f"UP GATE (BN) — {ds_name} ({len(dataset)} markets) — Baseline UP PnL: ${base_pnl:+.0f}")
        print(f"{'='*130}")

        results = []
        for interval in intervals:
            for bn_thresh in [-0.01, -0.02, -0.03, -0.05, -0.08]:
                for bn_pct in [30, 40, 50, 60, 70, 80]:
                    for fire_cd in [250, 230, 210, 180]:
                        r = simulate_up_gate_bn(dataset, interval, bn_thresh, bn_pct, fire_cd)
                        if r['blocked'] < 3: continue
                        r['vs'] = r['pnl'] - base_pnl
                        r['name'] = f"int={interval}s BN<{bn_thresh} p{bn_pct}% cd={fire_cd}"
                        r['interval'] = interval
                        r['ratio'] = f"{r['blocked']-r['blocked_won']}:{r['blocked_won']}"
                        results.append(r)

        results.sort(key=lambda r: -r['vs'])
        print(f"  Tested {len(results)} combos")
        print(f"\n  TOP 20:")
        print(f"  {'#':<4}{'Config':<50}{'PnL':>8}{'Blk':>6}{'BlkW':>6}{'Ratio':>8}{'vs':>8}")
        print(f"  {'─'*95}")
        for i, r in enumerate(results[:20]):
            print(f"  {i+1:<4}{r['name']:<50}${r['pnl']:>+6.0f}  {r['blocked']:>4}  "
                  f"{r['blocked_won']:>4}  {r['ratio']:>6}  ${r['vs']:>+6.0f}")

        print(f"\n  BEST PER INTERVAL:")
        print(f"  {'Interval':<10}{'Best config':<50}{'vs':>8}{'Blk':>6}{'Ratio':>8}")
        print(f"  {'─'*85}")
        for iv in intervals:
            iv_r = [r for r in results if r['interval'] == iv]
            if iv_r:
                best = max(iv_r, key=lambda r: r['vs'])
                print(f"  {iv:>3}s      {best['name']:<50}${best['vs']:>+6.0f}  "
                      f"{best['blocked']:>4}  {best['ratio']:>6}")

    # ══════════════════════════════════════════════════════════════
    # UP GATE (DN price): Find optimal interval
    # ══════════════════════════════════════════════════════════════
    for ds_name, dataset in [("OOS", test), ("TRAIN", train)]:
        base_pnl = 0
        for m in dataset:
            up_s = sample_Ns(m['ticks'], UP_ASK, 10)
            fired = set()
            for cd, price in up_s:
                if cd < 30 or price <= 0: continue
                zone = round(price * 20) / 20
                if zone in fired: continue
                fired.add(zone)
                won = (m['winner'] == 'UP')
                base_pnl += ((1.0 - price) * 5) if won else (-price * 5)

        print(f"\n{'='*130}")
        print(f"UP GATE (DN price) — {ds_name} ({len(dataset)} markets) — Baseline UP PnL: ${base_pnl:+.0f}")
        print(f"{'='*130}")

        results = []
        for interval in intervals:
            for dn_thresh_int in range(55, 81, 5):
                dn_thresh = dn_thresh_int / 100
                for dn_pct in [30, 40, 50, 60, 70]:
                    for fire_cd in [250, 230, 210]:
                        r = simulate_up_gate_dn(dataset, interval, dn_thresh, dn_pct, fire_cd)
                        if r['blocked'] < 3: continue
                        r['vs'] = r['pnl'] - base_pnl
                        r['name'] = f"int={interval}s DN>=${dn_thresh:.2f} p{dn_pct}% cd={fire_cd}"
                        r['interval'] = interval
                        r['ratio'] = f"{r['blocked']-r['blocked_won']}:{r['blocked_won']}"
                        results.append(r)

        results.sort(key=lambda r: -r['vs'])
        print(f"  Tested {len(results)} combos")
        print(f"\n  TOP 20:")
        print(f"  {'#':<4}{'Config':<50}{'PnL':>8}{'Blk':>6}{'BlkW':>6}{'Ratio':>8}{'vs':>8}")
        print(f"  {'─'*95}")
        for i, r in enumerate(results[:20]):
            print(f"  {i+1:<4}{r['name']:<50}${r['pnl']:>+6.0f}  {r['blocked']:>4}  "
                  f"{r['blocked_won']:>4}  {r['ratio']:>6}  ${r['vs']:>+6.0f}")

        print(f"\n  BEST PER INTERVAL:")
        print(f"  {'Interval':<10}{'Best config':<50}{'vs':>8}{'Blk':>6}{'Ratio':>8}")
        print(f"  {'─'*85}")
        for iv in intervals:
            iv_r = [r for r in results if r['interval'] == iv]
            if iv_r:
                best = max(iv_r, key=lambda r: r['vs'])
                print(f"  {iv:>3}s      {best['name']:<50}${best['vs']:>+6.0f}  "
                      f"{best['blocked']:>4}  {best['ratio']:>6}")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
No-Gate Decision Scanner
==========================
Markets fall into 3 categories:
  1. DN gate triggers (UP dominant) → block DN fires (proven +$521 OOS)
  2. UP gate triggers (DN dominant) → block UP fires (proven +$128 OOS)
  3. NO gate triggers → ???

For category 3, test all options:
  A. Release all (fire both sides freely)
  B. Block all (don't trade at all)
  C. Block DN only
  D. Block UP only
  E. Fire with entry limit ≤$0.20
  F. Fire with entry limit ≤$0.10

This tells us the optimal DEFAULT action when the market hasn't shown dominance.

Run: python3 no_gate_decision.py market_history.jsonl
"""
import json, sys

CD,UP_BID,UP_ASK,DN_BID,DN_ASK = 0,1,2,3,4

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

def check_dn_gate(ticks):
    """DN LIVE: UP≥$0.50 c2 at cd≤210 (10s sampling)"""
    up_s = sample_Ns(ticks, UP_ASK, 10)
    consec = 0
    for cd, p in up_s:
        if p >= 0.50:
            consec += 1
        else:
            consec = 0
        if consec >= 2 and cd <= 210:
            return True, cd
    return False, None

def check_up_gate(ticks):
    """UP LIVE: DN≥$0.65 p40% at cd≤250 (3s sampling)"""
    dn_s = sample_Ns(ticks, DN_ASK, 3)
    n = len(dn_s)
    if n < 3:
        return False, None
    for i in range(3, n+1):
        subset = dn_s[:i]
        cd = subset[-1][0]
        if cd > 250:
            continue
        above = sum(1 for _, p in subset if p >= 0.65)
        if (above / i * 100) >= 40:
            return True, cd
    return False, None

def compute_pnl(ticks, winner, side_filter=None, entry_limit=None, after_cd=None):
    """Compute PnL for fires on specified side(s)."""
    total = 0
    for side_idx, side_name in [(DN_ASK, 'DN'), (UP_ASK, 'UP')]:
        if side_filter and side_name not in side_filter:
            continue
        s = sample_Ns(ticks, side_idx, 10)
        fired = set()
        for cd, price in s:
            if cd < 30 or price <= 0:
                continue
            if after_cd is not None and cd > after_cd:
                continue
            if entry_limit and price > entry_limit:
                continue
            zone = round(price * 20) / 20
            if zone in fired:
                continue
            fired.add(zone)
            won = (winner == side_name)
            pnl = ((1.0 - price) * 5) if won else (-price * 5)
            total += pnl
    return total

def main():
    fp = sys.argv[1] if len(sys.argv) > 1 else "market_history.jsonl"
    print(f"Loading {fp}...")
    raw = load(fp)
    n = len(raw)
    split = int(n * 0.68)
    train = raw[:split]
    test = raw[split:]
    print(f"Loaded {n} markets (Train={len(train)} Test={len(test)})")

    for ds_name, dataset in [("OOS", test), ("TRAIN", train), ("ALL", raw)]:
        dn_gate_mkts = []
        up_gate_mkts = []
        both_gate_mkts = []
        no_gate_mkts = []

        for m in dataset:
            ticks = m['ticks']
            winner = m['winner']
            dn_trig, dn_cd = check_dn_gate(ticks)
            up_trig, up_cd = check_up_gate(ticks)

            if dn_trig and up_trig:
                both_gate_mkts.append(m)
            elif dn_trig:
                dn_gate_mkts.append(m)
            elif up_trig:
                up_gate_mkts.append(m)
            else:
                no_gate_mkts.append(m)

        print(f"\n{'='*100}")
        print(f"{ds_name} ({len(dataset)} markets)")
        print(f"{'='*100}")
        print(f"  DN gate triggers: {len(dn_gate_mkts)} markets")
        print(f"  UP gate triggers: {len(up_gate_mkts)} markets")
        print(f"  BOTH gates trigger: {len(both_gate_mkts)} markets")
        print(f"  NO gate triggers: {len(no_gate_mkts)} markets")

        # Analyze NO-GATE markets
        print(f"\n  ── NO-GATE MARKETS ({len(no_gate_mkts)}) ──")
        ng_winners = {'UP': 0, 'DN': 0}
        for m in no_gate_mkts:
            ng_winners[m['winner']] += 1
        print(f"  Winners: UP={ng_winners['UP']} DN={ng_winners['DN']} "
              f"(UP={ng_winners['UP']/max(len(no_gate_mkts),1)*100:.0f}%)")

        # Test all decisions for no-gate markets
        decisions = [
            ("A) Release all (fire freely)", None, None),
            ("B) Block ALL (don't trade)", [], None),
            ("C) Block DN only", ['UP'], None),
            ("D) Block UP only", ['DN'], None),
            ("E) Fire both ≤$0.25", None, 0.25),
            ("F) Fire both ≤$0.20", None, 0.20),
            ("G) Fire both ≤$0.15", None, 0.15),
            ("H) Fire both ≤$0.10", None, 0.10),
            ("I) Fire DN only ≤$0.25", ['DN'], 0.25),
            ("J) Fire UP only ≤$0.25", ['UP'], 0.25),
            ("K) Fire DN only ≤$0.15", ['DN'], 0.15),
            ("L) Fire UP only ≤$0.15", ['UP'], 0.15),
        ]

        print(f"\n  {'Decision':<40}{'PnL':>8}{'Per mkt':>9}")
        print(f"  {'─'*60}")

        for name, sides, limit in decisions:
            total = 0
            for m in no_gate_mkts:
                sf = sides  # None = both sides
                pnl = compute_pnl(m['ticks'], m['winner'], side_filter=sf, entry_limit=limit)
                total += pnl
            per_mkt = total / max(len(no_gate_mkts), 1)
            print(f"  {name:<40}${total:>+7.0f}  ${per_mkt:>+6.2f}/mkt")

        # Also show PnL for GATED markets (for comparison)
        print(f"\n  ── GATED MARKETS (for comparison) ──")

        # DN gate: block DN fires
        dn_pnl = 0
        for m in dn_gate_mkts:
            dn_pnl += compute_pnl(m['ticks'], m['winner'], side_filter=['UP'])
        print(f"  DN gate ({len(dn_gate_mkts)} mkts): block DN → ${dn_pnl:+.0f} "
              f"(${dn_pnl/max(len(dn_gate_mkts),1):+.2f}/mkt)")

        # UP gate: block UP fires
        up_pnl = 0
        for m in up_gate_mkts:
            up_pnl += compute_pnl(m['ticks'], m['winner'], side_filter=['DN'])
        print(f"  UP gate ({len(up_gate_mkts)} mkts): block UP → ${up_pnl:+.0f} "
              f"(${up_pnl/max(len(up_gate_mkts),1):+.2f}/mkt)")

        # BOTH gates
        both_pnl = 0
        for m in both_gate_mkts:
            both_pnl += compute_pnl(m['ticks'], m['winner'], side_filter=[])
        print(f"  BOTH gates ({len(both_gate_mkts)} mkts): block all → ${both_pnl:+.0f}")

        # Total system PnL with each no-gate decision
        print(f"\n  ── TOTAL SYSTEM PnL (gate + no-gate decision) ──")
        for name, sides, limit in decisions:
            ng_total = 0
            for m in no_gate_mkts:
                ng_total += compute_pnl(m['ticks'], m['winner'], side_filter=sides, entry_limit=limit)
            system = dn_pnl + up_pnl + both_pnl + ng_total
            print(f"  {name:<40} system=${system:>+7.0f}")

if __name__ == "__main__":
    main()

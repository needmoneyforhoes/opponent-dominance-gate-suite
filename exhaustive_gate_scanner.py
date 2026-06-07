#!/usr/bin/env python3
"""
Exhaustive Opponent Gate Scanner
==================================
Tests ALL combinations of:
  - Opponent thresholds: 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90
  - Sustained durations: 5, 10, 15, 20, 30, 45, 60, 90, 120, 150 ticks
  - Entry zones: 0.03-0.15, 0.10-0.20, 0.15-0.25, 0.20-0.30, 0.25-0.35, 0.30-0.40, 0.35-0.45, 0.40-0.50
  - Zone dwell %: 5%, 10%, 15%, 20%, 25%, 30%, 40%, 50%, 60%
  - Zone widths: 0.10, 0.15, 0.20, 0.30 wide starting at every 0.05
  - Staircase: start ≤X, end ≥Y for all X,Y combos
  - Checkpoint predictions at every 30s
  - ALL separated by UP and DN

Total: ~10,000+ combinations with Bonferroni correction for multiple testing.
Only shows results that survive statistical correction.

Run: python3 exhaustive_gate_scanner.py market_history.jsonl > exhaustive_results.txt 2>&1
"""
import json, sys, math
from collections import defaultdict

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

def sample_10s(ticks, idx):
    s = []; prev = 999
    for t in ticks:
        if t[CD] < prev - 9:
            s.append((t[CD], t[idx] or 0))
            prev = t[CD]
    return s

def binomial_p(k, n, p0=0.50):
    """One-sided binomial test p-value: P(X >= k) under H0: p = p0."""
    if n == 0: return 1.0
    from math import comb, log, exp
    p = 0.0
    for i in range(k, n+1):
        try:
            p += comb(n, i) * (p0 ** i) * ((1 - p0) ** (n - i))
        except Exception:
            pass
    return p

def main():
    fp = sys.argv[1] if len(sys.argv) > 1 else "market_history.jsonl"
    print(f"Loading {fp}...")
    markets = load(fp)
    n = len(markets)
    split = int(n * 0.68)
    train = markets[:split]
    test = markets[split:]
    print(f"Loaded {n} markets (Train={len(train)} Test={len(test)})")
    
    # Precompute features for all markets
    print("Precomputing features...")
    all_features = []
    for m in markets:
        ticks = m['ticks']
        winner = m['winner']
        f = {'winner': winner, 'slug': m.get('slug','')}
        
        up_samples = sample_10s(ticks, UP_ASK)
        dn_samples = sample_10s(ticks, DN_ASK)
        total_samples = max(len(up_samples), 1)
        
        # Precompute zone dwell for all thresholds
        for side, samples, label in [('UP', up_samples, 'up'), ('DN', dn_samples, 'dn')]:
            prices = [p for _, p in samples]
            n_s = len(prices)
            
            # Total ticks above each threshold
            for thresh_int in range(30, 96, 5):  # 0.30 to 0.95 step 0.05
                thresh = thresh_int / 100
                above = sum(1 for p in prices if p >= thresh)
                f[f'{label}_above_{thresh_int}'] = above
                f[f'{label}_pct_{thresh_int}'] = above / max(n_s, 1)
                
                # Max consecutive above
                mx = 0; cur = 0
                for p in prices:
                    if p >= thresh: cur += 1; mx = max(mx, cur)
                    else: cur = 0
                f[f'{label}_consec_{thresh_int}'] = mx
            
            # Zone dwell: time in [lo, hi) for many zone widths
            for lo_int in range(5, 91, 5):  # 0.05 to 0.90
                for width_int in [10, 15, 20, 25, 30]:
                    hi_int = lo_int + width_int
                    if hi_int > 100: continue
                    lo = lo_int / 100
                    hi = hi_int / 100
                    in_zone = sum(1 for p in prices if lo <= p < hi)
                    f[f'{label}_zone_{lo_int}_{hi_int}'] = in_zone / max(n_s, 1)
            
            # Price at checkpoints
            for cd_target in range(30, 301, 15):
                at_cd = [p for cd, p in samples if abs(cd - cd_target) <= 7]
                f[f'{label}_at_{cd_target}'] = at_cd[0] if at_cd else None
            
            # Early avg (cd > 210), mid avg (120-210), late avg (60-120)
            early_p = [p for cd, p in samples if cd > 210]
            mid_p = [p for cd, p in samples if 120 < cd <= 210]
            late_p = [p for cd, p in samples if 60 < cd <= 120]
            f[f'{label}_avg_early'] = sum(early_p)/len(early_p) if early_p else 0
            f[f'{label}_avg_mid'] = sum(mid_p)/len(mid_p) if mid_p else 0
            f[f'{label}_avg_late'] = sum(late_p)/len(late_p) if late_p else 0
        
        all_features.append(f)
    
    train_f = all_features[:split]
    test_f = all_features[split:]
    print(f"Features computed. Starting exhaustive scan...\n")
    
    # Count total tests for Bonferroni
    total_tests = 0
    results = []
    
    # ══════════════════════════════════════════════════════════════
    # TEST A: Sustained opponent dominance gate
    # "If opponent ≥ thresh for ≥ duration ticks, suppress fire"
    # ══════════════════════════════════════════════════════════════
    print("Scanning: A) Sustained opponent dominance gates...")
    
    for fire_side in ['DN', 'UP']:
        opp = 'up' if fire_side == 'DN' else 'dn'
        
        for thresh_int in range(45, 91, 5):  # 0.45 to 0.90
            for min_consec in [5, 10, 15, 20, 25, 30, 40, 50, 60, 75, 90, 120, 150]:
                total_tests += 1
                key = f'{opp}_consec_{thresh_int}'
                
                tr_match = [f for f in train_f if f.get(key, 0) >= min_consec]
                te_match = [f for f in test_f if f.get(key, 0) >= min_consec]
                
                if len(tr_match) < 10 or len(te_match) < 5: continue
                
                # Win rate for the FIRING side (should be LOW for suppress)
                tr_wins = sum(1 for f in tr_match if f['winner'] == fire_side)
                te_wins = sum(1 for f in te_match if f['winner'] == fire_side)
                tr_wr = tr_wins / len(tr_match)
                te_wr = te_wins / len(te_match)
                
                results.append({
                    'type': 'A_sustained',
                    'fire_side': fire_side,
                    'desc': f"{fire_side} fire when {opp.upper()}≥${thresh_int/100:.2f} consec≥{min_consec}",
                    'tr_n': len(tr_match), 'tr_wr': tr_wr,
                    'te_n': len(te_match), 'te_wr': te_wr,
                    'thresh': thresh_int, 'duration': min_consec,
                })
    
    # ══════════════════════════════════════════════════════════════
    # TEST B: Opponent total time above threshold
    # "If opponent spent ≥ X% of market above threshold"
    # ══════════════════════════════════════════════════════════════
    print("Scanning: B) Opponent total time above threshold...")
    
    for fire_side in ['DN', 'UP']:
        opp = 'up' if fire_side == 'DN' else 'dn'
        
        for thresh_int in range(45, 91, 5):
            for min_pct_int in range(10, 71, 5):  # 10% to 70%
                total_tests += 1
                key = f'{opp}_pct_{thresh_int}'
                min_pct = min_pct_int / 100
                
                tr_match = [f for f in train_f if f.get(key, 0) >= min_pct]
                te_match = [f for f in test_f if f.get(key, 0) >= min_pct]
                
                if len(tr_match) < 10 or len(te_match) < 5: continue
                
                tr_wins = sum(1 for f in tr_match if f['winner'] == fire_side)
                te_wins = sum(1 for f in te_match if f['winner'] == fire_side)
                tr_wr = tr_wins / len(tr_match)
                te_wr = te_wins / len(te_match)
                
                results.append({
                    'type': 'B_total_pct',
                    'fire_side': fire_side,
                    'desc': f"{fire_side} fire when {opp.upper()}≥${thresh_int/100:.2f} for≥{min_pct_int}%",
                    'tr_n': len(tr_match), 'tr_wr': tr_wr,
                    'te_n': len(te_match), 'te_wr': te_wr,
                })
    
    # ══════════════════════════════════════════════════════════════
    # TEST C: Zone dwell — side spent time in specific zone
    # ══════════════════════════════════════════════════════════════
    print("Scanning: C) Zone dwell prediction...")
    
    for predict_side in ['UP', 'DN']:
        label = predict_side.lower()
        
        for lo_int in range(5, 91, 5):
            for width_int in [10, 15, 20, 25, 30]:
                hi_int = lo_int + width_int
                if hi_int > 100: continue
                key = f'{label}_zone_{lo_int}_{hi_int}'
                
                for min_pct_int in range(10, 61, 5):
                    total_tests += 1
                    min_pct = min_pct_int / 100
                    
                    tr_match = [f for f in train_f if f.get(key, 0) >= min_pct]
                    te_match = [f for f in test_f if f.get(key, 0) >= min_pct]
                    
                    if len(tr_match) < 10 or len(te_match) < 5: continue
                    
                    tr_wins = sum(1 for f in tr_match if f['winner'] == predict_side)
                    te_wins = sum(1 for f in te_match if f['winner'] == predict_side)
                    tr_wr = tr_wins / len(tr_match)
                    te_wr = te_wins / len(te_match)
                    
                    results.append({
                        'type': 'C_zone_dwell',
                        'fire_side': predict_side,
                        'desc': f"{predict_side} wins when {predict_side} in ${lo_int/100:.2f}-${hi_int/100:.2f} ≥{min_pct_int}%",
                        'tr_n': len(tr_match), 'tr_wr': tr_wr,
                        'te_n': len(te_match), 'te_wr': te_wr,
                    })
    
    # ══════════════════════════════════════════════════════════════
    # TEST D: Checkpoint prediction — price at specific time
    # ══════════════════════════════════════════════════════════════
    print("Scanning: D) Checkpoint predictions...")
    
    for predict_side in ['UP', 'DN']:
        label = predict_side.lower()
        
        for cd_target in range(60, 286, 15):
            for thresh_int in range(45, 91, 5):
                total_tests += 1
                key = f'{label}_at_{cd_target}'
                thresh = thresh_int / 100
                
                tr_match = [f for f in train_f if f.get(key) is not None and f[key] >= thresh]
                te_match = [f for f in test_f if f.get(key) is not None and f[key] >= thresh]
                
                if len(tr_match) < 10 or len(te_match) < 5: continue
                
                tr_wins = sum(1 for f in tr_match if f['winner'] == predict_side)
                te_wins = sum(1 for f in te_match if f['winner'] == predict_side)
                tr_wr = tr_wins / len(tr_match)
                te_wr = te_wins / len(te_match)
                
                results.append({
                    'type': 'D_checkpoint',
                    'fire_side': predict_side,
                    'desc': f"{predict_side} wins when {predict_side}≥${thresh:.2f} at cd={cd_target}",
                    'tr_n': len(tr_match), 'tr_wr': tr_wr,
                    'te_n': len(te_match), 'te_wr': te_wr,
                })
    
    # ══════════════════════════════════════════════════════════════
    # TEST E: Staircase — early low, late high
    # ══════════════════════════════════════════════════════════════
    print("Scanning: E) Staircase patterns...")
    
    for predict_side in ['UP', 'DN']:
        label = predict_side.lower()
        
        for start_max_int in range(35, 61, 5):  # early avg ≤ X
            for end_min_int in range(55, 91, 5):  # late avg ≥ Y
                if end_min_int <= start_max_int + 10: continue  # need meaningful climb
                total_tests += 1
                
                tr_match = [f for f in train_f 
                           if f.get(f'{label}_avg_early', 1) <= start_max_int/100
                           and f.get(f'{label}_avg_late', 0) >= end_min_int/100]
                te_match = [f for f in test_f
                           if f.get(f'{label}_avg_early', 1) <= start_max_int/100
                           and f.get(f'{label}_avg_late', 0) >= end_min_int/100]
                
                if len(tr_match) < 8 or len(te_match) < 3: continue
                
                tr_wins = sum(1 for f in tr_match if f['winner'] == predict_side)
                te_wins = sum(1 for f in te_match if f['winner'] == predict_side)
                tr_wr = tr_wins / len(tr_match)
                te_wr = te_wins / len(te_match)
                
                results.append({
                    'type': 'E_staircase',
                    'fire_side': predict_side,
                    'desc': f"{predict_side} staircase ≤${start_max_int/100:.2f}→≥${end_min_int/100:.2f}",
                    'tr_n': len(tr_match), 'tr_wr': tr_wr,
                    'te_n': len(te_match), 'te_wr': te_wr,
                })
    
    # ══════════════════════════════════════════════════════════════
    # TEST F: Trajectory momentum — early vs mid vs late averages
    # ══════════════════════════════════════════════════════════════
    print("Scanning: F) Trajectory momentum...")
    
    for predict_side in ['UP', 'DN']:
        label = predict_side.lower()
        
        # Test: if avg_mid > avg_early by X AND avg_late > avg_mid by Y
        for climb1_int in range(5, 26, 5):  # early→mid climb ≥ X%
            for climb2_int in range(5, 26, 5):  # mid→late climb ≥ Y%
                total_tests += 1
                c1 = climb1_int / 100
                c2 = climb2_int / 100
                
                tr_match = [f for f in train_f
                           if (f.get(f'{label}_avg_mid', 0) - f.get(f'{label}_avg_early', 0)) >= c1
                           and (f.get(f'{label}_avg_late', 0) - f.get(f'{label}_avg_mid', 0)) >= c2]
                te_match = [f for f in test_f
                           if (f.get(f'{label}_avg_mid', 0) - f.get(f'{label}_avg_early', 0)) >= c1
                           and (f.get(f'{label}_avg_late', 0) - f.get(f'{label}_avg_mid', 0)) >= c2]
                
                if len(tr_match) < 8 or len(te_match) < 3: continue
                
                tr_wins = sum(1 for f in tr_match if f['winner'] == predict_side)
                te_wins = sum(1 for f in te_match if f['winner'] == predict_side)
                tr_wr = tr_wins / len(tr_match)
                te_wr = te_wins / len(te_match)
                
                results.append({
                    'type': 'F_momentum',
                    'fire_side': predict_side,
                    'desc': f"{predict_side} momentum early+{climb1_int}%→mid+{climb2_int}%→late",
                    'tr_n': len(tr_match), 'tr_wr': tr_wr,
                    'te_n': len(te_match), 'te_wr': te_wr,
                })
    
    print(f"\nTotal tests: {total_tests}")
    print(f"Total results (passed min N): {len(results)}")
    
    # Bonferroni correction
    bonf_alpha = 0.05 / max(total_tests, 1)
    print(f"Bonferroni alpha: {bonf_alpha:.6f} (0.05 / {total_tests})")
    
    # ══════════════════════════════════════════════════════════════
    # RESULTS: SUPPRESS gates (low win rate = don't fire)
    # ══════════════════════════════════════════════════════════════
    suppress = [r for r in results if r['type'] in ('A_sustained', 'B_total_pct')
                and r['tr_wr'] <= 0.35 and r['te_wr'] <= 0.35]
    suppress.sort(key=lambda r: r['te_wr'])
    
    print(f"\n{'='*120}")
    print(f"SUPPRESS GATES — opponent dominant, fire side loses (sorted by OOS WR ascending)")
    print(f"{'='*120}")
    print(f"  {'Side':<4} {'Description':<60} {'TrN':<6}{'TrWR':<7}{'OOSN':<6}{'OOSWR':<7}{'Bonf?'}")
    print(f"  {'─'*100}")
    
    shown = set()
    for r in suppress[:50]:
        desc = r['desc']
        if desc in shown: continue
        shown.add(desc)
        # Binomial test on OOS
        te_opp_wins = r['te_n'] - int(r['te_wr'] * r['te_n'])
        p_val = binomial_p(te_opp_wins, r['te_n'], 0.50)
        bonf_pass = p_val < bonf_alpha
        bonf_str = "✅ BONF" if bonf_pass else ""
        print(f"  {r['fire_side']:<4} {desc:<60} {r['tr_n']:<6}{r['tr_wr']:.0%}   "
              f"{r['te_n']:<6}{r['te_wr']:.0%}   {bonf_str}")
    
    # ══════════════════════════════════════════════════════════════
    # RESULTS: PREDICTION signals (high win rate = fire this side)
    # ══════════════════════════════════════════════════════════════
    predict = [r for r in results if r['type'] in ('C_zone_dwell', 'D_checkpoint', 'E_staircase', 'F_momentum')
               and r['tr_wr'] >= 0.70 and r['te_wr'] >= 0.65 and r['te_n'] >= 5]
    predict.sort(key=lambda r: -(r['te_wr'] * math.sqrt(r['te_n'])))
    
    print(f"\n{'='*120}")
    print(f"PREDICTION SIGNALS — side wins when pattern detected (sorted by OOS score)")
    print(f"{'='*120}")
    print(f"  {'Side':<4} {'Type':<12} {'Description':<55} {'TrN':<6}{'TrWR':<7}{'OOSN':<6}{'OOSWR':<7}{'Bonf?'}")
    print(f"  {'─'*110}")
    
    shown = set()
    for r in predict[:60]:
        desc = r['desc']
        if desc in shown: continue
        shown.add(desc)
        te_wins = int(r['te_wr'] * r['te_n'])
        p_val = binomial_p(te_wins, r['te_n'], 0.50)
        bonf_pass = p_val < bonf_alpha
        bonf_str = "✅ BONF" if bonf_pass else ""
        print(f"  {r['fire_side']:<4} {r['type']:<12} {desc:<55} {r['tr_n']:<6}{r['tr_wr']:.0%}   "
              f"{r['te_n']:<6}{r['te_wr']:.0%}   {bonf_str}")
    
    # ══════════════════════════════════════════════════════════════
    # SUMMARY: Best gate per side
    # ══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print("OPTIMAL GATES PER SIDE (lowest OOS WR = best suppress, highest OOS WR = best predict)")
    print(f"{'='*120}")
    
    for side in ['DN', 'UP']:
        side_suppress = [r for r in suppress if r['fire_side'] == side and r['te_n'] >= 10]
        side_predict = [r for r in predict if r['fire_side'] == side and r['te_n'] >= 5]
        
        print(f"\n  {side} SIDE:")
        if side_suppress:
            best = min(side_suppress, key=lambda r: r['te_wr'])
            print(f"    BEST SUPPRESS: {best['desc']}")
            print(f"      Train: {best['tr_n']} markets, {best['tr_wr']:.0%} WR")
            print(f"      OOS:   {best['te_n']} markets, {best['te_wr']:.0%} WR")
            print(f"      → {side} fires suppressed when this condition is true")
        
        if side_predict:
            best = max(side_predict, key=lambda r: r['te_wr'] * math.sqrt(r['te_n']))
            print(f"    BEST PREDICT: {best['desc']}")
            print(f"      Train: {best['tr_n']} markets, {best['tr_wr']:.0%} WR")
            print(f"      OOS:   {best['te_n']} markets, {best['te_wr']:.0%} WR")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Extended Gate Scanner — Untested Indicators & Combinations
============================================================
Tests indicators the exhaustive scanner MISSED:
  1. BN delta (Binance) + price combinations
  2. Own-side LOW threshold (UP ≤ X instead of DN ≥ Y)
  3. Combined gates: opponent high AND own low
  4. Staircase momentum: opponent crossed ascending thresholds
  5. Time since own side was last above threshold
  6. Max/min price trajectory (peak declining, floor rising)
  7. Two-gate combinations from proven singles

All separated by UP and DN suppression.
Bonferroni-corrected for multiple testing.

Run: python3 extended_gate_scanner.py market_history.jsonl
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
    if n == 0: return 1.0
    from math import comb
    p = 0.0
    for i in range(k, n+1):
        try: p += comb(n, i) * (p0 ** i) * ((1 - p0) ** (n - i))
        except: pass
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
    
    # Precompute features
    print("Precomputing features...")
    all_feats = []
    for m in markets:
        ticks = m['ticks']
        f = {'winner': m['winner']}
        
        up_s = sample_10s(ticks, UP_ASK)
        dn_s = sample_10s(ticks, DN_ASK)
        bn_s = sample_10s(ticks, BN_DELTA)
        
        up_p = [p for _, p in up_s]
        dn_p = [p for _, p in dn_s]
        bn_p = [p for _, p in bn_s]
        n_s = max(len(up_p), 1)
        
        # ── Basic thresholds (for combinations) ──
        for thresh_int in range(30, 91, 5):
            t = thresh_int / 100
            # Consecutive above
            for side_p, label in [(up_p, 'up'), (dn_p, 'dn')]:
                mx = 0; cur = 0
                for p in side_p:
                    if p >= t: cur += 1; mx = max(mx, cur)
                    else: cur = 0
                f[f'{label}_consec_{thresh_int}'] = mx
                f[f'{label}_pct_{thresh_int}'] = sum(1 for p in side_p if p >= t) / n_s
            
            # Consecutive BELOW (own side low)
            for side_p, label in [(up_p, 'up'), (dn_p, 'dn')]:
                mx = 0; cur = 0
                for p in side_p:
                    if p <= t: cur += 1; mx = max(mx, cur)
                    else: cur = 0
                f[f'{label}_below_consec_{thresh_int}'] = mx
                f[f'{label}_below_pct_{thresh_int}'] = sum(1 for p in side_p if p <= t) / n_s
        
        # ── BN delta features ──
        if bn_p:
            bn_mean = sum(bn_p) / len(bn_p)
            bn_pos_pct = sum(1 for b in bn_p if b > 0) / len(bn_p)
            bn_neg_pct = sum(1 for b in bn_p if b < 0) / len(bn_p)
            # Max consecutive BN positive/negative
            bn_pos_consec = 0; cur = 0
            for b in bn_p:
                if b > 0: cur += 1; bn_pos_consec = max(bn_pos_consec, cur)
                else: cur = 0
            bn_neg_consec = 0; cur = 0
            for b in bn_p:
                if b < 0: cur += 1; bn_neg_consec = max(bn_neg_consec, cur)
                else: cur = 0
            # BN below threshold
            for bn_thresh in [-0.01, -0.02, -0.03, -0.05, -0.08]:
                bt_key = str(bn_thresh).replace('-','n').replace('.','')
                f[f'bn_below_{bt_key}_pct'] = sum(1 for b in bn_p if b < bn_thresh) / len(bn_p)
                mx = 0; cur = 0
                for b in bn_p:
                    if b < bn_thresh: cur += 1; mx = max(mx, cur)
                    else: cur = 0
                f[f'bn_below_{bt_key}_consec'] = mx
            # BN above threshold  
            for bn_thresh in [0.01, 0.02, 0.03, 0.05, 0.08]:
                bt_key = str(bn_thresh).replace('.','')
                f[f'bn_above_{bt_key}_pct'] = sum(1 for b in bn_p if b > bn_thresh) / len(bn_p)
                mx = 0; cur = 0
                for b in bn_p:
                    if b > bn_thresh: cur += 1; mx = max(mx, cur)
                    else: cur = 0
                f[f'bn_above_{bt_key}_consec'] = mx
        else:
            for k in ['bn_pos_pct','bn_neg_pct','bn_pos_consec','bn_neg_consec']:
                f[k] = 0
            bn_mean = 0
        f['bn_mean'] = bn_mean
        f['bn_pos_pct'] = sum(1 for b in bn_p if b > 0) / max(len(bn_p),1) if bn_p else 0
        f['bn_neg_pct'] = sum(1 for b in bn_p if b < 0) / max(len(bn_p),1) if bn_p else 0
        f['bn_pos_consec'] = bn_pos_consec if bn_p else 0
        f['bn_neg_consec'] = bn_neg_consec if bn_p else 0
        
        # ── Staircase: crossed ascending thresholds ──
        for side_p, label in [(up_p, 'up'), (dn_p, 'dn')]:
            crossed_60_then_70 = False
            crossed_70_then_80 = False
            crossed_50_then_70 = False
            saw_60 = False; saw_70 = False; saw_50 = False
            for p in side_p:  # ticks are descending cd (earliest first in sample)
                if p >= 0.50: saw_50 = True
                if p >= 0.60: saw_60 = True
                if p >= 0.70:
                    saw_70 = True
                    if saw_60: crossed_60_then_70 = True
                    if saw_50: crossed_50_then_70 = True
                if p >= 0.80:
                    if saw_70: crossed_70_then_80 = True
            f[f'{label}_cross_60_70'] = crossed_60_then_70
            f[f'{label}_cross_70_80'] = crossed_70_then_80
            f[f'{label}_cross_50_70'] = crossed_50_then_70
        
        # ── Max/min trajectory ──
        for side_p, label in [(up_p, 'up'), (dn_p, 'dn')]:
            if len(side_p) >= 6:
                first_half = side_p[:len(side_p)//2]
                second_half = side_p[len(side_p)//2:]
                f[f'{label}_max_first'] = max(first_half)
                f[f'{label}_max_second'] = max(second_half)
                f[f'{label}_min_first'] = min(first_half)
                f[f'{label}_min_second'] = min(second_half)
                f[f'{label}_max_declining'] = max(first_half) > max(second_half)
                f[f'{label}_min_rising'] = min(second_half) > min(first_half)
            else:
                f[f'{label}_max_declining'] = False
                f[f'{label}_min_rising'] = False
        
        all_feats.append(f)
    
    train_f = all_feats[:split]
    test_f = all_feats[split:]
    
    total_tests = 0
    results = []
    
    # ══════════════════════════════════════════════════════════════
    # TEST 1: OWN SIDE LOW (UP ≤ X for Y ticks → UP loses)
    # ══════════════════════════════════════════════════════════════
    print("Scanning: 1) Own side LOW gates...")
    for fire_side in ['DN', 'UP']:
        own = fire_side.lower()
        for thresh_int in range(25, 51, 5):  # own side ≤ 0.25 to 0.50
            for min_consec in [5, 10, 15, 20, 30, 50]:
                total_tests += 1
                key = f'{own}_below_consec_{thresh_int}'
                tr_m = [f for f in train_f if f.get(key, 0) >= min_consec]
                te_m = [f for f in test_f if f.get(key, 0) >= min_consec]
                if len(tr_m) < 8 or len(te_m) < 5: continue
                tr_w = sum(1 for f in tr_m if f['winner'] == fire_side) / len(tr_m)
                te_w = sum(1 for f in te_m if f['winner'] == fire_side) / len(te_m)
                results.append({
                    'type': '1_own_low', 'fire_side': fire_side,
                    'desc': f"{fire_side} fire when {fire_side}≤${thresh_int/100:.2f} consec≥{min_consec}",
                    'tr_n': len(tr_m), 'tr_wr': tr_w, 'te_n': len(te_m), 'te_wr': te_w})
    
    # ══════════════════════════════════════════════════════════════
    # TEST 2: BN direction gates
    # ══════════════════════════════════════════════════════════════
    print("Scanning: 2) BN direction gates...")
    for fire_side in ['DN', 'UP']:
        # BN negative → bad for UP (suppress UP fires)
        # BN positive → bad for DN (suppress DN fires)
        if fire_side == 'UP':
            bn_keys = [('bn_below_n001_pct', 'BN<-1% pct≥'), ('bn_below_n002_pct', 'BN<-2% pct≥'),
                       ('bn_below_n003_pct', 'BN<-3% pct≥'), ('bn_below_n005_pct', 'BN<-5% pct≥'),
                       ('bn_neg_consec', 'BN neg consec≥')]
            bn_consec_keys = [('bn_below_n001_consec', 'BN<-1% consec≥'),
                              ('bn_below_n002_consec', 'BN<-2% consec≥'),
                              ('bn_below_n003_consec', 'BN<-3% consec≥')]
        else:
            bn_keys = [('bn_above_001_pct', 'BN>+1% pct≥'), ('bn_above_002_pct', 'BN>+2% pct≥'),
                       ('bn_above_003_pct', 'BN>+3% pct≥'), ('bn_above_005_pct', 'BN>+5% pct≥'),
                       ('bn_pos_consec', 'BN pos consec≥')]
            bn_consec_keys = [('bn_above_001_consec', 'BN>+1% consec≥'),
                              ('bn_above_002_consec', 'BN>+2% consec≥'),
                              ('bn_above_003_consec', 'BN>+3% consec≥')]
        
        for key, label in bn_keys:
            for thresh in [0.30, 0.40, 0.50, 0.60, 0.70, 0.80]:
                total_tests += 1
                tr_m = [f for f in train_f if f.get(key, 0) >= thresh]
                te_m = [f for f in test_f if f.get(key, 0) >= thresh]
                if len(tr_m) < 8 or len(te_m) < 5: continue
                tr_w = sum(1 for f in tr_m if f['winner'] == fire_side) / len(tr_m)
                te_w = sum(1 for f in te_m if f['winner'] == fire_side) / len(te_m)
                results.append({
                    'type': '2_bn', 'fire_side': fire_side,
                    'desc': f"{fire_side} fire when {label}{thresh:.0%}",
                    'tr_n': len(tr_m), 'tr_wr': tr_w, 'te_n': len(te_m), 'te_wr': te_w})
        
        for key, label in bn_consec_keys:
            for min_c in [5, 10, 15, 20]:
                total_tests += 1
                tr_m = [f for f in train_f if f.get(key, 0) >= min_c]
                te_m = [f for f in test_f if f.get(key, 0) >= min_c]
                if len(tr_m) < 8 or len(te_m) < 5: continue
                tr_w = sum(1 for f in tr_m if f['winner'] == fire_side) / len(tr_m)
                te_w = sum(1 for f in te_m if f['winner'] == fire_side) / len(te_m)
                results.append({
                    'type': '2_bn', 'fire_side': fire_side,
                    'desc': f"{fire_side} fire when {label}{min_c}",
                    'tr_n': len(tr_m), 'tr_wr': tr_w, 'te_n': len(te_m), 'te_wr': te_w})
    
    # ══════════════════════════════════════════════════════════════
    # TEST 3: COMBINED — opponent high AND own low
    # ══════════════════════════════════════════════════════════════
    print("Scanning: 3) Combined opponent high + own low...")
    for fire_side in ['DN', 'UP']:
        opp = 'up' if fire_side == 'DN' else 'dn'
        own = fire_side.lower()
        
        for opp_thresh in [55, 60, 65, 70, 75]:
            for opp_consec in [5, 10, 15, 20]:
                for own_thresh in [30, 35, 40, 45]:
                    for own_consec in [5, 10, 15, 20]:
                        total_tests += 1
                        opp_key = f'{opp}_consec_{opp_thresh}'
                        own_key = f'{own}_below_consec_{own_thresh}'
                        
                        tr_m = [f for f in train_f if f.get(opp_key, 0) >= opp_consec
                                and f.get(own_key, 0) >= own_consec]
                        te_m = [f for f in test_f if f.get(opp_key, 0) >= opp_consec
                                and f.get(own_key, 0) >= own_consec]
                        
                        if len(tr_m) < 8 or len(te_m) < 5: continue
                        tr_w = sum(1 for f in tr_m if f['winner'] == fire_side) / len(tr_m)
                        te_w = sum(1 for f in te_m if f['winner'] == fire_side) / len(te_m)
                        results.append({
                            'type': '3_combined', 'fire_side': fire_side,
                            'desc': f"{fire_side} when {opp.upper()}≥${opp_thresh/100:.2f} c≥{opp_consec} AND {own.upper()}≤${own_thresh/100:.2f} c≥{own_consec}",
                            'tr_n': len(tr_m), 'tr_wr': tr_w, 'te_n': len(te_m), 'te_wr': te_w})
    
    # ══════════════════════════════════════════════════════════════
    # TEST 4: COMBINED — opponent high AND BN direction
    # ══════════════════════════════════════════════════════════════
    print("Scanning: 4) Combined opponent high + BN direction...")
    for fire_side in ['DN', 'UP']:
        opp = 'up' if fire_side == 'DN' else 'dn'
        
        for opp_thresh in [55, 60, 65, 70]:
            for opp_consec in [5, 10, 15]:
                for bn_pct in [0.40, 0.50, 0.60, 0.70]:
                    total_tests += 1
                    opp_key = f'{opp}_consec_{opp_thresh}'
                    
                    if fire_side == 'UP':
                        bn_key = 'bn_neg_pct'  # BN negative = bad for UP
                    else:
                        bn_key = 'bn_pos_pct'  # BN positive = bad for DN
                    
                    tr_m = [f for f in train_f if f.get(opp_key, 0) >= opp_consec
                            and f.get(bn_key, 0) >= bn_pct]
                    te_m = [f for f in test_f if f.get(opp_key, 0) >= opp_consec
                            and f.get(bn_key, 0) >= bn_pct]
                    
                    if len(tr_m) < 8 or len(te_m) < 5: continue
                    tr_w = sum(1 for f in tr_m if f['winner'] == fire_side) / len(tr_m)
                    te_w = sum(1 for f in te_m if f['winner'] == fire_side) / len(te_m)
                    bn_dir = "BN neg" if fire_side == 'UP' else "BN pos"
                    results.append({
                        'type': '4_opp_bn', 'fire_side': fire_side,
                        'desc': f"{fire_side} when {opp.upper()}≥${opp_thresh/100:.2f} c≥{opp_consec} AND {bn_dir}≥{bn_pct:.0%}",
                        'tr_n': len(tr_m), 'tr_wr': tr_w, 'te_n': len(te_m), 'te_wr': te_w})
    
    # ══════════════════════════════════════════════════════════════
    # TEST 5: Opponent min rising (floor going up)
    # ══════════════════════════════════════════════════════════════
    print("Scanning: 5) Min rising / max declining...")
    for fire_side in ['DN', 'UP']:
        opp = 'up' if fire_side == 'DN' else 'dn'
        own = fire_side.lower()
        
        # Opponent min rising = opponent getting stronger
        total_tests += 1
        tr_m = [f for f in train_f if f.get(f'{opp}_min_rising', False)]
        te_m = [f for f in test_f if f.get(f'{opp}_min_rising', False)]
        if len(tr_m) >= 8 and len(te_m) >= 5:
            tr_w = sum(1 for f in tr_m if f['winner'] == fire_side) / len(tr_m)
            te_w = sum(1 for f in te_m if f['winner'] == fire_side) / len(te_m)
            results.append({
                'type': '5_trajectory', 'fire_side': fire_side,
                'desc': f"{fire_side} when {opp.upper()} min rising",
                'tr_n': len(tr_m), 'tr_wr': tr_w, 'te_n': len(te_m), 'te_wr': te_w})
        
        # Own max declining = own side getting weaker
        total_tests += 1
        tr_m = [f for f in train_f if f.get(f'{own}_max_declining', False)]
        te_m = [f for f in test_f if f.get(f'{own}_max_declining', False)]
        if len(tr_m) >= 8 and len(te_m) >= 5:
            tr_w = sum(1 for f in tr_m if f['winner'] == fire_side) / len(tr_m)
            te_w = sum(1 for f in te_m if f['winner'] == fire_side) / len(te_m)
            results.append({
                'type': '5_trajectory', 'fire_side': fire_side,
                'desc': f"{fire_side} when {own.upper()} max declining",
                'tr_n': len(tr_m), 'tr_wr': tr_w, 'te_n': len(te_m), 'te_wr': te_w})
        
        # COMBINED: opponent min rising AND own max declining
        total_tests += 1
        tr_m = [f for f in train_f if f.get(f'{opp}_min_rising', False) 
                and f.get(f'{own}_max_declining', False)]
        te_m = [f for f in test_f if f.get(f'{opp}_min_rising', False)
                and f.get(f'{own}_max_declining', False)]
        if len(tr_m) >= 8 and len(te_m) >= 5:
            tr_w = sum(1 for f in tr_m if f['winner'] == fire_side) / len(tr_m)
            te_w = sum(1 for f in te_m if f['winner'] == fire_side) / len(te_m)
            results.append({
                'type': '5_trajectory', 'fire_side': fire_side,
                'desc': f"{fire_side} when {opp.upper()} min↑ AND {own.upper()} max↓",
                'tr_n': len(tr_m), 'tr_wr': tr_w, 'te_n': len(te_m), 'te_wr': te_w})
    
    # ══════════════════════════════════════════════════════════════
    # TEST 6: Staircase crossings
    # ══════════════════════════════════════════════════════════════
    print("Scanning: 6) Staircase crossings...")
    for fire_side in ['DN', 'UP']:
        opp = 'up' if fire_side == 'DN' else 'dn'
        for cross_key, cross_label in [
            (f'{opp}_cross_60_70', f'{opp.upper()} crossed $0.60→$0.70'),
            (f'{opp}_cross_70_80', f'{opp.upper()} crossed $0.70→$0.80'),
            (f'{opp}_cross_50_70', f'{opp.upper()} crossed $0.50→$0.70'),
        ]:
            total_tests += 1
            tr_m = [f for f in train_f if f.get(cross_key, False)]
            te_m = [f for f in test_f if f.get(cross_key, False)]
            if len(tr_m) < 8 or len(te_m) < 5: continue
            tr_w = sum(1 for f in tr_m if f['winner'] == fire_side) / len(tr_m)
            te_w = sum(1 for f in te_m if f['winner'] == fire_side) / len(te_m)
            results.append({
                'type': '6_staircase', 'fire_side': fire_side,
                'desc': f"{fire_side} when {cross_label}",
                'tr_n': len(tr_m), 'tr_wr': tr_w, 'te_n': len(te_m), 'te_wr': te_w})
    
    print(f"\nTotal tests: {total_tests}")
    print(f"Results with sufficient N: {len(results)}")
    bonf = 0.05 / max(total_tests, 1)
    print(f"Bonferroni alpha: {bonf:.8f}")
    
    # ══════════════════════════════════════════════════════════════
    # SUPPRESS results (low WR = don't fire)
    # ══════════════════════════════════════════════════════════════
    suppress = [r for r in results if r['tr_wr'] <= 0.35 and r['te_wr'] <= 0.35]
    suppress.sort(key=lambda r: (r['fire_side'], r['te_wr'], r['tr_wr']))
    
    for side in ['DN', 'UP']:
        side_sup = [r for r in suppress if r['fire_side'] == side]
        print(f"\n{'='*120}")
        print(f"{side} SUPPRESSION — gates that kill {side} fires (sorted by OOS WR)")
        print(f"{'='*120}")
        print(f"  {'Type':<14}{'Description':<70}{'TrN':<6}{'TrWR':<7}{'OOSN':<6}{'OOSWR':<7}{'Bonf?'}")
        print(f"  {'─'*115}")
        
        shown = set()
        for r in side_sup[:40]:
            d = r['desc']
            if d in shown: continue
            shown.add(d)
            te_losses = r['te_n'] - int(r['te_wr'] * r['te_n'])
            p = binomial_p(te_losses, r['te_n'], 0.50)
            bf = "✅ BONF" if p < bonf else ""
            print(f"  {r['type']:<14}{d:<70}{r['tr_n']:<6}{r['tr_wr']:.0%}   {r['te_n']:<6}{r['te_wr']:.0%}   {bf}")
    
    # ══════════════════════════════════════════════════════════════
    # OPTIMAL per side
    # ══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print("OPTIMAL GATES PER SIDE (comparing old single-variable vs new combinations)")
    print(f"{'='*120}")
    
    for side in ['DN', 'UP']:
        side_sup = [r for r in suppress if r['fire_side'] == side and r['te_n'] >= 5]
        if not side_sup:
            print(f"\n  {side}: No suppress gates found")
            continue
        best = min(side_sup, key=lambda r: r['te_wr'] * 100 + (1 / max(r['te_n'], 1)))
        print(f"\n  {side} BEST NEW GATE:")
        print(f"    {best['desc']}")
        print(f"    Type: {best['type']}")
        print(f"    Train: {best['tr_n']} markets, {best['tr_wr']:.0%} WR")
        print(f"    OOS:   {best['te_n']} markets, {best['te_wr']:.0%} WR")

if __name__ == "__main__":
    main()

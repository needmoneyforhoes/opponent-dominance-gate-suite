#!/usr/bin/env python3
"""
gate_analysis_vps.py
━━━━━━━━━━━━━━━━━━━━
Run on VPS against full market_history.jsonl + opp_gate_history.jsonl
to find the statistically optimal dn_live gate policy.

Usage:
    python3 gate_analysis_vps.py \
        --history /home/polybot/polymarket-bot/market_history.jsonl \
        --gate    /home/polybot/polymarket-bot/opp_gate_history.jsonl \
        --out     gate_report.json
"""

import json, math, argparse, sys, time, os
from collections import defaultdict

try:
    from scipy import stats as st
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False
    print("WARNING: scipy not installed — p-values will be approximate")

# ── Progress ──────────────────────────────────────────────────────────────
class Progress:
    BAR_W = 35
    def __init__(self, total, label=''):
        self.total = total; self.label = label
        self.t0 = time.time(); self._done = False

    def update(self, done):
        if self._done: return
        elapsed = time.time() - self.t0
        frac = done / self.total if self.total else 1
        eta = self._fmt(elapsed / frac * (1 - frac)) if frac > 0 else '--:--'
        filled = int(self.BAR_W * frac)
        bar = '█' * filled + '░' * (self.BAR_W - filled)
        sys.stdout.write(
            f"\r  {self.label}  [{bar}] {frac*100:5.1f}%  "
            f"{done:,}/{self.total:,}  elapsed {self._fmt(elapsed)}  ETA {eta}  "
            f"({done/elapsed:,.0f}/s)   ")
        sys.stdout.flush()

    def done(self):
        self._done = True
        elapsed = self._fmt(time.time() - self.t0)
        sys.stdout.write(
            f"\r  {self.label}  [{'█'*self.BAR_W}] 100.0%  "
            f"{self.total:,}/{self.total:,}  elapsed {elapsed}  ✅\n")
        sys.stdout.flush()

    @staticmethod
    def _fmt(s):
        s = int(s); h, rem = divmod(s, 3600); m, sec = divmod(rem, 60)
        return f"{h}h{m:02d}m{sec:02d}s" if h else f"{m:02d}m{sec:02d}s"

# ── Stats helpers ─────────────────────────────────────────────────────────
def wilson_ci(wins, n, z=1.96):
    if n == 0: return 0, 0
    p = wins / n
    lo = max(0, (p+z**2/(2*n)-z*math.sqrt(p*(1-p)/n+z**2/(4*n**2)))/(1+z**2/n))
    hi = min(1, (p+z**2/(2*n)+z*math.sqrt(p*(1-p)/n+z**2/(4*n**2)))/(1+z**2/n))
    return lo, hi

def binom_p(wins, n, p0, alt='two-sided'):
    if not HAS_SCIPY:
        if n == 0: return 1.0
        z = (wins/n - p0) / math.sqrt(p0*(1-p0)/n)
        return 0.5*(1 - math.erf(abs(z)/math.sqrt(2))) * (2 if alt=='two-sided' else 1)
    return st.binomtest(wins, n, p0, alternative=alt).pvalue

def ev_per_block(accuracy, avg_entry):
    """EV of the gate BLOCKING a fire. Positive = gate saves money."""
    # accuracy = P(fire would have lost) = gate was right
    # gate saves: accuracy * (avg_entry * 5) per block
    # gate costs: (1-accuracy) * (1-avg_entry)/avg_entry * (avg_entry * 5)
    lose_saved = accuracy * avg_entry * 5
    win_missed = (1-accuracy) * (1-avg_entry) / avg_entry * avg_entry * 5
    return lose_saved - win_missed

def sig_stars(p):
    return '***' if p<0.001 else ('**' if p<0.01 else ('*' if p<0.05 else ('~' if p<0.1 else ' ')))

# ── Main ──────────────────────────────────────────────────────────────────
def main():
    t_global = time.time()

    parser = argparse.ArgumentParser()
    parser.add_argument('--history', required=True, help='market_history.jsonl (full tick)')
    parser.add_argument('--gate',    required=True, help='opp_gate_history.jsonl')
    parser.add_argument('--out',     default='gate_report.json')
    args = parser.parse_args()

    # ── Step 1: Load gate history ─────────────────────────────────────────
    print(f"\nStep 1/5 — Loading gate history from {args.gate} ...")
    with open(args.gate) as f:
        raw_gate = [json.loads(l) for l in f if l.strip()]
    gate_by_slug = {g.get('slug'): g for g in raw_gate}

    dn_live_slugs = {
        slug for slug,g in gate_by_slug.items()
        if isinstance(g.get('dn_live'), dict) and g['dn_live'].get('triggered')
    }
    print(f"  Gate entries: {len(gate_by_slug)}  |  dn_live triggered: {len(dn_live_slugs)}")

    # ── Step 2: Load market history ───────────────────────────────────────
    print(f"\nStep 2/5 — Loading {args.history} ...")
    sys.stdout.write("  Counting lines ..."); sys.stdout.flush()
    t0 = time.time()
    with open(args.history) as f:
        total_lines = sum(1 for l in f if l.strip())
    print(f"\r  Counting lines ... {total_lines:,} lines ({time.time()-t0:.1f}s)")

    markets = []
    prog = Progress(total_lines, 'Loading  ')
    with open(args.history) as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line: continue
            try: markets.append(json.loads(line))
            except Exception: continue
            if i % 500 == 0: prog.update(i+1)
    prog.done()
    print(f"  Loaded {len(markets):,} markets")

    # ── Step 3: Aggregate gate accuracy from gate history ─────────────────
    print(f"\nStep 3/5 — Gate accuracy analysis ...")
    dn_live_mkts = [g for g in raw_gate
                    if isinstance(g.get('dn_live'),dict) and g['dn_live'].get('triggered')]

    total_b = sum(g['dn_live'].get('blocks',0) for g in dn_live_mkts)
    total_w = sum(g['dn_live'].get('blocks_won',0) for g in dn_live_mkts)
    total_l = sum(g['dn_live'].get('blocks_lost',0) for g in dn_live_mkts)

    # nofloor (cheap entries ≤floor, tracked separately)
    nf_b = sum(g.get('dn_live_nofloor',{}).get('blocks',0) for g in dn_live_mkts)
    nf_w = sum(g.get('dn_live_nofloor',{}).get('blocks_won',0) for g in dn_live_mkts)

    gate_wr = total_w/total_b if total_b else 0
    ci_lo, ci_hi = wilson_ci(total_w, total_b)

    # Market-level: UP win rate when gate fires
    up_wins_mkt = sum(1 for g in dn_live_mkts if g.get('winner')=='UP')
    n_mkt = len(dn_live_mkts)
    mkt_up_wr = up_wins_mkt/n_mkt if n_mkt else 0
    mkt_ci_lo, mkt_ci_hi = wilson_ci(up_wins_mkt, n_mkt)

    # Trigger timing split
    early = [g for g in dn_live_mkts if (g['dn_live'].get('trigger_cd') or 0) >= 200]
    late  = [g for g in dn_live_mkts if (g['dn_live'].get('trigger_cd') or 0) < 200]

    print(f"  dn_live triggered: {n_mkt} markets, {total_b} blocks")
    print(f"  Gate accuracy: {gate_wr:.1%}  CI=[{ci_lo:.1%},{ci_hi:.1%}]")
    print(f"  UP win rate when gate fires: {mkt_up_wr:.1%}  CI=[{mkt_ci_lo:.1%},{mkt_ci_hi:.1%}]")

    # ── Step 4: Per-strategy and per-price breakdown ───────────────────────
    print(f"\nStep 4/5 — Per-strategy breakdown from market history ...")
    prog = Progress(len(markets), 'Analysing')

    by_strat   = defaultdict(lambda: {'n':0,'wins':0,'hypo':0.0})
    by_price   = defaultdict(lambda: {'n':0,'wins':0,'hypo':0.0})
    by_strat_price = defaultdict(lambda: {'n':0,'wins':0,'hypo':0.0})

    # Policy simulation
    policies = {
        'DISABLE_GATE':    0.0,  # allow all, ignore gate
        'FULL_BLOCK':      0.0,  # block all DN when gate active
        'FLOOR_10':        0.0,  # current: allow ≤$0.10
        'FLOOR_05':        0.0,  # tighter: allow ≤$0.05
        'INVERT_ALLOW_20': 0.0,  # allow $0.10-$0.45 (reversal range), block cheap
        'INVERT_ALLOW_30': 0.0,  # allow $0.10-$0.35
    }

    for idx, m in enumerate(markets):
        if idx % 300 == 0: prog.update(idx+1)

        slug = m.get('slug','')
        winner = m.get('winner')
        fires = m.get('fires') or []
        sc = m.get('skip_counterfactuals') or {}

        if slug not in dn_live_slugs: continue

        for fire in fires:
            side = fire.get('side')
            if side != 'DN': continue
            if fire.get('pre_gate_held') or fire.get('opp_gate_held'): continue

            p     = fire.get('entry_price') or 0
            h     = fire.get('hypo_pnl') or 0
            strat = fire.get('strategy','?')
            is_blocked = bool(fire.get('opp_gate_would_block') or
                              fire.get('opp_gate_blocked'))
            won   = h > 0

            # Price bucket (5-cent bins)
            pb = f"${(int(p*20)/20):.2f}-{(int(p*20)/20+0.05):.2f}"

            if not is_blocked:
                # Fire went through — appears in all "allow" policies
                by_strat[strat]['n'] += 1
                by_strat[strat]['hypo'] += h
                if won: by_strat[strat]['wins'] += 1

                by_price[pb]['n'] += 1
                by_price[pb]['hypo'] += h
                if won: by_price[pb]['wins'] += 1

                by_strat_price[f"{strat}|{pb}"]['n'] += 1
                by_strat_price[f"{strat}|{pb}"]['hypo'] += h
                if won: by_strat_price[f"{strat}|{pb}"]['wins'] += 1

                # Policy sim (non-blocked = passes in all allow policies)
                policies['DISABLE_GATE'] += h
                if p <= 0.10: policies['FLOOR_10'] += h
                if p <= 0.05: policies['FLOOR_05'] += h
                if 0.10 < p <= 0.45: policies['INVERT_ALLOW_20'] += h
                if 0.10 < p <= 0.35: policies['INVERT_ALLOW_30'] += h
                # FULL_BLOCK: $0 (fire suppressed)

            else:
                # Blocked — only DISABLE_GATE would have fired it
                policies['DISABLE_GATE'] += h
                # INVERT policies allow $0.10-$0.45
                if 0.10 < p <= 0.45: policies['INVERT_ALLOW_20'] += h
                if 0.10 < p <= 0.35: policies['INVERT_ALLOW_30'] += h
                # FULL_BLOCK, FLOOR_10, FLOOR_05: $0

    prog.done()

    # ── Step 5: Print and save results ────────────────────────────────────
    print(f"\nStep 5/5 — Building report ...")

    print(f"\n{'='*65}")
    print(f"GATE ACCURACY (n={total_b} blocks, {n_mkt} markets)")
    print(f"{'='*65}")
    avg_entry_est = 0.30  # typical blocked entry price
    for acc_label, acc in [('CI_lo', ci_lo), ('Observed', gate_wr), ('CI_hi', ci_hi)]:
        ev = ev_per_block(acc, avg_entry_est)
        print(f"  {acc_label:10} accuracy={acc:.1%}  EV/block={ev:+.2f}  "
              f"{'✅ gate saves' if ev>0 else '❌ gate costs'}")
    p_vs_be = binom_p(total_w, total_b, avg_entry_est, alt='less')
    print(f"\n  Binomial p(accuracy < breakeven {avg_entry_est:.0%}): {p_vs_be:.5f}{sig_stars(p_vs_be)}")
    print(f"  CI_lo={ci_lo:.1%} vs breakeven {avg_entry_est:.0%}: "
          f"{'✅ gate clearly EV+' if ci_lo>avg_entry_est else '⚠️ gate may be EV-'}")

    print(f"\n  Trigger timing:")
    for label, grp in [("Early (cd≥200)", early), ("Late (cd<200)", late)]:
        nb = sum(g['dn_live'].get('blocks',0) for g in grp)
        nw = sum(g['dn_live'].get('blocks_won',0) for g in grp)
        if nb:
            cl, ch = wilson_ci(nw, nb)
            ev = ev_per_block(nw/nb, avg_entry_est)
            print(f"    {label}: blocks={nb}  acc={nw/nb:.1%}  CI=[{cl:.1%},{ch:.1%}]  EV={ev:+.2f}")

    print(f"\n{'='*65}")
    print(f"POLICY SIMULATION (all dn_live-triggered markets)")
    print(f"{'='*65}")
    print(f"  {'Policy':<22} {'Total PnL':>12}  {'Per mkt':>9}  vs FULL_BLOCK")
    print("  " + "─"*55)
    base = policies['FULL_BLOCK']
    for pol, val in sorted(policies.items(), key=lambda x: -x[1]):
        diff = val - base
        mark = "← baseline" if pol == 'FULL_BLOCK' else f"{diff:+.2f}"
        print(f"  {pol:<22} {val:>+12.2f}  {val/max(n_mkt,1):>+9.2f}  {mark}")

    print(f"\n  Best policy: {max(policies, key=lambda k: policies[k])}")

    print(f"\n{'='*65}")
    print(f"FREE DN FIRES IN GATE MARKETS — by price bucket")
    print(f"{'='*65}")
    print(f"  {'Price':>12} {'n':>4} {'WR':>6} {'EV/fire':>8} {'CI_lo':>6}  verdict")
    print("  " + "─"*58)
    for pb in sorted(by_price.keys()):
        d = by_price[pb]
        n, w = d['n'], d['wins']
        if n < 3: continue
        wr = w/n; ev = d['hypo']/n
        cl, ch = wilson_ci(w, n)
        verd = "✅ EV+" if ev > 0 and cl > 0.20 else ("🔬 borderline" if abs(ev) < 0.5 else "❌ EV-")
        print(f"  {pb:>12} {n:4d} {wr:6.1%} {ev:+8.3f} {cl:6.1%}  {verd}")

    print(f"\n{'='*65}")
    print(f"TOP STRATEGIES IN GATE MARKETS (by volume, n≥10)")
    print(f"{'='*65}")
    print(f"  {'Strategy':<30} {'n':>4} {'WR':>6} {'EV/fire':>8}  verdict")
    print("  " + "─"*58)
    for strat, d in sorted(by_strat.items(), key=lambda x: -x[1]['n']):
        n, w = d['n'], d['wins']
        if n < 10: continue
        wr = w/n; ev = d['hypo']/n
        cl, _ = wilson_ci(w, n)
        verd = "✅ EV+" if ev > 0 else "❌ EV-"
        print(f"  {strat:<30} {n:4d} {wr:6.1%} {ev:+8.3f}  {verd}")

    # Total elapsed
    total_elapsed = Progress._fmt(time.time() - t_global)
    print(f"\n{'='*65}")
    print(f"Total runtime: {total_elapsed}")
    print(f"{'='*65}")

    # Save JSON
    output = {
        'n_markets_total': len(markets),
        'n_gate_triggered': n_mkt,
        'n_blocks': total_b,
        'gate_accuracy': round(gate_wr, 5),
        'gate_accuracy_ci': [round(ci_lo,5), round(ci_hi,5)],
        'gate_accuracy_breakeven': avg_entry_est,
        'gate_ev_at_observed': round(ev_per_block(gate_wr, avg_entry_est), 4),
        'gate_ev_at_ci_lo':    round(ev_per_block(ci_lo,  avg_entry_est), 4),
        'binom_p_vs_breakeven': round(p_vs_be, 6),
        'market_up_wr_when_gate': round(mkt_up_wr, 5),
        'market_up_wr_ci': [round(mkt_ci_lo,5), round(mkt_ci_hi,5)],
        'nofloor_blocks': nf_b,
        'nofloor_accuracy': round(nf_w/nf_b, 5) if nf_b else 0,
        'policies': {k: {'total': round(v,4), 'per_mkt': round(v/max(n_mkt,1),4)}
                     for k,v in policies.items()},
        'by_price': {pb: {'n':d['n'],'wins':d['wins'],
                          'wr': round(d['wins']/d['n'],4) if d['n'] else 0,
                          'ev': round(d['hypo']/d['n'],4) if d['n'] else 0}
                     for pb,d in by_price.items() if d['n'] >= 3},
        'by_strategy': {s: {'n':d['n'],'wins':d['wins'],
                            'wr': round(d['wins']/d['n'],4) if d['n'] else 0,
                            'ev': round(d['hypo']/d['n'],4) if d['n'] else 0}
                        for s,d in by_strat.items() if d['n'] >= 5},
        'runtime_seconds': round(time.time()-t_global, 1),
    }
    with open(args.out, 'w') as f:
        json.dump(output, f, indent=2)
    print(f"\n✅ Report saved to {args.out}")
    print(f"   Gate-triggered markets: {n_mkt} | Total blocks analyzed: {total_b}")

if __name__ == '__main__':
    main()

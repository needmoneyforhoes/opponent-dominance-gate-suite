#!/usr/bin/env python3
"""
Gate Backtest — Full reconstruction from market_history.jsonl
Simulates every gate condition on ALL historical markets using
skip_counterfactuals price snapshots (T-200 to T-30).

No future bias: gate triggers are computed from T-200 snapshot
(prices at cd=200, before most fires). Fire outcomes come from
market_recap_history.jsonl (realized outcomes only).

Usage:
    python3 gate_backtest.py                              # auto-find files
    python3 gate_backtest.py /path/to/data/dir
    python3 gate_backtest.py --mh /path/market_history.jsonl
                             --recap /path/market_recap.jsonl

Requirements: scipy  (pip install scipy)
"""

import json, math, os, sys, random, itertools
from collections import defaultdict

random.seed(42)

# ── Paths ─────────────────────────────────────────────────────────────
SEARCH_DIRS = [
    os.getcwd(),
    '/home/polybot/polymarket-bot',
    os.path.dirname(os.path.abspath(__file__)),
]

def find_file(name):
    for d in SEARCH_DIRS:
        p = os.path.join(d, name)
        if os.path.isfile(p): return p
    return None

MH_PATH    = None
RECAP_PATH = None

args = sys.argv[1:]
i = 0
while i < len(args):
    a = args[i]
    if a == '--mh' and i+1 < len(args):
        MH_PATH = args[i+1]; i += 2
    elif a.startswith('--mh='):
        MH_PATH = a[5:]; i += 1
    elif a == '--recap' and i+1 < len(args):
        RECAP_PATH = args[i+1]; i += 2
    elif a.startswith('--recap='):
        RECAP_PATH = a[8:]; i += 1
    elif os.path.isdir(a):
        SEARCH_DIRS.insert(0, a); i += 1
    else:
        i += 1

if not MH_PATH:    MH_PATH    = find_file('market_history.jsonl')
if not RECAP_PATH: RECAP_PATH = find_file('market_recap_history.jsonl')

if not MH_PATH or not RECAP_PATH:
    print("ERROR: Cannot find required files.")
    print(f"  market_history.jsonl     : {MH_PATH or 'NOT FOUND'}")
    print(f"  market_recap_history.jsonl: {RECAP_PATH or 'NOT FOUND'}")
    sys.exit(1)

print(f"market_history : {MH_PATH}")
print(f"market_recap   : {RECAP_PATH}")

# ── Config ────────────────────────────────────────────────────────────
N_PERMUTATIONS = 10000
IS_SPLIT       = 0.60
ALPHA_RAW      = 0.05
MIN_N          = 20       # min blocks to report
TRIGGER_CKPT   = 'T-200'  # snapshot used to determine gate trigger state

# ── Load ──────────────────────────────────────────────────────────────
def load_jsonl(path, max_rows=None):
    rows = []
    with open(path, buffering=8*1024*1024) as f:
        for i, line in enumerate(f):
            if max_rows and i >= max_rows: break
            line = line.strip()
            if line:
                try: rows.append(json.loads(line))
                except: pass
    return rows

print("Loading market_history.jsonl ...", flush=True)
mh_rows = load_jsonl(MH_PATH)
print(f"  {len(mh_rows):,} rows")

print("Loading market_recap_history.jsonl ...", flush=True)
recap_rows = load_jsonl(RECAP_PATH)
print(f"  {len(recap_rows):,} rows")

# Build slug → market_history lookup
mh_by_slug = {}
for r in mh_rows:
    slug = r.get('slug','')
    if slug: mh_by_slug[slug] = r

# ── Price reconstruction from skip_counterfactuals ───────────────────
def reconstruct_asks(cf_snap, winner):
    """From a skip_counterfactuals snapshot, reconstruct UP and DN ask prices.
    Binary market: winner_ask + loser_ask ≈ 1.00 (±0.01 spread).
    loser_ask ≈ 1 - winner_ask.
    """
    if not cf_snap: return None, None
    wa = cf_snap.get('winner_ask')
    if wa is None: return None, None
    if winner == 'UP':
        return wa, round(1 - wa, 4)   # UP_ask, DN_ask
    else:
        return round(1 - wa, 4), wa   # UP_ask, DN_ask

# ── Gate trigger simulation ───────────────────────────────────────────
# Each gate returns (triggered: bool, blocks_side: str or None)
# blocks_side = the SIDE this gate would BLOCK if triggered
# 'UP' = gate blocks UP entries, 'DN' = gate blocks DN entries,
# 'CHEAP' = gate blocks whichever side is cheap (crowd_gate)

def sim_gates(cf200, winner):
    """Simulate all gate trigger conditions from T-200 snapshot.
    Returns dict: gate_name → {'triggered': bool, 'blocks_side': str}
    """
    up_ask, dn_ask = reconstruct_asks(cf200, winner)
    if up_ask is None:
        return {}

    crowd_side = cf200.get('crowd_side','?')
    crowd_conv = cf200.get('crowd_conv', 0) or 0
    bn_delta   = cf200.get('bn_delta',   0) or 0

    gates = {}

    # dn_live: UP ≥ $0.50 → blocks DN entries
    # (condition: UP ask ≥ 0.50 for 2 consecutive 10s samples at cd ≤ 210)
    gates['dn_live'] = {
        'triggered':   up_ask >= 0.50,
        'blocks_side': 'DN',
        'threshold':   up_ask,
        'condition':   f'UP_ask={up_ask:.3f}≥0.50',
    }

    # dn_tight: UP ≥ $0.65 → blocks DN entries (stricter)
    gates['dn_tight'] = {
        'triggered':   up_ask >= 0.65,
        'blocks_side': 'DN',
        'threshold':   up_ask,
        'condition':   f'UP_ask={up_ask:.3f}≥0.65',
    }

    # dn_bn: BN delta > 3% → blocks DN entries
    gates['dn_bn'] = {
        'triggered':   bn_delta > 0.03,
        'blocks_side': 'DN',
        'threshold':   bn_delta,
        'condition':   f'BN={bn_delta:+.4f}>0.03',
    }

    # up_live: DN ≥ $0.70 → blocks UP entries
    gates['up_live'] = {
        'triggered':   dn_ask >= 0.70,
        'blocks_side': 'UP',
        'threshold':   dn_ask,
        'condition':   f'DN_ask={dn_ask:.3f}≥0.70',
    }

    # up_tight: DN ≥ $0.65 → blocks UP entries
    gates['up_tight'] = {
        'triggered':   dn_ask >= 0.65,
        'blocks_side': 'UP',
        'threshold':   dn_ask,
        'condition':   f'DN_ask={dn_ask:.3f}≥0.65',
    }

    # up_wide: DN ≥ $0.55 → blocks UP entries
    gates['up_wide'] = {
        'triggered':   dn_ask >= 0.55,
        'blocks_side': 'UP',
        'threshold':   dn_ask,
        'condition':   f'DN_ask={dn_ask:.3f}≥0.55',
    }

    # up_bn_wide: BN delta < -1% → blocks UP entries
    gates['up_bn_wide'] = {
        'triggered':   bn_delta < -0.01,
        'blocks_side': 'UP',
        'threshold':   bn_delta,
        'condition':   f'BN={bn_delta:+.4f}<-0.01',
    }

    # crowd_gate: cheap_side ask < $0.45 AND crowd_conv ≥ 0.60
    # cheap_side = opposite of crowd direction
    if crowd_conv >= 0.60 and crowd_side in ('UP','DN'):
        cheap_side = 'DN' if crowd_side == 'UP' else 'UP'
        cheap_ask  = dn_ask if cheap_side == 'DN' else up_ask
        cg_triggered = cheap_ask < 0.45
    else:
        cheap_side   = None
        cg_triggered = False
        cheap_ask    = None

    gates['crowd_gate'] = {
        'triggered':   cg_triggered,
        'blocks_side': cheap_side,
        'threshold':   cheap_ask,
        'condition':   (f'cheap={cheap_side}@{cheap_ask:.3f}<0.45 '
                        f'conv={crowd_conv:.2f}≥0.60') if cg_triggered else 'not triggered',
    }

    return gates

# ── Reconstruct gate stats from market data ───────────────────────────
print("\nReconstructing gate stats from market history ...", flush=True)

# Per-gate accumulator: list of (blocks_won, blocks_lost) per market
gate_market_stats = defaultdict(list)   # gate → [(won, lost), ...]
gate_names = ['dn_live','dn_tight','dn_bn',
              'up_live','up_tight','up_wide','up_bn_wide','crowd_gate']
LIVE_GATES  = ['dn_live', 'crowd_gate']

matched = 0
skipped_no_cf = 0
skipped_no_mh = 0

for recap in recap_rows:
    slug   = recap.get('slug','')
    winner = recap.get('winner','')
    fires  = recap.get('fires', [])
    if winner not in ('UP','DN') or not fires: continue

    mh = mh_by_slug.get(slug)
    if not mh:
        skipped_no_mh += 1
        continue

    cf_snap = (mh.get('skip_counterfactuals') or {}).get(TRIGGER_CKPT)
    if not cf_snap:
        skipped_no_cf += 1
        continue

    gate_states = sim_gates(cf_snap, winner)
    if not gate_states: continue

    matched += 1

    for gate in gate_names:
        gs = gate_states.get(gate, {})
        if not gs.get('triggered'): continue

        blocks_side = gs.get('blocks_side')
        if blocks_side is None: continue

        won = lost = 0
        for f in fires:
            f_side = f.get('side','')
            if f_side != blocks_side: continue  # gate only blocks this side
            # Only count fires that happen AFTER gate triggers (cd <= trigger window)
            # Gate triggers around cd=200-250; fires at cd > 200 happen BEFORE trigger
            f_cd = f.get('cd', 0)
            if f_cd > 200: continue   # pre-trigger fire — gate not yet active
            # This fire would be blocked
            if f_side == winner:  # fire direction == winner → WRONG block
                won += 1
            else:                 # fire direction != winner → CORRECT block
                lost += 1

        if won + lost > 0:
            gate_market_stats[gate].append({
                'won': won, 'lost': lost, 'winner': winner,
                'slug': slug
            })

print(f"  Matched: {matched}  "
      f"no_mh: {skipped_no_mh}  no_cf: {skipped_no_cf}")

# Compute entry prices from recap for EV calc
all_entries = [f['entry_price'] for r in recap_rows for f in r.get('fires',[])
               if 0.01 < f.get('entry_price',0) < 0.99]
AVG_ENTRY  = sum(all_entries)/len(all_entries) if all_entries else 0.32
WIN_PAYOFF = 1 - AVG_ENTRY
LOSS_COST  = AVG_ENTRY
BREAKEVEN  = LOSS_COST / (WIN_PAYOFF + LOSS_COST)

print(f"  Avg entry ${AVG_ENTRY:.4f}  Breakeven WR {BREAKEVEN*100:.2f}%")
print(f"  Total fires in recap: {len(all_entries):,}")

# ── Stats helpers ─────────────────────────────────────────────────────
def ev_block(wr): return (1-wr)*LOSS_COST - wr*WIN_PAYOFF

def wilson_ci(k, n, z=1.96):
    if n==0: return None, None
    p=k/n; c=(p+z**2/(2*n))/(1+z**2/n)
    m=z*math.sqrt(p*(1-p)/n+z**2/(4*n**2))/(1+z**2/n)
    return max(0,c-m), min(1,c+m)

def binom_p(k, n, p0=None):
    p0 = p0 or BREAKEVEN
    if n==0: return 1.0
    z=(k/n-p0)/math.sqrt(p0*(1-p0)/n)
    return float(math.erfc(abs(z)/math.sqrt(2)))

def perm_test(per_mkt, all_market_data, n_perm=N_PERMUTATIONS):
    """
    Correct permutation test: randomly select same number of markets
    from the full pool and compute WR. Tests whether the gate trigger
    selects better markets than a random same-size sample.
    H0: gate trigger is no better than random market selection.
    p = fraction of random samples with WR <= observed WR.
    """
    n_triggered = len(per_mkt)
    if n_triggered < 5: return 1.0
    obs_n   = sum(d['won']+d['lost'] for d in per_mkt)
    obs_won = sum(d['won'] for d in per_mkt)
    if obs_n == 0: return 1.0
    obs_wr = obs_won / obs_n

    perm_wrs = []
    all_slugs = list(all_market_data.keys())
    for _ in range(n_perm):
        sample = random.sample(all_slugs, min(n_triggered, len(all_slugs)))
        s_won = s_n = 0
        for slug in sample:
            d = all_market_data[slug]
            s_won += d['won']; s_n += d['won'] + d['lost']
        if s_n > 0: perm_wrs.append(s_won/s_n)

    if not perm_wrs: return 1.0
    return sum(1 for pw in perm_wrs if pw <= obs_wr) / len(perm_wrs)

def autocorr(per_mkt):
    seq=[(d['won']/(d['won']+d['lost'])) for d in per_mkt if d['won']+d['lost']>0]
    if len(seq)<5: return None
    n=len(seq); m=sum(seq)/n; v=sum((x-m)**2 for x in seq)/n
    if v==0: return 0.0
    c=sum((seq[i]-m)*(seq[i+1]-m) for i in range(n-1))/(n-1)
    return c/v

# ── Sort chronologically for walk-forward ─────────────────────────────
for gate in gate_names:
    gate_market_stats[gate].sort(key=lambda d: d.get('slug',''))

# ── Build all-market pool for permutation test ────────────────────────
# Pool = all markets that had ANY fires at cd <= 200
all_market_pool = {}
for recap in recap_rows:
    slug   = recap.get('slug','')
    winner = recap.get('winner','')
    fires  = [f for f in recap.get('fires',[]) if f.get('cd',0) <= 200]
    if not fires or winner not in ('UP','DN'): continue
    up_fires = [f for f in fires if f['side']=='UP']
    dn_fires = [f for f in fires if f['side']=='DN']
    # Generic block stats: fires that would lose = correct block
    won  = sum(1 for f in fires if f['side']==winner)   # wrong direction
    lost = sum(1 for f in fires if f['side']!=winner)   # right direction
    all_market_pool[slug] = {'won': won, 'lost': lost, 'winner': winner}

# ── Run tests ─────────────────────────────────────────────────────────
N_TESTS       = len(gate_names) + len(list(itertools.combinations(gate_names,2)))
ALPHA_BONF    = ALPHA_RAW / N_TESTS

def sep(t=''):
    print('\n'+'='*110)
    if t: print(f'  {t}')
    print('='*110)

def hdr():
    print(f"  {'Gate':<30} {'n':>5} {'WR%':>6} {'EV/blk':>8} {'95%CI':>16}"
          f" {'p_binom':>8} {'p_perm':>8} {'Bonf':>5}"
          f" {'OOS_WR':>7} {'OOS_p':>7}  Verdict")
    print(f"  {'-'*30} {'---':>5} {'---':>6} {'------':>8} {'------':>16}"
          f" {'-------':>8} {'------':>8} {'----':>5}"
          f" {'------':>7} {'-----':>7}  -------")

def test_gate(per_mkt):
    n = sum(d['won']+d['lost'] for d in per_mkt)
    won = sum(d['won'] for d in per_mkt)
    if n == 0: return None
    wr = won/n; evb = ev_block(wr)
    lo,hi = wilson_ci(won, n)
    pb = binom_p(won, n)
    pp = perm_test(per_mkt, all_market_pool)
    ac = autocorr(per_mkt)

    # Walk-forward split
    n_is = int(len(per_mkt)*IS_SPLIT)
    is_data  = per_mkt[:n_is]
    oos_data = per_mkt[n_is:]
    n_o  = sum(d['won']+d['lost'] for d in oos_data)
    won_o = sum(d['won'] for d in oos_data)
    wr_o = won_o/n_o if n_o >= MIN_N else None
    pb_o = binom_p(won_o, n_o) if n_o >= MIN_N else None

    bonf_pass = (pb < ALPHA_BONF and evb > 0)
    raw_pass  = (pb < ALPHA_RAW  and evb > 0)

    if bonf_pass:
        if wr_o is not None and ev_block(wr_o) > 0 and pb_o < 0.10:
            verdict = "✅ PROMOTE (Bonf+OOS)"
        elif wr_o is not None and ev_block(wr_o) > 0:
            verdict = "⚠️  Bonf confirmed, OOS weak"
        else:
            verdict = "⚠️  Bonf OK, OOS fails"
    elif raw_pass:
        verdict = f"⏳ p<0.05 pre-Bonf (need {N_TESTS}x more)"
    elif evb < 0 and pb < ALPHA_RAW:
        verdict = "❌ DO NOT promote"
    elif n < MIN_N:
        verdict = f"⏳ need {MIN_N-n} more blocks"
    else:
        verdict = "— keep shadow"

    return dict(n=n, won=won, wr=wr, evb=evb, lo=lo, hi=hi,
                pb=pb, pp=pp, ac=ac,
                n_o=n_o, wr_o=wr_o, pb_o=pb_o,
                bonf_pass=bonf_pass, verdict=verdict)

def fmt_row(label, res, status=''):
    if res is None:
        return f"  {label:<30} {'0':>5}  NO DATA"
    n=res['n']; wr=res['wr']; evb=res['evb']
    lo,hi=res['lo'],res['hi']; pb=res['pb']; pp=res['pp']
    n_o=res['n_o']; wr_o=res['wr_o']; pb_o=res['pb_o']
    bonf_s = "✅" if res['bonf_pass'] else ("⚠️" if pb<ALPHA_RAW else "✗")
    ci_s   = f"[{lo*100:.1f},{hi*100:.1f}]%" if lo else "—"
    oos_wr = f"{wr_o*100:.1f}%" if wr_o is not None else f"n={n_o}"
    oos_p  = f"{pb_o:.4f}" if pb_o is not None else "—"
    ac_s   = f"  ⚠️  ac={res['ac']:.2f}" if res['ac'] and abs(res['ac'])>0.20 else ""
    return (f"  {label:<30} {n:>5} {wr*100:>5.1f}% {evb:>+8.4f} {ci_s:>16}"
            f" {pb:>8.5f} {pp:>8.4f} {bonf_s:>5} {oos_wr:>7} {oos_p:>7}  {res['verdict']}{ac_s}")

# Precompute results
print(f"\nRunning tests: {N_TESTS} hypotheses  Bonferroni α={ALPHA_BONF:.5f}")

all_results = {}
for gate in gate_names:
    pm = gate_market_stats.get(gate, [])
    all_results[gate] = test_gate(pm)

# ── SECTION 1: Live baselines ─────────────────────────────────────────
sep("1. LIVE GATES — Baseline (reconstructed from market_history)")
print(f"  Trigger checkpoint: {TRIGGER_CKPT} (cd=200)")
hdr()
for g in LIVE_GATES:
    status = 'LIVE' if g in LIVE_GATES else 'shadow'
    print(fmt_row(g, all_results[g], status))

# ── SECTION 2: Shadow gates ───────────────────────────────────────────
shadow_gates = [g for g in gate_names if g not in LIVE_GATES]
sep("2. SHADOW GATES — Individual (Bonferroni-corrected)")
print(f"  {N_TESTS} total hypotheses  |  Bonferroni α={ALPHA_BONF:.5f}")
hdr()
for g in shadow_gates:
    print(fmt_row(g, all_results[g], 'shadow'))

# ── SECTION 3: Combinations ───────────────────────────────────────────
sep("3. SHADOW GATE PAIRS — Incremental over base (sorted by EV)")
hdr()

def combo_blocks(gate_list):
    """Union of blocks across gates (additive, conservative)."""
    combined = defaultdict(lambda: {'won':0,'lost':0,'winner':'','slug':''})
    for gate in gate_list:
        for d in gate_market_stats.get(gate,[]):
            slug = d['slug']
            combined[slug]['won']    += d['won']
            combined[slug]['lost']   += d['lost']
            combined[slug]['winner']  = d['winner']
            combined[slug]['slug']    = slug
    return list(combined.values())

pair_results = []
for g1, g2 in itertools.combinations(shadow_gates, 2):
    combined = combo_blocks([g1, g2])
    base     = combo_blocks(LIVE_GATES)
    # Incremental: what the pair adds over base
    base_won  = sum(d['won']  for d in combo_blocks(LIVE_GATES))
    base_lost = sum(d['lost'] for d in combo_blocks(LIVE_GATES))
    combo_won  = sum(d['won']  for d in combined)
    combo_lost = sum(d['lost'] for d in combined)
    inc_won    = combo_won  - base_won
    inc_lost   = combo_lost - base_lost
    inc_n      = inc_won + inc_lost
    if inc_n < MIN_N: continue
    wr = inc_won/inc_n; evb = ev_block(wr); pb = binom_p(inc_won, inc_n)
    pair_results.append((g1, g2, inc_n, wr, evb, pb))

pair_results.sort(key=lambda x: (-x[4] if x[4]>0 else 1, x[5]))
for g1, g2, n, wr, evb, pb in pair_results[:12]:
    bonf_s = "✅" if (pb<ALPHA_BONF and evb>0) else ("⚠️" if (pb<ALPHA_RAW and evb>0) else "✗")
    lo,hi = wilson_ci(int(wr*n), n)
    ci_s = f"[{lo*100:.1f},{hi*100:.1f}]%" if lo else "—"
    print(f"  {g1}+{g2:<25} {n:>5} {wr*100:>5.1f}% {evb:>+8.4f} {ci_s:>16}"
          f" {pb:>8.5f}  {'—':>8} {bonf_s:>5}")

# ── SECTION 4: Per-winner breakdown ──────────────────────────────────
sep("4. PER-WINNER BREAKDOWN — How each gate performs by market type")
print(f"  {'Gate':<22} {'UP_mkt':>28} {'DN_mkt':>28} {'UP_EV':>9} {'DN_EV':>9}")
print(f"  {'-'*22} {'n':>5} {'wr%':>5} {'total_ev':>8}"
      f"  {'n':>5} {'wr%':>5} {'total_ev':>8}  {'UP_EV':>8} {'DN_EV':>8}")
for gate in gate_names:
    pm = gate_market_stats.get(gate, [])
    up_won=up_lost=dn_won=dn_lost=0
    for d in pm:
        if d['winner']=='UP': up_won+=d['won']; up_lost+=d['lost']
        else:                 dn_won+=d['won']; dn_lost+=d['lost']
    up_n=up_won+up_lost; dn_n=dn_won+dn_lost
    up_wr=up_won/up_n if up_n else None
    dn_wr=dn_won/dn_n if dn_n else None
    up_ev=ev_block(up_wr) if up_wr is not None else None
    dn_ev=ev_block(dn_wr) if dn_wr is not None else None
    up_tev=up_ev*up_n if up_ev else 0
    dn_tev=dn_ev*dn_n if dn_ev else 0
    up_s = f"{up_n:>5} {up_wr*100:>4.0f}% {up_tev:>+8.2f}" if up_wr is not None else f"{'0':>5} {'—':>5} {'0':>8}"
    dn_s = f"{dn_n:>5} {dn_wr*100:>4.0f}% {dn_tev:>+8.2f}" if dn_wr is not None else f"{'0':>5} {'—':>5} {'0':>8}"
    up_ev_s=f"{up_ev:+.4f}" if up_ev else "   N/A"
    dn_ev_s=f"{dn_ev:+.4f}" if dn_ev else "   N/A"
    print(f"  {gate:<22} {up_s}  {dn_s}  {up_ev_s:>8} {dn_ev_s:>8}")

# ── SECTION 5: IID check ──────────────────────────────────────────────
sep("5. IID CHECK — Autocorrelation of per-market outcomes")
print(f"  |ac| > 0.20 → use p_perm not p_binom")
for gate in gate_names:
    pm = gate_market_stats.get(gate, [])
    ac = autocorr(pm)
    if ac is None: continue
    flag = "⚠️  use perm p" if abs(ac)>0.20 else "✅ IID ok"
    print(f"  {gate:<22} n_mkts={len(pm):>4}  ac={ac:+.3f}  {flag}")

# ── SECTION 6: Summary ────────────────────────────────────────────────
sep("6. FINAL RANKED SUMMARY")
ranked = sorted(all_results.items(),
                key=lambda x: (0 if x[1] and x[1]['bonf_pass'] else
                               1 if x[1] and x[1]['pb']<ALPHA_RAW else 2,
                               -(x[1]['evb'] if x[1] else -99)))
print(f"  {'Gate':<22} {'EV/blk':>8} {'p_binom':>9} {'p_perm':>8}"
      f" {'Bonf':>5} {'OOS_EV':>8} {'Verdict'}")
print(f"  {'-'*85}")
for gate, res in ranked:
    if res is None: continue
    oos_ev = ev_block(res['wr_o']) if res['wr_o'] is not None else None
    oos_s  = f"{oos_ev:+.4f}" if oos_ev is not None else "—"
    bonf_s = "✅" if res['bonf_pass'] else ("⚠️" if res['pb']<ALPHA_RAW else "✗")
    print(f"  {gate:<22} {res['evb']:>+8.4f} {res['pb']:>9.5f} {res['pp']:>8.4f}"
          f" {bonf_s:>5} {oos_s:>8}  {res['verdict']}")

sep("7. METHODOLOGY")
print(f"""
  Gate trigger reconstruction (no future bias):
    - Uses skip_counterfactuals[{TRIGGER_CKPT}] prices (market state at cd=200)
    - UP_ask = winner_ask if winner=UP, else 1 - winner_ask
    - DN_ask = winner_ask if winner=DN, else 1 - winner_ask
    - Crowd signal from crowd_side / crowd_conv in same snapshot
    - Gate trigger evaluated BEFORE most fires (fires at cd < 200)

  Block counting:
    - Fire is "blocked" if: gate triggered AND fire_side == gate.blocks_side
    - block_won  = blocked fire would have WON  (wrong block)
    - block_lost = blocked fire would have LOST (correct block)

  Multiple testing: Bonferroni α = {ALPHA_RAW}/{N_TESTS} = {ALPHA_BONF:.5f}
  Walk-forward split: {IS_SPLIT*100:.0f}% IS / {(1-IS_SPLIT)*100:.0f}% OOS (chronological)
  Permutation test: {N_PERMUTATIONS:,} permutations under H0
""")
sep("END")

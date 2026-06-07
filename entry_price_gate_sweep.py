"""
entry_price_gate_sweep.py — Real-time entry price filter sweep.

Tests: block UP fire if entry_price (up_ask at fire time) < threshold.
Uses entry_price field already in every fire record — no tick data needed.
Gate-aware: only FREE fires (pass all existing gates).
Full stat battery: Bonferroni, OOS, Bayesian, Kelly, permutation.
"""
import json, os, numpy as np
from scipy import stats as scipy_stats
from scipy.stats import beta as beta_dist

RECAP_PATH = os.path.expanduser('~/polymarket-bot/market_recap_history.jsonl')
IS_SPLIT   = 0.60
N_PERM     = 5000

print("Loading...", flush=True)
recap = {}
with open(RECAP_PATH) as f:
    for line in f:
        try:
            d = json.loads(line)
            if d.get('slug') and d.get('fires') and d.get('winner') in ('UP','DN'):
                recap[d['slug']] = d
        except: pass

common   = sorted(recap.keys())
split_i  = int(len(common) * IS_SPLIT)
OOS_slugs = set(common[split_i:])
print(f"  {len(common)} markets  IS={split_i}  OOS={len(common)-split_i}")

def free_up(fires):
    return [f for f in fires
            if f.get('side') == 'UP'
            and not f.get('pre_gate_held')
            and not f.get('opp_gate_held')
            and f.get('entry_price') is not None]

# Baseline
all_free_up = [(f, recap[s]['winner'], s in OOS_slugs)
               for s in common for f in free_up(recap[s]['fires'])]
base_n    = len(all_free_up)
base_wins = sum(1 for f,w,_ in all_free_up if f.get('hypo_pnl',0) > 0)
base_prec = base_wins / base_n
print(f"  Baseline free UP fires: {base_wins}/{base_n} = {base_prec*100:.1f}%  "
      f"avg_pnl={np.mean([f.get('hypo_pnl',0) for f,_,_ in all_free_up]):+.4f}")

# Thresholds to test: entry_price (up_ask) < X at fire time
THRESHOLDS = [0.45, 0.40, 0.35, 0.30, 0.25, 0.20]
N_TESTS    = len(THRESHOLDS)
ALPHA_BONF = 0.05 / N_TESTS
print(f"  {N_TESTS} thresholds  Bonferroni α={ALPHA_BONF:.5f}\n")

results = []
for thresh in THRESHOLDS:
    blocked = []   # fires where entry_price < thresh
    for s in common:
        winner = recap[s]['winner']
        is_oos = s in OOS_slugs
        for f in free_up(recap[s]['fires']):
            ep = f.get('entry_price', 1.0)
            if ep < thresh:
                blocked.append({
                    'hypo':   f.get('hypo_pnl', 0),
                    'won':    f.get('hypo_pnl', 0) > 0,
                    'winner': winner,
                    'cd':     f.get('cd', 0),
                    'ep':     ep,
                    'slug':   s,
                    'is_oos': is_oos,
                })

    n  = len(blocked)
    if n < 5: continue

    # correct = fire in DN win market (UP loses → blocking saves)
    # wrong   = fire in UP win market (UP wins → blocking costs)
    correct = [b for b in blocked if b['winner'] == 'DN']
    wrong   = [b for b in blocked if b['winner'] == 'UP']

    saves  = -sum(b['hypo'] for b in correct)   # losses prevented
    costs  =  sum(b['hypo'] for b in wrong if b['hypo'] > 0)  # wins missed
    net    = saves - costs

    k_wins = sum(1 for b in blocked if b['won'])
    p_binom = scipy_stats.binomtest(k_wins, n, base_prec, alternative='less').pvalue
    p_bonf  = min(p_binom * N_TESTS, 1.0)

    # OOS
    oos_c  = [b for b in correct if b['is_oos']]
    oos_w  = [b for b in wrong   if b['is_oos']]
    oos_n  = len(oos_c) + len(oos_w)
    oos_sav= -sum(b['hypo'] for b in oos_c)
    oos_cst=  sum(b['hypo'] for b in oos_w if b['hypo'] > 0)
    oos_net= oos_sav - oos_cst
    oos_ev = oos_net / oos_n if oos_n else 0

    # Market-level counts
    mkts_dn = len({b['slug'] for b in correct})
    mkts_up = len({b['slug'] for b in wrong})

    # Bayesian
    a_p=k_wins+1; b_p=n-k_wins+1
    p_bay  = beta_dist.cdf(base_prec, a_p, b_p)
    bf     = 1.0 / beta_dist.pdf(base_prec, a_p, b_p)
    ci_lo,ci_hi = beta_dist.ppf([0.025, 0.975], a_p, b_p)

    # OOS binomial
    oos_k = sum(1 for b in oos_c+oos_w if b['won'])
    oos_p_binom = scipy_stats.binomtest(oos_k, oos_n, base_prec,
                                         alternative='less').pvalue if oos_n else 1.0

    # Kelly (from blocked fires)
    hypos = [b['hypo'] for b in blocked]
    losses_k = [abs(h) for h in hypos if h < 0]
    wins_k   = [abs(h) for h in hypos if h > 0]
    f_star = None
    if losses_k and wins_k:
        p_l = 1 - k_wins/n; q_w = k_wins/n
        b_k = np.mean(losses_k) / np.mean(wins_k)
        f_star = p_l - q_w/b_k

    # Permutation test (IS only)
    is_hypos = [b['hypo'] for b in blocked if not b['is_oos']]
    p_perm = None
    if len(is_hypos) >= 5:
        obs_ev = -np.mean(is_hypos)   # negative mean = saves money
        np.random.seed(42)
        perm = [-np.mean(np.random.choice(is_hypos, len(is_hypos), replace=True))
                for _ in range(N_PERM)]
        p_perm = np.mean(np.array(perm) >= obs_ev)

    # CD distribution of blocked fires
    cds = [b['cd'] for b in blocked]

    results.append({
        'thresh': thresh, 'n': n,
        'n_correct': len(correct), 'n_wrong': len(wrong),
        'mkts_dn': mkts_dn, 'mkts_up': mkts_up,
        'saves': saves, 'costs': costs, 'net': net,
        'k_wins': k_wins, 'win_rate': k_wins/n,
        'p_binom': p_binom, 'p_bonf': p_bonf,
        'p_bay': p_bay, 'bf': bf, 'ci': (ci_lo, ci_hi),
        'oos_n': oos_n, 'oos_nc': len(oos_c), 'oos_nw': len(oos_w),
        'oos_ev': oos_ev, 'oos_net': oos_net, 'oos_p': oos_p_binom,
        'kelly': f_star, 'p_perm': p_perm,
        'cd_p10': np.percentile(cds,10), 'cd_p50': np.percentile(cds,50),
        'cd_p90': np.percentile(cds,90),
        'blocked_list': blocked,
    })

# ── SUMMARY TABLE ─────────────────────────────────────────────────────────
print(f"{'═'*115}")
print("ENTRY PRICE GATE: block UP fire if up_ask < threshold AT FIRE TIME")
print(f"{'═'*115}")
print(f"  {'thresh':>7} {'n_bl':>6} {'%DN':>5} {'saves':>8} {'costs':>8} {'net':>8} "
      f"{'win%':>6} {'OOS_n':>6} {'OOS_ev':>8} {'p_bonf':>10} {'Kelly':>7} {'perm':>7} sig")
print("  " + "-"*110)
for r in results:
    sig = '✅***' if r['p_bonf'] < ALPHA_BONF else ('⚠️' if r['p_binom'] < 0.05 else '')
    kelly = f"{r['kelly']:.3f}" if r['kelly'] else '-'
    perm  = f"{r['p_perm']:.3f}" if r['p_perm'] is not None else '-'
    oos_dn_pct = f"{r['oos_nc']/r['oos_n']*100:.0f}%DN" if r['oos_n'] else '-'
    print(f"  {r['thresh']:>7.2f} {r['n']:>6} "
          f"{r['n_correct']/r['n']*100:>4.0f}% {r['saves']:>+8.1f} {r['costs']:>+8.1f} "
          f"{r['net']:>+8.1f} {r['win_rate']*100:>5.0f}% "
          f"{r['oos_n']:>4}({oos_dn_pct}) {r['oos_ev']:>+8.2f} "
          f"{r['p_bonf']:>10.5f} {kelly:>7} {perm:>7} {sig}")

# ── DEEP DIVE ─────────────────────────────────────────────────────────────
print(f"\n{'═'*115}")
print("DEEP DIVE — ALL CANDIDATES")
print(f"{'═'*115}")
for r in results:
    sig = '✅ CONFIRMED' if r['p_bonf'] < ALPHA_BONF else '⚠️ marginal'
    print(f"\n  ★ entry_price < {r['thresh']:.2f}  ({sig})")
    print(f"    {r['n']} fires blocked: {r['n_correct']} in DN markets (+${r['saves']:.1f} saved)  "
          f"{r['n_wrong']} in UP markets (-${r['costs']:.1f} cost)")
    print(f"    Markets: {r['mkts_dn']} DN win  {r['mkts_up']} UP win")
    print(f"    Net IS+OOS: {r['net']:+.1f}  Fire win rate: {r['k_wins']}/{r['n']} = {r['win_rate']*100:.1f}%")
    print(f"    Binomial  p={r['p_binom']:.2e}  Bonf p={r['p_bonf']:.5f}")
    print(f"    Bayesian  P(rate<baseline)={r['p_bay']:.6f}  BF={r['bf']:.0f}  "
          f"CI=[{r['ci'][0]*100:.1f}%,{r['ci'][1]*100:.1f}%]")
    print(f"    OOS       n={r['oos_n']}  ev/fire={r['oos_ev']:+.3f}  net={r['oos_net']:+.1f}  "
          f"binomial p={r['oos_p']:.2e}")
    if r['kelly']: print(f"    Kelly f*  {r['kelly']:.4f}  {'✅ block EV+' if r['kelly']>0 else '❌ block EV-'}")
    if r['p_perm'] is not None: print(f"    Permutation p={r['p_perm']:.4f}  {'✅' if r['p_perm']<0.05 else '❌'}")
    print(f"    CD range of blocked fires: p10={r['cd_p10']:.0f}  median={r['cd_p50']:.0f}  p90={r['cd_p90']:.0f}")

    # CD distribution buckets
    cds = [b['cd'] for b in r['blocked_list']]
    for lo,hi in [(290,210),(209,150),(149,100),(99,0)]:
        n_in = sum(1 for c in cds if lo>=c>hi)
        pct  = n_in/len(cds)*100
        bar  = '█'*int(pct/3)
        print(f"      cd {lo:>3}→{hi:>3}: {n_in:>4} ({pct:>4.0f}%)  {bar}")

    # Check specific market
    mkt_1556700 = [b for b in r['blocked_list'] if '1777556700' in b['slug']]
    if mkt_1556700:
        saved = -sum(b['hypo'] for b in mkt_1556700)
        print(f"    Market 1777556700: blocks {len(mkt_1556700)} fires  saved={saved:+.2f}  "
              f"new_gc={-10.88+saved:+.2f}")
    else:
        print(f"    Market 1777556700: NOT triggered")

print()

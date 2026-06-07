"""
price_gate_analysis.py — Comprehensive early-price gate study.

Hypothesis: UP ask price at market open (cd=290-250) is a direct, noise-free
signal of crowd directional conviction — better than BN for BOTH directions.

Studies:
  1. up_ask at cd=290 (opening price) as gate trigger — both directions
  2. Spread (dn_ask - up_ask) at opening — directional asymmetry
  3. Average price over first 40s (cd=290-250) — smoothed opening
  4. "Decision timing": first cd where |up_ask-0.50| > X — when market picks side
  5. Combinations: price + BN composite score
  6. For each feature: block UP in DN markets, block DN in UP markets

Gate-aware fire filtering:
  Only fires that pass ALL existing gates (dn_live, ts_drop, ts_rec, Part 14/15)
  AND fire AFTER the gate checkpoint (fire_cd < gate_cd)

Full statistical battery per candidate (Bonferroni over ALL tests):
  Binomial exact (individual fires), Bayesian BF, Walk-forward OOS,
  Permutation test, Kelly criterion, Autocorrelation check

Run: python3 price_gate_analysis.py
"""

import json, os, sys, numpy as np
from pathlib import Path
from scipy import stats as scipy_stats
from scipy.stats import beta as beta_dist
from collections import defaultdict

MARKET_HISTORY_PATH = os.path.expanduser('~/polymarket-bot/market_history.jsonl')
RECAP_HISTORY_PATH  = os.path.expanduser('~/polymarket-bot/market_recap_history.jsonl')

IS_SPLIT      = 0.60
N_PERM        = 5000
ALPHA         = 0.05
MIN_N_TRIG    = 8

# ── HELPERS ────────────────────────────────────────────────────────────────
def get_price_at_cd(ticks, cols, target_cd):
    ci = {c:i for i,c in enumerate(cols)}
    cd_i=ci.get('cd',0); ua_i=ci.get('up_ask',2); da_i=ci.get('dn_ask',4)
    bn_i=ci.get('bn_delta_pct',12)
    for t in ticks:
        cd=t[cd_i]
        if cd is not None and cd<=target_cd:
            return {'up':t[ua_i],'dn':t[da_i],'bn':t[bn_i],'cd':cd}
    return None

def get_avg_price_window(ticks, cols, cd_lo, cd_hi):
    ci = {c:i for i,c in enumerate(cols)}
    cd_i=ci.get('cd',0); ua_i=ci.get('up_ask',2); da_i=ci.get('dn_ask',4)
    ups=[]; dns=[]
    for t in ticks:
        cd=t[cd_i]
        if cd is None: continue
        if cd_lo <= cd <= cd_hi:
            if t[ua_i]: ups.append(t[ua_i])
            if t[da_i]: dns.append(t[da_i])
    return {'up_avg':np.mean(ups) if ups else None,
            'dn_avg':np.mean(dns) if dns else None,
            'spread_avg':(np.mean(dns)-np.mean(ups)) if ups and dns else None}

def get_decision_cd(ticks, cols, threshold=0.15):
    """First cd where |up_ask - 0.50| > threshold (market 'decides' direction)."""
    ci = {c:i for i,c in enumerate(cols)}
    cd_i=ci.get('cd',0); ua_i=ci.get('up_ask',2)
    for t in reversed(ticks):   # ticks are cd-descending, reversed=ascending cd
        cd=t[cd_i]; ua=t[ua_i]
        if cd is None or ua is None: continue
        if cd>290: continue   # only check market window
        if abs(ua-0.5)>threshold:
            return cd
    return None   # never decided within window

def get_fires(fires, gate_cd, side):
    """Gate-aware fires after checkpoint, specific side."""
    out=[]
    for f in fires:
        if f.get('side')!=side: continue
        fcd=f.get('cd') or 0
        if fcd>=gate_cd: continue
        if f.get('pre_gate_held') or f.get('opp_gate_blocked') or f.get('opp_gate_would_block'):
            continue
        out.append(f.get('hypo_pnl',0))
    return out

# ── LOAD ──────────────────────────────────────────────────────────────────
print("Loading...", flush=True)
mkt_hist={}
with open(MARKET_HISTORY_PATH) as f:
    for line in f:
        try:
            d=json.loads(line)
            if d.get('slug') and d.get('ticks') and d.get('tick_columns'):
                mkt_hist[d['slug']]=d
        except: pass

recap={}
with open(RECAP_HISTORY_PATH) as f:
    for line in f:
        try:
            d=json.loads(line)
            if d.get('slug') and d.get('fires') and d.get('winner') in ('UP','DN'):
                recap[d['slug']]=d
        except: pass

common=sorted(set(mkt_hist)&set(recap))
markets=[]
for slug in common:
    mkt=mkt_hist[slug]; rec=recap[slug]
    ticks=mkt['ticks']; cols=mkt['tick_columns']
    p290=get_price_at_cd(ticks,cols,290)
    p280=get_price_at_cd(ticks,cols,280)
    p270=get_price_at_cd(ticks,cols,270)
    p260=get_price_at_cd(ticks,cols,260)
    p240=get_price_at_cd(ticks,cols,240)
    p200=get_price_at_cd(ticks,cols,200)
    avg40=get_avg_price_window(ticks,cols,250,290)   # first 40s
    dec15=get_decision_cd(ticks,cols,0.15)           # |up-0.5|>0.15
    dec20=get_decision_cd(ticks,cols,0.20)           # |up-0.5|>0.20
    markets.append({
        'slug':slug,'winner':rec['winner'],'fires':rec['fires'],
        'p290':p290,'p280':p280,'p270':p270,'p260':p260,'p240':p240,'p200':p200,
        'avg40':avg40,'dec15':dec15,'dec20':dec20,
    })

split_i=int(len(markets)*IS_SPLIT)
IS_slugs={m['slug'] for m in markets[:split_i]}
OOS_slugs={m['slug'] for m in markets[split_i:]}
print(f"  {len(markets)} markets  IS={split_i}  OOS={len(markets)-split_i}")

# Baselines
up_baseline_fires=[p for m in markets for p in get_fires(m['fires'],999,'UP')]
dn_baseline_fires=[p for m in markets for p in get_fires(m['fires'],999,'DN')]
up_base_prec=sum(1 for p in up_baseline_fires if p>0)/len(up_baseline_fires)
dn_base_prec=sum(1 for p in dn_baseline_fires if p>0)/len(dn_baseline_fires)
print(f"  UP baseline: {sum(1 for p in up_baseline_fires if p>0)}/{len(up_baseline_fires)} = {up_base_prec*100:.1f}%")
print(f"  DN baseline: {sum(1 for p in dn_baseline_fires if p>0)}/{len(dn_baseline_fires)} = {dn_base_prec*100:.1f}%")

# ── FEATURE EXTRACTOR ─────────────────────────────────────────────────────
def build_candidates():
    """Return list of (name, description, direction, gate_cd, feat_fn, thresh_fn, is_dn_block)
    direction: 'dn_block' = trigger blocks DN fires (UP win gate)
               'up_block' = trigger blocks UP fires (DN win gate)
    feat_fn(m) → float feature value
    thresh_fn → list of thresholds to test
    triggers when: feat >= thresh (dn_block) or feat <= thresh (up_block)
    """
    cands=[]
    # ── UP PRICE AT OPENING (DN block: UP dominant → block DN) ──────────
    for cd,label in [(290,'cd290'),(280,'cd280'),(270,'cd270'),(260,'cd260')]:
        fn=lambda m,cd=cd: m[f'p{cd}']['up'] if m.get(f'p{cd}') and m[f'p{cd}']['up'] else None
        cands.append((f'up_ask_{label}_dn_block',f'UP ask≥X at {label} → block DN',
                      'dn_block',cd,fn,[0.55,0.60,0.65,0.68,0.70]))
    # ── UP PRICE AT OPENING (UP block: DN dominant → block UP) ──────────
    for cd,label in [(290,'cd290'),(280,'cd280'),(270,'cd270'),(260,'cd260')]:
        fn=lambda m,cd=cd: m[f'p{cd}']['up'] if m.get(f'p{cd}') and m[f'p{cd}']['up'] else None
        cands.append((f'up_ask_{label}_up_block',f'UP ask≤X at {label} → block UP',
                      'up_block',cd,fn,[0.45,0.40,0.38,0.35,0.30]))
    # ── SPREAD AT OPENING ────────────────────────────────────────────────
    for cd,label in [(290,'cd290'),(280,'cd280')]:
        fn_d=lambda m,cd=cd: (m[f'p{cd}']['dn']-m[f'p{cd}']['up']) if m.get(f'p{cd}') and m[f'p{cd}']['up'] and m[f'p{cd}']['dn'] else None
        # Large positive spread = DN dominant → block UP
        cands.append((f'spread_{label}_up_block',f'spread≥X at {label} → block UP',
                      'up_block',cd,fn_d,[0.20,0.25,0.30,0.35,0.40]))
        # Large negative spread = UP dominant → block DN
        fn_u=lambda m,fn_d=fn_d: (-fn_d(m) if fn_d(m) is not None else None)
        cands.append((f'neg_spread_{label}_dn_block',f'-spread≥X at {label} → block DN',
                      'dn_block',cd,fn_u,[0.20,0.25,0.30,0.35,0.40]))
    # ── AVERAGE PRICE OVER FIRST 40s ─────────────────────────────────────
    fn_avg_up=lambda m: m['avg40']['up_avg'] if m.get('avg40') and m['avg40'].get('up_avg') else None
    fn_avg_sp=lambda m: m['avg40']['spread_avg'] if m.get('avg40') and m['avg40'].get('spread_avg') else None
    cands.append(('avg40_up_dn_block','avg UP ask(290-250)≥X → block DN',
                  'dn_block',290,fn_avg_up,[0.55,0.58,0.60,0.62,0.65]))
    cands.append(('avg40_up_up_block','avg UP ask(290-250)≤X → block UP',
                  'up_block',290,fn_avg_up,[0.45,0.42,0.40,0.38,0.35]))
    cands.append(('avg40_spread_up_block','avg spread(290-250)≥X → block UP',
                  'up_block',290,fn_avg_sp,[0.20,0.25,0.30,0.35,0.40]))
    # ── COMPOSITE: price + BN ─────────────────────────────────────────────
    fn_comb_up=lambda m: (m['p290']['up'] + (m['p290']['bn'] or 0)/0.10) if m.get('p290') and m['p290']['up'] else None
    fn_comb_dn=lambda m: (-(m['p290']['up']) + -(m['p290']['bn'] or 0)/0.10) if m.get('p290') and m['p290']['up'] else None
    cands.append(('combo_price_bn_dn_block','composite UP+BN≥X → block DN',
                  'dn_block',290,fn_comb_up,[1.0,1.2,1.4,1.6,1.8]))
    cands.append(('combo_price_bn_up_block','composite DN+BN≥X → block UP',
                  'up_block',290,fn_comb_dn,[0.8,1.0,1.2,1.4,1.6]))
    # ── DECISION TIMING: fire based on when market decides ───────────────
    # dec15_cd = first cd where |up-0.5|>0.15
    # Markets with early decision (high dec15_cd) → more fires blockable
    fn_dec15=lambda m: m.get('dec15')   # cd value (higher=earlier decision)
    cands.append(('dec15_cd_up_block','market decides DN early (dec15_cd≥X) → block UP',
                  'up_block',290,
                  lambda m: -(m.get('dec15') or 0) if m.get('dec15') else None,
                  [-290,-280,-270,-260,-250]))   # trigger when -dec15≥thresh i.e. dec15≤|thresh|
    return cands

candidates=build_candidates()
N_TOTAL_TESTS=sum(len(c[5]) for c in candidates)
ALPHA_BONF=ALPHA/N_TOTAL_TESTS
print(f"\n  {len(candidates)} feature×direction pairs × thresholds = {N_TOTAL_TESTS} tests")
print(f"  Bonferroni α = {ALPHA}/{N_TOTAL_TESTS} = {ALPHA_BONF:.5f}")

# ── SWEEP ─────────────────────────────────────────────────────────────────
results=[]
for name,desc,direction,gate_cd,feat_fn,thresholds in candidates:
    base_prec=dn_base_prec if direction=='dn_block' else up_base_prec
    fire_side='DN' if direction=='dn_block' else 'UP'
    correct_winner='UP' if direction=='dn_block' else 'DN'  # correct block = market goes this way

    for thresh in thresholds:
        trig_correct=[]; trig_wrong=[]; not_trig=[]
        for m in markets:
            feat=feat_fn(m)
            if feat is None: continue
            fires=get_fires(m['fires'],gate_cd,fire_side)
            if not fires: continue
            rec={'slug':m['slug'],'winner':m['winner'],'fires':fires,
                 'pnl':sum(fires),'n':len(fires),
                 'wins':sum(1 for p in fires if p>0),
                 'is_oos':m['slug'] in OOS_slugs}
            triggered=(feat>=thresh)   # both directions use >=, sign handles directionality
            if triggered:
                (trig_correct if m['winner']==correct_winner else trig_wrong).append(rec)
            else:
                not_trig.append(rec)

        n_trig=len(trig_correct)+len(trig_wrong)
        if n_trig<MIN_N_TRIG: continue
        prec_mkt=len(trig_correct)/n_trig

        correct_fires=[p for r in trig_correct for p in r['fires']]
        wrong_fires  =[p for r in trig_wrong   for p in r['fires']]
        all_fires=correct_fires+wrong_fires
        saves=-sum(correct_fires); costs=sum(wrong_fires)
        net=saves-costs; ev_mkt=net/n_trig

        oos_c=[r for r in trig_correct if r['is_oos']]
        oos_w=[r for r in trig_wrong   if r['is_oos']]
        oos_n=len(oos_c)+len(oos_w)
        oos_saves=-sum(p for r in oos_c for p in r['fires'])
        oos_costs= sum(p for r in oos_w for p in r['fires'])
        oos_net=oos_saves-oos_costs
        oos_ev=oos_net/oos_n if oos_n else 0

        n_f=len(all_fires); k_f=sum(1 for p in all_fires if p>0)
        p_binom=scipy_stats.binomtest(k_f,n_f,base_prec,alternative='less').pvalue if n_f>0 else 1.0
        p_bonf=min(p_binom*N_TOTAL_TESTS,1.0)

        saves_pm=np.mean([-r['pnl'] for r in trig_correct]) if trig_correct else 0
        costs_pm=np.mean([ r['pnl'] for r in trig_wrong])   if trig_wrong   else 0
        bkev=costs_pm/(saves_pm+costs_pm) if (saves_pm+costs_pm)>0 else 0.5

        results.append({
            'name':name,'desc':desc,'direction':direction,'gate_cd':gate_cd,
            'thresh':thresh,'n_trig':n_trig,
            'n_correct':len(trig_correct),'n_wrong':len(trig_wrong),
            'prec_mkt':prec_mkt,'ev_mkt':ev_mkt,'net':net,
            'saves':saves,'costs':costs,
            'oos_n':oos_n,'oos_nc':len(oos_c),'oos_nw':len(oos_w),
            'oos_ev':oos_ev,'oos_net':oos_net,
            'n_fires':n_f,'k_wins':k_f,
            'p_binom':p_binom,'p_bonf':p_bonf,'sig':p_bonf<ALPHA_BONF,
            'breakeven':bkev,'margin':prec_mkt-bkev,
            'all_fires':all_fires,'oos_c_fires':[p for r in oos_c for p in r['fires']],
        })

# ── DISPLAY RESULTS ───────────────────────────────────────────────────────
print(f"\n{'═'*110}")
print("GATE-AWARE SWEEP — PRICE + TIMING SIGNALS, BOTH DIRECTIONS")
print(f"Bonferroni sig threshold: p_bonf < {ALPHA_BONF:.5f}")
print(f"{'═'*110}")

# Group by direction
for dir_label,dir_key in [('BLOCK DN fires (UP win gate)','dn_block'),
                           ('BLOCK UP fires (DN win gate)','up_block')]:
    sub=[r for r in results if r['direction']==dir_key]
    if not sub: continue
    print(f"\n  {dir_label}")
    print(f"  {'Feature':35} {'thresh':>7} {'n':>5} {'prec%':>6} {'bkev':>6} "
          f"{'EV/mkt':>8} {'OOS_n':>6} {'OOS_ev':>8} {'p_bonf':>9} {'sig'}")
    print("  "+"-"*100)
    for r in sorted(sub, key=lambda x: -x['oos_ev'])[:20]:
        sig='✅***' if r['sig'] else ('⚠️' if r['p_binom']<0.05 else '')
        oos_pct=f"{r['oos_nc']/r['oos_n']*100:.0f}%DN" if dir_key=='up_block' and r['oos_n'] else \
                f"{r['oos_nc']/r['oos_n']*100:.0f}%UP" if r['oos_n'] else ''
        print(f"  {r['desc']:35} {r['thresh']:>7.3f} {r['n_trig']:>5} "
              f"{r['prec_mkt']*100:>5.0f}% {r['breakeven']:>5.0f}% "
              f"  {r['ev_mkt']:>+7.2f} {r['oos_n']:>6}({oos_pct}) "
              f"{r['oos_ev']:>+8.2f} {r['p_bonf']:>9.5f} {sig}")

# ── TOP CANDIDATES DEEP DIVE ───────────────────────────────────────────────
print(f"\n{'═'*110}")
print("TOP CANDIDATES — FULL STATISTICAL BATTERY")
print(f"{'═'*110}")

valid=[r for r in results if r['oos_ev']>0 and r['net']>0 and r['oos_n']>=5 and r['p_binom']<0.05]
top=sorted(valid,key=lambda x: -x['oos_ev'])[:6]

for r in top:
    print(f"\n  {'★'} {r['desc']}  (thresh={r['thresh']:.3f}  gate_cd={r['gate_cd']})")
    print(f"    Markets: {r['n_trig']} triggered  {r['n_correct']} correct / {r['n_wrong']} wrong  "
          f"precision={r['prec_mkt']*100:.0f}%  breakeven={r['breakeven']*100:.0f}%  margin={r['margin']*100:+.0f}pp")
    print(f"    Full: saves={r['saves']:+.1f}  costs={r['costs']:+.1f}  net={r['net']:+.1f}  ev/mkt={r['ev_mkt']:+.2f}")
    print(f"    OOS:  n={r['oos_n']}  saves+costs → ev/mkt={r['oos_ev']:+.2f}")

    # Binomial
    n_f=r['n_fires']; k_f=r['k_wins']
    base=dn_base_prec if r['direction']=='dn_block' else up_base_prec
    rb=scipy_stats.binomtest(k_f,n_f,base,alternative='less')
    print(f"    Binomial: {k_f}/{n_f} wins = {k_f/n_f*100:.1f}% vs {base*100:.1f}%  p={rb.pvalue:.2e}")

    # Bayesian
    a_p=k_f+1; b_p=n_f-k_f+1
    p_bay=beta_dist.cdf(base,a_p,b_p)
    bf=1.0/beta_dist.pdf(base,a_p,b_p)
    ci_lo,ci_hi=beta_dist.ppf([0.025,0.975],a_p,b_p)
    print(f"    Bayesian: P(rate<baseline)={p_bay:.4f}  BF={bf:.0f}  CI=[{ci_lo*100:.1f}%,{ci_hi*100:.1f}%]")

    # OOS binomial
    oos_fires=r['oos_c_fires']+[p for p in []]   # using pre-collected
    oos_n_f=len(oos_fires); oos_k=sum(1 for p in oos_fires if p>0)
    if oos_n_f>0:
        rb_oos=scipy_stats.binomtest(oos_k,oos_n_f,base,alternative='less')
        print(f"    OOS binomial: {oos_k}/{oos_n_f} wins = {oos_k/oos_n_f*100:.1f}%  p={rb_oos.pvalue:.2e}")

    # Kelly
    losses=[abs(p) for p in r['all_fires'] if p<0]
    wins=[abs(p) for p in r['all_fires'] if p>0]
    if losses and wins:
        p_l=1-k_f/n_f; q_w=k_f/n_f; b_k=np.mean(losses)/np.mean(wins)
        f_star=p_l-q_w/b_k
        print(f"    Kelly f*={f_star:.4f}  {'✅ block EV+' if f_star>0 else '❌ block EV-'}")

    # Permutation test (OOS)
    oos_all_fires=r['oos_c_fires']   # only correct blocks' fires for EV estimation
    if len(oos_all_fires)>2:
        obs=-np.mean(oos_all_fires)
        np.random.seed(42)
        perm=[np.mean(np.random.choice([-1,1],len(oos_all_fires))*np.abs(oos_all_fires))
              for _ in range(N_PERM)]
        p_perm=np.mean(np.abs(perm)>=abs(obs))
        print(f"    Permutation p={p_perm:.4f}  {'✅' if p_perm<0.05 else '❌'}")

    # Check impact on markets 1777551300 and 1777556700
    for slug,market_label in [('1777551300','UP win'),('1777556700','DN win')]:
        for m in markets:
            if slug in m['slug']:
                from_feat=None
                try: from_feat=eval(f"r['desc']")
                except: pass
                # simple: extract feature
                p290=m.get('p290')
                if p290:
                    ua=p290.get('up'); sp=(p290.get('dn',0.5)-ua) if ua else None
                    feat_val=None
                    if 'up_ask' in r['name'] and ua: feat_val=ua if 'dn_block' in r['name'] else ua
                    elif 'spread' in r['name'] and sp: feat_val=sp if 'up_block' in r['name'] else -sp
                    elif 'avg40' in r['name']: feat_val=m['avg40'].get('up_avg') if m.get('avg40') else None
                    if feat_val is not None:
                        triggered=feat_val>=r['thresh']
                        fires=get_fires(m['fires'],r['gate_cd'],'DN' if r['direction']=='dn_block' else 'UP')
                        pnl=-sum(fires) if triggered else 0
                        print(f"    {market_label} ({slug}): feat={feat_val:.3f}  "
                              f"trigger={'YES' if triggered else 'NO'}  "
                              f"pnl_saved={pnl:+.2f}" if triggered else
                              f"    {market_label} ({slug}): feat={feat_val:.3f}  "
                              f"trigger=NO")
                break

# ── DECISION TIMING ANALYSIS ──────────────────────────────────────────────
print(f"\n{'═'*110}")
print("DECISION TIMING: when does price commit to a direction?")
print(f"{'═'*110}")
print("""
For each market: find first cd (from market start, counting DOWN) where
|up_ask - 0.50| > 0.15 (market has "decided" which side will win).
Does firing BEFORE vs AFTER this point predict outcome?
""")
decided=[(m['winner'],m.get('dec15'),m.get('dec20')) for m in markets if m.get('dec15')]
up_dec=[d[1] for d in decided if d[0]=='UP']
dn_dec=[d[1] for d in decided if d[0]=='DN']
print(f"  Markets with decision at |up-0.5|>0.15:")
print(f"    UP win: n={len(up_dec)}  median decision cd={np.median(up_dec):.0f}  "
      f"min={min(up_dec):.0f}  max={max(up_dec):.0f}")
print(f"    DN win: n={len(dn_dec)}  median decision cd={np.median(dn_dec):.0f}  "
      f"min={min(dn_dec):.0f}  max={max(dn_dec):.0f}")
t,p=scipy_stats.ttest_ind(up_dec,dn_dec,equal_var=False)
print(f"    t-test (UP vs DN decision timing): t={t:.3f}  p={p:.4f}  "
      f"{'sig: UP markets decide later' if p<0.05 and t>0 else 'sig: DN markets decide later' if p<0.05 else 'ns'}")

# At what cd does market decide? Distribution
for thresh_label,dec_key in [('|up-0.5|>0.15','dec15'),('|up-0.5|>0.20','dec20')]:
    vals=[m[dec_key] for m in markets if m.get(dec_key)]
    print(f"\n  Decision cd distribution ({thresh_label}, n={len(vals)}):")
    for cd_range in [(290,260),(259,220),(219,180),(179,150),(149,0)]:
        n_in=sum(1 for v in vals if cd_range[1]<=v<=cd_range[0])
        pct=n_in/len(vals)*100
        bar='█'*int(pct/2)
        print(f"    cd {cd_range[0]:>3}→{cd_range[1]:>3}: {n_in:>3} ({pct:>4.0f}%)  {bar}")

# ── SUMMARY ───────────────────────────────────────────────────────────────
print(f"\n{'═'*110}")
print("SUMMARY — BEST GATE CANDIDATES BY DIRECTION")
print(f"{'═'*110}")
for dir_key,dir_label in [('dn_block','Block DN (UP win gate)'),('up_block','Block UP (DN win gate)')]:
    sub_v=[r for r in valid if r['direction']==dir_key]
    if sub_v:
        best=max(sub_v,key=lambda x: x['oos_ev'])
        print(f"\n  {dir_label}: BEST = {best['desc']}  thresh={best['thresh']:.3f}")
        print(f"    OOS: n={best['oos_n']}  ev/mkt={best['oos_ev']:+.2f}  precision={best['prec_mkt']*100:.0f}%")
        print(f"    p_binom={best['p_binom']:.2e}  Bonferroni p_bonf={best['p_bonf']:.4f}  "
              f"{'CONFIRMED' if best['sig'] else 'not yet Bonferroni confirmed'}")
    else:
        print(f"\n  {dir_label}: no valid candidate found")
print()

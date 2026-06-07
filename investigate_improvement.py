#!/usr/bin/env python3
"""
investigate_improvement.py — verify improvement is REAL (recent) not STALE (old data).

Distinguishes between:
  ✅ "Bot is genuinely improving" — recent fires winning more than old
  ❌ "Stalled on old data" — early markets winning, recent markets losing

Checks:
  1. WR over time (split by hour, day) to see if recent > older
  2. Auto-improvements happening per market (paused hours, SPRT updates)
  3. State file freshness (gate_induction_state, paused_hours)
  4. Most recent N markets' performance
  5. FLIP detection: are recent FLIPs winning (proves training data fresh)
  6. Bayesian alpha/beta — are recent fires moving posteriors?

Run: python3 investigate_improvement.py
"""
import os, json, time, pickle, glob
from collections import defaultdict
from datetime import datetime

NOW = time.time()


def safe_load_jsonl(path, max_bytes=20_000_000):
    if not os.path.exists(path): return []
    records = []
    with open(path, 'rb') as f:
        f.seek(0, 2); size = f.tell()
        f.seek(max(0, size - max_bytes))
        data = f.read().decode('utf-8', errors='ignore')
    for line in data.split('\n'):
        line = line.strip()
        if not line: continue
        try: records.append(json.loads(line))
        except: continue
    return records


def fmt_ago(seconds):
    if seconds < 60: return f"{seconds:.0f}s ago"
    if seconds < 3600: return f"{seconds/60:.1f}m ago"
    if seconds < 86400: return f"{seconds/3600:.1f}h ago"
    return f"{seconds/86400:.1f}d ago"


print("\n" + "═"*78)
print("  IS THE BOT IMPROVING FROM RECENT MARKETS — OR STALLED ON OLD DATA?")
print("═"*78)


# ── 1. Time-bucketed win rate ────────────────────────────────────────────
print("\n[1] WIN RATE OVER TIME — is it climbing or flat?")
fires = safe_load_jsonl('parameter_history.jsonl')
if not fires:
    print("  ❌ No parameter_history.jsonl data")
else:
    print(f"  Total fires loaded: {len(fires)}")
    
    # parameter_history.jsonl uses 'won' (boolean) — not 'hypo_pnl'
    # hypo_pnl is in market_recap_history.jsonl per fire entry instead
    # Bucket by hour-of-data
    bucket_size = 3600  # 1 hour buckets
    buckets = defaultdict(lambda: {"n": 0, "wins": 0, "base_n": 0, "base_w": 0,
                                    "flip_n": 0, "flip_w": 0})
    for f in fires:
        ts = f.get('ts', 0)
        if not isinstance(ts, (int, float)) or not ts:
            continue
        bucket_key = int(float(ts) // bucket_size) * bucket_size
        won = bool(f.get('won', False))
        strat = f.get('strategy', '')
        is_flip = strat.endswith('_FLIP')
        buckets[bucket_key]["n"] += 1
        if won: buckets[bucket_key]["wins"] += 1
        if is_flip:
            buckets[bucket_key]["flip_n"] += 1
            if won: buckets[bucket_key]["flip_w"] += 1
        else:
            buckets[bucket_key]["base_n"] += 1
            if won: buckets[bucket_key]["base_w"] += 1
    
    sorted_buckets = sorted(buckets.items())
    if len(sorted_buckets) > 24:
        sorted_buckets = sorted_buckets[-24:]
    
    print(f"  Last {len(sorted_buckets)} hour-buckets — split BASE vs FLIP:")
    print(f"  {'TIME':<14} {'TOTAL':<6} {'OVERALL':<8} {'BASE_WR':<10} {'FLIP_WR':<10} {'TREND'}")
    
    prev_wr = None
    for bucket_key, b in sorted_buckets:
        if b["n"] < 3: continue
        wr = b["wins"] / b["n"]
        base_wr = (b["base_w"] / b["base_n"]) if b["base_n"] else 0
        flip_wr = (b["flip_w"] / b["flip_n"]) if b["flip_n"] else 0
        when = datetime.fromtimestamp(bucket_key).strftime('%m-%d %H:%M')
        trend = ""
        if prev_wr is not None:
            if wr > prev_wr + 0.05: trend = "📈"
            elif wr < prev_wr - 0.05: trend = "📉"
            else: trend = "➡️"
        print(f"  {when:<14} {b['n']:<6} {wr*100:>5.1f}%   "
              f"{base_wr*100:>5.1f}% (n={b['base_n']:<3d})  "
              f"{flip_wr*100:>5.1f}% (n={b['flip_n']:<3d})  {trend}")
        prev_wr = wr
    
    # Compare first half vs second half of available data
    if len(sorted_buckets) >= 4:
        half = len(sorted_buckets) // 2
        old_n = sum(b["n"] for _, b in sorted_buckets[:half])
        old_w = sum(b["wins"] for _, b in sorted_buckets[:half])
        old_bn = sum(b["base_n"] for _, b in sorted_buckets[:half])
        old_bw = sum(b["base_w"] for _, b in sorted_buckets[:half])
        old_fn = sum(b["flip_n"] for _, b in sorted_buckets[:half])
        old_fw = sum(b["flip_w"] for _, b in sorted_buckets[:half])
        new_n = sum(b["n"] for _, b in sorted_buckets[half:])
        new_w = sum(b["wins"] for _, b in sorted_buckets[half:])
        new_bn = sum(b["base_n"] for _, b in sorted_buckets[half:])
        new_bw = sum(b["base_w"] for _, b in sorted_buckets[half:])
        new_fn = sum(b["flip_n"] for _, b in sorted_buckets[half:])
        new_fw = sum(b["flip_w"] for _, b in sorted_buckets[half:])
        
        old_wr = old_w / old_n if old_n else 0
        new_wr = new_w / new_n if new_n else 0
        old_base_wr = old_bw / old_bn if old_bn else 0
        new_base_wr = new_bw / new_bn if new_bn else 0
        old_flip_wr = old_fw / old_fn if old_fn else 0
        new_flip_wr = new_fw / new_fn if new_fn else 0
        
        print()
        print(f"  Older half: n={old_n:<5} OVERALL={old_wr*100:.1f}%  "
              f"BASE={old_base_wr*100:.1f}% (n={old_bn})  FLIP={old_flip_wr*100:.1f}% (n={old_fn})")
        print(f"  Newer half: n={new_n:<5} OVERALL={new_wr*100:.1f}%  "
              f"BASE={new_base_wr*100:.1f}% (n={new_bn})  FLIP={new_flip_wr*100:.1f}% (n={new_fn})")
        delta = (new_wr - old_wr) * 100
        base_delta = (new_base_wr - old_base_wr) * 100
        flip_delta = (new_flip_wr - old_flip_wr) * 100
        if delta > 5: print(f"  ✅ OVERALL IMPROVING: WR up {delta:+.1f}pp")
        elif delta < -5: print(f"  ❌ OVERALL DEGRADING: WR down {delta:+.1f}pp")
        else: print(f"  ➡️  OVERALL STABLE: WR change {delta:+.1f}pp")
        print(f"      BASE strategies: {base_delta:+.1f}pp change")
        print(f"      FLIP strategies: {flip_delta:+.1f}pp change")


# ── 2. State file freshness ──────────────────────────────────────────────
print("\n[2] STATE FILE FRESHNESS — are auto-improvements running per market?")
state_files = {
    'gate_induction_state.pkl': 'Per-strategy SPRT/Bayesian state',
    'auto_paused_hours.json': 'Auto-paused hours by ET hour',
    'auto_q_variants.json': 'Genesis-found Q-variants',
    'auto_portfolio_weights.json': 'Sharpe-weighted portfolio',
    'flip_shadow_log.jsonl': 'FLIP shadow fires',
    'parameter_history.jsonl': 'Base + FLIP fire history',
    'auto_improvements.log': 'Engine subprocess log',
    'counterfactual_log.jsonl': 'Counterfactual analysis',
    'market_history.jsonl': 'Market completion record',
}
for path, desc in state_files.items():
    if os.path.exists(path):
        age_sec = NOW - os.path.getmtime(path)
        size_kb = os.path.getsize(path) / 1024
        if age_sec < 600: status = "✅"
        elif age_sec < 1800: status = "🟡"
        else: status = "❌"
        print(f"  {status} {path:<32s} {fmt_ago(age_sec):<14s} ({size_kb:.0f} KB)  — {desc}")
    else:
        print(f"  — {path:<32s} not yet created  — {desc}")


# ── 3. Most recent N market performance ──────────────────────────────────
print("\n[3] LAST 10 MARKETS — hypo PnL trend (from market_recap_history.jsonl)")
mrh = safe_load_jsonl('market_recap_history.jsonl')
if not mrh:
    print("  ❌ market_recap_history.jsonl not found")
else:
    recent = mrh[-15:] if len(mrh) >= 15 else mrh
    print(f"  {'MARKET (winner)':<40s} {'FIRES':<6} {'HYPO PnL':<12} {'WR':<8} TREND")
    pnls = []
    for m in recent:
        slug = m.get('slug', '?')[:38]
        winner = m.get('winner', '?')
        fires_l = m.get('fires', [])
        n = len(fires_l)
        # FREE only fires for hypo_pnl (matches recap math)
        free_fires = [f for f in fires_l 
                      if not (f.get('extras', {}).get('opp_gate_held') or 
                              f.get('extras', {}).get('bn_floor_blocked') or
                              f.get('extras', {}).get('bn_side_vetoed') or
                              f.get('extras', {}).get('pre_gate_held'))]
        pnl = sum((f.get('hypo_pnl', 0) or 0) for f in free_fires)
        wins = sum(1 for f in free_fires if (f.get('hypo_pnl', 0) or 0) > 0)
        n_free = len(free_fires)
        wr = wins / n_free if n_free else 0
        pnls.append(pnl)
        # Trend arrow
        if len(pnls) >= 2:
            trend = "📈" if pnls[-1] > pnls[-2] else ("📉" if pnls[-1] < pnls[-2] else "➡️")
        else: trend = ""
        print(f"  {slug:<40s} {n_free:<6} ${pnl:>+8.2f}    {wr*100:>4.0f}%   {trend}")
    
    if len(pnls) >= 4:
        half = len(pnls) // 2
        old_avg = sum(pnls[:half]) / half
        new_avg = sum(pnls[half:]) / (len(pnls) - half)
        print()
        print(f"  Older {half} markets avg PnL: ${old_avg:+.2f}/market")
        print(f"  Newer {len(pnls)-half} markets avg PnL: ${new_avg:+.2f}/market")
        if new_avg > old_avg + 0.5: print(f"  ✅ PnL TREND IMPROVING by ${new_avg-old_avg:+.2f}")
        elif new_avg < old_avg - 0.5: print(f"  ❌ PnL TREND DEGRADING by ${new_avg-old_avg:+.2f}")
        else: print(f"  ➡️  PnL TREND STABLE")


# ── 4. FLIP edge over time (proves training is fresh) ────────────────────
print("\n[4] FLIP EDGE OVER TIME — is the bot still finding edges?")
flip_log = safe_load_jsonl('flip_shadow_log.jsonl')
if flip_log:
    # Bucket by day
    by_day = defaultdict(lambda: {"n": 0, "flip_wins": 0, "base_wins": 0})
    for f in flip_log:
        ts = f.get('ts', 0)
        if not ts: continue
        day = int(ts // 86400) * 86400
        by_day[day]["n"] += 1
        if f.get('flip_won'): by_day[day]["flip_wins"] += 1
        if f.get('actual_won'): by_day[day]["base_wins"] += 1
    
    sorted_days = sorted(by_day.items())[-7:]  # last 7 days
    print(f"  Last {len(sorted_days)} days of FLIP data:")
    print(f"  {'DAY':<14s} {'FIRES':<8} {'BASE_WR':<10} {'FLIP_WR':<10} {'EDGE'}")
    for day, b in sorted_days:
        if b["n"] < 5: continue
        flip_wr = b["flip_wins"] / b["n"]
        base_wr = b["base_wins"] / b["n"]
        edge = (flip_wr - base_wr) * 100
        when = datetime.fromtimestamp(day).strftime('%Y-%m-%d')
        print(f"  {when:<14s} {b['n']:<8} {base_wr*100:>5.1f}%    {flip_wr*100:>5.1f}%    {edge:>+5.1f}pp")


# ── 5. Are Bayesian posteriors moving? (proves real-time learning) ───────
print("\n[5] BAYESIAN POSTERIOR ACTIVITY — recent fires moving the dial?")
if os.path.exists('gate_induction_state.pkl'):
    try:
        with open('gate_induction_state.pkl', 'rb') as f:
            state = pickle.load(f)
        recent_activity = []
        for k, s in state.items():
            if not isinstance(k, tuple): continue
            fh = s.get('fire_history', [])
            if not fh: continue
            try:
                # fire_history items may be tuples (ts, won, ...) or dicts
                last = fh[-1]
                if isinstance(last, (tuple, list)):
                    last_fire_ts = float(last[0]) if last else 0
                elif isinstance(last, dict):
                    last_fire_ts = float(last.get('ts', 0))
                else:
                    last_fire_ts = 0
                if not last_fire_ts: continue
                age_sec = NOW - last_fire_ts
                a = s.get('bayes', {}).get('alpha', 1)
                b = s.get('bayes', {}).get('beta', 1)
                wr = a / (a+b) if (a+b) > 0 else 0
                recent_activity.append((f"{k[0]}|{k[1]}", wr, age_sec, len(fh)))
            except Exception: continue
        
        recent_activity.sort(key=lambda x: x[2])
        if recent_activity:
            print(f"  Top 15 most-recently-fired strategies:")
            print(f"  {'STRATEGY':<42s} {'TOTAL':<7} {'WR':<6} {'AGE':<14}")
            for name, wr, age, n_fh in recent_activity[:15]:
                print(f"  {name:<42s} {n_fh:<7} {wr*100:>4.0f}%  {fmt_ago(age):<14s}")
        else:
            print("  No fire_history timestamps available")
    except Exception as e:
        print(f"  Error reading state: {e}")


# ── 6. SPRT verdict changes (sliding window working?) ────────────────────
print("\n[6] SPRT VERDICT CHANGES — are bad strategies graduating to recovery?")
if os.path.exists('gate_induction_state.pkl'):
    try:
        with open('gate_induction_state.pkl', 'rb') as f:
            state = pickle.load(f)
        verdicts = defaultdict(int)
        recovering = []  # rejected but recent fires winning
        for k, s in state.items():
            if not isinstance(k, tuple): continue
            sprt = s.get('sprt', {})
            v = sprt.get('verdict', 'pending')
            verdicts[v] += 1
            
            if v == 'reject':
                # Check if recent window_fires are improving
                wf = sprt.get('window_fires', [])
                if len(wf) >= 10:
                    recent_5 = sum(wf[-5:]) / 5
                    older_5 = sum(wf[-10:-5]) / 5
                    if recent_5 > older_5 + 0.2:  # 20pp improvement
                        recovering.append((f"{k[0]}|{k[1]}", recent_5, older_5))
        
        print(f"  Current verdict distribution:")
        for v, n in verdicts.items():
            print(f"    {v}: {n}")
        
        if recovering:
            print(f"\n  🌱 REJECTED strategies showing recent recovery (last 5 fires > older 5):")
            for name, recent, older in recovering[:10]:
                print(f"    {name:<42s} older WR={older*100:.0f}% → recent WR={recent*100:.0f}%")
        else:
            print(f"\n  No rejected strategies in recovery yet")
    except Exception as e:
        print(f"  Error: {e}")


# ── 7. Auto-paused hours stability ───────────────────────────────────────
print("\n[7] AUTO-PAUSED HOURS — bot's confidence in time-of-day filters")
if os.path.exists('auto_paused_hours.json'):
    with open('auto_paused_hours.json') as f:
        ph = json.load(f)
    paused = ph.get('paused_hours_et', [])
    detail = ph.get('detail', {}).get('paused', [])
    overall = ph.get('overall_wr', 0)
    updated = ph.get('updated_at', '')
    print(f"  Last update: {updated}")
    print(f"  Overall WR: {overall*100:.1f}%")
    print(f"  Paused hours (ET): {paused}")
    if detail:
        print(f"  Detail:")
        for d in detail:
            hr = d.get('hour')
            wr = d.get('wr', 0) * 100
            n = d.get('n', 0)
            p = d.get('p', 1)
            print(f"    Hour {hr:02d}:00 ET  WR={wr:.1f}%  n={n}  p={p:.4f}")


print("\n" + "═"*78)
print("  HOW TO READ THIS")
print("═"*78)
print("""
  ✅ HEALTHY SIGNS:
    - State files updated <10 min ago (auto-improvements running per market)
    - Newer-half WR > older-half WR (genuine learning)
    - Most recent strategies fired <1 hour ago (system actively trading)
    - SPRT verdicts changing over time (sliding window adapting)
    - FLIP edges remaining strong day-over-day (real signal)
  
  ❌ STALLED SIGNS:
    - State files haven't updated in 1+ hour (engine subprocess crashed)
    - Newer-half WR same/worse than older (no learning)
    - All "rejected" strategies stuck (sliding window not working)
    - FLIP edges shrinking over time (bot losing edge)
""")

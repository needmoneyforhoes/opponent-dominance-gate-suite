#!/usr/bin/env python3
"""
PER-GATE INDEPENDENT CONTRIBUTION ATTRIBUTION

Computes each gate's marginal contribution to PnL using leave-one-out
methodology on historical fires.

For each gate G:
  Contribution_G = hypo_pnl(if_G_off) - hypo_pnl(if_G_on)
  
Plus per-strategy breakdown:
  Per-(strategy, gate): how much does this gate help/hurt this specific strategy?

This tells you which gates are pulling weight per strategy.

USAGE:
  python3 gate_attribution.py
"""
import json, math, os
from collections import defaultdict

os.chdir('/home/polybot/polymarket-bot/data')

print("Loading market_recap_history.jsonl...")
fires = []
with open('market_recap_history.jsonl') as f:
    for line in f:
        try: r = json.loads(line)
        except: continue
        for fire in r.get('fires', []):
            cd = fire.get('cd', 0)
            if cd is None or cd < 15: continue
            fires.append({
                'strategy': fire.get('strategy'),
                'side': fire.get('side'),
                'hypo': fire.get('hypo_pnl', 0),
                'won': fire.get('hypo_pnl', 0) > 0,
                'gates': {
                    'opp_gate':       bool(fire.get('opp_gate_held') or fire.get('pre_gate_held')),
                    'dedup':          bool(fire.get('dedup_excluded')),
                    'pricecap':       bool(fire.get('pricecap_excluded')),
                    'modelveto':      bool(fire.get('model_vetoed')),
                    'flipgate':       bool(fire.get('flip_gate_blocked')),
                    'bnveto':         bool(fire.get('bn_vetoed')),
                    'bnfloor':        bool(fire.get('bn_floor_blocked')),
                },
                'is_free': not any([
                    fire.get('opp_gate_held'), fire.get('pre_gate_held'),
                    fire.get('dedup_excluded'), fire.get('pricecap_excluded'),
                    fire.get('model_vetoed'), fire.get('flip_gate_blocked'),
                    fire.get('bn_vetoed'), fire.get('bn_floor_blocked'),
                ]),
            })

print(f"  Total fires: {len(fires)}")
n_free = sum(1 for f in fires if f['is_free'])
print(f"  Gate-clean (FREE): {n_free}")

# Per-gate analysis: what would happen if THIS gate were OFF?
# = include all fires that PASSED other gates but were blocked by THIS gate
print(f"\n{'='*80}")
print(f"PER-GATE INDEPENDENT CONTRIBUTION")
print(f"{'='*80}\n")
print(f"For each gate, compute: if THIS gate were OFF (others still on), what's the PnL impact?")
print()
print(f"{'gate':<15s} {'blocked_n':>10s} {'block_pnl':>10s} {'block_WR':>9s} {'avg_pnl':>9s} {'verdict':>20s}")
print('-'*80)

gate_results = {}
for gate_name in ['opp_gate', 'dedup', 'pricecap', 'modelveto', 'flipgate', 'bnveto', 'bnfloor']:
    # Fires that this gate (and ONLY this gate among the listed) blocked
    # AKA: would-have-been-FREE if this gate were off
    blocked_only_by_this = [f for f in fires if f['gates'][gate_name] and not any(
        f['gates'][g] for g in f['gates'] if g != gate_name
    )]
    n = len(blocked_only_by_this)
    if n == 0:
        print(f"{gate_name:<15s} {'0':>10s}  -        -          -                 -")
        continue
    
    pnl_if_off = sum(f['hypo'] for f in blocked_only_by_this)
    n_won = sum(1 for f in blocked_only_by_this if f['won'])
    wr = n_won/n
    avg = pnl_if_off/n
    
    # If pnl_if_off is positive, gate is COSTING us money (blocked winners)
    # If negative, gate is SAVING us money (blocked losers)
    if pnl_if_off > 0:
        verdict = "❌ blocks winners"
    elif pnl_if_off < -10:
        verdict = "✅ saves big losses"
    elif pnl_if_off < 0:
        verdict = "✅ saves losses"
    else:
        verdict = "neutral"
    
    gate_results[gate_name] = {
        'blocked_n': n, 'pnl_if_off': pnl_if_off,
        'wr_if_off': wr, 'avg_pnl_if_off': avg,
    }
    
    print(f"{gate_name:<15s}  {n:>9d}  ${pnl_if_off:>+7.2f}   {wr*100:>4.0f}%   ${avg:>+5.3f}   {verdict:>20s}")

print()
print("Interpretation:")
print("  block_pnl > 0 → gate is BLOCKING winners (costing money)")
print("  block_pnl < 0 → gate is BLOCKING losers (saving money)")

# Per-strategy × per-gate breakdown
print(f"\n{'='*100}")
print(f"PER-STRATEGY × PER-GATE BREAKDOWN")
print(f"{'='*100}\n")

strats = sorted({f['strategy'] for f in fires if f['strategy']})
gates_to_show = ['opp_gate', 'dedup', 'pricecap', 'modelveto', 'flipgate']

# Header
hdr = f"{'strategy':<32s}"
for g in gates_to_show:
    hdr += f" {g:>10s}"
hdr += f" {'free_pnl':>9s} {'free_wr':>8s}"
print(hdr)
print('-'*110)

for s in strats:
    s_fires = [f for f in fires if f['strategy'] == s]
    if len(s_fires) < 30: continue   # skip noise-level strategies
    
    row = f"{s:<32s}"
    for g in gates_to_show:
        blocked = [f for f in s_fires if f['gates'][g] and not any(
            f['gates'][gg] for gg in f['gates'] if gg != g
        )]
        if blocked:
            pnl = sum(f['hypo'] for f in blocked)
            row += f"  ${pnl:>+6.2f}"
        else:
            row += f"   {'-':>7s}"
    
    free = [f for f in s_fires if f['is_free']]
    if free:
        free_pnl = sum(f['hypo'] for f in free)
        n_won = sum(1 for f in free if f['won'])
        row += f"  ${free_pnl:>+6.2f}  {n_won/len(free)*100:>5.0f}%"
    else:
        row += f"      -          -"
    print(row)

print(f"\n=== ACTIONABLE INSIGHTS ===")
# Identify gates that are net-NEGATIVE per strategy (blocking too many winners)
print()
print("Gates blocking too many winners per strategy (block_pnl > +$5):")
for s in strats:
    s_fires = [f for f in fires if f['strategy'] == s]
    if len(s_fires) < 50: continue
    for g in gates_to_show:
        blocked = [f for f in s_fires if f['gates'][g] and not any(
            f['gates'][gg] for gg in f['gates'] if gg != g
        )]
        pnl = sum(f['hypo'] for f in blocked)
        if pnl > 5:
            print(f"  ⚠️  {s} × {g}: gate blocked +${pnl:.2f} of fires (winners blocked)")

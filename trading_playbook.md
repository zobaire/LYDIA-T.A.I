# Trading Playbook — Lydia Desk Rules

## Core principle
Math BEFORE the click. Never enter first and calculate after.

## The 6 hard rules
1. **Never market-chase.** Enter only at a level (order block, fair value gap, liquidity sweep). Use limit/stop orders.
2. **R:R floor = 2:1.** Below 2:1, no trade. Compute before entry.
3. **TP at a real target, SL at invalidation.** TP = opposing liquidity/structure. SL = just beyond what proves the idea wrong. Never park TP in noise.
4. **Trend filter: M15 + H1 must agree.** Conflict = skip, or half-size only if R:R >= 3.
5. **One idea, one ticket.** No stacking multiple tickets on the same setup.
6. **Fixed risk: 0.5-1% of balance per trade.**

## Sizing formula
```
Lot size = Risk($) / (Entry - SL distance in $ per 1.0 lot)
```
- BTC: 1.0 lot = 1 BTC, $1 move = $1. So 0.01 lot = $0.01 per point.
- EURUSD: 1.0 lot = 100,000 units, $1 pip move = $10. So 0.01 lot = $0.10 per pip.
- XAUUSD: 1.0 lot = 100 oz, $1 move = $100. So 0.01 lot = $1 per point.

## Entry checklist (run every trade)
- [ ] Trend: M15 + H1 aligned?
- [ ] Entry at a level (OB / FVG / sweep)?
- [ ] SL at invalidation, TP at opposing liquidity?
- [ ] R:R >= 2:1?
- [ ] Risk <= 1% of balance?
- [ ] One ticket only?

## Exit rules (autonomous, EV-based)
If P(win) * reward < P(lose) * risk (i.e. R:R below breakeven threshold), cut it.
- R:R 2:1 -> need ~33% win rate
- R:R 1:1 -> need ~50%
- R:R 0.5:1 -> need ~67%
- R:R 0.35:1 -> need ~74%
- R:R 0.02:1 -> need ~98% (never hold)

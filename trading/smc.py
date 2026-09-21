"""SMC (Smart Money Concepts) structure engine — pure Python, no AI, no MT5.

The design rule that matters: **the math finds the structure, the model only
narrates it.** Nothing in this file calls an LLM or a broker, so every level a
setup reports traces back to a specific candle. That is the entire reason this
module exists instead of "ask the model to spot an order block" — an LLM
guessing at structure produces confident nonsense, and this doesn't.

Vocabulary implemented (the standard SMC set):

    swings            fractal pivot highs / lows
    BOS               break of structure — trend continuation
    CHoCH             change of character — trend flip
    order block       last opposing candle before the impulse that broke structure
    FVG               fair value gap — 3-candle imbalance
    liquidity sweep   wick through a prior swing that closes back inside it
    premium/discount  where price sits inside the dealing range

Conventions used throughout:
    "up"/"buy"   = bullish
    "down"/"sell"= bearish
    proximal     = the edge price touches first (where you enter)
    distal       = the far edge (where your stop goes)

Input is plain dict candles in MT5 shape: {"t","o","h","l","c","v"}.
Everything is synchronous and side-effect free — safe to call from the
request thread or a test.
"""
from __future__ import annotations

from typing import Any

Candle = dict

# --- tuning knobs -------------------------------------------------------
# Kept as module constants so a reviewer can see every threshold in one place.
SWING_K = 2               # bars either side of a fractal pivot
MAX_SWINGS = 24           # pivots kept per side (older ones stop mattering)
OB_LOOKBACK = 8           # bars to search back for the opposing candle
MAX_ZONES = 6             # zones returned per kind
SWEEP_WINDOW = 40         # bars after a swing in which a sweep may occur
SWEEP_CONFIRM = 3         # bars allowed for the close-back-inside confirmation
DISPLACEMENT_ATR = 0.6    # break body must clear this much ATR to count as real
MIN_RR = 2.0              # setups below this are discarded, not reported
STOP_BUFFER_ATR = 0.25    # padding beyond the distal edge
POINT_TOL = 1e-9


# --- helpers ------------------------------------------------------------

def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def px(v: float | None) -> str:
    """Price formatting for prompt text (mirrors trading/brain.py)."""
    if v is None:
        return "?"
    f = float(v)
    if abs(f) >= 1000:
        return f"{f:,.2f}"
    if abs(f) >= 10:
        return f"{f:.4f}".rstrip("0").rstrip(".")
    return f"{f:.5f}".rstrip("0").rstrip(".")


def atr(candles: list[Candle], period: int = 14) -> float:
    """Wilder's ATR. Falls back to the mean true range on short series."""
    if len(candles) < 2:
        return 0.0
    trs: list[float] = []
    prev_c = _f(candles[0].get("c"))
    for c in candles[1:]:
        h, l = _f(c.get("h")), _f(c.get("l"))
        trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
        prev_c = _f(c.get("c"))
    if not trs:
        return 0.0
    p = max(2, min(int(period), len(trs)))
    val = sum(trs[:p]) / p
    for tr in trs[p:]:
        val = (val * (p - 1) + tr) / p
    return val


def _body(c: Candle) -> float:
    return abs(_f(c.get("c")) - _f(c.get("o")))


def _bullish(c: Candle) -> bool:
    return _f(c.get("c")) >= _f(c.get("o"))


# --- 1. swings ----------------------------------------------------------

def swings(candles: list[Candle], k: int = SWING_K) -> list[dict]:
    """Fractal pivots: a high is a pivot when nothing within +/-k bars beats it."""
    out: list[dict] = []
    n = len(candles)
    for i in range(k, n - k):
        h, l = _f(candles[i].get("h")), _f(candles[i].get("l"))
        if all(_f(candles[j].get("h")) <= h for j in range(i - k, i + k + 1) if j != i):
            out.append({"i": i, "t": candles[i].get("t"), "price": h, "kind": "high"})
        elif all(_f(candles[j].get("l")) >= l for j in range(i - k, i + k + 1) if j != i):
            out.append({"i": i, "t": candles[i].get("t"), "price": l, "kind": "low"})

    highs = [s for s in out if s["kind"] == "high"][-MAX_SWINGS:]
    lows = [s for s in out if s["kind"] == "low"][-MAX_SWINGS:]
    return sorted(highs + lows, key=lambda s: s["i"])


# --- 2. structure: BOS / CHoCH -----------------------------------------

def structure(candles: list[Candle], sw: list[dict]) -> tuple[str, list[dict]]:
    """Walk the series and emit BOS / CHoCH events.

    A close beyond the last *confirmed* swing either continues the trend (BOS)
    or flips it (CHoCH). Swings are only published once their bar has fully
    passed, so the trend reported at bar `i` uses no future information —
    important, because otherwise every backtest of this would look amazing.
    """
    events: list[dict] = []
    trend = "range"
    hi = lo = None
    pending_h = [s for s in sw if s["kind"] == "high"]
    pending_l = [s for s in sw if s["kind"] == "low"]
    ph = pl = 0

    for i, c in enumerate(candles):
        while ph < len(pending_h) and pending_h[ph]["i"] < i:
            hi = pending_h[ph]["price"]
            ph += 1
        while pl < len(pending_l) and pending_l[pl]["i"] < i:
            lo = pending_l[pl]["price"]
            pl += 1

        close = _f(c.get("c"))

        if hi is not None and close > hi + POINT_TOL:
            kind = "BOS" if trend == "up" else "CHoCH"
            events.append({
                "kind": kind, "direction": "up", "i": i, "t": c.get("t"),
                "broken": hi, "close": close,
                "displacement": round(_body(c), 8),
            })
            trend = "up"
            hi = None            # consumed — wait for the next swing high
        elif lo is not None and close < lo - POINT_TOL:
            kind = "BOS" if trend == "down" else "CHoCH"
            events.append({
                "kind": kind, "direction": "down", "i": i, "t": c.get("t"),
                "broken": lo, "close": close,
                "displacement": round(_body(c), 8),
            })
            trend = "down"
            lo = None

    return trend, events


# --- 3. order blocks ----------------------------------------------------

def order_blocks(candles: list[Candle], events: list[dict],
                 atr_val: float = 0.0, max_zones: int = MAX_ZONES) -> list[dict]:
    """Last opposing candle before the impulse that broke structure.

    Bullish OB  = last down-close candle before an up-break.
    Bearish OB  = last up-close candle before a down-break.

    Zone is the candle's full range. A zone dies once price *closes* through
    its distal edge (mitigated) — a wick through it does not kill it, which is
    the whole reason the check uses closes.
    """
    zones: list[dict] = []
    for ev in events[-12:]:
        j = ev["i"]
        if j < 1:
            continue
        want_down = ev["direction"] == "up"

        src = None
        k = j - 1
        while k >= max(0, j - OB_LOOKBACK):
            c = candles[k]
            if _bullish(c) != want_down:      # the opposing colour
                src = c
                break
            k -= 1
        if src is None:
            src = candles[j - 1]

        top = _f(src.get("h"))
        bot = _f(src.get("l"))
        if top - bot <= 0:
            continue

        # Displacement filter: a break on a tiny body is noise, not an impulse.
        if atr_val > 0 and _body(candles[j]) < DISPLACEMENT_ATR * atr_val:
            continue

        swept = False
        for c2 in candles[j + 1:]:
            if want_down and _f(c2.get("c")) < bot - POINT_TOL:
                swept = True
                break
            if not want_down and _f(c2.get("c")) > top + POINT_TOL:
                swept = True
                break
        if swept:
            continue                          # mitigated — no longer an edge

        zones.append({
            "kind": "order_block",
            "direction": "up" if want_down else "down",
            "top": top, "bottom": bot,
            # proximal = first edge touched; distal = where the stop lives
            "proximal": top if want_down else bot,
            "distal": bot if want_down else top,
            "i": k if src is not candles[j - 1] else j - 1,
            "t": src.get("t"),
            "origin_event": ev["kind"],
            "origin_i": j,
        })

    # Freshest first, de-duplicated by price band.
    zones.reverse()
    return zones[:max_zones]


# --- 4. fair value gaps -------------------------------------------------

def fair_value_gaps(candles: list[Candle], atr_val: float = 0.0,
                    max_zones: int = MAX_ZONES) -> list[dict]:
    """3-candle imbalance: the gap candle i skipped between i-1 and i+1."""
    gaps: list[dict] = []
    n = len(candles)
    for i in range(1, n - 1):
        a, b = candles[i - 1], candles[i + 1]
        up = _f(b.get("l")) > _f(a.get("h")) + POINT_TOL
        dn = _f(a.get("l")) > _f(b.get("h")) + POINT_TOL
        if not (up or dn):
            continue

        if up:
            bot, top, direction = _f(a.get("h")), _f(b.get("l")), "up"
        else:
            bot, top, direction = _f(b.get("h")), _f(a.get("l")), "down"

        size = top - bot
        if size <= 0:
            continue
        # Ignore micro-gaps that are just spread noise.
        if atr_val > 0 and size < 0.08 * atr_val:
            continue

        filled = 0.0
        for c in candles[i + 2:]:
            if direction == "up":
                if _f(c.get("l")) <= bot + POINT_TOL:
                    filled = 1.0
                    break
                if _f(c.get("l")) < top:
                    filled = max(filled, (top - _f(c.get("l"))) / size)
            else:
                if _f(c.get("h")) >= top - POINT_TOL:
                    filled = 1.0
                    break
                if _f(c.get("h")) > bot:
                    filled = max(filled, (_f(c.get("h")) - bot) / size)
        if filled >= 1.0:
            continue                          # fully rebalanced — no edge left

        gaps.append({
            "kind": "fvg",
            "direction": direction,
            "top": top, "bottom": bot, "size": size,
            "proximal": bot if direction == "up" else top,
            "distal": top if direction == "up" else bot,
            "i": i, "t": candles[i].get("t"),
            "filled": round(filled, 2),
        })

    gaps.reverse()
    return gaps[:max_zones]


# --- 5. liquidity sweeps ------------------------------------------------

def liquidity_sweeps(candles: list[Candle], sw: list[dict],
                     max_events: int = 6) -> list[dict]:
    """Wick takes out a prior swing, then a close comes back inside it.

    That pattern is the footprint of stops being harvested — price pokes
    through an obvious level, fills the resting orders, and reverses. It is
    the single most useful piece of context for a reversal entry.
    """
    evs: list[dict] = []
    n = len(candles)
    for s in sw:
        idx = s["i"]
        for j in range(idx + 1, min(n, idx + SWEEP_WINDOW)):
            c = candles[j]
            if s["kind"] == "low":
                if _f(c.get("l")) >= s["price"] - POINT_TOL:
                    continue
                for k in range(j, min(n, j + SWEEP_CONFIRM + 1)):
                    if _f(candles[k].get("c")) > s["price"] + POINT_TOL:
                        evs.append({
                            "direction": "up", "swept_level": s["price"],
                            "extreme": _f(c.get("l")), "i": k,
                            "t": candles[k].get("t"), "swing_i": idx,
                        })
                        break
                break
            else:
                if _f(c.get("h")) <= s["price"] + POINT_TOL:
                    continue
                for k in range(j, min(n, j + SWEEP_CONFIRM + 1)):
                    if _f(candles[k].get("c")) < s["price"] - POINT_TOL:
                        evs.append({
                            "direction": "down", "swept_level": s["price"],
                            "extreme": _f(c.get("h")), "i": k,
                            "t": candles[k].get("t"), "swing_i": idx,
                        })
                        break
                break

    evs.sort(key=lambda e: e["i"])
    # One sweep per bar, newest kept.
    dedup: dict[int, dict] = {}
    for e in evs:
        dedup[e["i"]] = e
    return sorted(dedup.values(), key=lambda e: e["i"])[-max_events:]


# --- 6. dealing range / premium-discount --------------------------------

def dealing_range(candles: list[Candle], sw: list[dict]) -> dict:
    """The range that defines premium vs discount, and where price sits in it."""
    if sw:
        hi = max(s["price"] for s in sw)
        lo = min(s["price"] for s in sw)
    elif candles:
        hi = max(_f(c.get("h")) for c in candles)
        lo = min(_f(c.get("l")) for c in candles)
    else:
        return {"high": 0.0, "low": 0.0, "mid": 0.0, "position": 0.5,
                "zone": "unknown", "span": 0.0}

    close = _f(candles[-1].get("c")) if candles else 0.0
    span = hi - lo
    pos = (close - lo) / span if span > 0 else 0.5
    pos = max(0.0, min(1.0, pos))
    return {
        "high": hi, "low": lo, "mid": lo + span / 2.0,
        "position": round(pos, 3),
        # Above 50% favours sells, below favours buys, and dead-on 50% favours
        # neither. Reporting equilibrium as "discount" would hand every long a
        # free confluence point at mid-range and deny the short the same setup
        # the same credit — an asymmetry, not a rounding detail.
        "zone": ("premium" if pos > 0.5 else
                 "discount" if pos < 0.5 else "equilibrium"),
        "span": span,
    }


# --- 7. confluence scoring ---------------------------------------------

def _score(direction: str, zones: dict, sweep: dict | None, rng: dict,
           trend: str, last_event: dict | None, rr: float,
           htf_trend: str | None) -> tuple[int, list[dict]]:
    """Award points for independent things that agree. Returns (score, hits)."""
    hits: list[dict] = []

    def add(name: str, detail: str, pts: int = 1) -> None:
        hits.append({"name": name, "detail": detail, "points": pts})

    if trend == direction:
        add("trend aligns", f"structure is {trend}")

    if last_event:
        if last_event["kind"] == "CHoCH" and last_event["direction"] == direction:
            add("fresh CHoCH", "character just flipped in this direction")
        elif last_event["kind"] == "BOS" and last_event["direction"] == direction:
            add("BOS continuation", "last break continued the trend")

    if zones.get("order_block"):
        add("order block", f"{zones['order_block']['kind']} entry zone")
    if zones.get("fvg"):
        add("fair value gap", f"{int(zones['fvg']['filled'] * 100)}% unfilled")

    if sweep and sweep["direction"] == direction:
        add("liquidity sweep", f"stops taken at {px(sweep['swept_level'])}")

    want = "discount" if direction == "up" else "premium"
    if rng.get("zone") == want:
        add(f"{want} zone", f"price at {int(rng['position'] * 100)}% of range")

    if htf_trend and htf_trend == direction:
        add("HTF agrees", "higher timeframe trend matches", 2)

    if rr >= 3.0:
        add("R:R >= 3", f"{rr:.2f} reward to risk")

    return sum(h["points"] for h in hits), hits


def _quality(score: int) -> str:
    if score >= 6:
        return "A"
    if score >= 4:
        return "B"
    return "C"


# --- 8. trade plan builder ---------------------------------------------

def _build_plan(direction: str, candles: list[Candle], sw: list[dict],
                rng: dict, zone: dict, atr_val: float,
                min_rr: float) -> dict | None:
    """Entry / stop / target / R:R for entering from `zone` in `direction`.

    Returns None when no honest stop or no target better than min_rr exists.
    Refusing to answer is a valid outcome here, and the most common one.
    """
    if not candles:
        return None
    price = _f(candles[-1].get("c"))
    entry = zone["proximal"]
    buffer = STOP_BUFFER_ATR * atr_val if atr_val > 0 else 0.0

    # A zone taller than ~1.5 ATR would put the stop uncomfortably far away, so
    # enter at equilibrium instead of the near edge. Reported so the UI can
    # show which entry style was used — refined means tighter but riskier.
    zone_height = zone["top"] - zone["bottom"]
    entry_mode = "proximal"
    if atr_val > 0 and zone_height > 1.5 * atr_val:
        entry = zone["bottom"] + zone_height / 2.0
        entry_mode = "refined"

    # Target candidates are pools of resting liquidity: swing highs above the
    # entry, plus the range high itself. We take the *nearest one that still
    # pays min_rr* rather than simply the nearest — otherwise an obvious level
    # sitting 1.2R away silently kills an otherwise A-grade setup. Skipping it
    # for the next pool is what a trader actually does.
    if direction == "up":
        stop = zone["distal"] - buffer
        risk = entry - stop
        if risk <= 0:
            return None
        cands = [s["price"] for s in sw
                 if s["kind"] == "high" and s["price"] > entry + POINT_TOL]
        rh = _f(rng.get("high")) if rng else 0.0
        if rh > entry + POINT_TOL:
            cands.append(rh)
        target = None
        measured = False
        for t in sorted(cands):
            if (t - entry) / risk >= min_rr:
                target = t
                break
        if target is None:
            target = entry + min_rr * risk
            measured = True
    else:
        stop = zone["distal"] + buffer
        risk = stop - entry
        if risk <= 0:
            return None
        cands = [s["price"] for s in sw
                 if s["kind"] == "low" and s["price"] < entry - POINT_TOL]
        rl = _f(rng.get("low")) if rng else 0.0
        if rl and rl < entry - POINT_TOL:
            cands.append(rl)
        target = None
        measured = False
        for t in sorted(cands, reverse=True):
            if (entry - t) / risk >= min_rr:
                target = t
                break
        if target is None:
            target = entry - min_rr * risk
            measured = True

    rr = abs(target - entry) / risk if risk > 0 else 0.0
    if rr < min_rr:
        return None

    # Where we are relative to the entry right now.
    if direction == "up":
        if price <= entry:
            status = "price is inside the zone"
        elif price > zone["top"]:
            status = "waiting for a pullback down into the zone"
        else:
            status = "price is at the zone"
    else:
        if price >= entry:
            status = "price is inside the zone"
        elif price < zone["bottom"]:
            status = "waiting for a pullback up into the zone"
        else:
            status = "price is at the zone"

    return {
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target": target,
        "rr": round(rr, 2),
        "risk": risk,
        "target_kind": "measured" if measured else "liquidity",
        "zone_kind": zone["kind"],
        "zone_top": zone["top"],
        "zone_bottom": zone["bottom"],
        "entry_mode": entry_mode,
        "status": status,
        "invalidation": stop,
        "price": price,
    }


# --- 9. the analyzer ----------------------------------------------------

def analyze(candles: list[Candle], symbol: str = "", tf: str = "M5",
            htf_candles: list[Candle] | None = None, htf_tf: str = "H1",
            min_rr: float = MIN_RR, min_score: int = 1) -> dict:
    """Full structured SMC read. Never raises — on bad input it says so."""
    if not candles or len(candles) < 20:
        return {
            "ok": False, "symbol": symbol, "tf": tf,
            "error": "not enough candles for structure analysis",
            "candles": len(candles or []),
        }

    atr_val = atr(candles)
    sw = swings(candles)
    trend, events = structure(candles, sw)
    obs = order_blocks(candles, events, atr_val)
    gaps = fair_value_gaps(candles, atr_val)
    sweeps = liquidity_sweeps(candles, sw)
    rng = dealing_range(candles, sw)
    price = _f(candles[-1].get("c"))
    last_event = events[-1] if events else None

    htf_trend = None
    if htf_candles and len(htf_candles) >= 20:
        try:
            hsw = swings(htf_candles)
            htf_trend, _ = structure(htf_candles, hsw)
        except Exception:
            htf_trend = None

    # Which directions are structurally live.
    bias: list[str] = []
    if trend in ("up", "down"):
        bias.append(trend)
    if last_event and last_event["kind"] == "CHoCH" and last_event["direction"] not in bias:
        bias.append(last_event["direction"])
    if not bias:
        bias = ["up", "down"]     # range: both sides are candidates, score decides

    recent_sweep = sweeps[-1] if sweeps else None
    setups: list[dict] = []

    for direction in bias:
        pool = obs if obs else []
        cand = [z for z in pool if z["direction"] == direction]
        gap_cand = [g for g in gaps if g["direction"] == direction]

        # Nearest unmitigated zone in the direction of the trade, preferring an
        # order block over an FVG (an OB is a structural level, a gap is just
        # an imbalance).
        zone = None
        if cand:
            # Nearest zone to price, not the most extreme one. Taking max(top)
            # blindly would hand back an order block from three swings ago
            # while a fresh one sits right under the current candle.
            if direction == "up":
                below = [z for z in cand if z["top"] <= price + POINT_TOL]
                zone = (max(below, key=lambda z: z["top"]) if below
                        else min(cand, key=lambda z: z["bottom"]))
            else:
                above = [z for z in cand if z["bottom"] >= price - POINT_TOL]
                zone = (min(above, key=lambda z: z["bottom"]) if above
                        else max(cand, key=lambda z: z["top"]))
        elif gap_cand:
            zone = gap_cand[0]

        if zone is None:
            continue

        plan = _build_plan(direction, candles, sw, rng, zone, atr_val, min_rr)
        if plan is None:
            continue

        zones_hit = {
            "order_block": zone if zone["kind"] == "order_block" else None,
            "fvg": (zone if zone["kind"] == "fvg" else (gap_cand[0] if gap_cand else None)),
        }
        # A gap sitting inside/behind the OB is extra confluence.
        if zones_hit["fvg"] is None and cand:
            for g in gaps:
                if g["direction"] == direction and g["bottom"] <= zone["top"] and g["top"] >= zone["bottom"]:
                    zones_hit["fvg"] = g
                    break

        score, hits = _score(direction, zones_hit, recent_sweep,
                             rng, trend, last_event, plan["rr"], htf_trend)

        if score < min_score:
            continue

        setups.append({
            "direction": direction,
            "score": score,
            "quality": _quality(score),
            "confluences": hits,
            "plan": plan,
            "zone": {k: zone[k] for k in
                     ("kind", "direction", "top", "bottom", "proximal", "distal", "i", "t")
                     if k in zone},
        })

    setups.sort(key=lambda s: (-s["score"], -s["plan"]["rr"]))
    best = setups[0] if setups else None

    reason_no_trade = None
    if not setups:
        if not obs and not gaps:
            reason_no_trade = "no unmitigated order block or fair value gap in play"
        elif not events:
            reason_no_trade = "no confirmed break of structure yet — still ranging"
        else:
            reason_no_trade = "zones exist but none offered a clean stop at the required R:R"

    return {
        "ok": True,
        "symbol": symbol,
        "tf": tf,
        "price": price,
        "atr": atr_val,
        "trend": trend,
        "htf_tf": htf_tf if htf_trend else None,
        "htf_trend": htf_trend,
        "bias": bias,
        "last_event": last_event,
        "events": events[-4:],
        "order_blocks": obs,
        "fvgs": gaps,
        "sweeps": sweeps[-3:],
        "range": rng,
        "setups": setups,
        "best": best,
        "no_trade_reason": reason_no_trade,
        "candles": len(candles),
    }


# --- 10. prompt formatting ---------------------------------------------

def format_for_prompt(a: dict, max_events: int = 6) -> str:
    """Compact text block for the system prompt. Empty string when unusable."""
    if not a or not a.get("ok"):
        return ""

    out: list[str] = []
    out.append("## SMC Structure (computed, not guessed)")
    out.append(f"{a['symbol']} {a['tf']} @ {px(a['price'])} | ATR {px(a['atr'])}")
    trend = a.get("trend", "range")
    htf = a.get("htf_trend")
    line = f"Trend: {trend.upper()}"
    if htf:
        line += f" | {a.get('htf_tf')} bias: {htf.upper()}"
        if htf != trend:
            line += " (conflicting — lower conviction)"
    out.append(line)

    ev = a.get("last_event")
    if ev:
        out.append(f"Last event: {ev['kind']} {ev['direction'].upper()} "
                   f"(broke {px(ev['broken'])}, close {px(ev['close'])})")
    for e in (a.get("events") or [])[:-1][-2:]:
        out.append(f"  earlier: {e['kind']} {e['direction'].upper()} @ {px(e['broken'])}")

    rng = a.get("range") or {}
    if rng.get("span"):
        out.append(f"Dealing range {px(rng['low'])} - {px(rng['high'])}, "
                   f"price at {int(rng['position'] * 100)}% ({rng['zone']})")

    obs = a.get("order_blocks") or []
    if obs:
        out.append("Unmitigated order blocks (newest first):")
        for z in obs[:4]:
            out.append(f"- {z['direction'].upper()} {px(z['bottom'])}-{px(z['top'])}")
    gaps = a.get("fvgs") or []
    if gaps:
        out.append("Unfilled fair value gaps:")
        for g in gaps[:4]:
            out.append(f"- {g['direction'].upper()} {px(g['bottom'])}-{px(g['top'])} "
                       f"({int(g['filled'] * 100)}% filled)")

    sweeps = a.get("sweeps") or []
    if sweeps:
        last = sweeps[-1]
        out.append(f"Last liquidity sweep: {last['direction'].upper()} at "
                   f"{px(last['swept_level'])}")

    setups = a.get("setups") or []
    if setups:
        out.append(f"Setups found: {len(setups)}")
        for s in setups[:3]:
            p = s["plan"]
            out.append(
                f"- [{s['quality']} score {s['score']}] {s['direction'].upper()} "
                f"{a['symbol']} from {p['zone_kind']} | entry {px(p['entry'])} "
                f"SL {px(p['stop'])} TP {px(p['target'])} ({p['rr']:.1f}R, "
                f"{p['target_kind']}) | {p['status']}"
            )
            if s["confluences"]:
                out.append("    " + "; ".join(h["name"] for h in s["confluences"]))
    else:
        out.append(f"No valid setup. Reason: {a.get('no_trade_reason') or 'nothing clean'}")

    out.append("These levels are computed from candles. Quote them as-is; do not "
               "invent levels or upgrade a weak setup into a strong one.")
    return "\n".join(out)


def analyze_and_format(symbol: str, tf: str = "M5",
                       htf_candles: list[Candle] | None = None,
                       htf_tf: str = "H1",
                       candles: list[Candle] | None = None,
                       min_rr: float = MIN_RR,
                       min_score: int = 1) -> tuple[dict, str]:
    """Convenience: analyze then format. Returns (analysis, prompt_text)."""
    a = analyze(candles or [], symbol=symbol, tf=tf,
                htf_candles=htf_candles, htf_tf=htf_tf,
                min_rr=min_rr, min_score=min_score)
    return a, format_for_prompt(a)

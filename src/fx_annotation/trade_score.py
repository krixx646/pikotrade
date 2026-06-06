"""Deterministic, backtestable trade-quality score.

A pure function (no network, no lookahead) that scores any found trade 0-100 from
features known at signal time - strategy edge (route tier), session quality,
reward (available R), and pair value - and maps it to a TAKE / CAUTION / SKIP
verdict. Because it is deterministic it can be backtested: score historical
trades, bucket by verdict, and check that TAKE out-earns SKIP. This is the gate;
any LLM commentary is layered on top, never the decision.

Scoring mirrors whatsapp_push._confidence so the alert confidence and this gate
agree. Thresholds (TAKE_MIN / CAUTION_MIN) are tuned from the filter backtest.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from fx_annotation.pair_value import pair_value_for_instrument

# Verdict thresholds on the 0-100 score. Set from scripts/backtest_trade_filter.py.
TAKE_MIN = 70
CAUTION_MIN = 55

# Historical per-band outcomes from the flagship filter backtest (MOMENTUM +
# HTF_MOMENTUM + HTF_ZONE pooled, 1yr, 9 pairs). PURELY INFORMATIONAL context for
# the analyst note - never used in the decision. Regenerate with:
#   python scripts/backtest_trade_filter.py --engine flagship
# Tuple: (lo, hi, win_rate_pct, avg_r_per_trade, sample_n)
BAND_STATS = (
    (0, 40, 49.5, -0.171, 777),
    (40, 55, 53.9, -0.014, 1725),
    (55, 70, 53.7, 0.126, 1232),
    (70, 85, 54.3, 0.194, 1840),
    (85, 101, 39.9, 1.151, 228),
)


def band_stats(score: int) -> tuple[str, float, float, int]:
    """(band label, historical win-rate %, avg R/trade, sample size) for a score."""
    for lo, hi, wr, ar, n in BAND_STATS:
        if lo <= score < hi:
            return (f"{lo}-{hi - 1}", wr, ar, n)
    return ("?", 0.0, 0.0, 0)

# Historical context per score band - pooled flagship backtest (MOMENTUM + HTF_MOMENTUM +
# HTF_ZONE, ~5.8k trades, 1yr, 9 pairs). (lo, hi, win_rate_pct, avg_R_per_trade). Informational
# only: shows BOTH the comfort side (hit rate) and the payoff side (expectancy) of each band.
SCORE_BANDS: tuple[tuple[int, int, float, float], ...] = (
    (0, 40, 49.5, -0.17),
    (40, 55, 53.9, -0.01),
    (55, 70, 53.7, 0.13),
    (70, 85, 54.3, 0.19),
    (85, 101, 39.9, 1.15),
)


def band_context(score: int) -> str:
    """One-line historical read for the score's band: '~54% win, +0.19R/trade avg'."""
    for lo, hi, win, exp in SCORE_BANDS:
        if lo <= score < hi:
            return f"~{win:.0f}% win, {exp:+.2f}R/trade avg"
    return "no historical reference"

# Historical performance per score band, measured by scripts/backtest_trade_filter.py
# (flagship pool: MOMENTUM + HTF_MOMENTUM + HTF_ZONE, 1yr, 9 pairs). Purely informational
# context for the owner - (low, high_exclusive, win_rate_pct, expectancy_R). Note the
# inversion at the top: the 85+ band wins less often but pays the most per trade.
BAND_HISTORY: tuple[tuple[int, int, float, float], ...] = (
    (0, 40, 49.5, -0.17),
    (40, 55, 53.9, -0.01),
    (55, 70, 53.7, 0.13),
    (70, 85, 54.3, 0.19),
    (85, 101, 39.9, 1.15),
)


def band_history(score: int) -> tuple[float, float] | None:
    """(historical win_rate_pct, expectancy_R) for this score's band, or None."""
    for lo, hi, wr, exp in BAND_HISTORY:
        if lo <= score < hi:
            return (wr, exp)
    return None


@dataclass(frozen=True)
class TradeScore:
    score: int
    verdict: str  # TAKE | CAUTION | SKIP
    prime: bool
    session: str
    reasons: tuple[str, ...]


def route_rank(route: str) -> int:
    """Tier rank (1=best edge .. 7=watch). Mirror of whatsapp_push._tier / _route_tier."""
    r = str(route or "").upper()
    if r.startswith("HTF_MOMENTUM"):
        return 1
    if r.startswith("HTF_ZONE"):
        return 2
    if r.startswith("MOMENTUM"):
        return 3
    if r.startswith("M15"):
        return 4
    if r.startswith("DYNAMIC"):
        return 5
    if r.startswith("REGIME") or r.startswith("RULE"):
        return 6
    return 7


def session_for(signal_time: str) -> tuple[str, bool]:
    """(session name, is_prime). Prime = London/NY/overlap (UTC). Mirror of _session_quality."""
    dt = _parse(signal_time)
    if dt is None:
        return ("unknown", True)
    h = dt.hour
    if 12 <= h < 16:
        return ("London/New York overlap", True)
    if 7 <= h < 12:
        return ("London", True)
    if 16 <= h < 21:
        return ("New York", True)
    return ("off-hours (Asian/late-US)", False)


def conviction(
    route: str,
    instrument: str,
    signal_time: str,
    available_r: float | None,
    pair_value_tier: str | None = None,
) -> TradeScore:
    reasons: list[str] = []
    rank = route_rank(route)
    score = {1: 35, 2: 32, 3: 30, 4: 25, 5: 20, 6: 12, 7: 8}.get(rank, 8)
    reasons.append(f"tier T{rank} (+{ {1:35,2:32,3:30,4:25,5:20,6:12,7:8}.get(rank,8) })")

    session_name, prime = session_for(signal_time)
    if session_name == "London/New York overlap":
        score += 30
        reasons.append("overlap session (+30)")
    elif session_name in ("London", "New York"):
        score += 22
        reasons.append(f"{session_name} session (+22)")
    elif session_name == "unknown":
        score += 15
    else:
        reasons.append("off-hours session (+0)")

    r = available_r
    if r is None:
        score += 8
    elif r >= 3:
        score += 20
        reasons.append(f"{r:g}R room (+20)")
    elif r >= 2:
        score += 14
        reasons.append(f"{r:g}R room (+14)")
    elif r >= 1.5:
        score += 8
        reasons.append(f"{r:g}R room (+8)")
    else:
        score += 3
        reasons.append(f"thin {r:g}R room (+3)")

    tier = (pair_value_tier or "").lower()
    if not tier:
        tier = pair_value_for_instrument(instrument).tier.lower()
    if tier in ("high_value", "core", "high"):
        score += 15
        reasons.append("high-value pair (+15)")
    elif tier in ("low_value", "low"):
        score += 0
        reasons.append("low-value pair (+0)")
    else:
        score += 8

    score = max(0, min(100, score))
    if score >= TAKE_MIN:
        verdict = "TAKE"
    elif score >= CAUTION_MIN:
        verdict = "CAUTION"
    else:
        verdict = "SKIP"
    return TradeScore(score=score, verdict=verdict, prime=prime, session=session_name, reasons=tuple(reasons))


def _parse(value: object) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

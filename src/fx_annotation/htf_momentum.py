"""Route A detector: HTF (H1) impulse-continuation move, executed on M15 for max R.

The idea (top-down day trade): find a real directional *move* on the 1-hour chart - a
fresh impulse leg in the trend direction that has hours of room to continue - then hand a
tight entry zone to the M15 layer so the entry's stop is small and the runner can ride the
continuation for a big R-multiple.

This module is pure detection: it takes a candle series (H1 in practice) and returns a
lightweight ``HtfSignal`` (no SignalCandidate, no OANDA, no exit logic), so it can be reused
by both the live route and the backtest with no circular imports. The caller decides how to
turn the signal into a tracked trade.
"""
from __future__ import annotations

from dataclasses import dataclass

from fx_annotation.bias import detect_bias
from fx_annotation.candles import Candle
from fx_annotation.structure import average_range


@dataclass(frozen=True)
class HtfMomentumParams:
    impulse_lookback: int = 10       # H1 bars to scan for the impulse leg (~10h)
    impulse_atr_mult: float = 2.0    # leg must exceed this * H1 ATR to count as a real move
    recent_extreme_within: int = 6   # the impulse extreme must be this recent (else stale)
    entry_anchor: float = 0.4        # pullback level to anchor the entry at (fraction of the leg)
    entry_band: float = 0.14         # thickness of the M15 entry zone (fraction of the leg) - kept
    bias_lookback: int = 80          # H1 bars for trend context
    target_ext: float = 1.0          # H1 target = impulse extreme + this * range (measured move)


@dataclass(frozen=True)
class HtfSignal:
    side: str
    entry_low: float
    entry_high: float
    sweep_price: float
    strength: float
    note: str
    imp_high: float = 0.0      # H1 impulse extreme high
    imp_low: float = 0.0       # H1 impulse extreme low
    rng: float = 0.0           # impulse range (imp_high - imp_low)
    target_price: float = 0.0  # fixed H1 target (measured-move projection of the impulse)


def htf_momentum_signal(
    window: list[Candle],
    params: HtfMomentumParams = HtfMomentumParams(),
) -> HtfSignal | None:
    """Detect an H1 impulse + shallow continuation pullback. Returns a thin M15 entry zone.

    The entry zone is intentionally *narrow* (a slice anchored at ``entry_anchor`` of the leg)
    so the M15 stop just past it is small - that small stop against the long HTF continuation
    is what produces the high R-multiple.
    """
    need = max(params.bias_lookback, params.impulse_lookback + 2, 40)
    if len(window) < need:
        return None
    atr = average_range(window[-30:], period=30)
    if atr <= 0:
        return None
    leg = window[-params.impulse_lookback :]
    highs = [c.high for c in leg]
    lows = [c.low for c in leg]
    imp_high = max(highs)
    imp_low = min(lows)
    hi_idx = highs.index(imp_high)
    lo_idx = lows.index(imp_low)
    rng = imp_high - imp_low
    if rng < atr * params.impulse_atr_mult:
        return None

    bias = detect_bias(window[-params.bias_lookback :]).direction
    n = len(leg)
    latest_close = window[-1].close
    strength = rng / atr
    half_band = 0.5 * params.entry_band * rng

    # BUY continuation: up-impulse (low before high), high is recent, bias bullish.
    if bias == "bullish" and lo_idx < hi_idx and (n - 1 - hi_idx) <= params.recent_extreme_within:
        anchor = imp_high - params.entry_anchor * rng
        entry_low = anchor - half_band
        entry_high = anchor + half_band
        if entry_high < latest_close:  # price still above the zone (room to pull back in)
            target = imp_high + params.target_ext * rng
            return HtfSignal("BUY", round(entry_low, 5), round(entry_high, 5), round(imp_low, 5),
                             round(strength, 2), f"H1 impulse {strength:.1f}xATR up; continuation buy",
                             round(imp_high, 5), round(imp_low, 5), round(rng, 5), round(target, 5))

    # SELL continuation: down-impulse (high before low), low is recent, bias bearish.
    if bias == "bearish" and hi_idx < lo_idx and (n - 1 - lo_idx) <= params.recent_extreme_within:
        anchor = imp_low + params.entry_anchor * rng
        entry_low = anchor - half_band
        entry_high = anchor + half_band
        if entry_low > latest_close:  # price still below the zone (room to pull back up)
            target = imp_low - params.target_ext * rng
            return HtfSignal("SELL", round(entry_low, 5), round(entry_high, 5), round(imp_high, 5),
                             round(strength, 2), f"H1 impulse {strength:.1f}xATR down; continuation sell",
                             round(imp_high, 5), round(imp_low, 5), round(rng, 5), round(target, 5))

    return None

"""Route B detector (EXPERIMENTAL, delete-safe): SMC reaction from an HTF zone -> M15 entry.

Different *thesis* from route A. Instead of riding an impulse, this waits for price to return
to a higher-timeframe point-of-interest (an H1 sweep+BOS order-block zone aligned with the H4
bias) and then takes the reaction on M15. It reuses the existing rule-engine SMC primitives
(detect_bias + find_recent_setups), just fed H4/H1 candles.

DELETE-SAFE: nothing in the live agent imports this module. Removing this file and
``scripts/backtest_htf_zone.py`` removes route B entirely with zero impact on anything else.
"""
from __future__ import annotations

from dataclasses import dataclass

from fx_annotation.bias import detect_bias
from fx_annotation.candles import Candle
from fx_annotation.htf_momentum import HtfSignal
from fx_annotation.setups import find_recent_setups
from fx_annotation.structure import average_range


@dataclass(frozen=True)
class HtfZoneParams:
    h4_bias_lookback: int = 60      # H4 bars for the directional bias (~10 trading days)
    h1_lookback: int = 200          # H1 bars scanned for the sweep+BOS zone
    swing_window: int = 2
    min_quality: int = 3            # reuse the rule engine's setup quality score
    max_zone_distance_atr: float = 3.0  # zone must be within reach of current price (H1 ATR)


def htf_zone_signal(
    h4_window: list[Candle],
    h1_window: list[Candle],
    params: HtfZoneParams = HtfZoneParams(),
) -> HtfSignal | None:
    """H4 bias + a fresh, in-reach H1 sweep/BOS zone -> an M15 reaction entry."""
    if len(h4_window) < params.h4_bias_lookback or len(h1_window) < 60:
        return None
    bias = detect_bias(h4_window[-params.h4_bias_lookback :])
    if bias.direction not in {"bullish", "bearish"}:
        return None

    h1 = h1_window[-params.h1_lookback :]
    atr = average_range(h1[-30:], period=30)
    if atr <= 0:
        return None
    setups, _, _ = find_recent_setups(h1, bias, swing_window=params.swing_window, limit=5)
    if not setups:
        return None

    want = "buy" if bias.direction == "bullish" else "sell"
    latest_close = h1[-1].close
    for setup in setups:
        if setup.side != want:
            continue
        if setup.quality_score < params.min_quality:
            continue
        zone = setup.entry_zone
        zone_ref = zone.high if want == "buy" else zone.low
        if abs(latest_close - zone_ref) > params.max_zone_distance_atr * atr:
            continue  # zone too far from price to be a realistic day-trade entry
        # For a buy, price must still be above the zone (room to drop into it); inverse for sell.
        if want == "buy" and latest_close < zone.low:
            continue
        if want == "sell" and latest_close > zone.high:
            continue
        side = "BUY" if want == "buy" else "SELL"
        sweep_price = setup.sweep.swept_price
        note = (
            f"H4 {bias.direction} + H1 sweep/BOS zone (Q{setup.quality_score}); "
            f"M15 reaction {side} from HTF POI"
        )
        return HtfSignal(side, round(zone.low, 5), round(zone.high, 5), round(sweep_price, 5),
                         float(setup.quality_score), note)
    return None

from dataclasses import dataclass

from fx_annotation.candles import Candle
from fx_annotation.structure import average_range, detect_swings


@dataclass(frozen=True)
class ScoreFactor:
    name: str
    points: float
    note: str


@dataclass(frozen=True)
class DynamicScoreSignal:
    strategy: str
    side: str
    score: float
    entry_low: float
    entry_high: float
    stop_reference: float
    factors: tuple[ScoreFactor, ...]


TREND_EFFICIENCY_MIN = 0.32
RANGE_EFFICIENCY_MAX = 0.22
HIGH_VOLATILITY_RATIO = 2.3
RANGE_MIN_WIDTH_ATR = 3.5
RANGE_MAX_WIDTH_ATR = 30.0


def best_dynamic_score(candles: list[Candle]) -> DynamicScoreSignal | None:
    if len(candles) < 220:
        return None

    signals = [
        _trend_continuation(candles),
        _breakout_continuation(candles),
        _range_reversal(candles),
    ]
    valid = [signal for signal in signals if signal is not None]
    if not valid:
        return None
    return sorted(valid, key=lambda signal: signal.score, reverse=True)[0]


def detect_regime(candles: list[Candle]) -> str:
    """Classify the current market regime from M15 candles.

    Returns one of: "trending_up", "trending_down", "ranging",
    "high_volatility", "unclear". A volatility spike (recent ATR far above its
    own baseline) is flagged first because zone/range behaviour is unreliable
    around it.
    """
    if len(candles) < 220:
        return "unclear"
    avg_range = average_range(candles, period=30)
    if avg_range <= 0:
        return "unclear"

    recent_vol = average_range(candles[-14:], period=14)
    baseline_vol = average_range(candles[-64:-14], period=50)
    if baseline_vol > 0 and recent_vol / baseline_vol >= HIGH_VOLATILITY_RATIO:
        return "high_volatility"

    closes = [candle.close for candle in candles]
    ema50 = _ema_values(closes, 50)
    ema200 = _ema_values(closes, 200)
    if not ema50 or not ema200:
        return "unclear"

    latest = candles[-1]
    efficiency = _directional_efficiency(candles, lookback=96)
    sample = candles[-96:]
    width_atr = (max(candle.high for candle in sample) - min(candle.low for candle in sample)) / avg_range

    if efficiency >= TREND_EFFICIENCY_MIN:
        if latest.close > ema50[-1] > ema200[-1]:
            return "trending_up"
        if latest.close < ema50[-1] < ema200[-1]:
            return "trending_down"
    if efficiency <= RANGE_EFFICIENCY_MAX and RANGE_MIN_WIDTH_ATR <= width_atr <= RANGE_MAX_WIDTH_ATR:
        return "ranging"
    return "unclear"


def best_regime_range_signal(candles: list[Candle]) -> DynamicScoreSignal | None:
    """Range-reversal signal that only fires when the regime is ranging.

    In a trend or a volatility spike this returns None, so the route never
    fades a strong trend or trades through unreliable conditions.
    """
    if detect_regime(candles) != "ranging":
        return None
    return _range_reversal(candles)


def _directional_efficiency(candles: list[Candle], lookback: int) -> float:
    sample = candles[-lookback:] if len(candles) > lookback else candles
    if len(sample) < 2:
        return 0.0
    path = sum(candle.high - candle.low for candle in sample)
    if path <= 0:
        return 0.0
    return abs(sample[-1].close - sample[0].open) / path


def _trend_continuation(candles: list[Candle]) -> DynamicScoreSignal | None:
    latest = candles[-1]
    ema50_values = _ema_values([candle.close for candle in candles], 50)
    ema200_values = _ema_values([candle.close for candle in candles], 200)
    if len(ema50_values) < 6 or len(ema200_values) < 6:
        return None

    ema50 = ema50_values[-1]
    ema200 = ema200_values[-1]
    ema50_slope = ema50_values[-1] - ema50_values[-6]
    ema200_slope = ema200_values[-1] - ema200_values[-6]
    avg_range = average_range(candles, period=30)
    if avg_range <= 0:
        return None

    side = "BUY" if latest.close > ema50 > ema200 else "SELL" if latest.close < ema50 < ema200 else ""
    if not side:
        return None

    factors: list[ScoreFactor] = []
    if side == "BUY":
        _add(factors, 1.7 if ema50_slope > 0 and ema200_slope > 0 else 0.7, "trend_alignment", "Price and EMAs align bullish.")
        pullback_depth = max(0.0, (ema50 - min(candle.low for candle in candles[-12:])) / avg_range)
        _add(factors, min(1.2, pullback_depth * 0.45), "pullback_quality", "Recent pullback held near trend mean.")
        candle_points = _bullish_candle_points(latest, avg_range)
    else:
        _add(factors, 1.7 if ema50_slope < 0 and ema200_slope < 0 else 0.7, "trend_alignment", "Price and EMAs align bearish.")
        pullback_depth = max(0.0, (max(candle.high for candle in candles[-12:]) - ema50) / avg_range)
        _add(factors, min(1.2, pullback_depth * 0.45), "pullback_quality", "Recent pullback held near trend mean.")
        candle_points = _bearish_candle_points(latest, avg_range)

    rsi = _rsi([candle.close for candle in candles], 14)
    if rsi is not None:
        if side == "BUY":
            _add(factors, 1.0 if 45 <= rsi <= 68 else 0.3, "momentum", f"RSI momentum is {rsi:.1f}.")
        else:
            _add(factors, 1.0 if 32 <= rsi <= 55 else 0.3, "momentum", f"RSI momentum is {rsi:.1f}.")
    _add(factors, candle_points, "candle_quality", "Latest candle supports continuation.")
    _add(factors, _volatility_points(candles), "volatility", "Recent volatility is usable.")
    _add(factors, _structure_points(candles, side), "structure", "Recent swings support the direction.")

    return _signal_from_factors("trend_continuation", side, candles, factors)


def _breakout_continuation(candles: list[Candle]) -> DynamicScoreSignal | None:
    latest = candles[-1]
    avg_range = average_range(candles, period=30)
    if avg_range <= 0:
        return None
    prior = candles[-25:-1]
    range_high = max(candle.high for candle in prior)
    range_low = min(candle.low for candle in prior)
    range_width = range_high - range_low
    if range_width <= 0:
        return None

    side = "BUY" if latest.close > range_high else "SELL" if latest.close < range_low else ""
    if not side:
        return None

    factors: list[ScoreFactor] = []
    compression = max(0.0, 1.4 - range_width / max(avg_range, 1e-12))
    _add(factors, min(1.4, compression), "compression", "Prior range was compressed enough for expansion.")
    breakout_distance = (latest.close - range_high) if side == "BUY" else (range_low - latest.close)
    _add(factors, min(1.7, breakout_distance / avg_range), "breakout_close", "Latest close broke the range.")
    _add(factors, _bullish_candle_points(latest, avg_range) if side == "BUY" else _bearish_candle_points(latest, avg_range), "candle_quality", "Breakout candle has directional body.")
    _add(factors, _volume_points(candles), "tick_volume", "Tick volume expanded versus recent candles.")
    _add(factors, _volatility_points(candles), "volatility", "Volatility supports breakout follow-through.")

    return _signal_from_factors("breakout_continuation", side, candles, factors)


def _range_reversal(candles: list[Candle]) -> DynamicScoreSignal | None:
    latest = candles[-1]
    avg_range = average_range(candles, period=30)
    if avg_range <= 0:
        return None
    sample = candles[-96:]
    range_high = max(candle.high for candle in sample)
    range_low = min(candle.low for candle in sample)
    active_range = range_high - range_low
    if active_range <= 0:
        return None

    position = (latest.close - range_low) / active_range
    side = "BUY" if position <= 0.18 else "SELL" if position >= 0.82 else ""
    if not side:
        return None

    factors: list[ScoreFactor] = []
    edge_points = (0.18 - position) / 0.18 if side == "BUY" else (position - 0.82) / 0.18
    _add(factors, min(1.5, max(0.3, edge_points * 1.5)), "range_edge", "Price is near an active range edge.")
    midpoint = (range_high + range_low) / 2
    room_to_midpoint = abs(midpoint - latest.close) / avg_range
    _add(factors, min(1.2, room_to_midpoint / 3), "mean_reversion_room", "There is room back toward the range midpoint.")
    wick_points = _lower_wick_points(latest) if side == "BUY" else _upper_wick_points(latest)
    _add(factors, wick_points, "rejection", "Latest candle shows edge rejection.")
    rsi = _rsi([candle.close for candle in candles], 14)
    if rsi is not None:
        if side == "BUY":
            _add(factors, 1.0 if rsi <= 38 else 0.2, "exhaustion", f"RSI is {rsi:.1f} near lower exhaustion.")
        else:
            _add(factors, 1.0 if rsi >= 62 else 0.2, "exhaustion", f"RSI is {rsi:.1f} near upper exhaustion.")
    _add(factors, _volatility_points(candles), "volatility", "Volatility is sufficient for mean-reversion attempt.")
    _add(factors, _structure_points(candles, side), "structure", "Recent swings do not fight the reversal.")

    return _signal_from_factors("range_reversal", side, candles, factors)


def _signal_from_factors(
    strategy: str,
    side: str,
    candles: list[Candle],
    factors: list[ScoreFactor],
) -> DynamicScoreSignal:
    latest = candles[-1]
    avg_range = average_range(candles, period=30)
    score = min(10.0, round(sum(max(0.0, factor.points) for factor in factors), 2))
    half_width = avg_range * 0.25
    if side == "BUY":
        entry_low = latest.close - half_width
        entry_high = latest.close + half_width
        stop_reference = min(candle.low for candle in candles[-10:])
    else:
        entry_low = latest.close - half_width
        entry_high = latest.close + half_width
        stop_reference = max(candle.high for candle in candles[-10:])
    return DynamicScoreSignal(
        strategy=strategy,
        side=side,
        score=score,
        entry_low=min(entry_low, entry_high),
        entry_high=max(entry_low, entry_high),
        stop_reference=stop_reference,
        factors=tuple(factors),
    )


def _add(factors: list[ScoreFactor], points: float, name: str, note: str) -> None:
    factors.append(ScoreFactor(name=name, points=round(max(0.0, points), 2), note=note))


def _ema_values(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    multiplier = 2 / (period + 1)
    ema = sum(values[:period]) / period
    result = [ema]
    for value in values[period:]:
        ema = (value - ema) * multiplier + ema
        result.append(ema)
    return result


def _rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for current, previous in zip(values[-period:], values[-period - 1 : -1]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    relative_strength = avg_gain / avg_loss
    return 100 - (100 / (1 + relative_strength))


def _bullish_candle_points(candle: Candle, avg_range: float) -> float:
    if avg_range <= 0 or not candle.bullish:
        return 0.1
    body = candle.close - candle.open
    return min(1.2, body / avg_range)


def _bearish_candle_points(candle: Candle, avg_range: float) -> float:
    if avg_range <= 0 or not candle.bearish:
        return 0.1
    body = candle.open - candle.close
    return min(1.2, body / avg_range)


def _upper_wick_points(candle: Candle) -> float:
    candle_range = candle.high - candle.low
    if candle_range <= 0:
        return 0.0
    return min(1.2, (candle.high - max(candle.open, candle.close)) / candle_range * 1.6)


def _lower_wick_points(candle: Candle) -> float:
    candle_range = candle.high - candle.low
    if candle_range <= 0:
        return 0.0
    return min(1.2, (min(candle.open, candle.close) - candle.low) / candle_range * 1.6)


def _volatility_points(candles: list[Candle]) -> float:
    recent = average_range(candles, period=14)
    baseline = average_range(candles[:-14], period=50) if len(candles) > 64 else average_range(candles, period=50)
    if baseline <= 0:
        return 0.0
    ratio = recent / baseline
    if 0.8 <= ratio <= 1.8:
        return 0.9
    if 0.55 <= ratio <= 2.3:
        return 0.45
    return 0.1


def _volume_points(candles: list[Candle]) -> float:
    if len(candles) < 31:
        return 0.0
    latest = candles[-1].volume
    baseline = sum(candle.volume for candle in candles[-31:-1]) / 30
    if baseline <= 0:
        return 0.0
    ratio = latest / baseline
    if ratio >= 1.4:
        return 1.0
    if ratio >= 1.1:
        return 0.5
    return 0.1


def _structure_points(candles: list[Candle], side: str) -> float:
    swings = detect_swings(candles[-120:], window=2)
    highs = [swing for swing in swings if swing.kind == "high"]
    lows = [swing for swing in swings if swing.kind == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return 0.4
    higher_high = highs[-1].price > highs[-2].price
    higher_low = lows[-1].price > lows[-2].price
    lower_high = highs[-1].price < highs[-2].price
    lower_low = lows[-1].price < lows[-2].price
    if side == "BUY" and (higher_high or higher_low):
        return 1.0
    if side == "SELL" and (lower_high or lower_low):
        return 1.0
    return 0.25

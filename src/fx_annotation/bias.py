from dataclasses import dataclass

from fx_annotation.candles import Candle
from fx_annotation.structure import SwingPoint, detect_swings


@dataclass(frozen=True)
class Bias:
    direction: str
    reason: str


def detect_bias(candles: list[Candle], swing_window: int = 2) -> Bias:
    swings = detect_swings(candles, window=swing_window)
    return _with_ma200_context(detect_bias_from_swings(swings), candles)


def detect_bias_from_swings(swings: list[SwingPoint]) -> Bias:
    highs = [swing for swing in swings if swing.kind == "high"]
    lows = [swing for swing in swings if swing.kind == "low"]

    if len(highs) < 2 or len(lows) < 2:
        return Bias(
            direction="neutral",
            reason="Not enough higher-timeframe swing points to define bias.",
        )

    previous_high, latest_high = highs[-2], highs[-1]
    previous_low, latest_low = lows[-2], lows[-1]

    higher_high = latest_high.price > previous_high.price
    higher_low = latest_low.price > previous_low.price
    lower_high = latest_high.price < previous_high.price
    lower_low = latest_low.price < previous_low.price

    if higher_high and higher_low:
        return Bias(
            direction="bullish",
            reason="Latest higher-timeframe swing high and swing low are both higher.",
        )

    if lower_high and lower_low:
        return Bias(
            direction="bearish",
            reason="Latest higher-timeframe swing high and swing low are both lower.",
        )

    return Bias(
        direction="neutral",
        reason="Higher-timeframe swings are mixed, so bias is unclear.",
    )


def _with_ma200_context(bias: Bias, candles: list[Candle]) -> Bias:
    if len(candles) < 200:
        return bias

    latest_close = candles[-1].close
    ma200 = sum(candle.close for candle in candles[-200:]) / 200
    avg_range = _average_range(candles[-30:])
    distance = abs(latest_close - ma200)
    if avg_range > 0 and distance < avg_range * 0.2:
        ma_context = f"Price is near the 200-period moving average ({ma200:.5f}), so MA trend context is neutral."
        return Bias(direction=bias.direction, reason=f"{bias.reason} {ma_context}")

    ma_direction = "bullish" if latest_close > ma200 else "bearish"
    ma_context = (
        f"Price is {'above' if ma_direction == 'bullish' else 'below'} "
        f"the 200-period moving average ({ma200:.5f}), supporting {ma_direction} context."
    )

    if bias.direction == "neutral":
        return Bias(
            direction="neutral",
            reason=f"{bias.reason} {ma_context}",
        )

    if bias.direction != ma_direction:
        return Bias(
            direction=bias.direction,
            reason=f"{bias.reason} However, {ma_context}",
        )

    return Bias(direction=bias.direction, reason=f"{bias.reason} {ma_context}")


def _average_range(candles: list[Candle]) -> float:
    if not candles:
        return 0.0
    return sum(candle.high - candle.low for candle in candles) / len(candles)

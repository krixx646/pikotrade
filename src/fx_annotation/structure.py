from dataclasses import dataclass

from fx_annotation.candles import Candle


@dataclass(frozen=True)
class SwingPoint:
    index: int
    price: float
    kind: str


@dataclass(frozen=True)
class Sweep:
    index: int
    swept_index: int
    swept_price: float
    kind: str


@dataclass(frozen=True)
class BreakOfStructure:
    index: int
    broken_index: int
    broken_price: float
    direction: str


@dataclass(frozen=True)
class FairValueGap:
    index: int
    low: float
    high: float
    direction: str


def average_range(candles: list[Candle], period: int = 20) -> float:
    sample = candles[-period:] if len(candles) > period else candles
    if not sample:
        return 0.0
    return sum(candle.high - candle.low for candle in sample) / len(sample)


def detect_swings(
    candles: list[Candle],
    window: int = 2,
    min_prominence: float | None = None,
) -> list[SwingPoint]:
    swings: list[SwingPoint] = []
    prominence = (
        min_prominence
        if min_prominence is not None
        else average_range(candles, period=30) * 0.35
    )

    for index in range(window, len(candles) - window):
        candle = candles[index]
        left = candles[index - window : index]
        right = candles[index + 1 : index + window + 1]
        nearby = left + right

        high_prominence = candle.high - max(other.high for other in nearby)
        low_prominence = min(other.low for other in nearby) - candle.low

        if all(candle.high > other.high for other in nearby) and high_prominence >= prominence:
            swings.append(SwingPoint(index=index, price=candle.high, kind="high"))

        if all(candle.low < other.low for other in nearby) and low_prominence >= prominence:
            swings.append(SwingPoint(index=index, price=candle.low, kind="low"))

    return swings


def detect_liquidity_sweeps(
    candles: list[Candle],
    swings: list[SwingPoint],
    lookback_swings: int = 6,
    min_rejection_ratio: float = 0.25,
) -> list[Sweep]:
    sweeps: list[Sweep] = []

    for index, candle in enumerate(candles):
        prior_swings = [swing for swing in swings if swing.index < index]
        candle_sweeps: list[tuple[float, Sweep]] = []

        for swing in prior_swings[-lookback_swings:]:
            candle_range = candle.high - candle.low
            if candle_range <= 0:
                continue

            if swing.kind == "high" and candle.high > swing.price and candle.close < swing.price:
                rejection = (candle.high - candle.close) / candle_range
                if rejection >= min_rejection_ratio:
                    distance = candle.high - swing.price
                    candle_sweeps.append(
                        (
                            distance,
                            Sweep(
                                index=index,
                                swept_index=swing.index,
                                swept_price=swing.price,
                                kind="buy_side_liquidity",
                            ),
                        )
                    )

            if swing.kind == "low" and candle.low < swing.price and candle.close > swing.price:
                rejection = (candle.close - candle.low) / candle_range
                if rejection >= min_rejection_ratio:
                    distance = swing.price - candle.low
                    candle_sweeps.append(
                        (
                            distance,
                            Sweep(
                                index=index,
                                swept_index=swing.index,
                                swept_price=swing.price,
                                kind="sell_side_liquidity",
                            ),
                        )
                    )

        sweeps.extend(_dedupe_candle_sweeps(candle_sweeps))

    return sweeps


def _dedupe_candle_sweeps(candle_sweeps: list[tuple[float, Sweep]]) -> list[Sweep]:
    strongest_by_kind: dict[str, tuple[float, Sweep]] = {}

    for distance, sweep in candle_sweeps:
        current = strongest_by_kind.get(sweep.kind)
        if current is None or distance > current[0]:
            strongest_by_kind[sweep.kind] = (distance, sweep)

    return [value[1] for value in strongest_by_kind.values()]


def detect_fair_value_gaps(
    candles: list[Candle],
    min_size_ratio: float = 0.25,
) -> list[FairValueGap]:
    gaps: list[FairValueGap] = []
    reference_range = average_range(candles, period=30)

    for index in range(2, len(candles)):
        left = candles[index - 2]
        middle = candles[index - 1]
        right = candles[index]

        if left.high < right.low:
            low = left.high
            high = right.low
            if _gap_is_meaningful(high - low, reference_range, middle, min_size_ratio):
                gaps.append(FairValueGap(index=index, low=low, high=high, direction="bullish"))

        if left.low > right.high:
            low = right.high
            high = left.low
            if _gap_is_meaningful(high - low, reference_range, middle, min_size_ratio):
                gaps.append(FairValueGap(index=index, low=low, high=high, direction="bearish"))

    return gaps


def _gap_is_meaningful(
    gap_size: float,
    reference_range: float,
    middle_candle: Candle,
    min_size_ratio: float = 0.25,
) -> bool:
    if reference_range <= 0:
        return gap_size > 0
    displacement = middle_candle.high - middle_candle.low
    return gap_size >= reference_range * min_size_ratio and displacement >= reference_range


def detect_break_after_sweep(
    candles: list[Candle],
    swings: list[SwingPoint],
    sweep: Sweep,
    max_bars_after: int = 30,
) -> BreakOfStructure | None:
    if sweep.kind == "sell_side_liquidity":
        target_kind = "high"
        direction = "bullish"
    else:
        target_kind = "low"
        direction = "bearish"

    structure_points = [
        swing
        for swing in swings
        if swing.kind == target_kind and swing.index < sweep.index
    ]
    if not structure_points:
        return None

    structure = structure_points[-1]
    end_index = min(len(candles), sweep.index + max_bars_after + 1)

    for index in range(sweep.index + 1, end_index):
        candle = candles[index]
        if direction == "bullish" and candle.close > structure.price:
            return BreakOfStructure(
                index=index,
                broken_index=structure.index,
                broken_price=structure.price,
                direction=direction,
            )
        if direction == "bearish" and candle.close < structure.price:
            return BreakOfStructure(
                index=index,
                broken_index=structure.index,
                broken_price=structure.price,
                direction=direction,
            )

    return None

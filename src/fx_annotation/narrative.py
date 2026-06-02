from dataclasses import dataclass
from datetime import datetime

from fx_annotation.candles import Candle
from fx_annotation.poi import ZoneLadderItem
from fx_annotation.structure import SwingPoint, average_range, detect_swings


@dataclass(frozen=True)
class LiquidityPool:
    price: float
    kind: str
    reason: str


@dataclass(frozen=True)
class HtfNarrative:
    timeframe: str
    direction: str
    phase: str
    highest_high: float
    highest_high_index: int
    lowest_low: float
    lowest_low_index: int
    active_from_index: int
    active_from_time: datetime
    active_from_anchor: str
    range_low: float
    range_high: float
    zones_inside_range: tuple[str, ...]
    last_line_of_defense: str
    liquidity_pools: tuple[LiquidityPool, ...]
    summary: str


def build_htf_narrative(
    candles: list[Candle],
    zones: list[ZoneLadderItem],
    direction: str,
    timeframe: str = "H4",
    lookback: int | None = None,
) -> HtfNarrative | None:
    visible = candles[-lookback:] if lookback is not None and len(candles) > lookback else candles
    if not visible:
        return None

    offset = len(candles) - len(visible)
    highest_swing, lowest_swing = _range_anchor_swings(candles, visible, offset)
    highest_index = highest_swing.index
    lowest_index = lowest_swing.index
    highest_high = highest_swing.price
    lowest_low = lowest_swing.price
    latest_close = visible[-1].close
    active_from_index = min(highest_index, lowest_index)
    active_from_anchor = "highest_high" if highest_index < lowest_index else "lowest_low"
    active_local_start = max(0, active_from_index - offset)
    active_visible = visible[active_local_start:]
    active_offset = offset + active_local_start

    range_low = min(lowest_low, highest_high)
    range_high = max(lowest_low, highest_high)
    zones_inside = tuple(
        _zone_key(zone)
        for zone in zones
        if _zone_inside_range(zone, range_low, range_high) and _zone_is_valid_context(zone)
    )
    last_line = _last_line_of_defense(zones, direction, range_low, range_high)
    pools = _liquidity_pools(active_visible, active_offset, highest_high, highest_index, lowest_low, lowest_index)
    phase = _phase(direction, latest_close, range_low, range_high, highest_index, lowest_index)

    return HtfNarrative(
        timeframe=timeframe,
        direction=direction,
        phase=phase,
        highest_high=highest_high,
        highest_high_index=highest_index,
        lowest_low=lowest_low,
        lowest_low_index=lowest_index,
        active_from_index=active_from_index,
        active_from_time=candles[active_from_index].time,
        active_from_anchor=active_from_anchor,
        range_low=range_low,
        range_high=range_high,
        zones_inside_range=zones_inside,
        last_line_of_defense=last_line,
        liquidity_pools=tuple(pools),
        summary=(
            f"{timeframe} narrative: {direction} / {phase}. "
            f"Active range {range_low:.5f}-{range_high:.5f}; "
            f"HH {highest_high:.5f}, LL {lowest_low:.5f}. "
            f"Active story starts at {active_from_anchor}."
        ),
    )


def narrative_summary(narrative: HtfNarrative | None) -> str:
    if narrative is None:
        return "No HTF narrative available."
    pools = ", ".join(
        f"{pool.kind} {pool.price:.5f}" for pool in narrative.liquidity_pools[:4]
    )
    if not pools:
        pools = "none"
    return (
        f"{narrative.summary} Last line of defense: "
        f"{narrative.last_line_of_defense or 'none'}. "
        f"Active from {narrative.active_from_time.isoformat()}. Liquidity pools: {pools}."
    )


def _range_anchor_swings(
    candles: list[Candle],
    visible: list[Candle],
    offset: int,
) -> tuple[SwingPoint, SwingPoint]:
    swings = [
        swing
        for swing in detect_swings(candles, window=2)
        if offset <= swing.index < offset + len(visible)
    ]
    highs = [swing for swing in swings if swing.kind == "high"]
    lows = [swing for swing in swings if swing.kind == "low"]

    if highs:
        highest = max(highs, key=lambda swing: swing.price)
    else:
        local_index = max(range(len(visible)), key=lambda index: visible[index].high)
        highest = SwingPoint(index=offset + local_index, price=visible[local_index].high, kind="high")

    if lows:
        lowest = min(lows, key=lambda swing: swing.price)
    else:
        local_index = min(range(len(visible)), key=lambda index: visible[index].low)
        lowest = SwingPoint(index=offset + local_index, price=visible[local_index].low, kind="low")

    return highest, lowest


def _zone_inside_range(zone: ZoneLadderItem, range_low: float, range_high: float) -> bool:
    return zone.low <= range_high and zone.high >= range_low


def _last_line_of_defense(
    zones: list[ZoneLadderItem],
    direction: str,
    range_low: float,
    range_high: float,
) -> str:
    inside = [
        zone
        for zone in zones
        if _zone_inside_range(zone, range_low, range_high) and _zone_is_valid_context(zone)
    ]
    if direction == "bullish":
        demand = [zone for zone in inside if zone.side == "demand"]
        if demand:
            zone = sorted(demand, key=lambda item: item.low)[0]
            return _zone_key(zone)
    if direction == "bearish":
        supply = [zone for zone in inside if zone.side == "supply"]
        if supply:
            zone = sorted(supply, key=lambda item: item.high, reverse=True)[0]
            return _zone_key(zone)
    return ""


def _liquidity_pools(
    candles: list[Candle],
    offset: int,
    highest_high: float,
    highest_index: int,
    lowest_low: float,
    lowest_index: int,
) -> list[LiquidityPool]:
    pools = [
        LiquidityPool(
            price=highest_high,
            kind="highest_high",
            reason="Highest high in active HTF range; buy-side liquidity rests above it.",
        ),
        LiquidityPool(
            price=lowest_low,
            kind="lowest_low",
            reason="Lowest low in active HTF range; sell-side liquidity rests below it.",
        ),
    ]
    tolerance = average_range(candles, period=20) * 0.35
    pools.extend(_equal_level_pools(candles, offset, "high", tolerance, highest_index, lowest_index))
    pools.extend(_equal_level_pools(candles, offset, "low", tolerance, highest_index, lowest_index))
    return pools[:8]


def _equal_level_pools(
    candles: list[Candle],
    offset: int,
    kind: str,
    tolerance: float,
    highest_index: int,
    lowest_index: int,
) -> list[LiquidityPool]:
    if tolerance <= 0:
        return []

    values = [
        (offset + index, candle.high if kind == "high" else candle.low)
        for index, candle in enumerate(candles)
    ]
    pools: list[LiquidityPool] = []
    used: set[int] = {highest_index, lowest_index}
    for index, price in values:
        if index in used:
            continue
        neighbors = [
            other_price
            for other_index, other_price in values
            if other_index != index and other_index not in used and abs(other_price - price) <= tolerance
        ]
        if not neighbors:
            continue
        used.add(index)
        pool_kind = "equal_highs" if kind == "high" else "equal_lows"
        reason = (
            "Equal highs / double-top style liquidity."
            if kind == "high"
            else "Equal lows / double-bottom style liquidity."
        )
        pools.append(LiquidityPool(price=price, kind=pool_kind, reason=reason))
        if len(pools) >= 3:
            break
    return pools


def _phase(
    direction: str,
    latest_close: float,
    range_low: float,
    range_high: float,
    highest_index: int,
    lowest_index: int,
) -> str:
    if range_high <= range_low:
        return "unknown"
    position = (latest_close - range_low) / (range_high - range_low)
    if direction == "bullish":
        if highest_index > lowest_index and position >= 0.65:
            return "continuation_near_high"
        return "pullback_into_range"
    if direction == "bearish":
        if lowest_index > highest_index and position <= 0.35:
            return "continuation_near_low"
        return "pullback_into_range"
    if position >= 0.65:
        return "upper_range_reaction"
    if position <= 0.35:
        return "lower_range_reaction"
    return "middle_of_range"


def _zone_key(zone: ZoneLadderItem) -> str:
    return f"{zone.timeframe} {zone.side} {zone.low:.5f}-{zone.high:.5f} {zone.state}"


def _zone_is_valid_context(zone: ZoneLadderItem) -> bool:
    return zone.state not in {"failed", "below_untouched", "above_untouched"}

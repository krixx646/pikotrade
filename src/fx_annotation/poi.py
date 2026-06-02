from dataclasses import dataclass
from datetime import datetime

from fx_annotation.candles import Candle
from fx_annotation.structure import average_range


MAX_BASE_CANDLES = 3


@dataclass(frozen=True)
class PointOfInterest:
    low: float
    high: float
    side: str
    source: str
    index: int
    candle_time: datetime
    touched_now: bool
    distance_to_price: float


@dataclass(frozen=True)
class ZoneLadderItem:
    low: float
    high: float
    side: str
    timeframe: str
    source: str
    index: int
    candle_time: datetime
    state: str
    distance_to_price: float
    reason: str


def detect_htf_pois(
    candles: list[Candle],
    current_price: float,
    limit: int = 8,
    active_from_time: datetime | None = None,
    bias_direction: str = "neutral",
    timeframe: str = "HTF",
) -> list[PointOfInterest]:
    focused_sides = _focused_sides(bias_direction)
    if not focused_sides:
        return []
    candidates = [
        _poi_from_zone(zone, current_price)
        for zone in _order_block_zones(
            candles=candles,
            current_price=current_price,
            timeframe=timeframe,
            focused_sides=focused_sides,
            active_from_time=active_from_time,
        )
        if _zone_is_valid_for_action(zone)
    ]

    return sorted(candidates, key=lambda poi: (not poi.touched_now, poi.distance_to_price))[:limit]


def detect_zone_ladder(
    h4_candles: list[Candle],
    h1_candles: list[Candle],
    current_price: float,
    bias_direction: str,
    limit: int = 10,
    active_from_time: datetime | None = None,
) -> list[ZoneLadderItem]:
    focused_sides = _focused_sides(bias_direction)
    h4_zones = _ladder_from_candles(
        h4_candles,
        current_price=current_price,
        timeframe="H4",
        focused_sides=focused_sides,
        active_from_time=active_from_time,
    )
    h1_zones = _ladder_from_candles(
        h1_candles,
        current_price=current_price,
        timeframe="H1",
        focused_sides=focused_sides,
        active_from_time=active_from_time,
    )

    combined = h4_zones + h1_zones
    return _limit_zones_preserving_origin(combined, limit)


def detect_timeframe_zones(
    candles: list[Candle],
    current_price: float,
    timeframe: str,
    bias_direction: str,
    limit: int = 10,
    active_from_time: datetime | None = None,
) -> list[ZoneLadderItem]:
    focused_sides = _focused_sides(bias_direction)
    if not focused_sides:
        return []
    zones = _order_block_zones(
        candles=candles,
        current_price=current_price,
        timeframe=timeframe,
        focused_sides=focused_sides,
        active_from_time=active_from_time,
    )
    return _limit_zones_preserving_origin(zones, limit)


def nearest_relevant_poi(
    pois: list[PointOfInterest],
    side: str,
    current_price: float | None = None,
) -> PointOfInterest | None:
    desired_side = "demand" if side == "buy" else "supply"
    matching = [poi for poi in pois if poi.side == desired_side]
    if current_price is not None:
        if side == "buy":
            matching = [poi for poi in matching if poi.low <= current_price]
        if side == "sell":
            matching = [poi for poi in matching if poi.high >= current_price]
    if not matching:
        return None
    return sorted(matching, key=lambda poi: (not poi.touched_now, poi.distance_to_price))[0]


def poi_summary(poi: PointOfInterest | None) -> str:
    if poi is None:
        return "No relevant HTF POI detected."
    touch = "price is inside/mitigating" if poi.touched_now else f"distance {poi.distance_to_price:.5f}"
    return f"{poi.side} {poi.low:.5f}-{poi.high:.5f} ({poi.source}, {touch})"


def latest_poi_touch_index(
    candles: list[Candle],
    poi: PointOfInterest,
    end_index: int | None = None,
) -> int | None:
    last_index = len(candles) - 1 if end_index is None else min(end_index, len(candles) - 1)
    for index in range(last_index, -1, -1):
        candle = candles[index]
        if candle.low <= poi.high and candle.high >= poi.low:
            return index
    return None


def zone_ladder_summary(zones: list[ZoneLadderItem], limit: int = 5) -> str:
    if not zones:
        return "No HTF/1H zone ladder detected."
    return "; ".join(
        f"{zone.timeframe} {zone.side} {zone.low:.5f}-{zone.high:.5f} {zone.state}"
        for zone in zones[:limit]
    )


def _poi_from_zone(zone: ZoneLadderItem, current_price: float) -> PointOfInterest:
    return PointOfInterest(
        low=zone.low,
        high=zone.high,
        side=zone.side,
        source=zone.source,
        index=zone.index,
        candle_time=zone.candle_time,
        touched_now=zone.low <= current_price <= zone.high,
        distance_to_price=zone.distance_to_price,
    )


def _ladder_from_candles(
    candles: list[Candle],
    current_price: float,
    timeframe: str,
    focused_sides: set[str],
    active_from_time: datetime | None,
) -> list[ZoneLadderItem]:
    return _order_block_zones(
        candles=candles,
        current_price=current_price,
        timeframe=timeframe,
        focused_sides=focused_sides,
        active_from_time=active_from_time,
    )


def _order_block_zones(
    candles: list[Candle],
    current_price: float,
    timeframe: str,
    focused_sides: set[str],
    active_from_time: datetime | None,
) -> list[ZoneLadderItem]:
    if len(candles) < 8:
        return []

    reference_range = average_range(candles, period=30)
    zones: list[ZoneLadderItem] = []
    seen: set[tuple[int, str]] = set()
    zones.extend(
        _active_anchor_origin_zones(
            candles=candles,
            current_price=current_price,
            timeframe=timeframe,
            focused_sides=focused_sides,
            active_from_time=active_from_time,
            reference_range=reference_range,
        )
    )

    for index in range(6, len(candles)):
        if _bullish_displacement(candles, index, reference_range):
            base = _base_before_displacement(
                candles=candles,
                displacement_index=index,
                side="demand",
                reference_range=reference_range,
            )
            if base is not None:
                zone = _zone_from_base(
                    candles=candles,
                    base=base,
                    side="demand",
                    timeframe=timeframe,
                    current_price=current_price,
                    source=f"{timeframe} demand base before bullish displacement at candle {index}",
                )
                if zone.side in focused_sides and _zone_is_active(zone, active_from_time):
                    key = (zone.index, zone.side)
                    if key not in seen:
                        seen.add(key)
                        zones.append(zone)

        if _bearish_displacement(candles, index, reference_range):
            base = _base_before_displacement(
                candles=candles,
                displacement_index=index,
                side="supply",
                reference_range=reference_range,
            )
            if base is not None:
                zone = _zone_from_base(
                    candles=candles,
                    base=base,
                    side="supply",
                    timeframe=timeframe,
                    current_price=current_price,
                    source=f"{timeframe} supply base before bearish displacement at candle {index}",
                )
                if zone.side in focused_sides and _zone_is_active(zone, active_from_time):
                    key = (zone.index, zone.side)
                    if key not in seen:
                        seen.add(key)
                        zones.append(zone)

    return _dedupe_overlapping_zones(zones)


def _active_anchor_origin_zones(
    candles: list[Candle],
    current_price: float,
    timeframe: str,
    focused_sides: set[str],
    active_from_time: datetime | None,
    reference_range: float,
) -> list[ZoneLadderItem]:
    anchor_index = _active_anchor_index(candles, active_from_time)
    if anchor_index is None:
        return []

    zones: list[ZoneLadderItem] = []
    local_window = candles[anchor_index : min(len(candles), anchor_index + 12)]
    if not local_window:
        return []

    anchor = candles[anchor_index]
    tolerance = max(reference_range * 0.55, (anchor.high - anchor.low) * 0.5)

    if "demand" in focused_sides and anchor.low <= min(candle.low for candle in local_window):
        base = _base_after_anchor(
            candles=candles,
            anchor_index=anchor_index,
            side="demand",
            tolerance=tolerance,
        )
        if base:
            zones.append(
                _zone_from_base(
                    candles=candles,
                    base=base,
                    side="demand",
                    timeframe=timeframe,
                    current_price=current_price,
                    source=f"{timeframe} demand origin base at active lowest low; sell-side liquidity rests below",
                )
            )

    if "supply" in focused_sides and anchor.high >= max(candle.high for candle in local_window):
        base = _base_after_anchor(
            candles=candles,
            anchor_index=anchor_index,
            side="supply",
            tolerance=tolerance,
        )
        if base:
            zones.append(
                _zone_from_base(
                    candles=candles,
                    base=base,
                    side="supply",
                    timeframe=timeframe,
                    current_price=current_price,
                    source=f"{timeframe} supply origin base at active highest high; buy-side liquidity rests above",
                )
            )

    return zones


def _zone_from_source_candle(
    candles: list[Candle],
    source_index: int,
    side: str,
    timeframe: str,
    current_price: float,
    displacement_index: int,
) -> ZoneLadderItem:
    source = candles[source_index]
    low = source.low
    high = source.high
    poi = PointOfInterest(
        low=low,
        high=high,
        side=side,
        source=(
            f"{timeframe} demand order block before bullish displacement"
            if side == "demand"
            else f"{timeframe} supply order block before bearish displacement"
        ),
        index=source_index,
        candle_time=source.time,
        touched_now=low <= current_price <= high,
        distance_to_price=_distance_to_zone(current_price, low, high),
    )
    avg_range = average_range(candles, period=20)
    latest_close = candles[-1].close if candles else 0.0
    state = _zone_state(candles, poi, avg_range)
    return ZoneLadderItem(
        low=low,
        high=high,
        side=side,
        timeframe=timeframe,
        source=f"{poi.source} at displacement candle {displacement_index}",
        index=source_index,
        candle_time=source.time,
        state=state,
        distance_to_price=poi.distance_to_price,
        reason=_zone_reason(poi, state, latest_close),
    )


def _zone_from_base(
    candles: list[Candle],
    base: tuple[int, int],
    side: str,
    timeframe: str,
    current_price: float,
    source: str,
) -> ZoneLadderItem:
    start, end = base
    base_candles = candles[start : end + 1]
    low = min(candle.low for candle in base_candles)
    high = max(candle.high for candle in base_candles)
    poi = PointOfInterest(
        low=low,
        high=high,
        side=side,
        source=source,
        index=start,
        candle_time=candles[start].time,
        touched_now=low <= current_price <= high,
        distance_to_price=_distance_to_zone(current_price, low, high),
    )
    avg_range = average_range(candles, period=20)
    latest_close = candles[-1].close if candles else 0.0
    state = _zone_state(candles, poi, avg_range)
    return ZoneLadderItem(
        low=low,
        high=high,
        side=side,
        timeframe=timeframe,
        source=source,
        index=start,
        candle_time=candles[start].time,
        state=state,
        distance_to_price=poi.distance_to_price,
        reason=_zone_reason(poi, state, latest_close),
    )


def _base_before_displacement(
    candles: list[Candle],
    displacement_index: int,
    side: str,
    reference_range: float,
    lookback: int = MAX_BASE_CANDLES,
) -> tuple[int, int] | None:
    if displacement_index <= 0:
        return None

    want_bearish = side == "demand"
    source_index = _last_opposing_candle_index(candles, displacement_index, want_bearish=want_bearish)
    if source_index is None:
        return None

    start = source_index
    end = source_index
    base_low = candles[source_index].low
    base_high = candles[source_index].high
    max_width = reference_range * 1.6 if reference_range > 0 else base_high - base_low

    for index in range(source_index - 1, max(-1, displacement_index - lookback - 1), -1):
        if end - index + 1 > MAX_BASE_CANDLES:
            break
        candidate = candles[index]
        new_low = min(base_low, candidate.low)
        new_high = max(base_high, candidate.high)
        if reference_range > 0 and new_high - new_low > max_width:
            break
        if not _base_candle_matches_side(candidate, side, reference_range):
            break
        start = index
        base_low = new_low
        base_high = new_high

    for index in range(source_index + 1, displacement_index):
        if index - start + 1 > MAX_BASE_CANDLES:
            break
        candidate = candles[index]
        new_low = min(base_low, candidate.low)
        new_high = max(base_high, candidate.high)
        if reference_range > 0 and new_high - new_low > max_width:
            break
        if not _base_candle_matches_side(candidate, side, reference_range):
            break
        end = index
        base_low = new_low
        base_high = new_high

    if not _base_has_valid_position(candles[start : end + 1], side):
        return None
    return (start, end)


def _base_candle_matches_side(candle: Candle, side: str, reference_range: float) -> bool:
    candle_range = candle.high - candle.low
    body = abs(candle.close - candle.open)
    if candle_range <= 0:
        return False
    if reference_range > 0 and candle_range > reference_range * 1.25:
        return False
    if body <= candle_range * 0.65:
        return True
    return candle.bearish if side == "demand" else candle.bullish


def _base_has_valid_position(base_candles: list[Candle], side: str) -> bool:
    if not base_candles:
        return False
    if side == "demand":
        return any(candle.bearish for candle in base_candles)
    return any(candle.bullish for candle in base_candles)


def _active_anchor_index(candles: list[Candle], active_from_time: datetime | None) -> int | None:
    if active_from_time is None:
        return None
    for index, candle in enumerate(candles):
        if candle.time == active_from_time:
            return index
    return None


def _base_after_anchor(
    candles: list[Candle],
    anchor_index: int,
    side: str,
    tolerance: float,
    max_base_candles: int = MAX_BASE_CANDLES,
) -> tuple[int, int] | None:
    anchor = candles[anchor_index]
    end = anchor_index
    for index in range(anchor_index + 1, min(len(candles), anchor_index + max_base_candles)):
        candle = candles[index]
        if side == "demand":
            if candle.low > anchor.low + tolerance:
                break
        else:
            if candle.high < anchor.high - tolerance:
                break
        end = index

    if end == anchor_index:
        return (anchor_index, anchor_index)
    return (anchor_index, end)


def _bullish_displacement(
    candles: list[Candle],
    index: int,
    reference_range: float,
) -> bool:
    candle = candles[index]
    if not candle.bullish:
        return False
    if not _has_displacement_body(candle, reference_range):
        return False
    recent_high = max(previous.high for previous in candles[max(0, index - 6) : index])
    closes_near_high = (candle.high - candle.close) <= (candle.high - candle.low) * 0.35
    return candle.close > recent_high and closes_near_high


def _bearish_displacement(
    candles: list[Candle],
    index: int,
    reference_range: float,
) -> bool:
    candle = candles[index]
    if not candle.bearish:
        return False
    if not _has_displacement_body(candle, reference_range):
        return False
    recent_low = min(previous.low for previous in candles[max(0, index - 6) : index])
    closes_near_low = (candle.close - candle.low) <= (candle.high - candle.low) * 0.35
    return candle.close < recent_low and closes_near_low


def _has_displacement_body(candle: Candle, reference_range: float) -> bool:
    candle_range = candle.high - candle.low
    body = abs(candle.close - candle.open)
    if candle_range <= 0:
        return False
    if reference_range <= 0:
        return body > 0
    return candle_range >= reference_range * 1.15 and body >= reference_range * 0.55


def _last_opposing_candle_index(
    candles: list[Candle],
    displacement_index: int,
    want_bearish: bool,
    lookback: int = 6,
) -> int | None:
    for index in range(displacement_index - 1, max(-1, displacement_index - lookback - 1), -1):
        candle = candles[index]
        if want_bearish and candle.bearish:
            return index
        if not want_bearish and candle.bullish:
            return index
    return None


def _zone_is_active(
    zone: ZoneLadderItem,
    active_from_time: datetime | None,
) -> bool:
    return active_from_time is None or zone.candle_time >= active_from_time


def _dedupe_overlapping_zones(zones: list[ZoneLadderItem]) -> list[ZoneLadderItem]:
    selected: list[ZoneLadderItem] = []
    for zone in sorted(zones, key=_zone_quality_sort_key):
        if any(_same_zone_area(zone, existing) for existing in selected):
            continue
        selected.append(zone)
    return sorted(selected, key=_zone_ladder_sort_key)


def _same_zone_area(first: ZoneLadderItem, second: ZoneLadderItem) -> bool:
    if first.side != second.side or first.timeframe != second.timeframe:
        return False
    overlap = min(first.high, second.high) - max(first.low, second.low)
    if overlap <= 0:
        return False
    first_width = max(first.high - first.low, 0.0)
    second_width = max(second.high - second.low, 0.0)
    smaller_width = min(first_width, second_width)
    return smaller_width > 0 and overlap / smaller_width >= 0.55


def _zone_quality_sort_key(zone: ZoneLadderItem) -> tuple[int, float, float]:
    state_ranks = {
        "inside": 0,
        "approaching": 1,
        "respected": 2,
        "untouched": 3,
        "failed": 4,
        "below_untouched": 5,
        "above_untouched": 5,
    }
    width = zone.high - zone.low
    return (state_ranks.get(zone.state, 9), zone.distance_to_price, width)


def _zone_state(
    candles: list[Candle],
    poi: PointOfInterest,
    avg_range: float,
) -> str:
    if not candles:
        return "unknown"
    latest = candles[-1]
    latest_close = latest.close
    touched = _latest_zone_retest_index(candles, poi)

    if _zone_invalidated(candles, poi, avg_range):
        return "failed"
    if poi.low <= latest_close <= poi.high:
        return "inside"
    if poi.side == "demand" and latest_close < poi.low:
        return "failed" if touched is not None else "below_untouched"
    if poi.side == "supply" and latest_close > poi.high:
        return "failed" if touched is not None else "above_untouched"
    if touched is not None and _reacted_from_zone(latest_close, poi, avg_range):
        return "respected"
    if avg_range > 0 and poi.distance_to_price <= avg_range * 2:
        return "approaching"
    return "untouched"


def _zone_invalidated(
    candles: list[Candle],
    poi: PointOfInterest,
    avg_range: float,
) -> bool:
    buffer = _invalidation_buffer(poi, avg_range)
    for candle in candles[poi.index + 1 :]:
        if poi.side == "demand" and candle.close < poi.low - buffer:
            return True
        if poi.side == "supply" and candle.close > poi.high + buffer:
            return True
    return False


def _invalidation_buffer(poi: PointOfInterest, avg_range: float) -> float:
    zone_width = max(poi.high - poi.low, 0.0)
    buffers = [zone_width * 0.03]
    if avg_range > 0:
        buffers.append(avg_range * 0.08)
    return max(buffers)


def _reacted_from_zone(
    latest_close: float,
    poi: PointOfInterest,
    avg_range: float,
) -> bool:
    if avg_range <= 0:
        return False
    if poi.side == "demand":
        return latest_close > poi.high and latest_close - poi.high >= avg_range * 0.5
    return latest_close < poi.low and poi.low - latest_close >= avg_range * 0.5


def _latest_zone_retest_index(candles: list[Candle], poi: PointOfInterest) -> int | None:
    for index in range(len(candles) - 1, poi.index, -1):
        candle = candles[index]
        if candle.low <= poi.high and candle.high >= poi.low:
            return index
    return None


def _zone_reason(poi: PointOfInterest, state: str, latest_close: float) -> str:
    if state == "failed":
        return f"Price decisively closed through this {poi.side} zone; discard it as a buy/sell zone."
    if state == "inside":
        return f"Price is currently testing this {poi.side} zone."
    if state == "respected":
        return f"Price reacted from this {poi.side} zone; watch 15M for sweep then Market Shift/BOS."
    if state == "approaching":
        return f"Price is approaching this {poi.side} zone."
    if state in {"below_untouched", "above_untouched"}:
        return f"Price is beyond this {poi.side} zone without a recorded test in the sampled candles."
    direction = "below" if latest_close > poi.high else "above"
    return f"Untouched {poi.side} zone {direction} current price."


def _focused_sides(bias_direction: str) -> set[str]:
    if bias_direction == "bullish":
        return {"demand"}
    if bias_direction == "bearish":
        return {"supply"}
    return set()


def _should_prefer_h1_refinement(
    h4_zones: list[ZoneLadderItem],
    h1_zones: list[ZoneLadderItem],
    h4_candles: list[Candle],
) -> bool:
    if not h1_zones:
        return False
    if not h4_zones:
        return True
    h4_avg_range = average_range(h4_candles, period=20)
    nearest_h4 = sorted(h4_zones, key=lambda zone: zone.distance_to_price)[0]
    h4_width = nearest_h4.high - nearest_h4.low
    return h4_avg_range > 0 and h4_width >= h4_avg_range * 1.5


def _zone_ladder_sort_key(zone: ZoneLadderItem) -> tuple[int, float, int]:
    state_ranks = {
        "inside": 0,
        "approaching": 1,
        "respected": 2,
        "untouched": 3,
        "failed": 4,
        "below_untouched": 5,
        "above_untouched": 5,
    }
    timeframe_rank = 0 if zone.timeframe == "H1" else 1
    return (state_ranks.get(zone.state, 9), zone.distance_to_price, timeframe_rank)


def _zone_is_valid_for_action(zone: ZoneLadderItem) -> bool:
    return zone.state not in {"failed", "below_untouched", "above_untouched"}


def _limit_zones_preserving_origin(
    zones: list[ZoneLadderItem],
    limit: int,
) -> list[ZoneLadderItem]:
    sorted_zones = sorted(zones, key=_zone_ladder_sort_key)
    origin_zones = [zone for zone in sorted_zones if "origin base" in zone.source]
    selected: list[ZoneLadderItem] = []

    for zone in origin_zones + sorted_zones:
        if any(_same_ladder_zone(zone, existing) for existing in selected):
            continue
        selected.append(zone)
        if len(selected) >= limit:
            break

    return sorted(selected, key=_zone_ladder_sort_key)


def _same_ladder_zone(first: ZoneLadderItem, second: ZoneLadderItem) -> bool:
    return (
        first.side == second.side
        and first.timeframe == second.timeframe
        and abs(first.low - second.low) <= 0.00001
        and abs(first.high - second.high) <= 0.00001
    )


def _distance_to_zone(price: float, low: float, high: float) -> float:
    if low <= price <= high:
        return 0.0
    if price < low:
        return low - price
    return price - high

from dataclasses import dataclass

from fx_annotation.bias import Bias
from fx_annotation.candles import Candle
from fx_annotation.structure import (
    BreakOfStructure,
    Sweep,
    SwingPoint,
    detect_break_after_sweep,
    detect_liquidity_sweeps,
    detect_swings,
)


MAX_ENTRY_BASE_CANDLES = 3


@dataclass(frozen=True)
class EntryZone:
    low: float
    high: float
    source: str
    touched_after_bos: bool
    first_touch_index: int | None


@dataclass(frozen=True)
class SetupCandidate:
    side: str
    bias: Bias
    sweep: Sweep
    bos: BreakOfStructure
    entry_zone: EntryZone
    status: str
    reason: str
    quality_score: int
    quality_notes: tuple[str, ...]
    current_state: str


def find_latest_setup(
    candles: list[Candle],
    bias: Bias,
    swing_window: int = 2,
) -> tuple[SetupCandidate | None, list[SwingPoint], list[Sweep]]:
    setups, swings, sweeps = find_recent_setups(
        candles=candles,
        bias=bias,
        swing_window=swing_window,
        limit=1,
    )
    return (setups[0] if setups else None), swings, sweeps


def find_recent_setups(
    candles: list[Candle],
    bias: Bias,
    swing_window: int = 2,
    limit: int = 5,
) -> tuple[list[SetupCandidate], list[SwingPoint], list[Sweep]]:
    swings = detect_swings(candles, window=swing_window)
    sweeps = detect_liquidity_sweeps(candles, swings)
    setups: list[SetupCandidate] = []
    seen_keys: set[tuple[int, str]] = set()

    for sweep in reversed(sweeps):
        bos = detect_break_after_sweep(candles, swings, sweep)
        if bos is None:
            continue

        setup = _build_setup(candles, bias, sweep, bos)
        key = (setup.bos.index, setup.side)
        if key in seen_keys:
            continue

        seen_keys.add(key)
        setups.append(setup)

        if len(setups) >= limit * 3:
            break

    setups = _rank_setups(setups)[:limit]
    return setups, swings, sweeps


def _build_setup(
    candles: list[Candle],
    bias: Bias,
    sweep: Sweep,
    bos: BreakOfStructure,
) -> SetupCandidate:
    side = "buy" if bos.direction == "bullish" else "sell"
    entry_zone = _find_entry_zone(candles, sweep, bos)
    bias_match = _bias_direction_matches_side(bias.direction, side)
    quality_score, quality_notes = _score_setup_quality(candles, side, sweep, bos, entry_zone)
    current_state = _current_zone_state(candles, bos, entry_zone)

    if quality_score < 2:
        status = "low_quality"
        reason = "Entry pattern exists, but setup quality filters are weak."
    elif current_state == "expired_after_bos":
        status = "expired"
        reason = "Entry pattern existed, but too many candles have passed since BOS."
    elif bias.direction == "neutral":
        status = "watchlist"
        reason = "Entry pattern exists, but higher-timeframe bias is neutral."
    elif bias_match:
        status = "candidate"
        reason = "Entry pattern aligns with the higher-timeframe bias."
    else:
        status = "bias_mismatch"
        reason = "Entry pattern exists, but it conflicts with higher-timeframe bias."

    return SetupCandidate(
        side=side,
        bias=bias,
        sweep=sweep,
        bos=bos,
        entry_zone=entry_zone,
        status=status,
        reason=reason,
        quality_score=quality_score,
        quality_notes=tuple(quality_notes),
        current_state=current_state,
    )


def _find_entry_zone(
    candles: list[Candle],
    sweep: Sweep,
    bos: BreakOfStructure,
) -> EntryZone:
    start = min(sweep.index, bos.index)
    end = max(sweep.index, bos.index)
    impulse_candles = candles[start : end + 1]
    reference_range = _average_range(candles[max(0, start - 30) : end + 1])

    if bos.direction == "bullish":
        base = _entry_base_before_bos(candles, start, bos.index, "buy", reference_range)
        if base is not None:
            zone = _entry_zone_from_base(candles, base, "15M demand base that caused bullish BOS")
        else:
            zone = _retracement_zone(impulse_candles, "bullish")
    else:
        base = _entry_base_before_bos(candles, start, bos.index, "sell", reference_range)
        if base is not None:
            zone = _entry_zone_from_base(candles, base, "15M supply base that caused bearish BOS")
        else:
            zone = _retracement_zone(impulse_candles, "bearish")

    first_touch_index = _zone_first_touch_index(candles, bos.index + 1, zone)
    return EntryZone(
        low=zone.low,
        high=zone.high,
        source=zone.source,
        touched_after_bos=first_touch_index is not None,
        first_touch_index=first_touch_index,
    )


def _entry_base_before_bos(
    candles: list[Candle],
    start_index: int,
    bos_index: int,
    side: str,
    reference_range: float,
    lookback: int = MAX_ENTRY_BASE_CANDLES,
) -> tuple[int, int] | None:
    want_bearish = side == "buy"
    source_index = _last_opposing_index(candles, start_index, bos_index, want_bearish)
    if source_index is None:
        return None

    start = source_index
    end = source_index
    base_low = candles[source_index].low
    base_high = candles[source_index].high
    max_width = reference_range * 1.6 if reference_range > 0 else base_high - base_low

    for index in range(source_index - 1, max(start_index - 1, source_index - lookback - 1), -1):
        if end - index + 1 > MAX_ENTRY_BASE_CANDLES:
            break
        candidate = candles[index]
        new_low = min(base_low, candidate.low)
        new_high = max(base_high, candidate.high)
        if reference_range > 0 and new_high - new_low > max_width:
            break
        if not _entry_base_candle(candidate, side, reference_range):
            break
        start = index
        base_low = new_low
        base_high = new_high

    for index in range(source_index + 1, bos_index):
        if index - start + 1 > MAX_ENTRY_BASE_CANDLES:
            break
        candidate = candles[index]
        new_low = min(base_low, candidate.low)
        new_high = max(base_high, candidate.high)
        if reference_range > 0 and new_high - new_low > max_width:
            break
        if not _entry_base_candle(candidate, side, reference_range):
            break
        end = index
        base_low = new_low
        base_high = new_high

    return (start, end)


def _last_opposing_index(
    candles: list[Candle],
    start_index: int,
    end_index: int,
    want_bearish: bool,
) -> int | None:
    for index in range(end_index - 1, start_index - 1, -1):
        candle = candles[index]
        if want_bearish and candle.bearish:
            return index
        if not want_bearish and candle.bullish:
            return index
    return None


def _entry_base_candle(candle: Candle, side: str, reference_range: float) -> bool:
    candle_range = candle.high - candle.low
    body = abs(candle.close - candle.open)
    if candle_range <= 0:
        return False
    if reference_range > 0 and candle_range > reference_range * 1.25:
        return False
    if body <= candle_range * 0.65:
        return True
    return candle.bearish if side == "buy" else candle.bullish


def _entry_zone_from_base(
    candles: list[Candle],
    base: tuple[int, int],
    source: str,
) -> EntryZone:
    base_candles = candles[base[0] : base[1] + 1]
    return EntryZone(
        low=min(candle.low for candle in base_candles),
        high=max(candle.high for candle in base_candles),
        source=source,
        touched_after_bos=False,
        first_touch_index=None,
    )


def _retracement_zone(candles: list[Candle], direction: str) -> EntryZone:
    high = max(candle.high for candle in candles)
    low = min(candle.low for candle in candles)
    price_range = high - low

    if direction == "bullish":
        zone_low = low + price_range * 0.50
        zone_high = low + price_range * 0.70
    else:
        zone_low = high - price_range * 0.70
        zone_high = high - price_range * 0.50

    return EntryZone(
        low=min(zone_low, zone_high),
        high=max(zone_low, zone_high),
        source="50-70 percent impulse retracement",
        touched_after_bos=False,
        first_touch_index=None,
    )


def _zone_first_touch_index(
    candles: list[Candle],
    start_index: int,
    zone: EntryZone,
) -> int | None:
    for index in range(start_index, len(candles)):
        candle = candles[index]
        if candle.low <= zone.high and candle.high >= zone.low:
            return index
    return None


def _score_setup_quality(
    candles: list[Candle],
    side: str,
    sweep: Sweep,
    bos: BreakOfStructure,
    entry_zone: EntryZone,
) -> tuple[int, list[str]]:
    score = 0
    notes: list[str] = []
    reference_range = _average_range(candles[max(0, sweep.index - 30) : bos.index + 1])
    bos_candle = candles[bos.index]
    break_distance = abs(bos_candle.close - bos.broken_price)

    if reference_range > 0 and break_distance >= reference_range * 0.20:
        score += 1
        notes.append("BOS close has meaningful distance beyond broken structure.")
    else:
        notes.append("BOS close is shallow; structure break may be weak.")

    displacement = _impulse_displacement(candles, sweep.index, bos.index)
    if reference_range > 0 and displacement >= reference_range * 1.25:
        score += 1
        notes.append("Sweep-to-BOS move shows useful displacement.")
    else:
        notes.append("Sweep-to-BOS move lacks strong displacement.")

    if _zone_near_range_edge(candles, side, entry_zone):
        score += 1
        notes.append("Entry zone is closer to the recent range edge.")
    else:
        notes.append("Entry zone is near the middle of the recent range.")

    if entry_zone.source == "50-70 percent impulse retracement":
        score = 0
        notes.append("Retracement-only zone is not a valid demand/supply base; do not mark as actionable.")

    return score, notes


def _current_zone_state(
    candles: list[Candle],
    bos: BreakOfStructure,
    entry_zone: EntryZone,
) -> str:
    if not candles:
        return "unknown"

    latest_index = len(candles) - 1
    latest = candles[-1]
    in_zone = latest.low <= entry_zone.high and latest.high >= entry_zone.low

    if in_zone:
        return "at_entry_zone_now"

    if entry_zone.first_touch_index is None:
        return "waiting_for_first_pullback"

    if latest_index - bos.index > 48:
        return "expired_after_bos"

    bars_since_touch = latest_index - entry_zone.first_touch_index
    if bars_since_touch > 12:
        return "stale_after_pullback"

    return "recently_left_entry_zone"


def _impulse_displacement(candles: list[Candle], start_index: int, end_index: int) -> float:
    impulse = candles[start_index : end_index + 1]
    if not impulse:
        return 0.0
    return max(candle.high for candle in impulse) - min(candle.low for candle in impulse)


def _zone_near_range_edge(
    candles: list[Candle],
    side: str,
    entry_zone: EntryZone,
    lookback: int = 96,
) -> bool:
    recent = candles[-lookback:] if len(candles) > lookback else candles
    if not recent:
        return False

    range_high = max(candle.high for candle in recent)
    range_low = min(candle.low for candle in recent)
    price_range = range_high - range_low
    if price_range <= 0:
        return False

    zone_mid = (entry_zone.low + entry_zone.high) / 2
    position = (zone_mid - range_low) / price_range

    if side == "buy":
        return position <= 0.55
    return position >= 0.45


def _average_range(candles: list[Candle]) -> float:
    if not candles:
        return 0.0
    return sum(candle.high - candle.low for candle in candles) / len(candles)


def _bias_matches_setup(bias: Bias, setup: SetupCandidate) -> bool:
    return bias.direction == "neutral" or _bias_direction_matches_side(
        bias.direction,
        setup.side,
    )


def _rank_setups(setups: list[SetupCandidate]) -> list[SetupCandidate]:
    return sorted(
        setups,
        key=lambda setup: (
            _status_rank(setup.status),
            _current_state_rank(setup.current_state),
            setup.quality_score,
            setup.bos.index,
        ),
        reverse=True,
    )


def _status_rank(status: str) -> int:
    if status == "candidate":
        return 3
    if status == "watchlist":
        return 2
    if status == "expired":
        return 0
    if status == "low_quality":
        return 0
    return 1


def _current_state_rank(current_state: str) -> int:
    ranks = {
        "at_entry_zone_now": 5,
        "waiting_for_first_pullback": 4,
        "recently_left_entry_zone": 3,
        "stale_after_pullback": 1,
        "expired_after_bos": 0,
    }
    return ranks.get(current_state, 0)


def _bias_direction_matches_side(direction: str, side: str) -> bool:
    return (direction == "bullish" and side == "buy") or (
        direction == "bearish" and side == "sell"
    )

from dataclasses import dataclass
from datetime import datetime, timedelta

from fx_annotation.candles import Candle
from fx_annotation.market_watch import InstrumentState
from fx_annotation.structure import average_range


@dataclass(frozen=True)
class ScheduleDecision:
    next_check_time: datetime
    reason: str
    distance_to_zone: float | None
    distance_in_ranges: float | None
    latest_price: float | None
    session: str
    interval_minutes: int


def schedule_next_check(state: InstrumentState, now: datetime) -> ScheduleDecision:
    latest_price = _latest_price(state)
    distance = _distance_to_zone(state, latest_price)
    avg_range = average_range(state.entry_candles, period=20) if state.entry_candles else 0.0
    distance_in_ranges = None
    if distance is not None and avg_range > 0:
        distance_in_ranges = distance / avg_range

    session = market_session(now)
    interval, reason = _interval_for_state(state, distance_in_ranges, session)

    return ScheduleDecision(
        next_check_time=now + timedelta(minutes=interval),
        reason=reason,
        distance_to_zone=distance,
        distance_in_ranges=distance_in_ranges,
        latest_price=latest_price,
        session=session,
        interval_minutes=interval,
    )


def market_session(now: datetime) -> str:
    hour = now.hour
    if 7 <= hour < 12:
        return "london"
    if 12 <= hour < 16:
        return "london_new_york_overlap"
    if 16 <= hour < 21:
        return "new_york"
    return "off_session"


def _interval_for_state(
    state: InstrumentState,
    distance_in_ranges: float | None,
    session: str,
) -> tuple[int, str]:
    session_factor = 1.0 if session != "off_session" else 1.8

    if state.status == "entry_candidate_now":
        return 5, "Entry zone is active now; recheck quickly."

    if state.status != "expired" and getattr(state, "htf_poi_sequence", "") in {
        "poi_touched_after_sweep",
        "poi_touch_too_old",
    }:
        return _minutes(120, session_factor), "HTF POI sequence is invalid; wait for fresh sweep and BOS."

    if state.status == "waiting_for_htf_poi":
        return _minutes(60, session_factor), "Waiting for price to mitigate the relevant HTF POI first."

    if state.status == "wait_for_pullback":
        return _distance_interval(
            distance_in_ranges,
            close_minutes=5,
            medium_minutes=15,
            far_minutes=30,
            session_factor=session_factor,
            reason="Waiting for first pullback into the entry zone.",
        )

    if state.status == "potential_future_setup":
        current_state = state.primary_setup.current_state if state.primary_setup else ""
        if current_state == "recently_left_entry_zone":
            return _minutes(10, session_factor), "Recently left entry zone; recheck for return or invalidation."
        if current_state == "stale_after_pullback":
            return _stale_interval(distance_in_ranges, session_factor)
        return _distance_interval(
            distance_in_ranges,
            close_minutes=10,
            medium_minutes=30,
            far_minutes=60,
            session_factor=session_factor,
            reason="Potential setup exists; schedule depends on distance to entry zone.",
        )

    if state.status == "watchlist":
        return _minutes(60, session_factor), "Bias is unclear; recheck for clearer higher-timeframe structure."

    if state.status in {"low_quality", "conflict"}:
        return _minutes(120, session_factor), "Low-priority state; recheck only after structure changes."

    if state.status == "expired":
        return _minutes(240, session_factor), "Expired setup; wait for fresh structure."

    return _minutes(60, session_factor), "No clear setup; periodic scan."


def _stale_interval(
    distance_in_ranges: float | None,
    session_factor: float,
) -> tuple[int, str]:
    if distance_in_ranges is None:
        return _minutes(120, session_factor), "Setup is stale; distance unavailable."
    if distance_in_ranges <= 2.0:
        return _minutes(60, session_factor), "Setup is stale, but price is still near enough to monitor."
    if distance_in_ranges <= 5.0:
        return _minutes(120, session_factor), "Setup is stale and price is moderately far from the zone."
    return _minutes(240, session_factor), "Setup is stale and price is far from the zone."


def _distance_interval(
    distance_in_ranges: float | None,
    close_minutes: int,
    medium_minutes: int,
    far_minutes: int,
    session_factor: float,
    reason: str,
) -> tuple[int, str]:
    if distance_in_ranges is None:
        return _minutes(medium_minutes, session_factor), f"{reason} Distance unavailable."
    if distance_in_ranges <= 0.75:
        return _minutes(close_minutes, session_factor), f"{reason} Price is close to the zone."
    if distance_in_ranges <= 2.0:
        return _minutes(medium_minutes, session_factor), f"{reason} Price is moderately far from the zone."
    return _minutes(far_minutes, session_factor), f"{reason} Price is far from the zone."


def _minutes(base_minutes: int, factor: float) -> int:
    return max(5, int(round(base_minutes * factor)))


def _latest_price(state: InstrumentState) -> float | None:
    if not state.entry_candles:
        return None
    return state.entry_candles[-1].close


def _distance_to_zone(state: InstrumentState, latest_price: float | None) -> float | None:
    if latest_price is None or state.primary_setup is None:
        return None

    zone = state.primary_setup.entry_zone
    if zone.low <= latest_price <= zone.high:
        return 0.0
    if latest_price < zone.low:
        return zone.low - latest_price
    return latest_price - zone.high

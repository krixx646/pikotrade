from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json

from fx_annotation.market_watch import InstrumentState
from fx_annotation.pair_value import pair_value_record
from fx_annotation.scheduler import ScheduleDecision, schedule_next_check


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MEMORY_PATH = PROJECT_ROOT / "outputs" / "live_memory.json"
DEFAULT_ALERTS_PATH = PROJECT_ROOT / "outputs" / "alerts.json"


@dataclass(frozen=True)
class MemoryUpdate:
    instrument: str
    status: str
    next_check_time: datetime
    reason: str
    distance_to_zone: float | None
    distance_in_ranges: float | None
    session: str
    alert: str
    story_phase: str
    active_zone: str
    pair_value_label: str


def load_memory(path: Path = DEFAULT_MEMORY_PATH) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_memory(memory: dict[str, dict[str, object]], path: Path = DEFAULT_MEMORY_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(memory, indent=2, sort_keys=True), encoding="utf-8")


def update_live_memory(
    states: list[InstrumentState],
    path: Path = DEFAULT_MEMORY_PATH,
    alerts_path: Path = DEFAULT_ALERTS_PATH,
    now: datetime | None = None,
) -> list[MemoryUpdate]:
    current_time = now or datetime.now(timezone.utc)
    memory = load_memory(path)
    alerts = load_memory(alerts_path)
    updates: list[MemoryUpdate] = []

    for state in states:
        if state.error:
            continue

        previous_record = memory.get(state.instrument, {})
        story = build_story_state(state, previous_record, current_time)
        schedule = schedule_next_check(state, current_time)
        alert = alert_for_state(state, schedule)
        memory[state.instrument] = {
            "instrument": state.instrument,
            "status": state.status,
            "action": state.action,
            "updated_at": current_time.isoformat(),
            "next_check_time": schedule.next_check_time.isoformat(),
            "next_check_reason": schedule.reason,
            "latest_price": schedule.latest_price,
            "distance_to_zone": schedule.distance_to_zone,
            "distance_in_ranges": schedule.distance_in_ranges,
            "session": schedule.session,
            "interval_minutes": schedule.interval_minutes,
            "bias": state.bias.direction if state.bias is not None else "unknown",
            "pair_value": _pair_value_record(state),
            "pair_value_tier": state.pair_value.tier if state.pair_value else "unvalidated",
            "pair_value_label": state.pair_value.label if state.pair_value else "UNVALIDATED PAIR",
            "pair_value_note": state.pair_value.note if state.pair_value else "",
            "htf_narrative": _narrative_record(state),
            "trade_target": _trade_target_record(state),
            "available_r": state.available_r,
            "confluence": _confluence_record(state),
            "a_grade_score": state.confluence.score if state.confluence else None,
            "a_grade_passed": state.confluence.passed if state.confluence else False,
            "trade_target_price": state.trade_target.price if state.trade_target else None,
            "trade_target_timeframe": state.trade_target.timeframe if state.trade_target else "",
            "trade_target_reason": state.trade_target.reason if state.trade_target else "",
            "primary_side": state.primary_setup.side if state.primary_setup else "",
            "entry_zone_low": state.primary_setup.entry_zone.low if state.primary_setup else None,
            "entry_zone_high": state.primary_setup.entry_zone.high if state.primary_setup else None,
            "entry_zone_source": state.primary_setup.entry_zone.source if state.primary_setup else "",
            "sweep_time": _candle_time(state.entry_candles, state.primary_setup.sweep.index)
            if state.primary_setup
            else "",
            "sweep_price": state.primary_setup.sweep.swept_price if state.primary_setup else None,
            "sweep_kind": state.primary_setup.sweep.kind if state.primary_setup else "",
            "bos_time": _candle_time(state.entry_candles, state.primary_setup.bos.index)
            if state.primary_setup
            else "",
            "bos_price": state.primary_setup.bos.broken_price if state.primary_setup else None,
            "bos_direction": state.primary_setup.bos.direction if state.primary_setup else "",
            "current_state": state.primary_setup.current_state if state.primary_setup else "",
            "htf_poi_sequence": state.htf_poi_sequence,
            "htf_poi_low": state.relevant_htf_poi.low if state.relevant_htf_poi else None,
            "htf_poi_high": state.relevant_htf_poi.high if state.relevant_htf_poi else None,
            "htf_poi_side": state.relevant_htf_poi.side if state.relevant_htf_poi else "",
            "htf_poi_touched_now": state.relevant_htf_poi.touched_now if state.relevant_htf_poi else False,
            "zone_ladder": [
                {
                    "timeframe": zone.timeframe,
                    "side": zone.side,
                    "state": zone.state,
                    "low": zone.low,
                    "high": zone.high,
                    "candle_time": zone.candle_time.isoformat(),
                    "distance_to_price": zone.distance_to_price,
                    "source": zone.source,
                    "reason": zone.reason,
                }
                for zone in state.zone_ladder
            ],
            "story": story,
            "quality_score": state.primary_setup.quality_score if state.primary_setup else None,
            "reversal_warning": state.reversal_warning,
            "alert": alert,
        }
        if alert:
            alerts[f"{state.instrument}:{current_time.isoformat()}"] = {
                "instrument": state.instrument,
                "status": state.status,
                "alert": alert,
                "created_at": current_time.isoformat(),
                "pair_value": _pair_value_record(state),
                "pair_value_tier": state.pair_value.tier if state.pair_value else "unvalidated",
                "pair_value_label": state.pair_value.label if state.pair_value else "UNVALIDATED PAIR",
                "pair_value_note": state.pair_value.note if state.pair_value else "",
                "latest_price": schedule.latest_price,
                "entry_zone_low": state.primary_setup.entry_zone.low if state.primary_setup else None,
                "entry_zone_high": state.primary_setup.entry_zone.high if state.primary_setup else None,
            }
        updates.append(
            MemoryUpdate(
                instrument=state.instrument,
                status=state.status,
                next_check_time=schedule.next_check_time,
                reason=schedule.reason,
                distance_to_zone=schedule.distance_to_zone,
                distance_in_ranges=schedule.distance_in_ranges,
                session=schedule.session,
                alert=alert,
                story_phase=str(story.get("phase", "")),
                active_zone=str(story.get("active_zone", "")),
                pair_value_label=state.pair_value.label if state.pair_value else "UNVALIDATED PAIR",
            )
        )

    save_memory(memory, path)
    save_memory(alerts, alerts_path)
    return updates


def _candle_time(candles: list[object], index: int) -> str:
    if index < 0 or index >= len(candles):
        return ""
    candle = candles[index]
    time = getattr(candle, "time", None)
    return time.isoformat() if time is not None else ""


def _confluence_record(state: InstrumentState) -> dict[str, object] | None:
    grade = state.confluence
    if grade is None:
        return None
    return {
        "score": grade.score,
        "max_score": grade.max_score,
        "passed": grade.passed,
        "reasons": list(grade.reasons),
        "failures": list(grade.failures),
        "metrics": grade.metrics,
    }


def _pair_value_record(state: InstrumentState) -> dict[str, str] | None:
    if state.pair_value is None:
        return None
    return pair_value_record(state.pair_value)


def alert_for_state(
    state: InstrumentState,
    schedule: ScheduleDecision,
) -> str:
    if state.htf_poi_sequence not in {"", "valid", "no_m15_setup"}:
        return ""

    if state.status == "entry_candidate_now":
        return f"Entry zone is active now. {_pair_value_alert_text(state)}"

    if (
        state.status in {"wait_for_pullback", "potential_future_setup"}
        and schedule.distance_in_ranges is not None
        and schedule.distance_in_ranges <= 0.75
    ):
        return f"Price is close to the watched entry zone. {_pair_value_alert_text(state)}"

    return ""


def render_memory_updates(updates: list[MemoryUpdate]) -> str:
    lines = [
        "## Live Memory Updates",
        "",
        "These are the next planned revisit times for current market states.",
        "",
    ]

    for update in updates:
        distance = ""
        if update.distance_in_ranges is not None:
            distance = f", distance {update.distance_in_ranges:.2f}x avg candle range"
        alert = f", ALERT: {update.alert}" if update.alert else ""
        story = f", story {update.story_phase}" if update.story_phase else ""
        lines.append(
            f"- `{update.instrument}`: {update.status} [{update.pair_value_label}]{story}, next check {update.next_check_time.isoformat()} ({update.session}{distance}) - {update.reason}{alert}"
        )

    return "\n".join(lines) + "\n"


def _pair_value_alert_text(state: InstrumentState) -> str:
    if state.pair_value is None:
        return "Pair value: UNVALIDATED PAIR."
    return f"Pair value: {state.pair_value.label}. {state.pair_value.note}"


def due_instruments(
    instruments: list[str],
    path: Path = DEFAULT_MEMORY_PATH,
    now: datetime | None = None,
) -> list[str]:
    current_time = now or datetime.now(timezone.utc)
    memory = load_memory(path)
    due: list[str] = []

    for instrument in instruments:
        record = memory.get(instrument)
        if not record:
            due.append(instrument)
            continue

        next_check_raw = record.get("next_check_time")
        if not isinstance(next_check_raw, str):
            due.append(instrument)
            continue

        try:
            next_check_time = datetime.fromisoformat(next_check_raw)
        except ValueError:
            due.append(instrument)
            continue

        if next_check_time <= current_time:
            due.append(instrument)

    return due


def next_due_summary(
    path: Path = DEFAULT_MEMORY_PATH,
    now: datetime | None = None,
) -> str:
    current_time = now or datetime.now(timezone.utc)
    memory = load_memory(path)
    future_checks: list[tuple[datetime, str]] = []

    for instrument, record in memory.items():
        next_check_raw = record.get("next_check_time")
        if not isinstance(next_check_raw, str):
            continue
        try:
            next_check_time = datetime.fromisoformat(next_check_raw)
        except ValueError:
            continue
        if next_check_time > current_time:
            future_checks.append((next_check_time, instrument))

    if not future_checks:
        return "No future checks are scheduled."

    next_check_time, instrument = sorted(future_checks)[0]
    return f"Next due instrument: `{instrument}` at {next_check_time.isoformat()}."


def build_story_state(
    state: InstrumentState,
    previous_record: dict[str, object],
    current_time: datetime,
) -> dict[str, object]:
    previous_story = previous_record.get("story")
    if not isinstance(previous_story, dict):
        previous_story = {}

    active_zone = _select_active_zone(state)
    discarded_zones = _discarded_zones(previous_story, state)
    latest_price = state.entry_candles[-1].close if state.entry_candles else None

    if active_zone is None:
        return {
            "phase": "no_active_zone",
            "active_zone": "",
            "active_zone_state": "",
            "trade_side": "",
            "liquidity_sweep_seen": False,
            "market_shift_seen": False,
            "discarded_zones": discarded_zones,
            "last_price": latest_price,
            "updated_at": current_time.isoformat(),
            "note": "No H4/H1 zone is active enough to track.",
        }

    active_key = _zone_key(active_zone)
    same_zone = previous_story.get("active_zone") == active_key
    trade_side = "buy" if active_zone.side == "demand" else "sell"
    liquidity_sweep_seen = _liquidity_sweep_seen(state, trade_side)
    market_shift_seen = _market_shift_seen(state, trade_side)

    if same_zone:
        liquidity_sweep_seen = bool(previous_story.get("liquidity_sweep_seen")) or liquidity_sweep_seen
        market_shift_seen = bool(previous_story.get("market_shift_seen")) or market_shift_seen

    phase = _story_phase(
        zone_state=active_zone.state,
        liquidity_sweep_seen=liquidity_sweep_seen,
        market_shift_seen=market_shift_seen,
        current_state=state.primary_setup.current_state if state.primary_setup else "",
    )

    return {
        "phase": phase,
        "active_zone": active_key,
        "active_zone_timeframe": active_zone.timeframe,
        "active_zone_side": active_zone.side,
        "active_zone_state": active_zone.state,
        "active_zone_low": active_zone.low,
        "active_zone_high": active_zone.high,
        "trade_side": trade_side,
        "liquidity_sweep_seen": liquidity_sweep_seen,
        "market_shift_seen": market_shift_seen,
        "discarded_zones": discarded_zones,
        "last_price": latest_price,
        "updated_at": current_time.isoformat(),
        "note": _story_note(phase, active_zone),
    }


def _select_active_zone(state: InstrumentState) -> object | None:
    if not state.zone_ladder:
        return None
    ranks = {
        "respected": 5,
        "inside": 4,
        "approaching": 3,
        "untouched": 2,
        "failed": 0,
        "below_untouched": 0,
        "above_untouched": 0,
    }
    candidates = [zone for zone in state.zone_ladder if ranks.get(zone.state, 0) > 0]
    if not candidates:
        return None
    return sorted(candidates, key=lambda zone: (ranks.get(zone.state, 0), -zone.distance_to_price), reverse=True)[0]


def _discarded_zones(
    previous_story: dict[str, object],
    state: InstrumentState,
) -> list[str]:
    previous = previous_story.get("discarded_zones")
    discarded = [str(item) for item in previous] if isinstance(previous, list) else []
    for zone in state.zone_ladder:
        if zone.state != "failed":
            continue
        key = _zone_key(zone)
        if key not in discarded:
            discarded.append(key)
    return discarded[-20:]


def _zone_key(zone: object) -> str:
    return (
        f"{getattr(zone, 'timeframe', '')} {getattr(zone, 'side', '')} "
        f"{getattr(zone, 'low', 0.0):.5f}-{getattr(zone, 'high', 0.0):.5f}"
    )


def _liquidity_sweep_seen(state: InstrumentState, trade_side: str) -> bool:
    expected_kind = "sell_side_liquidity" if trade_side == "buy" else "buy_side_liquidity"
    if state.primary_setup is not None and state.primary_setup.side == trade_side:
        return state.primary_setup.sweep.kind == expected_kind
    recent_sweeps = state.sweeps[-8:]
    return any(sweep.kind == expected_kind for sweep in recent_sweeps)


def _market_shift_seen(state: InstrumentState, trade_side: str) -> bool:
    if state.primary_setup is None:
        return False
    if state.primary_setup.side != trade_side:
        return False
    if state.primary_setup.status in {"invalid_poi_sequence", "waiting_htf_poi", "low_quality"}:
        return False
    return state.htf_poi_sequence == "valid"


def _story_phase(
    zone_state: str,
    liquidity_sweep_seen: bool,
    market_shift_seen: bool,
    current_state: str,
) -> str:
    if zone_state in {"untouched", "approaching"}:
        return "monitoring_zone"
    if zone_state == "inside":
        return "testing_zone"
    if zone_state == "failed":
        return "zone_failed"
    if zone_state == "respected" and not liquidity_sweep_seen:
        return "waiting_for_liquidity_sweep"
    if liquidity_sweep_seen and not market_shift_seen:
        return "waiting_for_15m_market_shift"
    if market_shift_seen and current_state == "at_entry_zone_now":
        return "entry_zone_ready"
    if market_shift_seen and current_state == "waiting_for_first_pullback":
        return "waiting_for_entry_pullback"
    if market_shift_seen:
        return "execution_setup_formed"
    return "monitoring_zone"


def _story_note(phase: str, active_zone: object) -> str:
    zone = _zone_key(active_zone)
    notes = {
        "monitoring_zone": f"Monitoring {zone}; wait for price to test or respect it.",
        "testing_zone": f"Price is testing {zone}; wait for reaction before execution logic.",
        "waiting_for_liquidity_sweep": f"{zone} was respected; wait for 15M liquidity sweep.",
        "waiting_for_15m_market_shift": f"Liquidity sweep is seen at {zone}; wait for 15M Market Shift/BOS.",
        "waiting_for_entry_pullback": f"Market Shift/BOS is seen; wait for pullback to the LTF entry zone.",
        "entry_zone_ready": f"Entry zone is active now after zone respect, sweep, and Market Shift/BOS.",
        "execution_setup_formed": f"Execution setup exists; monitor current entry-zone state.",
        "zone_failed": f"{zone} failed; discard it and monitor the next ladder zone.",
    }
    return notes.get(phase, f"Tracking {zone}.")


def _narrative_record(state: InstrumentState) -> dict[str, object]:
    narrative = state.htf_narrative
    if narrative is None:
        return {}
    return {
        "timeframe": narrative.timeframe,
        "direction": narrative.direction,
        "phase": narrative.phase,
        "highest_high": narrative.highest_high,
        "lowest_low": narrative.lowest_low,
        "active_from_index": narrative.active_from_index,
        "active_from_time": narrative.active_from_time.isoformat(),
        "active_from_anchor": narrative.active_from_anchor,
        "range_low": narrative.range_low,
        "range_high": narrative.range_high,
        "last_line_of_defense": narrative.last_line_of_defense,
        "zones_inside_range": list(narrative.zones_inside_range),
        "liquidity_pools": [
            {
                "kind": pool.kind,
                "price": pool.price,
                "reason": pool.reason,
            }
            for pool in narrative.liquidity_pools
        ],
        "summary": narrative.summary,
    }


def _trade_target_record(state: InstrumentState) -> dict[str, object]:
    target = state.trade_target
    if target is None:
        return {}
    return {
        "price": target.price,
        "side": target.side,
        "timeframe": target.timeframe,
        "swing_kind": target.swing_kind,
        "candle_time": target.candle_time.isoformat(),
        "index": target.index,
        "reason": target.reason,
        "available_r": state.available_r,
    }

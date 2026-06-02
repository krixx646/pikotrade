from dataclasses import dataclass, replace

from fx_annotation.ai_review import AiReviewInput, build_ai_review_prompt
from fx_annotation.bias import Bias, detect_bias
from fx_annotation.candles import Candle
from fx_annotation.confluence import ConfluenceGrade, confluence_snapshot, grade_setup_confluence
from fx_annotation.narrative import HtfNarrative, build_htf_narrative, narrative_summary
from fx_annotation.oanda_client import OandaClient
from fx_annotation.pair_value import PairValue, pair_value_for_instrument
from fx_annotation.poi import (
    PointOfInterest,
    ZoneLadderItem,
    detect_htf_pois,
    detect_zone_ladder,
    latest_poi_touch_index,
    nearest_relevant_poi,
    poi_summary,
    zone_ladder_summary,
)
from fx_annotation.setups import SetupCandidate, find_recent_setups
from fx_annotation.structure import FairValueGap, Sweep, SwingPoint, average_range, detect_fair_value_gaps
from fx_annotation.trade_targets import TradeTarget, available_r, select_trade_target


DEFAULT_WATCHLIST = [
    "EUR_USD",
    "GBP_USD",
    "USD_JPY",
    "USD_CAD",
    "AUD_USD",
    "NZD_USD",
    "EUR_JPY",
    "GBP_JPY",
    "XAU_USD",
    "BTC_USD",
]


@dataclass(frozen=True)
class InstrumentState:
    instrument: str
    status: str
    action: str
    bias: Bias | None
    primary_setup: SetupCandidate | None
    recent_setups: list[SetupCandidate]
    htf_narrative: HtfNarrative | None
    htf_pois: list[PointOfInterest]
    zone_ladder: list[ZoneLadderItem]
    relevant_htf_poi: PointOfInterest | None
    htf_poi_sequence: str
    swings: list[SwingPoint]
    sweeps: list[Sweep]
    fair_value_gaps: list[FairValueGap]
    entry_candles: list[Candle]
    ai_context: str
    trade_target: TradeTarget | None = None
    available_r: float | None = None
    confluence: ConfluenceGrade | None = None
    pair_value: PairValue | None = None
    reversal_warning: dict[str, object] | None = None
    error: str = ""


def scan_market(
    client: OandaClient,
    instruments: list[str],
    fundamentals: str = "",
    bias_granularity: str = "H4",
    refinement_granularity: str = "H1",
    entry_granularity: str = "M15",
    setup_limit: int = 5,
) -> list[InstrumentState]:
    states: list[InstrumentState] = []

    for instrument in instruments:
        states.append(
            scan_instrument(
                client=client,
                instrument=instrument,
                fundamentals=fundamentals,
                bias_granularity=bias_granularity,
                refinement_granularity=refinement_granularity,
                entry_granularity=entry_granularity,
                setup_limit=setup_limit,
            )
        )

    return sorted(states, key=_state_rank, reverse=True)


def scan_instrument(
    client: OandaClient,
    instrument: str,
    fundamentals: str,
    bias_granularity: str = "H4",
    refinement_granularity: str = "H1",
    entry_granularity: str = "M15",
    setup_limit: int = 5,
) -> InstrumentState:
    try:
        bias_candles = _completed(
            client.fetch_candles(instrument, bias_granularity, count=300)
        )
        refinement_candles = _completed(
            client.fetch_candles(instrument, refinement_granularity, count=300)
        )
        entry_candles = _completed(
            client.fetch_candles(instrument, entry_granularity, count=400)
        )
        h4_bias = detect_bias(bias_candles)
        h1_bias = detect_bias(refinement_candles)
        bias = _effective_bias(h4_bias, h1_bias, refinement_granularity)
        narrative_candles = refinement_candles if _uses_refinement_bias(h4_bias, h1_bias) else bias_candles
        narrative_timeframe = refinement_granularity if _uses_refinement_bias(h4_bias, h1_bias) else bias_granularity
        latest_price = entry_candles[-1].close if entry_candles else bias_candles[-1].close
        preliminary_narrative = build_htf_narrative(
            candles=narrative_candles,
            zones=[],
            direction=bias.direction,
            timeframe=narrative_timeframe,
        )
        active_from_time = preliminary_narrative.active_from_time if preliminary_narrative else None
        h4_pois = detect_htf_pois(
            bias_candles,
            latest_price,
            active_from_time=active_from_time,
            bias_direction=bias.direction,
            timeframe=bias_granularity,
        )
        h1_pois = detect_htf_pois(
            refinement_candles,
            latest_price,
            active_from_time=active_from_time,
            bias_direction=bias.direction,
            timeframe=refinement_granularity,
        )
        zone_ladder = detect_zone_ladder(
            h4_candles=bias_candles,
            h1_candles=refinement_candles,
            current_price=latest_price,
            bias_direction=bias.direction,
            active_from_time=active_from_time,
        )
        htf_pois = (
            h1_pois
            if _should_use_refinement_pois(h4_pois, h1_pois, h4_bias)
            else h4_pois + h1_pois
        )
        htf_narrative = build_htf_narrative(
            candles=narrative_candles,
            zones=zone_ladder,
            direction=bias.direction,
            timeframe=narrative_timeframe,
        )
        recent_setups, swings, sweeps = find_recent_setups(
            entry_candles,
            bias,
            limit=setup_limit,
        )
        reversal_warning = _opposite_shift_warning(recent_setups, bias, entry_candles)
        recent_setups = _directional_setups(recent_setups, bias)
        if bias.direction == "neutral":
            recent_setups = []
        else:
            recent_setups = [
                _apply_htf_poi_sequence(entry_candles, setup, htf_pois, latest_price)
                for setup in recent_setups
            ]
        recent_setups = _rank_poi_adjusted_setups(recent_setups)[:setup_limit]
        fair_value_gaps = detect_fair_value_gaps(entry_candles)
        primary_setup = recent_setups[0] if recent_setups else None
        relevant_htf_poi = (
            nearest_relevant_poi(htf_pois, primary_setup.side, latest_price)
            if primary_setup is not None
            else None
        )
        htf_poi_sequence = _htf_poi_sequence_state(entry_candles, primary_setup, relevant_htf_poi)
        trade_target, target_r = _trade_target_for_setup(
            primary_setup,
            instrument,
            entry_candles,
            refinement_candles,
            bias_candles,
            zone_ladder,
            active_from_time,
        )
        confluence = _confluence_for_setup(primary_setup, entry_candles, htf_narrative, target_r)
        pair_value = pair_value_for_instrument(instrument)
        status, action = classify_state(bias, primary_setup, relevant_htf_poi, zone_ladder)
        ai_context = build_ai_review_prompt(
            AiReviewInput(
                instrument=instrument,
                bias_granularity=bias_granularity,
                entry_granularity=entry_granularity,
                fundamentals=fundamentals,
                bias=bias,
                entry_candles=entry_candles,
                swings=swings,
                sweeps=sweeps,
                primary_setup=primary_setup,
                recent_setups=recent_setups,
                trade_target=trade_target,
                available_r=target_r,
            )
        )

        return InstrumentState(
            instrument=instrument,
            status=status,
            action=action,
            bias=bias,
            primary_setup=primary_setup,
            recent_setups=recent_setups,
            htf_narrative=htf_narrative,
            htf_pois=htf_pois,
            zone_ladder=zone_ladder,
            relevant_htf_poi=relevant_htf_poi,
            htf_poi_sequence=htf_poi_sequence,
            swings=swings,
            sweeps=sweeps,
            fair_value_gaps=fair_value_gaps,
            entry_candles=entry_candles,
            ai_context=ai_context,
            trade_target=trade_target,
            available_r=target_r,
            confluence=confluence,
            pair_value=pair_value,
            reversal_warning=reversal_warning,
        )
    except Exception as error:
        return InstrumentState(
            instrument=instrument,
            status="error",
            action="Skip until data issue is fixed.",
            bias=None,
            primary_setup=None,
            recent_setups=[],
            htf_narrative=None,
            htf_pois=[],
            zone_ladder=[],
            relevant_htf_poi=None,
            htf_poi_sequence="unknown",
            swings=[],
            sweeps=[],
            fair_value_gaps=[],
            entry_candles=[],
            ai_context="",
            trade_target=None,
            available_r=None,
            confluence=None,
            pair_value=pair_value_for_instrument(instrument),
            reversal_warning=None,
            error=str(error),
        )


def _trade_target_for_setup(
    setup: SetupCandidate | None,
    instrument: str,
    entry_candles: list[Candle],
    h1_candles: list[Candle],
    h4_candles: list[Candle],
    zone_ladder: list[ZoneLadderItem],
    active_from_time: object,
) -> tuple[TradeTarget | None, float | None]:
    if setup is None:
        return None, None
    entry_price = setup.entry_zone.high if setup.side == "buy" else setup.entry_zone.low
    stop_loss = _setup_stop_loss(setup, instrument, entry_candles)
    risk = abs(entry_price - stop_loss)
    target = select_trade_target(
        side=setup.side.upper(),
        entry_price=entry_price,
        h1_candles=h1_candles,
        h4_candles=h4_candles,
        active_from_time=active_from_time if hasattr(active_from_time, "isoformat") else None,
        risk=risk,
        minimum_r=3.0,
        zones=zone_ladder,
        fixed_rr=3.0,
    )
    return target, available_r(target, entry_price, risk)


def _confluence_for_setup(
    setup: SetupCandidate | None,
    entry_candles: list[Candle],
    narrative: HtfNarrative | None,
    target_r: float | None,
) -> ConfluenceGrade | None:
    if setup is None:
        return None
    return grade_setup_confluence(
        setup=setup,
        candles=entry_candles,
        narrative=narrative,
        side=setup.side.upper(),
        target_r=target_r,
    )


def _setup_stop_loss(setup: SetupCandidate, instrument: str, entry_candles: list[Candle]) -> float:
    # Keep live target math aligned with forward testing's test-only stop model.
    reference_range = average_range(entry_candles, period=30)
    buffer = reference_range * _stop_buffer_multiplier(instrument)
    if setup.side == "buy":
        return min(setup.entry_zone.low, setup.sweep.swept_price) - buffer
    return max(setup.entry_zone.high, setup.sweep.swept_price) + buffer


def _stop_buffer_multiplier(instrument: str) -> float:
    if instrument in {"XAU_USD", "BTC_USD"}:
        return 0.35
    return 0.15


def _effective_bias(h4_bias: Bias, h1_bias: Bias, refinement_granularity: str) -> Bias:
    if h4_bias.direction != "neutral":
        return h4_bias
    if h1_bias.direction == "neutral":
        return h4_bias
    return Bias(
        direction=h1_bias.direction,
        reason=f"H4 is neutral/noisy, so using {refinement_granularity} refinement: {h1_bias.reason}",
    )


def _uses_refinement_bias(h4_bias: Bias, h1_bias: Bias) -> bool:
    return h4_bias.direction == "neutral" and h1_bias.direction != "neutral"


def _directional_setups(
    setups: list[SetupCandidate],
    bias: Bias,
) -> list[SetupCandidate]:
    if bias.direction == "bullish":
        return [setup for setup in setups if setup.side == "buy"]
    if bias.direction == "bearish":
        return [setup for setup in setups if setup.side == "sell"]
    return []


def _opposite_shift_warning(
    setups: list[SetupCandidate],
    bias: Bias,
    candles: list[Candle],
) -> dict[str, object] | None:
    if bias.direction == "bullish":
        opposite_side = "sell"
        message = "Possible bearish market shift warning: buy-side liquidity sweep and bearish BOS detected against bullish trend."
    elif bias.direction == "bearish":
        opposite_side = "buy"
        message = "Possible bullish market shift warning: sell-side liquidity sweep and bullish BOS detected against bearish trend."
    else:
        return None

    candidates = [
        setup
        for setup in setups
        if setup.side == opposite_side
        and setup.quality_score >= 2
        and setup.current_state != "expired_after_bos"
    ]
    if not candidates:
        return None

    setup = sorted(candidates, key=lambda item: item.quality_score, reverse=True)[0]
    return {
        "side": setup.side,
        "message": message,
        "sweep_time": _candle_time(candles, setup.sweep.index),
        "sweep_price": setup.sweep.swept_price,
        "bos_time": _candle_time(candles, setup.bos.index),
        "bos_price": setup.bos.broken_price,
        "quality_score": setup.quality_score,
    }


def _candle_time(candles: list[Candle], index: int) -> str:
    if index < 0 or index >= len(candles):
        return ""
    return candles[index].time.isoformat()


def _should_use_refinement_pois(
    h4_pois: list[PointOfInterest],
    h1_pois: list[PointOfInterest],
    h4_bias: Bias,
) -> bool:
    return h4_bias.direction == "neutral" and bool(h1_pois)


def classify_state(
    bias: Bias,
    setup: SetupCandidate | None,
    relevant_htf_poi: PointOfInterest | None = None,
    zone_ladder: list[ZoneLadderItem] | None = None,
) -> tuple[str, str]:
    ladder_state = _best_zone_ladder_state(zone_ladder or [])
    if bias.direction == "neutral":
        return (
            "no_clear_state",
            "Higher-timeframe direction is neutral, so the rule route will not mark buy or sell zones yet.",
        )
    if setup is None:
        if ladder_state in {"inside", "approaching", "respected"}:
            return (
                "potential_future_setup",
                "Zone ladder is active. Wait for price reaction, then 15M liquidity sweep and Market Shift/BOS.",
            )
        return (
            "potential_future_setup",
            "Bias exists but no complete entry pattern yet. Monitor for sweep, BOS, and pullback.",
        )

    if setup.status == "low_quality":
        return (
            "low_quality",
            "Pattern exists, but quality filters are weak. Do not treat as an entry.",
        )

    if setup.status == "waiting_htf_poi":
        if ladder_state in {"inside", "approaching", "respected"}:
            return (
                "potential_future_setup",
                "HTF/1H zone ladder is active, but 15M confirmation is not complete yet.",
            )
        return (
            "waiting_for_htf_poi",
            "M15 pattern exists, but the relevant HTF POI has not been mitigated before the setup.",
        )

    if setup.status == "invalid_poi_sequence":
        return (
            "potential_future_setup",
            "HTF POI mitigation did not happen before the M15 sweep/BOS sequence. Wait for fresh structure.",
        )

    if setup.status == "expired":
        return (
            "expired",
            "Pattern existed, but too much time has passed since BOS. Wait for fresh structure.",
        )

    if setup.status == "candidate" and setup.current_state == "at_entry_zone_now":
        return (
            "entry_candidate_now",
            "Review immediately. Chart pattern aligns with bias and price is at the entry zone now.",
        )

    if setup.status == "candidate" and setup.current_state == "waiting_for_first_pullback":
        return (
            "wait_for_pullback",
            "Bias and setup align. Monitor until price returns to the marked zone.",
        )

    if setup.status == "candidate":
        return (
            "potential_future_setup",
            "Bias and setup align, but the entry touch is not current. Recheck before acting.",
        )

    if setup.status == "watchlist":
        return (
            "watchlist",
            "Technical pattern exists, but higher-timeframe bias is neutral.",
        )

    return (
        "conflict",
        "Technical pattern conflicts with higher-timeframe bias. Treat as low priority.",
    )


def render_market_watch_report(states: list[InstrumentState]) -> str:
    lines = [
        "# Autonomous Market Watch Report",
        "",
        "This report scans multiple instruments without asking the user to manually choose a chart.",
        "",
        "## Summary",
        "",
    ]

    for state in states:
        lines.append(f"- `{state.instrument}`: {state.status} - {state.action}")

    lines.extend(["", "## Instrument Details", ""])

    for state in states:
        lines.extend(_instrument_lines(state))

    return "\n".join(lines) + "\n"


def _instrument_lines(state: InstrumentState) -> list[str]:
    lines = [
        f"### {state.instrument}",
        "",
        f"- Status: {state.status}",
        f"- Action: {state.action}",
    ]

    if state.error:
        lines.extend([f"- Error: {state.error}", ""])
        return lines

    if state.bias is not None:
        lines.extend(
            [
                f"- Bias: {state.bias.direction}",
                f"- Bias reason: {state.bias.reason}",
                f"- Pair value: {_pair_value_line(state)}",
                f"- HTF narrative: {narrative_summary(state.htf_narrative)}",
                f"- Swing points: {len(state.swings)}",
                f"- Liquidity sweeps: {len(state.sweeps)}",
                f"- Fair value gaps: {len(state.fair_value_gaps)}",
                f"- HTF POIs: {len(state.htf_pois)}",
                f"- Zone ladder: {zone_ladder_summary(state.zone_ladder)}",
                f"- Relevant HTF POI: {poi_summary(state.relevant_htf_poi)}",
                f"- HTF POI sequence: {state.htf_poi_sequence}",
                f"- Recent setup candidates: {len(state.recent_setups)}",
            ]
        )

    if state.primary_setup is not None:
        setup = state.primary_setup
        confluence = confluence_snapshot(state.confluence)
        lines.extend(
            [
                f"- Primary side: {setup.side.upper()}",
                f"- Setup status: {setup.status}",
                f"- Entry zone: {setup.entry_zone.low:.5f} - {setup.entry_zone.high:.5f}",
                f"- Zone source: {setup.entry_zone.source}",
                f"- Pullback touched: {setup.entry_zone.touched_after_bos}",
                f"- Current state: {setup.current_state}",
                f"- Quality score: {setup.quality_score}",
            ]
        )
        if confluence is not None:
            failures = ", ".join(str(item) for item in confluence.get("failures", [])[:3]) or "none"
            lines.append(
                f"- A-grade confluence: {confluence.get('score')}/{confluence.get('max_score')} "
                f"passed={confluence.get('passed')} failures={failures}"
            )

    lines.append("")
    return lines


def _pair_value_line(state: InstrumentState) -> str:
    if state.pair_value is None:
        return "UNVALIDATED PAIR - No pair tier is available."
    return f"{state.pair_value.label} - {state.pair_value.note}"


def _state_rank(state: InstrumentState) -> int:
    ranks = {
        "entry_candidate_now": 6,
        "wait_for_pullback": 5,
        "potential_future_setup": 4,
        "watchlist": 3,
        "conflict": 2,
        "waiting_for_htf_poi": 2,
        "no_clear_state": 1,
        "low_quality": 1,
        "expired": 1,
        "error": 0,
    }
    return ranks.get(state.status, 0)


def _completed(candles: list[Candle]) -> list[Candle]:
    return [candle for candle in candles if candle.complete]


def _best_zone_ladder_state(zones: list[ZoneLadderItem]) -> str:
    if not zones:
        return ""
    ranks = {
        "inside": 5,
        "approaching": 4,
        "respected": 3,
        "untouched": 2,
        "failed": 1,
    }
    return sorted(zones, key=lambda zone: ranks.get(zone.state, 0), reverse=True)[0].state


def _apply_htf_poi_sequence(
    candles: list[Candle],
    setup: SetupCandidate,
    htf_pois: list[PointOfInterest],
    latest_price: float,
) -> SetupCandidate:
    relevant_poi = nearest_relevant_poi(htf_pois, setup.side, latest_price)
    sequence_state = _htf_poi_sequence_state(candles, setup, relevant_poi)
    if sequence_state == "valid":
        return setup

    if setup.status in {"expired", "low_quality"}:
        return setup

    status = "waiting_htf_poi"
    reason = "Relevant HTF POI has not been mitigated before the M15 sweep/BOS setup."
    if sequence_state in {"poi_touched_after_sweep", "poi_touch_too_old"}:
        status = "invalid_poi_sequence"
        reason = "HTF POI mitigation is not sequenced before the M15 sweep and BOS."

    return replace(
        setup,
        status=status,
        reason=reason,
        quality_notes=(*setup.quality_notes, f"HTF POI sequence: {sequence_state}."),
    )


def _htf_poi_sequence_state(
    candles: list[Candle],
    setup: SetupCandidate | None,
    relevant_poi: PointOfInterest | None,
    max_bars_between_poi_and_sweep: int = 96,
) -> str:
    if setup is None:
        return "no_m15_setup"
    if relevant_poi is None:
        return "no_relevant_htf_poi"

    touch_index = latest_poi_touch_index(candles, relevant_poi, end_index=setup.sweep.index)
    if touch_index is None:
        full_touch_index = latest_poi_touch_index(candles, relevant_poi)
        if full_touch_index is not None and full_touch_index > setup.sweep.index:
            return "poi_touched_after_sweep"
        return "waiting_for_htf_poi"

    if setup.sweep.index - touch_index > max_bars_between_poi_and_sweep:
        return "poi_touch_too_old"

    return "valid"


def _rank_poi_adjusted_setups(setups: list[SetupCandidate]) -> list[SetupCandidate]:
    return sorted(
        setups,
        key=lambda setup: (
            _setup_status_rank(setup.status),
            _setup_current_state_rank(setup.current_state),
            setup.quality_score,
            setup.bos.index,
        ),
        reverse=True,
    )


def _setup_status_rank(status: str) -> int:
    ranks = {
        "candidate": 5,
        "watchlist": 4,
        "bias_mismatch": 3,
        "invalid_poi_sequence": 2,
        "waiting_htf_poi": 2,
        "expired": 1,
        "low_quality": 1,
    }
    return ranks.get(status, 0)


def _setup_current_state_rank(current_state: str) -> int:
    ranks = {
        "at_entry_zone_now": 5,
        "waiting_for_first_pullback": 4,
        "recently_left_entry_zone": 3,
        "stale_after_pullback": 1,
        "expired_after_bos": 0,
    }
    return ranks.get(current_state, 0)

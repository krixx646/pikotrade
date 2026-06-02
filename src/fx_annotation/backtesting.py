from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import time

from fx_annotation.bias import Bias, detect_bias
from fx_annotation.candles import Candle
from fx_annotation.confluence import ConfluenceConfig, confluence_snapshot, grade_setup_confluence
from fx_annotation.config import PROJECT_ROOT
from fx_annotation.forward_testing import (
    MAX_BOS_AGE_HOURS,
    MIN_FORWARD_TEST_QUALITY,
    SignalCandidate,
    _entry_price,
    _stop_loss,
    _target_price,
)
from fx_annotation.market_watch import (
    _apply_htf_poi_sequence,
    _directional_setups,
    _effective_bias,
    _htf_poi_sequence_state,
    _rank_poi_adjusted_setups,
    _should_use_refinement_pois,
    _uses_refinement_bias,
    classify_state,
)
from fx_annotation.narrative import HtfNarrative, build_htf_narrative
from fx_annotation.oanda_client import OandaClient
from fx_annotation.poi import (
    PointOfInterest,
    ZoneLadderItem,
    detect_htf_pois,
    detect_zone_ladder,
    nearest_relevant_poi,
)
from fx_annotation.setups import SetupCandidate, find_recent_setups
from fx_annotation.structure import average_range, detect_fair_value_gaps
from fx_annotation.trade_targets import TradeTarget, available_r, select_trade_target, target_snapshot


DEFAULT_CACHE_DIR = PROJECT_ROOT / "outputs" / "backtests" / "cache"
DEFAULT_OUTPUT_JSON = PROJECT_ROOT / "outputs" / "backtests" / "rule_backtest.json"
DEFAULT_OUTPUT_MD = PROJECT_ROOT / "outputs" / "backtests" / "rule_backtest.md"
GRANULARITY_SECONDS = {
    "M15": 15 * 60,
    "M5": 5 * 60,
    "H1": 60 * 60,
    "H4": 4 * 60 * 60,
}


@dataclass(frozen=True)
class BacktestConfig:
    instrument: str
    start: datetime
    end: datetime
    strategy_mode: str = "mtf_sniper"
    rr: float = 3.0
    timeout_bars: int = 48
    scan_interval_bars: int = 1
    h4_lookback: int = 300
    h1_lookback: int = 300
    m15_lookback: int = 400
    m5_lookback: int = 600
    max_trades: int = 0
    max_bos_age_hours: float = float(MAX_BOS_AGE_HOURS)
    min_setup_quality: int = MIN_FORWARD_TEST_QUALITY
    min_room_to_active_extreme_r: float = 0.0
    require_h1_alignment: bool = False
    require_htf_poi_touched_now: bool = False
    require_entry_reaction_candle: bool = False
    require_refined_entry: bool = False
    require_premium_discount: bool = False
    premium_discount_edge: float = 0.5
    require_market_regime: bool = False
    regime_min_range_atr: float = 6.0
    regime_min_directional_efficiency: float = 0.0
    regime_require_pullback_phase: bool = True
    require_a_grade_confluence: bool = False
    a_grade_min_score: int = 5
    one_trade_per_htf_zone: bool = False
    breakeven_after_r: float = 0.0


@dataclass(frozen=True)
class ReplayState:
    bias: Bias
    h4_bias: Bias
    h1_bias: Bias
    narrative: HtfNarrative | None
    htf_pois: list[PointOfInterest]
    zone_ladder: list[ZoneLadderItem]
    primary_setup: SetupCandidate | None
    relevant_htf_poi: PointOfInterest | None
    htf_poi_sequence: str
    status: str
    action: str
    latest_price: float
    entry_candles: list[Candle]
    h1_candles: list[Candle]
    h4_candles: list[Candle]
    refined_entry_candles: list[Candle]
    refined_setup: SetupCandidate | None


def run_rule_backtest(
    client: OandaClient,
    configs: list[BacktestConfig],
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> dict[str, object]:
    trades: list[dict[str, object]] = []
    diagnostics: dict[str, object] = {}
    for config in configs:
        result = _run_instrument_backtest(client, config, cache_dir)
        trades.extend(result["trades"])
        diagnostics[config.instrument] = result["diagnostics"]
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "engine": "rule_only",
        "trades": trades,
        "summary": summarize_backtest(trades),
        "diagnostics": diagnostics,
    }


def render_backtest_markdown(result: dict[str, object]) -> str:
    trades = [trade for trade in result.get("trades", []) if isinstance(trade, dict)]
    summary = result.get("summary", {})
    lines = [
        "# Rule-Only Backtest",
        "",
        "Backtest uses only the algorithm/rule engine. AI routes are not called.",
        "",
        "## Summary",
        "",
    ]
    if isinstance(summary, dict):
        for key in (
            "trades",
            "tp_hit",
            "sl_hit",
            "breakeven",
            "timeout",
            "no_fill",
            "win_rate_resolved",
            "average_r",
            "average_mfe_r",
            "average_mae_r",
            "average_target_r",
            "target_3r_plus",
            "target_4r_plus",
            "target_5r_plus",
        ):
            lines.append(f"- {key}: `{summary.get(key, '')}`")
        failure_tags = summary.get("failure_tags", {})
        if isinstance(failure_tags, dict) and failure_tags:
            lines.append("- failure_tags:")
            for tag, count in sorted(failure_tags.items(), key=lambda item: item[1], reverse=True):
                lines.append(f"  - `{tag}`: `{count}`")
    lines.extend(["", "## By Instrument", ""])
    by_instrument = summary.get("by_instrument", {}) if isinstance(summary, dict) else {}
    if isinstance(by_instrument, dict):
        for instrument, stats in sorted(by_instrument.items()):
            lines.append(f"- `{instrument}`: {stats}")
    lines.extend(["", "## Rejection Diagnostics", ""])
    diagnostics = result.get("diagnostics", {})
    if isinstance(diagnostics, dict):
        for instrument, stats in sorted(diagnostics.items()):
            if not isinstance(stats, dict):
                continue
            lines.append(f"### {instrument}")
            lines.append("")
            lines.append(f"- evaluated scans: `{stats.get('evaluated_scans', 0)}`")
            reasons = stats.get("rejected_reasons", {})
            if isinstance(reasons, dict):
                for reason, count in sorted(reasons.items(), key=lambda item: item[1], reverse=True)[:10]:
                    lines.append(f"- rejected `{reason}`: `{count}`")
            lines.append("")
    lines.extend(["", "## Recent Trades", ""])
    for trade in trades[-50:]:
        lines.append(
            "- "
            f"`{trade.get('instrument')}` {trade.get('side')} {trade.get('result')} "
            f"entry {trade.get('entry_price')} SL {trade.get('stop_loss')} TP {trade.get('target_price')} "
            f"R {trade.get('realized_r')} targetR {trade.get('available_r')}"
        )
    lines.append("")
    return "\n".join(lines)


def summarize_backtest(trades: list[dict[str, object]]) -> dict[str, object]:
    counts = {"tp_hit": 0, "sl_hit": 0, "sl_hit_ambiguous": 0, "breakeven": 0, "timeout": 0, "no_fill": 0}
    by_instrument: dict[str, dict[str, int]] = {}
    r_values: list[float] = []
    mfe_values: list[float] = []
    mae_values: list[float] = []
    failure_tags: dict[str, int] = {}
    target_r_values: list[float] = []
    for trade in trades:
        result = str(trade.get("result", ""))
        if result in counts:
            counts[result] += 1
        instrument = str(trade.get("instrument", ""))
        stats = by_instrument.setdefault(
            instrument,
            {"trades": 0, "tp_hit": 0, "sl_hit": 0, "breakeven": 0, "timeout": 0, "no_fill": 0},
        )
        stats["trades"] += 1
        if result == "tp_hit":
            stats["tp_hit"] += 1
        elif result in {"sl_hit", "sl_hit_ambiguous"}:
            stats["sl_hit"] += 1
        elif result == "breakeven":
            stats["breakeven"] += 1
        elif result == "timeout":
            stats["timeout"] += 1
        elif result == "no_fill":
            stats["no_fill"] += 1
        r_values.append(float(trade.get("realized_r", 0.0)))
        mfe_values.append(float(trade.get("max_favorable_r", 0.0)))
        mae_values.append(float(trade.get("max_adverse_r", 0.0)))
        target_r = trade.get("available_r")
        if isinstance(target_r, int | float):
            target_r_values.append(float(target_r))
        for tag in trade.get("failure_tags", []):
            if isinstance(tag, str):
                failure_tags[tag] = failure_tags.get(tag, 0) + 1
    wins = counts["tp_hit"]
    resolved = counts["tp_hit"] + counts["sl_hit"] + counts["sl_hit_ambiguous"]
    return {
        "trades": len(trades),
        "tp_hit": counts["tp_hit"],
        "sl_hit": counts["sl_hit"] + counts["sl_hit_ambiguous"],
        "breakeven": counts["breakeven"],
        "timeout": counts["timeout"],
        "no_fill": counts["no_fill"],
        "win_rate_resolved": f"{wins / resolved * 100:.1f}%" if resolved else "n/a",
        "average_r": _average(r_values),
        "average_mfe_r": _average(mfe_values),
        "average_mae_r": _average(mae_values),
        "average_target_r": _average(target_r_values),
        "target_3r_plus": sum(1 for value in target_r_values if value >= 3.0),
        "target_4r_plus": sum(1 for value in target_r_values if value >= 4.0),
        "target_5r_plus": sum(1 for value in target_r_values if value >= 5.0),
        "failure_tags": failure_tags,
        "by_instrument": by_instrument,
    }


def save_backtest_outputs(
    result: dict[str, object],
    json_path: Path = DEFAULT_OUTPUT_JSON,
    markdown_path: Path = DEFAULT_OUTPUT_MD,
) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_backtest_markdown(result), encoding="utf-8")


def _run_instrument_backtest(
    client: OandaClient,
    config: BacktestConfig,
    cache_dir: Path,
) -> dict[str, object]:
    fetch_start = config.start - timedelta(days=90)
    h4 = _load_or_fetch_candles(client, config.instrument, "H4", fetch_start, config.end, cache_dir)
    h1 = _load_or_fetch_candles(client, config.instrument, "H1", fetch_start, config.end, cache_dir)
    m15 = _load_or_fetch_candles(client, config.instrument, "M15", fetch_start, config.end + timedelta(days=3), cache_dir)
    m5 = [] if config.strategy_mode == "m15_simplified" else _load_or_fetch_candles(client, config.instrument, "M5", fetch_start, config.end + timedelta(days=3), cache_dir)
    trades: list[dict[str, object]] = []
    rejected_reasons: dict[str, int] = {}
    evaluated_scans = 0
    seen_zones: set[str] = set()
    seen_htf_zones: set[str] = set()
    blocked_until_index = 0

    start_index = _first_index_at_or_after(m15, config.start)
    end_index = _first_index_at_or_after(m15, config.end)
    for index in range(start_index, end_index, max(1, config.scan_interval_bars)):
        if index < blocked_until_index:
            continue
        evaluated_scans += 1
        now = m15[index].time
        state = _replay_state(config, h4, h1, m15[: index + 1], [candle for candle in m5 if candle.time <= now], now)
        candidate, rejected_reason = _candidate_from_replay_state(config, state, now)
        if candidate is None:
            rejected_reasons[rejected_reason] = rejected_reasons.get(rejected_reason, 0) + 1
            continue
        key = f"{config.instrument}:{candidate.side}:{candidate.entry_low:.5f}-{candidate.entry_high:.5f}"
        if key in seen_zones:
            continue
        htf_key = _htf_zone_key(state)
        if config.one_trade_per_htf_zone and htf_key and htf_key in seen_htf_zones:
            rejected_reasons["htf_zone_already_tested"] = rejected_reasons.get("htf_zone_already_tested", 0) + 1
            continue
        seen_zones.add(key)
        trade = _simulate_trade(config, candidate, state, m15, m5, index, rejected_reason)
        trades.append(trade)
        if htf_key:
            seen_htf_zones.add(htf_key)
        blocked_until_index = int(trade.get("exit_index", index + 1)) + 1
        if config.max_trades and len(trades) >= config.max_trades:
            break
    return {
        "trades": trades,
        "diagnostics": {
            "evaluated_scans": evaluated_scans,
            "rejected_reasons": rejected_reasons,
        },
    }


def _replay_state(
    config: BacktestConfig,
    h4: list[Candle],
    h1: list[Candle],
    m15: list[Candle],
    m5: list[Candle],
    now: datetime,
) -> ReplayState:
    h4_window = [candle for candle in h4 if candle.time <= now][-config.h4_lookback :]
    h1_window = [candle for candle in h1 if candle.time <= now][-config.h1_lookback :]
    m15_window = m15[-config.m15_lookback :]
    m5_window = m5[-config.m5_lookback :]
    h4_bias = detect_bias(h4_window)
    h1_bias = detect_bias(h1_window)
    latest_price = m15_window[-1].close
    if config.strategy_mode == "m15_simplified":
        bias = detect_bias(m15_window)
        narrative_candles = m15_window
        narrative_timeframe = "M15"
    else:
        bias = _effective_bias(h4_bias, h1_bias, "H1")
        narrative_candles = h1_window if _uses_refinement_bias(h4_bias, h1_bias) else h4_window
        narrative_timeframe = "H1" if _uses_refinement_bias(h4_bias, h1_bias) else "H4"
    preliminary_narrative = build_htf_narrative(
        candles=narrative_candles,
        zones=[],
        direction=bias.direction,
        timeframe=narrative_timeframe,
    )
    active_from_time = preliminary_narrative.active_from_time if preliminary_narrative else None
    if config.strategy_mode == "m15_simplified":
        htf_pois = []
        zone_ladder = []
    else:
        h4_pois = detect_htf_pois(h4_window, latest_price, active_from_time=active_from_time, bias_direction=bias.direction, timeframe="H4")
        h1_pois = detect_htf_pois(h1_window, latest_price, active_from_time=active_from_time, bias_direction=bias.direction, timeframe="H1")
        zone_ladder = detect_zone_ladder(h4_window, h1_window, latest_price, bias.direction, active_from_time=active_from_time)
        htf_pois = h1_pois if _should_use_refinement_pois(h4_pois, h1_pois, h4_bias) else h4_pois + h1_pois
    narrative = build_htf_narrative(narrative_candles, zone_ladder, bias.direction, narrative_timeframe)
    recent_setups, _swings, _sweeps = find_recent_setups(m15_window, bias, limit=5)
    recent_setups = _directional_setups(recent_setups, bias)
    if bias.direction == "neutral":
        recent_setups = []
    elif config.strategy_mode == "m15_simplified":
        pass
    else:
        recent_setups = [
            _apply_htf_poi_sequence(m15_window, setup, htf_pois, latest_price)
            for setup in recent_setups
        ]
    recent_setups = _rank_poi_adjusted_setups(recent_setups)[:5]
    primary_setup = recent_setups[0] if recent_setups else None
    refined_setup = None if config.strategy_mode == "m15_simplified" else _refined_m5_setup(m5_window, bias, primary_setup)
    relevant_htf_poi = nearest_relevant_poi(htf_pois, primary_setup.side, latest_price) if primary_setup else None
    htf_poi_sequence = "" if config.strategy_mode == "m15_simplified" else _htf_poi_sequence_state(m15_window, primary_setup, relevant_htf_poi)
    status, action = classify_state(bias, primary_setup, relevant_htf_poi, zone_ladder)
    _ = detect_fair_value_gaps(m15_window)
    return ReplayState(
        bias=bias,
        h4_bias=h4_bias,
        h1_bias=h1_bias,
        narrative=narrative,
        htf_pois=htf_pois,
        zone_ladder=zone_ladder,
        primary_setup=primary_setup,
        relevant_htf_poi=relevant_htf_poi,
        htf_poi_sequence=htf_poi_sequence,
        status=status,
        action=action,
        latest_price=latest_price,
        entry_candles=m15_window,
        h1_candles=h1_window,
        h4_candles=h4_window,
        refined_entry_candles=m5_window,
        refined_setup=refined_setup,
    )


def _candidate_from_replay_state(
    config: BacktestConfig,
    state: ReplayState,
    now: datetime,
) -> tuple[SignalCandidate | None, str]:
    setup = state.primary_setup
    if setup is None:
        return None, "no_primary_setup"
    actionable_statuses = {"entry_candidate_now"}
    actionable_states = {"at_entry_zone_now"}
    if config.strategy_mode == "m15_simplified":
        actionable_statuses.add("wait_for_pullback")
        actionable_states.add("waiting_for_first_pullback")
    if state.status not in actionable_statuses:
        return None, f"status_{state.status}"
    if state.htf_poi_sequence not in {"", "valid"}:
        return None, f"htf_poi_sequence_{state.htf_poi_sequence}"
    if setup.current_state not in actionable_states:
        return None, f"current_state_{setup.current_state}"
    if setup.quality_score < config.min_setup_quality:
        return None, f"quality_{setup.quality_score}"
    bos_age_hours = _bos_age_hours(setup, state.entry_candles, now)
    if bos_age_hours is None or bos_age_hours > config.max_bos_age_hours:
        return None, f"bos_age_{bos_age_hours}"
    if setup.current_state == "at_entry_zone_now" and not (setup.entry_zone.low <= state.latest_price <= setup.entry_zone.high):
        return None, "latest_price_outside_zone"
    if config.strategy_mode != "m15_simplified" and config.require_h1_alignment and state.h1_bias.direction != state.bias.direction:
        return None, f"h1_alignment_{state.h1_bias.direction}_vs_{state.bias.direction}"
    require_htf_poi_touched_now = config.require_htf_poi_touched_now and config.strategy_mode != "m15_simplified"
    require_refined_entry = config.require_refined_entry and config.strategy_mode != "m15_simplified"
    if require_htf_poi_touched_now and (
        state.relevant_htf_poi is None or not state.relevant_htf_poi.touched_now
    ):
        return None, "htf_poi_not_touched_now"
    if require_refined_entry and state.refined_setup is None:
        return None, "no_refined_entry_setup"
    candidates: list[SignalCandidate | None] = []
    if state.refined_setup is not None:
        candidates.append(_candidate_from_setup(config, state, state.refined_setup, state.refined_entry_candles, now, "M5"))
    if not require_refined_entry:
        candidates.append(_candidate_from_setup(config, state, setup, state.entry_candles, now, "M15"))
    candidates = [candidate for candidate in candidates if candidate is not None]
    if not candidates:
        return None, "no_entry_candidate_with_target"
    ranked = sorted(candidates, key=lambda item: float(item.available_r or 0.0), reverse=True)
    candidate = ranked[0]
    minimum_r = max(3.0, config.min_room_to_active_extreme_r)
    if candidate.available_r is None or candidate.available_r < minimum_r:
        return None, f"target_available_r_{candidate.available_r}"
    if config.require_premium_discount:
        premium_discount_reason = _premium_discount_rejection(candidate, state.narrative, config.premium_discount_edge)
        if premium_discount_reason:
            return None, premium_discount_reason
    if config.require_market_regime:
        regime_reason = _market_regime_rejection(config, state, candidate)
        if regime_reason:
            return None, regime_reason
    if config.require_a_grade_confluence:
        confluence_reason = _a_grade_confluence_rejection(config, state, setup, candidate)
        if confluence_reason:
            return None, confluence_reason
    if config.require_entry_reaction_candle and setup.current_state == "at_entry_zone_now":
        reaction_setup = state.refined_setup if candidate.entry_timeframe == "M5" else setup
        reaction_candles = state.refined_entry_candles if candidate.entry_timeframe == "M5" else state.entry_candles
        if reaction_setup is None or not _entry_reaction_candle(reaction_setup, reaction_candles):
            return None, "no_entry_reaction_candle"
    return (
        candidate,
        "",
    )


def _candidate_from_setup(
    config: BacktestConfig,
    state: ReplayState,
    setup: SetupCandidate,
    candles: list[Candle],
    now: datetime,
    entry_timeframe: str,
) -> SignalCandidate | None:
    side = setup.side.upper()
    if setup.bos.index < 0 or setup.bos.index >= len(candles):
        return None
    candidate = SignalCandidate(
        route="Rule",
        instrument=config.instrument,
        side=side,
        status=state.status,
        entry_low=setup.entry_zone.low,
        entry_high=setup.entry_zone.high,
        source=f"{entry_timeframe} {setup.entry_zone.source}",
        signal_time=now.isoformat(),
        sweep_price=setup.sweep.swept_price,
        bos_time=candles[setup.bos.index].time.isoformat(),
        notes=state.action,
        entry_timeframe=entry_timeframe,
    )
    entry_price = _entry_price(candidate)
    risk = abs(entry_price - _stop_loss(candidate, candles))
    target = select_trade_target(
        side=side,
        entry_price=entry_price,
        h1_candles=state.h1_candles,
        h4_candles=state.h4_candles,
        active_from_time=state.narrative.active_from_time if state.narrative else None,
        risk=risk,
        minimum_r=3.0,
        zones=state.zone_ladder,
        fixed_rr=config.rr,
    )
    if target is None:
        return None
    return SignalCandidate(
        route=candidate.route,
        instrument=candidate.instrument,
        side=candidate.side,
        status=candidate.status,
        entry_low=candidate.entry_low,
        entry_high=candidate.entry_high,
        source=candidate.source,
        signal_time=candidate.signal_time,
        sweep_price=candidate.sweep_price,
        bos_time=candidate.bos_time,
        notes=candidate.notes,
        target_price=target.price,
        target_timeframe=target.timeframe,
        target_reason=target.reason,
        available_r=available_r(target, entry_price, risk),
        entry_timeframe=entry_timeframe,
    )


def _refined_m5_setup(
    m5_candles: list[Candle],
    bias: Bias,
    m15_setup: SetupCandidate | None,
) -> SetupCandidate | None:
    if m15_setup is None or not m5_candles:
        return None
    setups, _swings, _sweeps = find_recent_setups(m5_candles, bias, limit=5)
    directional = [
        setup
        for setup in _directional_setups(setups, bias)
        if setup.side == m15_setup.side
        and setup.status == "candidate"
        and setup.current_state == "at_entry_zone_now"
        and setup.quality_score >= MIN_FORWARD_TEST_QUALITY
    ]
    if not directional:
        return None
    return sorted(directional, key=lambda item: (item.quality_score, item.bos.index), reverse=True)[0]


def _htf_zone_key(state: ReplayState) -> str:
    poi = state.relevant_htf_poi
    if poi is None:
        return ""
    return f"{poi.side}:{poi.low:.3f}-{poi.high:.3f}"


def _entry_reaction_candle(setup: SetupCandidate, candles: list[Candle]) -> bool:
    if not candles:
        return False
    latest = candles[-1]
    zone_mid = (setup.entry_zone.low + setup.entry_zone.high) / 2
    if setup.side == "buy":
        return latest.close > latest.open and latest.close >= zone_mid
    return latest.close < latest.open and latest.close <= zone_mid


def _premium_discount_rejection(
    candidate: SignalCandidate,
    narrative: HtfNarrative | None,
    edge: float,
) -> str:
    if narrative is None:
        return "no_htf_narrative"
    price_range = narrative.range_high - narrative.range_low
    if price_range <= 0:
        return "invalid_htf_range"
    zone_mid = (candidate.entry_low + candidate.entry_high) / 2
    position = (zone_mid - narrative.range_low) / price_range
    buy_limit = max(0.0, min(0.5, edge))
    sell_limit = 1.0 - buy_limit
    if candidate.side == "BUY" and position > buy_limit:
        return f"not_in_discount_{position:.2f}"
    if candidate.side == "SELL" and position < sell_limit:
        return f"not_in_premium_{position:.2f}"
    return ""


def _market_regime_rejection(
    config: BacktestConfig,
    state: ReplayState,
    candidate: SignalCandidate,
) -> str:
    narrative = state.narrative
    if narrative is None:
        return "regime_no_narrative"
    if config.regime_require_pullback_phase and narrative.phase != "pullback_into_range":
        return f"regime_phase_{narrative.phase}"

    active_range = narrative.range_high - narrative.range_low
    avg_range = average_range(state.entry_candles, period=30)
    if avg_range <= 0 or active_range / avg_range < config.regime_min_range_atr:
        ratio = active_range / avg_range if avg_range > 0 else 0.0
        return f"regime_chop_range_{ratio:.1f}"

    efficiency = _directional_efficiency(state.entry_candles, lookback=96)
    if efficiency < config.regime_min_directional_efficiency:
        return f"regime_chop_efficiency_{efficiency:.2f}"

    if candidate.side == "BUY" and state.bias.direction != "bullish":
        return f"regime_bias_{state.bias.direction}"
    if candidate.side == "SELL" and state.bias.direction != "bearish":
        return f"regime_bias_{state.bias.direction}"
    return ""


def _directional_efficiency(candles: list[Candle], lookback: int) -> float:
    sample = candles[-lookback:] if len(candles) > lookback else candles
    if len(sample) < 2:
        return 0.0
    path = sum(candle.high - candle.low for candle in sample)
    if path <= 0:
        return 0.0
    return abs(sample[-1].close - sample[0].open) / path


def _a_grade_confluence_rejection(
    config: BacktestConfig,
    state: ReplayState,
    setup: SetupCandidate,
    candidate: SignalCandidate,
) -> str:
    grade = grade_setup_confluence(
        setup=setup,
        candles=state.entry_candles,
        narrative=state.narrative,
        side=candidate.side,
        target_r=candidate.available_r,
        config=_confluence_config(config),
    )
    if grade.passed:
        return ""
    failures = "_".join(grade.failures[:3]) if grade.failures else "score"
    return f"a_grade_{grade.score}_of_{grade.max_score}_{failures}"


def _confluence_config(config: BacktestConfig) -> ConfluenceConfig:
    return ConfluenceConfig(
        min_score=config.a_grade_min_score,
        premium_discount_edge=config.premium_discount_edge,
        require_pullback_phase=config.regime_require_pullback_phase,
        min_range_atr=config.regime_min_range_atr,
        min_target_r=max(3.0, config.min_room_to_active_extreme_r),
    )


def _simulate_trade(
    config: BacktestConfig,
    candidate: SignalCandidate,
    state: ReplayState,
    m15: list[Candle],
    m5: list[Candle],
    signal_index: int,
    rejected_reason: str,
) -> dict[str, object]:
    trade_candles = m5 if candidate.entry_timeframe == "M5" else m15
    entry_context_candles = state.refined_entry_candles if candidate.entry_timeframe == "M5" else state.entry_candles
    signal_time = m15[signal_index].time
    trade_signal_index = _first_index_at_or_after(trade_candles, signal_time)
    timeout_bars = config.timeout_bars * 3 if candidate.entry_timeframe == "M5" else config.timeout_bars
    entry_price = _entry_price(candidate)
    stop_loss = _stop_loss(candidate, entry_context_candles)
    target_price = candidate.target_price or _target_price(entry_price, stop_loss, candidate.side, config.rr)
    risk = abs(entry_price - stop_loss) or 1.0
    confluence = _trade_confluence_snapshot(config, state, candidate)
    result = "timeout"
    result_time = ""
    exit_index = min(len(trade_candles) - 1, trade_signal_index + timeout_bars)
    max_favorable = 0.0
    max_adverse = 0.0
    fill_index = _first_entry_fill_index(
        candles=trade_candles,
        start_index=trade_signal_index + 1,
        end_index=exit_index,
        entry_price=entry_price,
        side=candidate.side,
        entry_low=candidate.entry_low,
        entry_high=candidate.entry_high,
        require_reaction=config.require_entry_reaction_candle,
    )
    if fill_index is None:
        result = "no_fill"
        result_time = trade_candles[exit_index].time.isoformat() if exit_index < len(trade_candles) else ""
        room_to_active_extreme_r = candidate.available_r
        return {
            "instrument": config.instrument,
            "strategy_mode": config.strategy_mode,
            "side": candidate.side,
            "signal_time": candidate.signal_time,
            "entry_price": entry_price,
            "entry_low": candidate.entry_low,
            "entry_high": candidate.entry_high,
            "stop_loss": stop_loss,
            "target_price": target_price,
            "target_timeframe": candidate.target_timeframe,
            "target_reason": candidate.target_reason,
            "available_r": candidate.available_r,
            "entry_timeframe": candidate.entry_timeframe,
            "risk": risk,
            "result": result,
            "result_time": result_time,
            "exit_index": exit_index,
            "realized_r": 0.0,
            "max_favorable_r": 0.0,
            "max_adverse_r": 0.0,
            "room_to_active_extreme_r": room_to_active_extreme_r,
            "confluence": confluence,
            "failure_tags": ["entry_not_filled"],
            "rejected_reason": rejected_reason,
            "decision_snapshot": _decision_snapshot(state),
        }

    protected = False
    for index in range(fill_index, min(len(trade_candles), trade_signal_index + timeout_bars + 1)):
        candle = trade_candles[index]
        if candidate.side == "BUY":
            max_favorable = max(max_favorable, candle.high - entry_price)
            max_adverse = max(max_adverse, entry_price - candle.low)
            hit_tp = candle.high >= target_price
            if config.breakeven_after_r > 0 and not protected and max_favorable / risk >= config.breakeven_after_r:
                protected = True
            hit_sl = candle.low <= (entry_price if protected else stop_loss)
        else:
            max_favorable = max(max_favorable, entry_price - candle.low)
            max_adverse = max(max_adverse, candle.high - entry_price)
            hit_tp = candle.low <= target_price
            if config.breakeven_after_r > 0 and not protected and max_favorable / risk >= config.breakeven_after_r:
                protected = True
            hit_sl = candle.high >= (entry_price if protected else stop_loss)
        if hit_sl and hit_tp:
            result = "sl_hit_ambiguous"
            result_time = candle.time.isoformat()
            exit_index = index
            break
        if hit_sl:
            result = "breakeven" if protected else "sl_hit"
            result_time = candle.time.isoformat()
            exit_index = index
            break
        if hit_tp:
            result = "tp_hit"
            result_time = candle.time.isoformat()
            exit_index = index
            break
    if not result_time and exit_index < len(trade_candles):
        result_time = trade_candles[exit_index].time.isoformat()

    realized_r = float(candidate.available_r or config.rr) if result == "tp_hit" else -1.0 if result in {"sl_hit", "sl_hit_ambiguous"} else 0.0
    room_to_active_extreme_r = candidate.available_r
    return {
        "instrument": config.instrument,
        "strategy_mode": config.strategy_mode,
        "side": candidate.side,
        "signal_time": candidate.signal_time,
        "entry_price": entry_price,
        "entry_low": candidate.entry_low,
        "entry_high": candidate.entry_high,
        "stop_loss": stop_loss,
        "target_price": target_price,
        "target_timeframe": candidate.target_timeframe,
        "target_reason": candidate.target_reason,
        "available_r": candidate.available_r,
        "entry_timeframe": candidate.entry_timeframe,
        "risk": risk,
        "result": result,
        "result_time": result_time,
        "exit_index": exit_index,
        "realized_r": realized_r,
        "max_favorable_r": max_favorable / risk,
        "max_adverse_r": max_adverse / risk,
        "room_to_active_extreme_r": room_to_active_extreme_r,
        "confluence": confluence,
        "failure_tags": _failure_tags(
            result=result,
            max_favorable_r=max_favorable / risk,
            max_adverse_r=max_adverse / risk,
            room_to_active_extreme_r=room_to_active_extreme_r,
            state=state,
            signal_time=signal_time,
            strategy_mode=config.strategy_mode,
        ),
        "rejected_reason": rejected_reason,
        "decision_snapshot": _decision_snapshot(state),
    }


def _trade_confluence_snapshot(
    config: BacktestConfig,
    state: ReplayState,
    candidate: SignalCandidate,
) -> dict[str, object] | None:
    setup = state.primary_setup
    if setup is None:
        return None
    grade = grade_setup_confluence(
        setup=setup,
        candles=state.entry_candles,
        narrative=state.narrative,
        side=candidate.side,
        target_r=candidate.available_r,
        config=_confluence_config(config),
    )
    return confluence_snapshot(grade)


def _first_entry_fill_index(
    candles: list[Candle],
    start_index: int,
    end_index: int,
    entry_price: float,
    side: str,
    entry_low: float,
    entry_high: float,
    require_reaction: bool,
) -> int | None:
    for index in range(start_index, min(end_index + 1, len(candles))):
        candle = candles[index]
        if candle.low <= entry_price <= candle.high and (
            not require_reaction or _candle_reacts_from_zone(candle, side, entry_low, entry_high)
        ):
            return index
    return None


def _candle_reacts_from_zone(
    candle: Candle,
    side: str,
    entry_low: float,
    entry_high: float,
) -> bool:
    zone_mid = (entry_low + entry_high) / 2
    if side == "BUY":
        return candle.close > candle.open and candle.close >= zone_mid
    if side == "SELL":
        return candle.close < candle.open and candle.close <= zone_mid
    return False


def _decision_snapshot(state: ReplayState) -> dict[str, object]:
    setup = state.primary_setup
    return {
        "status": state.status,
        "action": state.action,
        "latest_price": state.latest_price,
        "bias": asdict(state.bias),
        "h4_bias": asdict(state.h4_bias),
        "h1_bias": asdict(state.h1_bias),
        "htf_poi_sequence": state.htf_poi_sequence,
        "narrative": _narrative_snapshot(state.narrative),
        "relevant_htf_poi": _poi_snapshot(state.relevant_htf_poi),
        "zone_ladder": [_zone_snapshot(zone) for zone in state.zone_ladder[:5]],
        "setup": _setup_snapshot(setup, state.entry_candles) if setup else None,
        "refined_setup": _setup_snapshot(state.refined_setup, state.refined_entry_candles) if state.refined_setup else None,
    }


def _room_to_active_extreme_r(
    candidate: SignalCandidate,
    entry_price: float,
    risk: float,
    state: ReplayState,
) -> float | None:
    narrative = state.narrative
    if narrative is None or risk <= 0:
        return None
    if candidate.side == "BUY":
        return (narrative.range_high - entry_price) / risk
    return (entry_price - narrative.range_low) / risk


def _failure_tags(
    result: str,
    max_favorable_r: float,
    max_adverse_r: float,
    room_to_active_extreme_r: float | None,
    state: ReplayState,
    signal_time: datetime,
    strategy_mode: str,
) -> list[str]:
    if result in {"tp_hit", "breakeven"}:
        return []
    tags: list[str] = []
    setup = state.primary_setup
    if max_favorable_r < 1.0:
        tags.append("failed_before_1r")
    elif max_favorable_r < 2.0:
        tags.append("failed_between_1r_and_2r")
    if max_adverse_r >= 1.0 and max_favorable_r < 1.0:
        tags.append("immediate_stop_pressure")
    if room_to_active_extreme_r is None or room_to_active_extreme_r < 3.0:
        tags.append("no_clean_room_to_3r")
    if strategy_mode != "m15_simplified":
        if state.h1_bias.direction != state.bias.direction:
            tags.append("h1_bias_conflict")
        if state.relevant_htf_poi is None:
            tags.append("no_relevant_htf_poi")
        elif not state.relevant_htf_poi.touched_now:
            tags.append("htf_poi_not_currently_touched")
    if setup is not None:
        bos_age_hours = _bos_age_hours(setup, state.entry_candles, signal_time)
        if bos_age_hours is not None and bos_age_hours > 6:
            tags.append("bos_older_than_6h")
        if not _entry_reaction_candle(setup, state.entry_candles):
            tags.append("no_entry_reaction_candle")
    return tags


def _setup_snapshot(setup: SetupCandidate, candles: list[Candle]) -> dict[str, object]:
    return {
        "side": setup.side,
        "status": setup.status,
        "reason": setup.reason,
        "quality_score": setup.quality_score,
        "quality_notes": list(setup.quality_notes),
        "current_state": setup.current_state,
        "entry_zone_low": setup.entry_zone.low,
        "entry_zone_high": setup.entry_zone.high,
        "entry_zone_source": setup.entry_zone.source,
        "sweep_time": candles[setup.sweep.index].time.isoformat(),
        "sweep_price": setup.sweep.swept_price,
        "sweep_kind": setup.sweep.kind,
        "bos_time": candles[setup.bos.index].time.isoformat(),
        "bos_price": setup.bos.broken_price,
        "bos_direction": setup.bos.direction,
    }


def _narrative_snapshot(narrative: HtfNarrative | None) -> dict[str, object] | None:
    if narrative is None:
        return None
    return {
        "timeframe": narrative.timeframe,
        "direction": narrative.direction,
        "phase": narrative.phase,
        "active_from_time": narrative.active_from_time.isoformat(),
        "active_from_anchor": narrative.active_from_anchor,
        "range_low": narrative.range_low,
        "range_high": narrative.range_high,
        "highest_high": narrative.highest_high,
        "lowest_low": narrative.lowest_low,
    }


def _poi_snapshot(poi: PointOfInterest | None) -> dict[str, object] | None:
    if poi is None:
        return None
    return {
        "low": poi.low,
        "high": poi.high,
        "side": poi.side,
        "source": poi.source,
        "touched_now": poi.touched_now,
        "distance_to_price": poi.distance_to_price,
    }


def _zone_snapshot(zone: ZoneLadderItem) -> dict[str, object]:
    return {
        "low": zone.low,
        "high": zone.high,
        "side": zone.side,
        "timeframe": zone.timeframe,
        "state": zone.state,
        "source": zone.source,
        "reason": zone.reason,
    }


def _load_or_fetch_candles(
    client: OandaClient,
    instrument: str,
    granularity: str,
    start: datetime,
    end: datetime,
    cache_dir: Path,
) -> list[Candle]:
    cache_path = cache_dir / instrument / f"{granularity}_{start.date()}_{end.date()}.json"
    if cache_path.exists():
        return [_candle_from_record(record) for record in json.loads(cache_path.read_text(encoding="utf-8"))]
    candles = _fetch_range_chunked(client, instrument, granularity, start, end)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps([_candle_record(candle) for candle in candles], indent=2), encoding="utf-8")
    return candles


def _fetch_range_chunked(
    client: OandaClient,
    instrument: str,
    granularity: str,
    start: datetime,
    end: datetime,
) -> list[Candle]:
    step = timedelta(seconds=GRANULARITY_SECONDS[granularity] * 4900)
    current = start
    by_time: dict[str, Candle] = {}
    while current < end:
        chunk_end = min(end, current + step)
        candles = client.fetch_candles_range(instrument, granularity, current, chunk_end)
        for candle in candles:
            if candle.complete:
                by_time[candle.time.isoformat()] = candle
        current = chunk_end
        time.sleep(0.2)
    return sorted(by_time.values(), key=lambda candle: candle.time)


def _candle_record(candle: Candle) -> dict[str, object]:
    return {
        "time": candle.time.isoformat(),
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
        "complete": candle.complete,
    }


def _candle_from_record(record: dict[str, object]) -> Candle:
    return Candle(
        time=datetime.fromisoformat(str(record["time"])),
        open=float(record["open"]),
        high=float(record["high"]),
        low=float(record["low"]),
        close=float(record["close"]),
        volume=int(record["volume"]),
        complete=bool(record["complete"]),
    )


def _first_index_at_or_after(candles: list[Candle], value: datetime) -> int:
    for index, candle in enumerate(candles):
        if candle.time >= value:
            return index
    return len(candles)


def _bos_age_hours(setup: SetupCandidate, candles: list[Candle], now: datetime) -> float | None:
    if setup.bos.index < 0 or setup.bos.index >= len(candles):
        return None
    return (now - candles[setup.bos.index].time).total_seconds() / 3600


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 3)

"""Historical backtester for the non-rule routes (M15_SIMPLE, DYNAMIC_SCORE, REGIME_RANGE).

It replays M15 history bar by bar, generates each route's signal with the exact
live signal functions, applies the same live entry gate, and then simulates the
partial-then-trail exit by driving the live ``_new_test`` / ``_update_partial_trail_test``
code with growing historical candle slices. Because it reuses the production
functions, the realized-R results are directly comparable to the live forward tests.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

from fx_annotation.backtesting import (
    DEFAULT_CACHE_DIR,
    BacktestConfig,
    _candidate_from_replay_state,
    _load_or_fetch_candles,
    _replay_state,
)
from fx_annotation.bias import detect_bias
from fx_annotation.candles import Candle
from fx_annotation.config import PROJECT_ROOT
from fx_annotation.dynamic_scoring import best_dynamic_score, best_regime_range_signal
from fx_annotation.forward_testing import (
    DYNAMIC_SCORE_MINIMUM,
    REGIME_RANGE_MINIMUM,
    REGIME_RANGE_OVERLAP_PENALTY,
    SignalCandidate,
    _dynamic_score_candidate,
    _entry_price,
    _float_or_none,
    _m15_directional_setups,
    _m15_simple_candidate,
    _new_test,
    _pip_size,
    _regime_range_candidate,
    _session_for_signal_time,
    _strict_live_entry_rejection,
    _update_partial_trail_test,
)
from fx_annotation.setups import find_recent_setups
from fx_annotation.structure import average_range


DEFAULT_OUTPUT_JSON = PROJECT_ROOT / "outputs" / "backtests" / "route_backtest.json"
DEFAULT_OUTPUT_MD = PROJECT_ROOT / "outputs" / "backtests" / "route_backtest.md"

SUPPORTED_ROUTES = ("RULE", "M15_SIMPLE", "DYNAMIC_SCORE", "REGIME_RANGE")
SCORE_ROUTES = ("M15_SIMPLE", "DYNAMIC_SCORE", "REGIME_RANGE")
RR_VALUES: tuple[float, ...] = (3.0,)

# Round-trip transaction cost in pips (spread + typical slippage) per instrument.
# These are realistic retail estimates; tune to your broker with --spread-pips.
DEFAULT_COST_PIPS: dict[str, float] = {
    "EUR_USD": 1.0,
    "GBP_USD": 1.4,
    "USD_JPY": 1.0,
    "USD_CAD": 1.6,
    "AUD_USD": 1.2,
    "NZD_USD": 1.8,
    "EUR_JPY": 1.8,
    "GBP_JPY": 2.5,
    "XAU_USD": 4.0,
    "BTC_USD": 40.0,
}
DEFAULT_COST_PIPS_FALLBACK = 1.5


def _cost_r(instrument: str, risk: float, spread_pips: float | None) -> float:
    if risk <= 0:
        return 0.0
    pips = spread_pips if spread_pips is not None else DEFAULT_COST_PIPS.get(instrument, DEFAULT_COST_PIPS_FALLBACK)
    return (pips * _pip_size(instrument)) / risk


@dataclass(frozen=True)
class RouteBacktestConfig:
    instrument: str
    start: datetime
    end: datetime
    routes: tuple[str, ...] = SUPPORTED_ROUTES
    timeout_bars: int = 48
    scan_interval_bars: int = 1
    m15_lookback: int = 400
    max_wait_bars: int = 12
    spread_pips: float | None = None
    rule_require_a_grade: bool = True
    rule_a_grade_min_score: int = 5
    rule_max_bos_age_hours: float = 6.0
    rule_min_setup_quality: int = 3
    rule_refined_entry: bool = False  # GATE: require an M5-confirmed entry (lower-TF turn) — reduces frequency
    rule_m5_stop: bool = False  # MILK (stop only): keep the M15 entry, tighten only the stop to M5 structure
    rule_m5_entry: bool = False  # MILK (entry+stop): relocate the entry to the M5 reaction in the zone, tight M5 stop
    # Exit model: "partial_trail" (legacy, bank 50% @1.5R + trail) or
    # "scale_trail" (day-trade: tight stop, multi-R ladder, trailing runner).
    exit_model: str = "partial_trail"
    stop_mode: str = "sweep"  # "sweep" (wide) or "zone" (tight, just past the entry zone)
    stop_buffer_atr: float = 0.1
    ladder: tuple[tuple[float, float], ...] = ((1.0, 0.25), (2.0, 0.25), (3.0, 0.25))
    trail_distance_r: float = 1.0
    max_hold_bars: int = 96  # day-trade window (~1 day on M15); raise for swing mode
    entry_confirmation: str = "touch"  # "touch" (fill on tap) or "rejection" (wait for reaction close)


DEFAULT_LADDER: tuple[tuple[float, float], ...] = ((1.0, 0.25), (2.0, 0.25), (3.0, 0.25))


def parse_ladder(text: str) -> tuple[tuple[float, float], ...]:
    """Parse a ladder spec like "1:0.25,2:0.25,3:0.25" into ((1.0,0.25),...)."""
    rungs: list[tuple[float, float]] = []
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        r_str, frac_str = chunk.split(":")
        rungs.append((float(r_str), float(frac_str)))
    total = sum(frac for _, frac in rungs)
    if total > 1.0 + 1e-9:
        raise ValueError(f"Ladder fractions sum to {total:.2f} (>1.0); leave room for the trailed runner.")
    return tuple(rungs)


def run_route_backtest(
    client: object,
    configs: list[RouteBacktestConfig],
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
        "engine": "route_partial_trail",
        "exit_model": "partial_then_trail",
        "trades": trades,
        "summary": summarize_route_backtest(trades),
        "diagnostics": diagnostics,
    }


def _run_instrument_backtest(
    client: object,
    config: RouteBacktestConfig,
    cache_dir: Path,
) -> dict[str, object]:
    fetch_start = config.start - timedelta(days=90)
    m15 = _load_or_fetch_candles(
        client, config.instrument, "M15", fetch_start, config.end + timedelta(days=3), cache_dir
    )
    needs_htf = "RULE" in config.routes
    h4 = _load_or_fetch_candles(client, config.instrument, "H4", fetch_start, config.end, cache_dir) if needs_htf else []
    h1 = _load_or_fetch_candles(client, config.instrument, "H1", fetch_start, config.end, cache_dir) if needs_htf else []
    needs_m5 = needs_htf and (config.rule_refined_entry or config.rule_m5_stop or config.rule_m5_entry)
    m5 = _load_or_fetch_candles(client, config.instrument, "M5", fetch_start, config.end + timedelta(days=3), cache_dir) if needs_m5 else []
    rule_config = _rule_config(config) if needs_htf else None

    trades: list[dict[str, object]] = []
    no_fill: dict[str, int] = {route: 0 for route in config.routes}
    signals: dict[str, int] = {route: 0 for route in config.routes}
    blocked_until: dict[str, int] = {route: 0 for route in config.routes}

    start_index = _first_index_at_or_after(m15, config.start)
    end_index = _first_index_at_or_after(m15, config.end)
    for index in range(start_index, end_index, max(1, config.scan_interval_bars)):
        window = m15[max(0, index + 1 - config.m15_lookback) : index + 1]
        if not window:
            continue
        signal_time_iso = m15[index].time.isoformat()
        now = m15[index].time
        for route in config.routes:
            if index < blocked_until[route]:
                continue
            if route == "RULE":
                # Gate mode feeds M5 to the candidate (require M5 confirmation, fewer trades).
                # Milk mode keeps the M15 entry untouched (full frequency) and refines only the stop later.
                m5_to_now = m5[: _first_index_at_or_after(m5, now) + 1] if (m5 and config.rule_refined_entry) else []
                candidate = _rule_candidate(rule_config, h4, h1, m15[: index + 1], m5_to_now, now)
            else:
                candidate = _candidate_for_route(route, config.instrument, window, signal_time_iso)
                if candidate is not None and _strict_live_entry_rejection(candidate, window, RR_VALUES):
                    candidate = None
            if candidate is None:
                continue
            signals[route] += 1
            on_m5_gate = m5 and config.rule_refined_entry and getattr(candidate, "entry_timeframe", "M15") == "M5"
            ran_on_m5 = False
            if config.exit_model == "scale_trail":
                if on_m5_gate or (route == "RULE" and config.rule_m5_entry and m5):
                    m5_idx = _first_index_at_or_after(m5, now)
                    if m5_idx >= len(m5):
                        continue
                    ran_on_m5 = True
                    trade = _simulate_scale_trail(candidate, m5, m5_idx, config, bar_scale=3, entry_relocate=config.rule_m5_entry)
                elif route == "RULE" and config.rule_m5_stop and m5:
                    trade = _simulate_scale_trail(candidate, m15, index, config, m5_stop=m5)
                else:
                    trade = _simulate_scale_trail(candidate, m15, index, config)
            else:
                trade = _simulate_partial_trail(candidate, m15, index, config)
            if trade.get("result") == "no_fill":
                no_fill[route] += 1
                continue
            trades.append(trade)
            # Block overlap in M15-index space: an M5 sim returns an M5 exit index, so
            # convert it back via the exit time before comparing against M15 bars.
            exit_i = int(trade.get("exit_index", index + 1))
            if ran_on_m5:
                exit_time = m5[min(exit_i, len(m5) - 1)].time
                blocked_until[route] = _first_index_at_or_after(m15, exit_time) + 1
            else:
                blocked_until[route] = exit_i + 1
    return {
        "trades": trades,
        "diagnostics": {
            "candidates": signals,
            "no_fill": no_fill,
            "m15_candles": len(m15),
        },
    }


def _rule_config(config: RouteBacktestConfig) -> BacktestConfig:
    return BacktestConfig(
        instrument=config.instrument,
        start=config.start,
        end=config.end,
        strategy_mode="mtf_sniper",
        rr=3.0,
        timeout_bars=config.timeout_bars,
        max_bos_age_hours=config.rule_max_bos_age_hours,
        min_setup_quality=config.rule_min_setup_quality,
        require_a_grade_confluence=config.rule_require_a_grade,
        a_grade_min_score=config.rule_a_grade_min_score,
        require_refined_entry=config.rule_refined_entry,
    )


def _rule_candidate(
    rule_config: BacktestConfig | None,
    h4: list[Candle],
    h1: list[Candle],
    m15_to_now: list[Candle],
    m5_to_now: list[Candle],
    now: datetime,
) -> SignalCandidate | None:
    if rule_config is None:
        return None
    state = _replay_state(rule_config, h4, h1, m15_to_now, m5_to_now, now)
    candidate, _reason = _candidate_from_replay_state(rule_config, state, now)
    return candidate


def _candidate_for_route(
    route: str,
    instrument: str,
    window: list[Candle],
    signal_time_iso: str,
) -> SignalCandidate | None:
    if route == "DYNAMIC_SCORE":
        signal = best_dynamic_score(window)
        if signal is None or signal.score < DYNAMIC_SCORE_MINIMUM:
            return None
        return _dynamic_score_candidate(instrument, signal, signal_time_iso)
    if route == "REGIME_RANGE":
        signal = best_regime_range_signal(window)
        if signal is None:
            return None
        session = _session_for_signal_time(signal_time_iso)
        minimum = REGIME_RANGE_MINIMUM + (
            REGIME_RANGE_OVERLAP_PENALTY if session == "london_new_york_overlap" else 0.0
        )
        if signal.score < minimum:
            return None
        return _regime_range_candidate(instrument, signal, signal_time_iso, session)
    if route == "M15_SIMPLE":
        if len(window) < 80:
            return None
        bias = detect_bias(window)
        if bias.direction not in {"bullish", "bearish"}:
            return None
        setups, _swings, _sweeps = find_recent_setups(window, bias, limit=5)
        for setup in _m15_directional_setups(setups, bias):
            candidate = _m15_simple_candidate(instrument, setup, window, signal_time_iso)
            if candidate is not None:
                return candidate
        return None
    return None


def _simulate_partial_trail(
    candidate: SignalCandidate,
    m15: list[Candle],
    signal_index: int,
    config: RouteBacktestConfig,
) -> dict[str, object]:
    """Drive the live partial-trail test forward over historical candles.

    Returns a trade record with realized_r, outcome, best_r and timing. If the
    entry never fills within ``max_wait_bars``, the record's result is ``no_fill``.
    """
    signal_time_iso = m15[signal_index].time.isoformat()
    window_at_signal = m15[: signal_index + 1]
    test = _new_test(candidate, window_at_signal, RR_VALUES, signal_time_iso)

    fill_deadline = signal_index + config.max_wait_bars
    feed_limit = signal_index + config.max_wait_bars + config.timeout_bars + 5
    exit_index = signal_index
    for j in range(signal_index + 1, min(len(m15), feed_limit + 1)):
        now_iso = m15[j].time.isoformat()
        _update_partial_trail_test(test, m15[: j + 1], config.timeout_bars, now_iso)
        if test.get("status") == "closed":
            exit_index = j
            break
        if str(test.get("status")) == "waiting_entry" and j >= fill_deadline:
            break
        exit_index = j

    if test.get("status") != "closed":
        return {
            "route": candidate.route,
            "instrument": candidate.instrument,
            "side": candidate.side,
            "signal_time": signal_time_iso,
            "result": "no_fill" if str(test.get("status")) == "waiting_entry" else "unresolved",
            "realized_r": None,
            "outcome": None,
            "exit_index": exit_index,
        }

    entry_price = float(test.get("entry_price", 0.0))
    risk = float(test.get("risk", 0.0)) or 1.0
    peak = _float_or_none(test.get("runner_peak"))
    best_r = ((peak - entry_price) / risk if candidate.side == "BUY" else (entry_price - peak) / risk) if peak is not None else 0.0
    gross = _float_or_none(test.get("realized_r"))
    cost_r = _cost_r(candidate.instrument, risk, config.spread_pips)
    net = round(gross - cost_r, 4) if gross is not None else None
    return {
        "route": candidate.route,
        "instrument": candidate.instrument,
        "side": candidate.side,
        "signal_time": signal_time_iso,
        "entry_time": test.get("entry_time"),
        "exit_time": test.get("exit_time"),
        "entry_price": entry_price,
        "stop_loss": float(test.get("stop_loss", 0.0)),
        "risk": risk,
        "available_r": getattr(candidate, "available_r", None),
        "result": str(test.get("outcome")),
        "outcome": str(test.get("outcome")),
        "realized_r": gross,
        "realized_r_net": net,
        "cost_r": round(cost_r, 4),
        "runner_exit_r": _float_or_none(test.get("runner_exit_r")),
        "best_r": round(max(0.0, best_r), 4),
        "exit_index": exit_index,
    }


def _scale_trail_stop(candidate: SignalCandidate, window: list[Candle], config: RouteBacktestConfig) -> tuple[float, float]:
    """Return (entry_price, stop_loss) for the scale-trail model.

    In "zone" mode the stop sits just past the entry zone (tight) instead of at
    the sweep extreme, which shrinks risk and lifts the achievable R-multiple.
    """
    entry_price = _entry_price(candidate)
    avg_range = average_range(window, period=30)
    buffer = avg_range * config.stop_buffer_atr
    if config.stop_mode == "zone":
        if candidate.side == "BUY":
            stop = candidate.entry_low - buffer
        else:
            stop = candidate.entry_high + buffer
    else:  # "sweep" (wide, mirrors the legacy stop)
        if candidate.side == "BUY":
            base = min(v for v in (candidate.entry_low, candidate.sweep_price) if v is not None)
            stop = base - buffer
        else:
            base = max(v for v in (candidate.entry_high, candidate.sweep_price) if v is not None)
            stop = base + buffer
    return entry_price, stop


def _m5_refined_stop(
    side: str,
    entry_price: float,
    m5: list[Candle],
    fill_time: datetime,
    default_stop: float,
    buffer_atr: float,
    lookback: int = 6,
) -> float:
    """Tighten the stop to the M5 micro-structure around the moment of the tap.

    Keeps the M15 entry exactly where it is; only relocates the protective stop to
    just beyond the recent M5 swing extreme (the structure that, if broken, kills the
    trade). Returns the tighter stop only when it sits between the entry and the
    original M15 stop — otherwise it falls back to the M15 stop (never loosens).
    """
    window = [candle for candle in m5 if candle.time <= fill_time][-30:]
    if len(window) < 2:
        return default_stop
    buffer = average_range(window, period=min(30, len(window))) * buffer_atr
    recent = window[-lookback:]
    if side == "BUY":
        refined = min(candle.low for candle in recent) - buffer
        if default_stop < refined < entry_price:
            return refined
    else:
        refined = max(candle.high for candle in recent) + buffer
        if entry_price < refined < default_stop:
            return refined
    return default_stop


def _m5_entry_fill(
    candidate: SignalCandidate,
    m5: list[Candle],
    signal_index: int,
    buffer: float,
    max_wait_bars: int,
) -> tuple[int, float, float] | None:
    """Relocate the entry to the M5 reaction inside the M15 zone (the user's technique).

    Waits for price to come into the M15 zone, then for an M5 candle to *react* in the
    trade direction (a real rejection), and enters at that candle's close with a tight
    stop just beyond the recent M5 swing extreme. Entry is deeper and risk is smaller
    than the M15 zone-edge entry, so the same move yields more R.
    """
    side = candidate.side
    zlo, zhi = candidate.entry_low, candidate.entry_high
    last = min(len(m5), signal_index + 1 + max_wait_bars)
    entered = False
    for j in range(signal_index + 1, last):
        c = m5[j]
        body_mid = (c.high + c.low) / 2
        if side == "BUY":
            if c.low <= zhi:
                entered = True
            if entered and c.close > c.open and c.close >= body_mid:
                swing_low = min(x.low for x in m5[max(signal_index + 1, j - 3) : j + 1])
                stop = swing_low - buffer
                if stop < c.close:
                    return j, c.close, stop
        else:
            if c.high >= zlo:
                entered = True
            if entered and c.close < c.open and c.close <= body_mid:
                swing_high = max(x.high for x in m5[max(signal_index + 1, j - 3) : j + 1])
                stop = swing_high + buffer
                if stop > c.close:
                    return j, c.close, stop
    return None


def _scale_trail_fill(
    candidate: SignalCandidate,
    m15: list[Candle],
    signal_index: int,
    config: RouteBacktestConfig,
    buffer: float,
    max_wait_bars: int,
) -> tuple[int, float, float] | None:
    """Find the fill bar, entry price and stop for the scale-trail model.

    "touch": fill the instant price taps the zone edge (sloppy, original).
    "rejection": only fill once price dips into the zone AND a candle closes
    back out in the trade direction (a real reaction), entering at that close.
    """
    side = candidate.side
    window = m15[: signal_index + 1]
    last = min(len(m15), signal_index + 1 + max_wait_bars)
    zlo, zhi = candidate.entry_low, candidate.entry_high

    if config.entry_confirmation == "rejection":
        for j in range(signal_index + 1, last):
            c = m15[j]
            if side == "BUY" and c.low <= zhi and c.close > zhi:
                return j, c.close, zlo - buffer
            if side == "SELL" and c.high >= zlo and c.close < zlo:
                return j, c.close, zhi + buffer
        return None

    entry_price, stop_loss = _scale_trail_stop(candidate, window, config)
    for j in range(signal_index + 1, last):
        c = m15[j]
        if c.low <= entry_price <= c.high:
            return j, entry_price, stop_loss
    return None


def _simulate_scale_trail(
    candidate: SignalCandidate,
    m15: list[Candle],
    signal_index: int,
    config: RouteBacktestConfig,
    bar_scale: int = 1,
    m5_stop: list[Candle] | None = None,
    entry_relocate: bool = False,
) -> dict[str, object]:
    """Day-trade exit model: tight stop, multi-R profit ladder, trailing runner.

    Banks a configurable fraction at each ladder R-level, moves the stop to
    breakeven after the first partial, trails the remaining runner ``trail_distance_r``
    behind the peak, and force-closes after ``max_hold_bars`` (the day-trade window).

    ``bar_scale`` scales the wait/hold windows when simulating on a finer timeframe
    (e.g. 3 for M5 candles, so the day-trade window stays the same wall-clock length).
    """
    window = m15[: signal_index + 1]
    side = candidate.side
    signal_time_iso = m15[signal_index].time.isoformat()
    avg_range = average_range(window, period=30)
    buffer = avg_range * config.stop_buffer_atr
    max_wait_bars = config.max_wait_bars * bar_scale
    max_hold_bars = config.max_hold_bars * bar_scale

    def _no_fill() -> dict[str, object]:
        return {"route": candidate.route, "instrument": candidate.instrument, "side": side,
                "signal_time": signal_time_iso, "result": "no_fill", "realized_r": None,
                "outcome": None, "exit_index": signal_index}

    # --- fill: touch / rejection (M15 zone-edge) or m5_reaction (relocate entry deeper) ---
    if entry_relocate:
        fill = _m5_entry_fill(candidate, m15, signal_index, buffer, max_wait_bars)
    else:
        fill = _scale_trail_fill(candidate, m15, signal_index, config, buffer, max_wait_bars)
    if fill is None:
        return _no_fill()
    fill_index, entry_price, stop_loss = fill
    if m5_stop:
        stop_loss = _m5_refined_stop(side, entry_price, m5_stop, m15[fill_index].time, stop_loss, config.stop_buffer_atr)
    risk = abs(entry_price - stop_loss)
    if risk <= 0:
        return _no_fill()

    def r_of(price: float) -> float:
        return (price - entry_price) / risk if side == "BUY" else (entry_price - price) / risk

    ladder = config.ladder
    remaining = 1.0
    realized = 0.0
    stop = stop_loss
    rung = 0
    peak = entry_price
    partial_taken = False
    outcome = "open"
    exit_index = fill_index
    deadline = fill_index + max_hold_bars

    for j in range(fill_index + 1, min(len(m15), deadline + 1)):
        c = m15[j]
        exit_index = j
        # trail uses the peak from prior bars (no same-bar look-ahead)
        if partial_taken:
            if side == "BUY":
                stop = max(stop, peak - config.trail_distance_r * risk)
            else:
                stop = min(stop, peak + config.trail_distance_r * risk)
        # 1) adverse: stop hit (conservative — checked before targets)
        stop_hit = c.low <= stop if side == "BUY" else c.high >= stop
        if stop_hit:
            realized += remaining * r_of(stop)
            remaining = 0.0
            outcome = "loss" if r_of(stop) < -0.05 else ("breakeven" if abs(r_of(stop)) <= 0.05 else "trail_win")
            break
        # 2) update peak with this bar
        peak = max(peak, c.high) if side == "BUY" else min(peak, c.low)
        # 3) bank any ladder rungs this bar reaches
        while rung < len(ladder):
            level_r, frac = ladder[rung]
            level_price = entry_price + level_r * risk if side == "BUY" else entry_price - level_r * risk
            reached = c.high >= level_price if side == "BUY" else c.low <= level_price
            if not reached:
                break
            frac = min(frac, remaining)
            realized += frac * level_r
            remaining -= frac
            if rung == 0:
                partial_taken = True
                stop = max(stop, entry_price) if side == "BUY" else min(stop, entry_price)
            rung += 1
        if remaining <= 1e-9:
            outcome = "ladder_full"
            break
    else:
        # timeout: mark the remaining runner at the last close
        last = m15[min(len(m15) - 1, deadline)]
        realized += remaining * r_of(last.close)
        remaining = 0.0
        outcome = "timeout"

    if outcome == "open":
        last = m15[exit_index]
        realized += remaining * r_of(last.close)
        outcome = "timeout"

    best_r = r_of(peak)
    cost_r = _cost_r(candidate.instrument, risk, config.spread_pips)
    net = round(realized - cost_r, 4)
    return {
        "route": candidate.route,
        "instrument": candidate.instrument,
        "side": side,
        "signal_time": signal_time_iso,
        "entry_time": m15[fill_index].time.isoformat(),
        "exit_time": m15[exit_index].time.isoformat(),
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "risk": risk,
        "available_r": getattr(candidate, "available_r", None),
        "result": outcome,
        "outcome": outcome,
        "realized_r": round(realized, 4),
        "realized_r_net": net,
        "cost_r": round(cost_r, 4),
        "best_r": round(max(0.0, best_r), 4),
        "exit_index": exit_index,
    }


def summarize_route_backtest(trades: list[dict[str, object]]) -> dict[str, object]:
    overall = _empty_stats()
    by_route: dict[str, dict[str, object]] = {}
    for trade in trades:
        route = str(trade.get("route", "?"))
        stats = by_route.setdefault(route, _empty_stats())
        _accumulate(stats, trade)
        _accumulate(overall, trade)
    return {
        "overall": _finalize(overall),
        "by_route": {route: _finalize(stats) for route, stats in by_route.items()},
    }


def _empty_stats() -> dict[str, object]:
    return {
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "scratch": 0,
        "outcomes": {},
        "r_values": [],
        "net_values": [],
        "best_r_values": [],
    }


def _accumulate(stats: dict[str, object], trade: dict[str, object]) -> None:
    realized = trade.get("realized_r")
    if realized is None:
        return
    stats["trades"] = int(stats["trades"]) + 1
    outcomes = stats["outcomes"]
    outcome = str(trade.get("outcome"))
    outcomes[outcome] = int(outcomes.get(outcome, 0)) + 1
    realized = float(realized)
    stats["r_values"].append(realized)
    net = trade.get("realized_r_net")
    stats["net_values"].append(float(net) if net is not None else realized)
    best = trade.get("best_r")
    if best is not None:
        stats["best_r_values"].append(float(best))
    if realized > 0.05:
        stats["wins"] = int(stats["wins"]) + 1
    elif realized < -0.05:
        stats["losses"] = int(stats["losses"]) + 1
    else:
        stats["scratch"] = int(stats["scratch"]) + 1


def _finalize(stats: dict[str, object]) -> dict[str, object]:
    r_values: list[float] = stats["r_values"]
    net_values: list[float] = stats["net_values"]
    best_values: list[float] = stats["best_r_values"]
    trades = int(stats["trades"])
    wins = int(stats["wins"])
    losses = int(stats["losses"])
    decided = wins + losses
    expectancy = sum(r_values) / len(r_values) if r_values else 0.0
    expectancy_net = sum(net_values) / len(net_values) if net_values else 0.0
    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "scratch": int(stats["scratch"]),
        "win_rate": f"{wins / trades * 100:.1f}%" if trades else "n/a",
        "win_rate_decided": f"{wins / decided * 100:.1f}%" if decided else "n/a",
        "expectancy_r": round(expectancy, 4),
        "expectancy_r_net": round(expectancy_net, 4),
        "total_r": round(sum(r_values), 4),
        "total_r_net": round(sum(net_values), 4),
        "avg_best_r": round(sum(best_values) / len(best_values), 4) if best_values else 0.0,
        "max_best_r": round(max(best_values), 4) if best_values else 0.0,
        "outcomes": dict(stats["outcomes"]),
    }


def render_route_backtest_markdown(result: dict[str, object]) -> str:
    summary = result.get("summary", {})
    overall = summary.get("overall", {})
    by_route = summary.get("by_route", {})
    lines = [
        "# Route Backtest (partial-then-trail exit)",
        "",
        f"Created: {result.get('created_at', '')}",
        "Exit model: bank 50% at 1.5R, runner trails 1R behind peak (uncapped), breakeven after partial.",
        "Win = realized R > 0. Expectancy = average realized R per closed trade.",
        "",
        "## Overall",
        "",
        _stats_line(overall),
        "",
        "## By route",
        "",
    ]
    for route in sorted(by_route):
        lines.append(f"### {route}")
        lines.append("")
        lines.append(_stats_line(by_route[route]))
        lines.append(f"- outcomes: {by_route[route].get('outcomes', {})}")
        lines.append("")
    return "\n".join(lines)


def _stats_line(stats: dict[str, object]) -> str:
    return (
        f"- trades: {stats.get('trades', 0)} | "
        f"expectancy gross: {stats.get('expectancy_r', 0.0)}R | "
        f"expectancy net: {stats.get('expectancy_r_net', 0.0)}R | "
        f"total net: {stats.get('total_r_net', 0.0)}R | "
        f"win rate: {stats.get('win_rate', 'n/a')} "
        f"(decided {stats.get('win_rate_decided', 'n/a')}) | "
        f"max best: {stats.get('max_best_r', 0.0)}R"
    )


def save_route_backtest_outputs(
    result: dict[str, object],
    json_path: Path = DEFAULT_OUTPUT_JSON,
    markdown_path: Path = DEFAULT_OUTPUT_MD,
) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    markdown_path.write_text(render_route_backtest_markdown(result), encoding="utf-8")


def _first_index_at_or_after(candles: list[Candle], value: datetime) -> int:
    for index, candle in enumerate(candles):
        if candle.time >= value:
            return index
    return len(candles)

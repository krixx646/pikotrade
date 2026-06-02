"""EXPERIMENTAL — isolated momentum / continuation entry (delete-safe).

This is a *separate angle* on the entry problem. The live agent and the rule
route enter on deep pullbacks into zones, which structurally lands us in chop
(see the regime diagnostic). This module tries the opposite: detect a fresh
impulse leg in the trend direction and enter on a *shallow* continuation
pullback, so we enter while the trend is still alive and the move can run.

It is fully self-contained: it imports the existing data loader and the
scale-trail exit read-only, and is NOT wired into forward testing or any live
route. Deleting this file and scripts/backtest_momentum.py removes the feature
entirely with no impact on the rest of the agent.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fx_annotation.backtesting import _load_or_fetch_candles
from fx_annotation.bias import detect_bias
from fx_annotation.candles import Candle
from fx_annotation.forward_testing import SignalCandidate
from fx_annotation.route_backtesting import (
    DEFAULT_CACHE_DIR,
    RouteBacktestConfig,
    _first_index_at_or_after,
    _simulate_scale_trail,
    summarize_route_backtest,
)
from fx_annotation.structure import average_range


@dataclass(frozen=True)
class MomentumParams:
    impulse_lookback: int = 10      # bars to scan for the impulse leg
    impulse_atr_mult: float = 2.5   # leg size must exceed this * ATR to count as an impulse
    recent_extreme_within: int = 6  # the impulse extreme must be this recent (else it's stale)
    shallow_retrace: float = 0.33   # near edge of the continuation entry band (fraction of leg)
    deep_retrace: float = 0.55      # far edge / stop side of the entry band
    bias_lookback: int = 120        # M15 bars for trend context


def _momentum_signal(
    window: list[Candle],
    instrument: str,
    signal_time: str,
    params: MomentumParams,
) -> SignalCandidate | None:
    if len(window) < max(params.bias_lookback, params.impulse_lookback + 2, 40):
        return None
    atr = average_range(window[-30:], period=30)
    if atr <= 0:
        return None
    leg = window[-params.impulse_lookback :]
    highs = [c.high for c in leg]
    lows = [c.low for c in leg]
    imp_high = max(highs)
    imp_low = min(lows)
    hi_idx = highs.index(imp_high)
    lo_idx = lows.index(imp_low)
    rng = imp_high - imp_low
    if rng < atr * params.impulse_atr_mult:
        return None

    bias = detect_bias(window[-params.bias_lookback :]).direction
    n = len(leg)
    latest_close = window[-1].close

    # BUY continuation: up-impulse (low before high), high is recent, price pulling back, bias bullish.
    if bias == "bullish" and lo_idx < hi_idx and (n - 1 - hi_idx) <= params.recent_extreme_within:
        entry_high = imp_high - params.shallow_retrace * rng
        entry_low = imp_high - params.deep_retrace * rng
        if entry_low < latest_close:  # price still above the deep bound (room to retrace in)
            return _candidate(instrument, "BUY", entry_low, entry_high, imp_low, signal_time, rng, atr)

    # SELL continuation: down-impulse (high before low), low is recent, price pulling back up, bias bearish.
    if bias == "bearish" and hi_idx < lo_idx and (n - 1 - lo_idx) <= params.recent_extreme_within:
        entry_low = imp_low + params.shallow_retrace * rng
        entry_high = imp_low + params.deep_retrace * rng
        if entry_high > latest_close:
            return _candidate(instrument, "SELL", entry_low, entry_high, imp_high, signal_time, rng, atr)

    return None


def _candidate(
    instrument: str,
    side: str,
    entry_low: float,
    entry_high: float,
    sweep_price: float,
    signal_time: str,
    rng: float,
    atr: float,
) -> SignalCandidate:
    return SignalCandidate(
        route="MOMENTUM",
        instrument=instrument,
        side=side,
        status="candidate",
        entry_low=round(min(entry_low, entry_high), 5),
        entry_high=round(max(entry_low, entry_high), 5),
        source="M15 impulse continuation",
        signal_time=signal_time,
        sweep_price=round(sweep_price, 5),
        bos_time=signal_time,
        notes=f"impulse {rng / atr:.1f}xATR",
        entry_timeframe="M15",
    )


def run_momentum_backtest(
    client: object,
    configs: list[RouteBacktestConfig],
    params: MomentumParams = MomentumParams(),
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> dict[str, object]:
    trades: list[dict[str, object]] = []
    diagnostics: dict[str, object] = {}
    for config in configs:
        result = _run_instrument(client, config, params, cache_dir)
        trades.extend(result["trades"])
        diagnostics[config.instrument] = result["diagnostics"]
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "engine": "momentum_continuation",
        "exit_model": config.exit_model if configs else "scale_trail",
        "trades": trades,
        "summary": summarize_route_backtest(trades),
        "diagnostics": diagnostics,
    }


def _run_instrument(
    client: object,
    config: RouteBacktestConfig,
    params: MomentumParams,
    cache_dir: Path,
) -> dict[str, object]:
    fetch_start = config.start - timedelta(days=90)
    m15 = _load_or_fetch_candles(client, config.instrument, "M15", fetch_start, config.end + timedelta(days=3), cache_dir)
    trades: list[dict[str, object]] = []
    signals = 0
    no_fill = 0
    blocked_until = 0
    start_index = _first_index_at_or_after(m15, config.start)
    end_index = _first_index_at_or_after(m15, config.end)
    for index in range(start_index, end_index, max(1, config.scan_interval_bars)):
        if index < blocked_until:
            continue
        window = m15[max(0, index + 1 - config.m15_lookback) : index + 1]
        if not window:
            continue
        candidate = _momentum_signal(window, config.instrument, m15[index].time.isoformat(), params)
        if candidate is None:
            continue
        signals += 1
        trade = _simulate_scale_trail(candidate, m15, index, config)
        if trade.get("result") == "no_fill":
            no_fill += 1
            continue
        trades.append(trade)
        blocked_until = int(trade.get("exit_index", index + 1)) + 1
    return {"trades": trades, "diagnostics": {"candidates": signals, "no_fill": no_fill, "m15_candles": len(m15)}}

"""Route A backtest: HTF (H1) impulse-continuation detected on H1, executed on M15.

Detects the move on the 1-hour chart and runs the same scale-and-trail exit on M15 candles,
so realized-R is directly comparable to the M15 MOMENTUM and route backtests.
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fx_annotation.config import load_oanda_config
from fx_annotation.forward_testing import SignalCandidate
from fx_annotation.htf_momentum import HtfMomentumParams, htf_momentum_signal
from fx_annotation.oanda_client import OandaClient
from fx_annotation.backtesting import DEFAULT_CACHE_DIR, _load_or_fetch_candles
from fx_annotation.route_backtesting import (
    RouteBacktestConfig,
    _first_index_at_or_after,
    _simulate_scale_trail,
    parse_ladder,
    summarize_route_backtest,
)


def _date(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _avg_range(candles, period=30):
    from fx_annotation.structure import average_range
    return average_range(candles, period=period)


def _simulate_htf_target(sig, m15, signal_index, config, stop_lookback, partial_r, use_partial):
    """The user's model: enter on M15, stop on M15 structure (room to breathe), ride to the
    fixed H1 target. Returns planned_r (the structural R the setup offers) and realized_r.
    """
    side = sig.side
    zlo, zhi = sig.entry_low, sig.entry_high
    target = sig.target_price
    buffer = _avg_range(m15[: signal_index + 1], 30) * config.stop_buffer_atr
    last = min(len(m15), signal_index + 1 + config.max_wait_bars)
    fill = None
    for j in range(signal_index + 1, last):
        c = m15[j]
        if side == "BUY" and c.low <= zhi:
            fill = (j, zhi)
            break
        if side == "SELL" and c.high >= zlo:
            fill = (j, zlo)
            break
    if fill is None:
        return {"result": "no_fill"}
    fj, entry = fill
    win = m15[max(0, fj - stop_lookback) : fj + 1]
    if side == "BUY":
        stop = min(x.low for x in win) - buffer
        risk = entry - stop
    else:
        stop = max(x.high for x in win) + buffer
        risk = stop - entry
    if risk <= 0:
        return {"result": "no_fill"}
    planned_r = (target - entry) / risk if side == "BUY" else (entry - target) / risk
    if planned_r <= 0:
        return {"result": "no_fill"}

    def r_of(p):
        return (p - entry) / risk if side == "BUY" else (entry - p) / risk

    remaining = 1.0
    realized = 0.0
    partial_taken = False
    cur_stop = stop
    best_r = 0.0
    outcome = "open"
    deadline = fj + config.max_hold_bars
    for j in range(fj + 1, min(len(m15), deadline + 1)):
        c = m15[j]
        best_r = max(best_r, r_of(c.high if side == "BUY" else c.low))
        stop_hit = c.low <= cur_stop if side == "BUY" else c.high >= cur_stop
        if stop_hit:
            realized += remaining * r_of(cur_stop)
            outcome = "be_stop" if partial_taken else "stop"
            remaining = 0.0
            break
        target_hit = c.high >= target if side == "BUY" else c.low <= target
        if target_hit:
            realized += remaining * planned_r
            outcome = "target"
            remaining = 0.0
            break
        if use_partial and not partial_taken:
            milestone = entry + partial_r * risk if side == "BUY" else entry - partial_r * risk
            reached = c.high >= milestone if side == "BUY" else c.low <= milestone
            if reached:
                realized += 0.5 * partial_r
                remaining = 0.5
                cur_stop = entry  # move to breakeven
                partial_taken = True
    if remaining > 0:  # timeout
        realized += remaining * r_of(m15[min(deadline, len(m15) - 1)].close)
        outcome = "timeout"
    return {"result": "filled", "side": side, "entry": round(entry, 5), "stop": round(stop, 5),
            "target": round(target, 5), "planned_r": round(planned_r, 3),
            "realized_r": round(realized, 4), "best_r": round(best_r, 3), "outcome": outcome,
            "exit_index": j}


def _candidate(instrument: str, sig, signal_time: str) -> SignalCandidate:
    return SignalCandidate(
        route="HTF_MOMENTUM",
        instrument=instrument,
        side=sig.side,
        status=f"htf_impulse:{sig.strength}xATR",
        entry_low=sig.entry_low,
        entry_high=sig.entry_high,
        source="H1 impulse continuation -> M15 entry",
        signal_time=signal_time,
        sweep_price=sig.sweep_price,
        bos_time=signal_time,
        notes=sig.note,
        target_price=None,
        target_timeframe="fixed",
        target_reason="Trailing runner (scale-and-trail), no fixed cap.",
        available_r=3.0,
        entry_timeframe="M15",
    )


def _run_instrument_htf_target(client, config, params, cache_dir, stop_lookback, partial_r, use_partial):
    fetch_start = config.start - timedelta(days=120)
    h1 = _load_or_fetch_candles(client, config.instrument, "H1", fetch_start, config.end + timedelta(days=3), cache_dir)
    m15 = _load_or_fetch_candles(client, config.instrument, "M15", fetch_start, config.end + timedelta(days=3), cache_dir)
    trades: list[dict] = []
    signals = 0
    no_fill = 0
    blocked_until = None
    start_h1 = _first_index_at_or_after(h1, config.start)
    end_h1 = _first_index_at_or_after(h1, config.end)
    for i in range(start_h1, end_h1):
        if blocked_until is not None and h1[i].time < blocked_until:
            continue
        sig = htf_momentum_signal(h1[max(0, i + 1 - 240) : i + 1], params)
        if sig is None:
            continue
        m15_idx = _first_index_at_or_after(m15, h1[i].time)
        if m15_idx >= len(m15):
            continue
        signals += 1
        tr = _simulate_htf_target(sig, m15, m15_idx, config, stop_lookback, partial_r, use_partial)
        if tr.get("result") != "filled":
            no_fill += 1
            continue
        tr["instrument"] = config.instrument
        tr["signal_time"] = h1[i].time.isoformat()
        trades.append(tr)
        blocked_until = m15[min(int(tr["exit_index"]), len(m15) - 1)].time
    return {"trades": trades, "diagnostics": {"candidates": signals, "no_fill": no_fill,
                                              "h1_candles": len(h1), "m15_candles": len(m15)}}


def _run_instrument(client, config: RouteBacktestConfig, params: HtfMomentumParams, cache_dir: Path,
                    entry_mode: str = "zone"):
    """entry_mode:
      'zone'     - fill at the M15 zone edge, stop = zone width (risk scales with the H1 leg).
      'reaction' - wait for an M5 reaction inside the zone, enter on its close with a TIGHT stop
                   just beyond the local M5 swing (risk decoupled from the H1 leg -> bigger R).
    """
    fetch_start = config.start - timedelta(days=120)
    h1 = _load_or_fetch_candles(client, config.instrument, "H1", fetch_start, config.end + timedelta(days=3), cache_dir)
    exec_tf = "M5" if entry_mode == "reaction" else "M15"
    bar_scale = 3 if entry_mode == "reaction" else 1
    execc = _load_or_fetch_candles(client, config.instrument, exec_tf, fetch_start, config.end + timedelta(days=3), cache_dir)
    trades: list[dict] = []
    signals = 0
    no_fill = 0
    blocked_until = None  # datetime: skip H1 detections until the prior trade's exit
    start_h1 = _first_index_at_or_after(h1, config.start)
    end_h1 = _first_index_at_or_after(h1, config.end)
    for i in range(start_h1, end_h1):
        if blocked_until is not None and h1[i].time < blocked_until:
            continue
        window = h1[max(0, i + 1 - 240) : i + 1]
        sig = htf_momentum_signal(window, params)
        if sig is None:
            continue
        exec_idx = _first_index_at_or_after(execc, h1[i].time)
        if exec_idx >= len(execc):
            continue
        signals += 1
        candidate = _candidate(config.instrument, sig, h1[i].time.isoformat())
        trade = _simulate_scale_trail(candidate, execc, exec_idx, config, bar_scale=bar_scale,
                                      entry_relocate=(entry_mode == "reaction"))
        if trade.get("result") == "no_fill":
            no_fill += 1
            continue
        trades.append(trade)
        exit_idx = int(trade.get("exit_index", exec_idx + 1))
        blocked_until = execc[min(exit_idx, len(execc) - 1)].time
    return {"trades": trades, "diagnostics": {"candidates": signals, "no_fill": no_fill,
                                              "h1_candles": len(h1), f"{exec_tf.lower()}_candles": len(execc)}}


def main() -> int:
    p = argparse.ArgumentParser(description="Backtest route A: H1 impulse continuation, M15 execution.")
    p.add_argument("--instruments", default="XAU_USD,GBP_USD,EUR_USD,USD_JPY")
    p.add_argument("--start", default="2025-01-01")
    p.add_argument("--end", default="2025-05-01")
    p.add_argument("--max-wait-bars", type=int, default=24, help="M15 bars to wait for the pullback fill.")
    p.add_argument("--stop-buffer-atr", type=float, default=0.1)
    p.add_argument("--ladder", default="1:0.25,2:0.25,3:0.25")
    p.add_argument("--trail-distance-r", type=float, default=1.0)
    p.add_argument("--max-hold-bars", type=int, default=96, help="M15 day-trade window (~1 day).")
    p.add_argument("--spread-pips", type=float, default=None)
    p.add_argument("--impulse-atr-mult", type=float, default=2.0)
    p.add_argument("--bias-lookback", type=int, default=80)
    p.add_argument("--entry-mode", choices=("zone", "reaction", "htf_target"), default="zone",
                   help="'htf_target' = M15 entry + M15-swing stop + fixed H1 measured-move target (your model).")
    p.add_argument("--target-ext", type=float, default=1.0, help="H1 target = impulse extreme + ext*range.")
    p.add_argument("--stop-lookback", type=int, default=10, help="M15 bars for the structural swing stop.")
    p.add_argument("--no-partial", action="store_true", help="Ride 100%% to the H1 target (no 1R partial/BE).")
    p.add_argument("--partial-r", type=float, default=1.0, help="Bank 50%% here then move to BE.")
    p.add_argument("--json-output", default="outputs/backtests/htf_momentum.json")
    args = p.parse_args()

    params = HtfMomentumParams(impulse_atr_mult=args.impulse_atr_mult, bias_lookback=args.bias_lookback,
                               target_ext=args.target_ext)
    instruments = [s.strip() for s in args.instruments.split(",") if s.strip()]
    configs = [
        RouteBacktestConfig(
            instrument=inst,
            start=_date(args.start),
            end=_date(args.end),
            routes=("HTF_MOMENTUM",),
            max_wait_bars=args.max_wait_bars,
            spread_pips=args.spread_pips,
            exit_model="scale_trail",
            stop_mode="zone",
            stop_buffer_atr=args.stop_buffer_atr,
            ladder=parse_ladder(args.ladder),
            trail_distance_r=args.trail_distance_r,
            max_hold_bars=args.max_hold_bars,
        )
        for inst in instruments
    ]
    client = OandaClient(load_oanda_config())
    trades: list[dict] = []
    diagnostics: dict = {}
    for config in configs:
        if args.entry_mode == "htf_target":
            res = _run_instrument_htf_target(client, config, params, DEFAULT_CACHE_DIR,
                                             args.stop_lookback, args.partial_r, not args.no_partial)
        else:
            res = _run_instrument(client, config, params, DEFAULT_CACHE_DIR, entry_mode=args.entry_mode)
        trades.extend(res["trades"])
        diagnostics[config.instrument] = res["diagnostics"]
    result = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "engine": f"htf_momentum (H1 detect -> {args.entry_mode} execute)",
        "trades": trades,
        "diagnostics": diagnostics,
    }
    out = PROJECT_ROOT / args.json_output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    rs = [t["realized_r"] for t in trades if t.get("realized_r") is not None]
    br = [t.get("best_r", 0.0) for t in trades]
    wins = [r for r in rs if r > 0.05]
    n = len(rs)
    print(f"HTF_MOMENTUM [{args.entry_mode}]: {n} trades  (diagnostics: {diagnostics})")
    if n and args.entry_mode == "htf_target":
        planned = [t["planned_r"] for t in trades]
        target_hits = sum(1 for t in trades if t.get("outcome") == "target")
        print(f"  --- PLANNED R the setup offers (target dist / M15-stop dist) ---")
        print(f"  avg planned {sum(planned)/n:.2f}R  median {sorted(planned)[n//2]:.2f}R  "
              f"%offering>=3R {100*sum(1 for p in planned if p>=3)/n:.0f}%  "
              f"%>=2R {100*sum(1 for p in planned if p>=2)/n:.0f}%")
        print(f"  --- REALIZED (did price reach the H1 target before the M15 stop?) ---")
        print(f"  win {100*len(wins)/n:.1f}%  target-hit {100*target_hits/n:.0f}%  "
              f"avg realized {sum(rs)/n:+.3f}R  total {sum(rs):+.1f}R  max realized {max(rs):+.2f}R")
    elif n:
        print(f"  win {100*len(wins)/n:.1f}%  gross {sum(rs)/n:+.3f}R  "
              f"avg best_r {sum(br)/n:+.2f}R  %>=3R {100*sum(1 for b in br if b>=3)/n:.0f}%")
    print(f"JSON: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

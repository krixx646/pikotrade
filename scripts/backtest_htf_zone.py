"""Route B backtest (EXPERIMENTAL, delete-safe): SMC HTF-zone reaction, executed on M15.

Detects an H4-biased H1 sweep/BOS zone and runs the same scale-and-trail M15 exit, so its
realized-R is directly comparable to route A (HTF_MOMENTUM).

DELETE-SAFE: removing this file and src/fx_annotation/htf_zone.py removes route B entirely.
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
from fx_annotation.htf_zone import HtfZoneParams, htf_zone_signal
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


def _candidate(instrument: str, sig, signal_time: str) -> SignalCandidate:
    return SignalCandidate(
        route="HTF_ZONE",
        instrument=instrument,
        side=sig.side,
        status=f"htf_zone:Q{sig.strength:.0f}",
        entry_low=sig.entry_low,
        entry_high=sig.entry_high,
        source="H4 bias + H1 SMC zone -> M15 reaction",
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


def _run_instrument(client, config: RouteBacktestConfig, params: HtfZoneParams, cache_dir: Path):
    fetch_start = config.start - timedelta(days=160)
    h4 = _load_or_fetch_candles(client, config.instrument, "H4", fetch_start, config.end + timedelta(days=3), cache_dir)
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
        h1_win = h1[max(0, i + 1 - 260) : i + 1]
        h4_idx = _first_index_at_or_after(h4, h1[i].time)
        h4_win = h4[max(0, h4_idx - 120) : h4_idx]  # H4 bars strictly before now (no look-ahead)
        sig = htf_zone_signal(h4_win, h1_win, params)
        if sig is None:
            continue
        m15_idx = _first_index_at_or_after(m15, h1[i].time)
        if m15_idx >= len(m15):
            continue
        signals += 1
        candidate = _candidate(config.instrument, sig, h1[i].time.isoformat())
        trade = _simulate_scale_trail(candidate, m15, m15_idx, config)
        if trade.get("result") == "no_fill":
            no_fill += 1
            continue
        trades.append(trade)
        exit_idx = int(trade.get("exit_index", m15_idx + 1))
        blocked_until = m15[min(exit_idx, len(m15) - 1)].time
    return {"trades": trades, "diagnostics": {"candidates": signals, "no_fill": no_fill,
                                              "h4_candles": len(h4), "h1_candles": len(h1), "m15_candles": len(m15)}}


def main() -> int:
    p = argparse.ArgumentParser(description="Backtest route B: H4/H1 SMC zone, M15 execution.")
    p.add_argument("--instruments", default="XAU_USD,GBP_USD,EUR_USD,USD_JPY")
    p.add_argument("--start", default="2025-01-01")
    p.add_argument("--end", default="2025-05-01")
    p.add_argument("--max-wait-bars", type=int, default=24)
    p.add_argument("--stop-buffer-atr", type=float, default=0.1)
    p.add_argument("--ladder", default="1:0.25,2:0.25,3:0.25")
    p.add_argument("--trail-distance-r", type=float, default=1.0)
    p.add_argument("--max-hold-bars", type=int, default=96)
    p.add_argument("--spread-pips", type=float, default=None)
    p.add_argument("--min-quality", type=int, default=3)
    p.add_argument("--json-output", default="outputs/backtests/htf_zone.json")
    args = p.parse_args()

    params = HtfZoneParams(min_quality=args.min_quality)
    instruments = [s.strip() for s in args.instruments.split(",") if s.strip()]
    configs = [
        RouteBacktestConfig(
            instrument=inst,
            start=_date(args.start),
            end=_date(args.end),
            routes=("HTF_ZONE",),
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
        res = _run_instrument(client, config, params, DEFAULT_CACHE_DIR)
        trades.extend(res["trades"])
        diagnostics[config.instrument] = res["diagnostics"]
    result = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "engine": "htf_zone (H4 bias + H1 SMC zone -> M15 execute)",
        "trades": trades,
        "summary": summarize_route_backtest(trades),
        "diagnostics": diagnostics,
    }
    out = PROJECT_ROOT / args.json_output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    rs = [t["realized_r"] for t in trades if t.get("realized_r") is not None]
    nets = [t.get("realized_r_net") for t in trades if t.get("realized_r_net") is not None]
    br = [t.get("best_r", 0.0) for t in trades]
    wins = [r for r in rs if r > 0.05]
    n = len(rs)
    print(f"HTF_ZONE: {n} trades  (diagnostics: {diagnostics})")
    if n:
        print(f"  win {100*len(wins)/n:.1f}%  gross {sum(rs)/n:+.3f}R  net {sum(nets)/n:+.3f}R  "
              f"total net {sum(nets):+.2f}R  avg best_r {sum(br)/n:+.2f}R  "
              f"%>=3R {100*sum(1 for b in br if b>=3)/n:.0f}%")
    print(f"JSON: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

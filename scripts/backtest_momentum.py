"""EXPERIMENTAL standalone backtest for the isolated momentum/continuation entry.

Delete-safe: removing this file and src/fx_annotation/momentum_entry.py removes
the feature with no impact on the live agent or other backtests.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fx_annotation.config import load_oanda_config
from fx_annotation.oanda_client import OandaClient
from fx_annotation.route_backtesting import RouteBacktestConfig, parse_ladder
from fx_annotation.momentum_entry import MomentumParams, run_momentum_backtest


def _date(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def main() -> int:
    p = argparse.ArgumentParser(description="Backtest the isolated momentum/continuation entry.")
    p.add_argument("--instruments", default="XAU_USD,GBP_USD")
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--end", default="2024-07-01")
    p.add_argument("--max-wait-bars", type=int, default=12)
    p.add_argument("--stop-buffer-atr", type=float, default=0.1)
    p.add_argument("--ladder", default="1:0.25,2:0.25,3:0.25")
    p.add_argument("--trail-distance-r", type=float, default=1.0)
    p.add_argument("--max-hold-bars", type=int, default=96)
    p.add_argument("--spread-pips", type=float, default=None)
    p.add_argument("--impulse-atr-mult", type=float, default=2.5)
    p.add_argument("--json-output", default="outputs/backtests/momentum.json")
    args = p.parse_args()

    instruments = [s.strip() for s in args.instruments.split(",") if s.strip()]
    configs = [
        RouteBacktestConfig(
            instrument=inst,
            start=_date(args.start),
            end=_date(args.end),
            routes=("MOMENTUM",),
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
    result = run_momentum_backtest(client, configs, MomentumParams(impulse_atr_mult=args.impulse_atr_mult))

    out = PROJECT_ROOT / args.json_output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    trades = result["trades"]
    rs = [t["realized_r"] for t in trades if t.get("realized_r") is not None]
    nets = [t.get("realized_r_net") for t in trades if t.get("realized_r_net") is not None]
    br = [t.get("best_r", 0.0) for t in trades]
    wins = [r for r in rs if r > 0.05]
    n = len(rs)
    print(f"Momentum entry: {n} trades  (diagnostics: {result['diagnostics']})")
    if n:
        print(f"  win {100*len(wins)/n:.1f}%  gross {sum(rs)/n:+.3f}R  net {sum(nets)/n:+.3f}R  "
              f"avg best_r {sum(br)/n:+.2f}R  %>=2R {100*sum(1 for b in br if b>=2)/n:.0f}%  "
              f"avg winner {sum(wins)/len(wins) if wins else 0:+.2f}R")
    print(f"JSON: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

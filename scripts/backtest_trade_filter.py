"""Backtest the deterministic trade-quality filter (the gate).

Runs route backtests, scores every historical trade with trade_score.conviction
(features known at signal time - no lookahead), then reports realized net-R
expectancy and win rate per verdict (TAKE/CAUTION/SKIP) and per score band. The
point: prove TAKE trades out-earn SKIP trades before the score is trusted live.

Example:
  python scripts/backtest_trade_filter.py --start 2024-06-01 --end 2025-06-01
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from fx_annotation.config import load_oanda_config
from fx_annotation.oanda_client import OandaClient
from fx_annotation.route_backtesting import (
    DEFAULT_CACHE_DIR,
    RouteBacktestConfig,
    parse_ladder,
    run_route_backtest,
)
from fx_annotation.trade_score import CAUTION_MIN, TAKE_MIN, conviction

WATCHLIST = "EUR_USD,GBP_USD,USD_JPY,USD_CAD,AUD_USD,NZD_USD,EUR_JPY,GBP_JPY,XAU_USD"
BANDS = [(0, 40), (40, 55), (55, 70), (70, 85), (85, 101)]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate the deterministic trade-quality filter.")
    p.add_argument("--instruments", default=WATCHLIST)
    p.add_argument("--start", default="2024-06-01")
    p.add_argument("--end", default="2025-06-01")
    p.add_argument("--routes", default="RULE,DYNAMIC_SCORE,M15_SIMPLE,REGIME_RANGE")
    p.add_argument("--exit-model", default="partial_trail", choices=["partial_trail", "scale_trail"])
    p.add_argument("--scan-interval-bars", type=int, default=1, help="Sample every N M15 bars (raise to speed up).")
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    return p.parse_args()


def _stats(trades: list[dict]) -> tuple[int, float, float, float]:
    rs = [float(t["realized_r_net"]) for t in trades if t.get("realized_r_net") is not None]
    if not rs:
        return (0, 0.0, 0.0, 0.0)
    wins = [r for r in rs if r > 0]
    return (len(rs), round(100 * len(wins) / len(rs), 1), round(sum(rs) / len(rs), 3), round(sum(rs), 1))


def _line(label: str, trades: list[dict]) -> str:
    n, wr, exp, tot = _stats(trades)
    return f"  {label:<22} n={n:<5} win={wr:>5}%  exp={exp:+.3f}R/trade  total={tot:+.1f}R"


def main() -> int:
    args = parse_args()
    start = _date(args.start)
    end = _date(args.end)
    instruments = [i.strip() for i in args.instruments.split(",") if i.strip()]
    routes = tuple(r.strip() for r in args.routes.split(",") if r.strip())
    configs = [
        RouteBacktestConfig(
            instrument=ins, start=start, end=end, routes=routes,
            exit_model=args.exit_model, ladder=parse_ladder("1:0.25,2:0.25,3:0.25"),
            scan_interval_bars=args.scan_interval_bars,
        )
        for ins in instruments
    ]
    client = OandaClient(load_oanda_config())
    print(f"Running route backtest: {len(instruments)} instruments, routes={routes}, {args.start}..{args.end}")
    result = run_route_backtest(client, configs, cache_dir=Path(args.cache_dir))
    trades = result["trades"]

    for t in trades:
        ts = conviction(str(t.get("route", "")), str(t.get("instrument", "")),
                        str(t.get("signal_time", "")), _f(t.get("available_r")))
        t["_score"] = ts.score
        t["_verdict"] = ts.verdict
        t["_prime"] = ts.prime

    print(f"\nScored {len(trades)} trades. Thresholds: TAKE>={TAKE_MIN}, CAUTION>={CAUTION_MIN}, else SKIP.\n")

    print("By verdict (the gate):")
    for v in ("TAKE", "CAUTION", "SKIP"):
        print(_line(v, [t for t in trades if t["_verdict"] == v]))

    print("\nBy score band:")
    for lo, hi in BANDS:
        band = [t for t in trades if lo <= t["_score"] < hi]
        print(_line(f"{lo}-{hi - 1}", band))

    print("\nSanity - session split:")
    print(_line("PRIME", [t for t in trades if t["_prime"]]))
    print(_line("OFF-HOURS", [t for t in trades if not t["_prime"]]))

    print("\nWhole pool:")
    print(_line("ALL", trades))

    take = [t for t in trades if t["_verdict"] == "TAKE"]
    skip = [t for t in trades if t["_verdict"] == "SKIP"]
    _, _, take_exp, _ = _stats(take)
    _, _, skip_exp, _ = _stats(skip)
    print(f"\nVerdict: TAKE exp {take_exp:+.3f}R vs SKIP exp {skip_exp:+.3f}R  ->  "
          f"{'FILTER ADDS EDGE' if take_exp > skip_exp else 'NO EDGE - do not trust it'}")
    return 0


def _f(value: object) -> float | None:
    try:
        return None if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return None


def _date(value: str) -> datetime:
    d = datetime.fromisoformat(value)
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())

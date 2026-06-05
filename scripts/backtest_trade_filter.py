"""Backtest the deterministic trade-quality filter (the gate).

Runs route/flagship backtests, scores every historical trade with
trade_score.conviction (features known at signal time - no lookahead), then
reports realized net-R expectancy and win rate per verdict (TAKE/CAUTION/SKIP),
per score band, and PER ROUTE. The point: prove TAKE trades out-earn SKIP trades
before the score is trusted live - especially on the flagship routes.

Engines:
  --engine routes    DYNAMIC_SCORE/M15_SIMPLE/REGIME_RANGE/RULE (route harness)
  --engine flagship  MOMENTUM + HTF_MOMENTUM (ride-to-target) + HTF_ZONE
  --engine momentum | htf_momentum | htf_zone   single flagship route

Example:
  python scripts/backtest_trade_filter.py --engine flagship --start 2024-01-01 --end 2025-06-01
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

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
LADDER = "1:0.25,2:0.25,3:0.25"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate the deterministic trade-quality filter.")
    p.add_argument("--engine", default="routes",
                   choices=["routes", "flagship", "momentum", "htf_momentum", "htf_zone"])
    p.add_argument("--instruments", default=WATCHLIST)
    p.add_argument("--start", default="2024-06-01")
    p.add_argument("--end", default="2025-06-01")
    p.add_argument("--routes", default="DYNAMIC_SCORE,M15_SIMPLE,REGIME_RANGE", help="for --engine routes")
    p.add_argument("--exit-model", default="partial_trail", choices=["partial_trail", "scale_trail"])
    p.add_argument("--scan-interval-bars", type=int, default=2, help="Sample every N M15 bars (raise to speed up).")
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    return p.parse_args()


def _metric(t: dict) -> float | None:
    """Realized net-R if available, else realized-R (htf_target has no cost)."""
    for k in ("realized_r_net", "realized_r"):
        v = t.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
    return None


def _stats(trades: list[dict]) -> tuple[int, float, float, float]:
    rs = [m for m in (_metric(t) for t in trades) if m is not None]
    if not rs:
        return (0, 0.0, 0.0, 0.0)
    wins = [r for r in rs if r > 0]
    return (len(rs), round(100 * len(wins) / len(rs), 1), round(sum(rs) / len(rs), 3), round(sum(rs), 1))


def _line(label: str, trades: list[dict]) -> str:
    n, wr, exp, tot = _stats(trades)
    return f"  {label:<22} n={n:<5} win={wr:>5}%  exp={exp:+.3f}R/trade  total={tot:+.1f}R"


def _configs(instruments, start, end, routes, exit_model, scan, **extra):
    return [
        RouteBacktestConfig(
            instrument=ins, start=start, end=end, routes=routes,
            exit_model=exit_model, ladder=parse_ladder(LADDER),
            scan_interval_bars=scan, stop_mode="zone", **extra,
        )
        for ins in instruments
    ]


def collect(args, client, instruments, start, end) -> list[dict]:
    eng = args.engine
    if eng == "routes":
        routes = tuple(r.strip() for r in args.routes.split(",") if r.strip())
        cfgs = _configs(instruments, start, end, routes, args.exit_model, args.scan_interval_bars)
        return list(run_route_backtest(client, cfgs, cache_dir=Path(args.cache_dir))["trades"])

    trades: list[dict] = []
    want = {"momentum", "htf_momentum", "htf_zone"} if eng == "flagship" else {eng}

    if "momentum" in want:
        from fx_annotation.momentum_entry import MomentumParams, run_momentum_backtest
        cfgs = _configs(instruments, start, end, ("MOMENTUM",), "scale_trail", args.scan_interval_bars,
                        max_wait_bars=12, trail_distance_r=1.0, max_hold_bars=96)
        res = run_momentum_backtest(client, cfgs, MomentumParams())
        trades.extend(res["trades"])
        print(f"  MOMENTUM signals: {sum(d.get('candidates',0) for d in res['diagnostics'].values())}")

    if "htf_momentum" in want:
        import backtest_htf_momentum as bm
        from fx_annotation.htf_momentum import HtfMomentumParams
        params = HtfMomentumParams(impulse_atr_mult=2.0, bias_lookback=80, target_ext=0.0)  # live Profile A
        for ins in instruments:
            cfg = _configs([ins], start, end, ("HTF_MOMENTUM",), "scale_trail", 1,
                           max_wait_bars=24, trail_distance_r=1.0, max_hold_bars=96)[0]
            res = bm._run_instrument_htf_target(client, cfg, params, Path(args.cache_dir),
                                                stop_lookback=10, partial_r=1.0, use_partial=False)
            for t in res["trades"]:
                t.setdefault("route", "HTF_MOMENTUM")
                t["available_r"] = t.get("planned_r")  # real structural R for this setup
            trades.extend(res["trades"])

    if "htf_zone" in want:
        import backtest_htf_zone as bz
        from fx_annotation.htf_zone import HtfZoneParams
        for ins in instruments:
            cfg = _configs([ins], start, end, ("HTF_ZONE",), "scale_trail", 1,
                           max_wait_bars=24, trail_distance_r=1.0, max_hold_bars=96)[0]
            res = bz._run_instrument(client, cfg, HtfZoneParams(min_quality=3), Path(args.cache_dir))
            trades.extend(res["trades"])

    return trades


def _report(label: str, trades: list[dict]) -> None:
    if not trades:
        print(f"\n[{label}] no trades.")
        return
    print(f"\n[{label}]  ({len(trades)} trades)")
    print("By verdict (the gate):")
    for v in ("TAKE", "CAUTION", "SKIP"):
        print(_line(v, [t for t in trades if t["_verdict"] == v]))
    print("By score band:")
    for lo, hi in BANDS:
        print(_line(f"{lo}-{hi - 1}", [t for t in trades if lo <= t["_score"] < hi]))
    print("Session split:")
    print(_line("PRIME", [t for t in trades if t["_prime"]]))
    print(_line("OFF-HOURS", [t for t in trades if not t["_prime"]]))
    take = [t for t in trades if t["_verdict"] == "TAKE"]
    skip = [t for t in trades if t["_verdict"] == "SKIP"]
    _, _, te, _ = _stats(take)
    _, _, se, _ = _stats(skip)
    verdict = "FILTER ADDS EDGE" if (take and te > se) else "NO EDGE - do not trust it"
    print(f"  -> TAKE exp {te:+.3f}R vs SKIP exp {se:+.3f}R  ->  {verdict}")


def main() -> int:
    args = parse_args()
    start = _date(args.start)
    end = _date(args.end)
    instruments = [i.strip() for i in args.instruments.split(",") if i.strip()]
    client = OandaClient(load_oanda_config())
    print(f"Engine={args.engine}  instruments={len(instruments)}  {args.start}..{args.end}")
    trades = collect(args, client, instruments, start, end)

    for t in trades:
        ts = conviction(str(t.get("route", "")), str(t.get("instrument", "")),
                        str(t.get("signal_time", "")), _f(t.get("available_r")))
        t["_score"], t["_verdict"], t["_prime"] = ts.score, ts.verdict, ts.prime

    print(f"\nScored {len(trades)} trades. Thresholds: TAKE>={TAKE_MIN}, CAUTION>={CAUTION_MIN}, else SKIP.")
    routes_present = sorted({str(t.get("route", "")) for t in trades})
    for r in routes_present:
        _report(r, [t for t in trades if str(t.get("route", "")) == r])
    if len(routes_present) > 1:
        _report("ALL ROUTES POOLED", trades)
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

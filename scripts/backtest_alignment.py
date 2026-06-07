"""Validate the owner-only HTF-alignment confirmation against WIN RATE.

This answers "does STRONG H4/H1 alignment actually make a trade more likely to go
my way?" It generates the flagship trades, computes each trade's H4/H1 trend AT
SIGNAL TIME (same trend_of() the live analyst uses, strictly past candles - no
lookahead), buckets by alignment, and reports win rate (and expectancy) per
bucket. Unlike the R-gate, the headline metric here is WIN RATE.

Example:
  python scripts/backtest_alignment.py --start 2024-06-01 --end 2025-06-01
"""
import argparse
import sys
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from fx_annotation.backtesting import DEFAULT_CACHE_DIR, _load_or_fetch_candles
from fx_annotation.config import load_oanda_config
from fx_annotation.oanda_client import OandaClient
from fx_annotation.route_backtesting import _first_index_at_or_after
from fx_annotation.trade_score import alignment, trend_of

from backtest_trade_filter import WATCHLIST, collect

ORDER = ["STRONG", "LEAN", "MIXED", "NEUTRAL", "COUNTER-TREND"]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate the HTF-alignment confirmation vs win rate.")
    p.add_argument("--instruments", default=WATCHLIST)
    p.add_argument("--start", default="2024-06-01")
    p.add_argument("--end", default="2025-06-01")
    p.add_argument("--engine", default="flagship", choices=["flagship", "momentum", "htf_momentum", "htf_zone"])
    p.add_argument("--scan-interval-bars", type=int, default=2)
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    return p.parse_args()


def _metric(t: dict) -> float | None:
    for k in ("realized_r_net", "realized_r"):
        v = t.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
    return None


def _stats(trades: list[dict]) -> tuple[int, float, float]:
    rs = [m for m in (_metric(t) for t in trades) if m is not None]
    if not rs:
        return (0, 0.0, 0.0)
    wins = [r for r in rs if r > 0.05]
    return (len(rs), round(100 * len(wins) / len(rs), 1), round(sum(rs) / len(rs), 3))


def _line(label: str, trades: list[dict]) -> str:
    n, wr, exp = _stats(trades)
    return f"  {label:<16} n={n:<5} win={wr:>5}%   exp={exp:+.3f}R/trade"


def _date(value: str) -> datetime:
    d = datetime.fromisoformat(value)
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def main() -> int:
    args = parse_args()
    start, end = _date(args.start), _date(args.end)
    instruments = [i.strip() for i in args.instruments.split(",") if i.strip()]
    client = OandaClient(load_oanda_config())
    cache = Path(args.cache_dir)

    collect_args = Namespace(engine=args.engine, instruments=args.instruments, routes="",
                             exit_model="scale_trail", scan_interval_bars=args.scan_interval_bars,
                             cache_dir=args.cache_dir)
    print(f"Engine={args.engine}  instruments={len(instruments)}  {args.start}..{args.end}")
    trades = collect(collect_args, client, instruments, start, end)

    # Load H4/H1 once per instrument; tag each trade's alignment at signal time (no lookahead).
    htf: dict[str, tuple[list, list]] = {}
    fetch_start = start - timedelta(days=120)
    for ins in instruments:
        h4 = _load_or_fetch_candles(client, ins, "H4", fetch_start, end + timedelta(days=3), cache)
        h1 = _load_or_fetch_candles(client, ins, "H1", fetch_start, end + timedelta(days=3), cache)
        htf[ins] = (h4, h1)

    tagged = 0
    for t in trades:
        ins = str(t.get("instrument", ""))
        st = _date(str(t.get("signal_time", "")).replace("Z", "+00:00")) if t.get("signal_time") else None
        h4, h1 = htf.get(ins, ([], []))
        if st is None or not h4 or not h1:
            t["_align"] = "NEUTRAL"
            continue
        i4 = _first_index_at_or_after(h4, st)
        i1 = _first_index_at_or_after(h1, st)
        h4_trend = trend_of([c.close for c in h4[max(0, i4 - 30):i4]])
        h1_trend = trend_of([c.close for c in h1[max(0, i1 - 40):i1]])
        label, _e, _d = alignment(str(t.get("side", "")), h4_trend, h1_trend)
        t["_align"] = label
        tagged += 1

    routes = sorted({str(t.get("route", "")) for t in trades})
    print(f"\nTagged {tagged}/{len(trades)} trades with HTF alignment. Headline metric = WIN RATE.")
    for r in routes + (["ALL POOLED"] if len(routes) > 1 else []):
        pool = trades if r == "ALL POOLED" else [t for t in trades if str(t.get("route", "")) == r]
        print(f"\n[{r}]  ({len(pool)} trades)")
        for label in ORDER:
            print(_line(label, [t for t in pool if t.get("_align") == label]))
        strong = [t for t in pool if t.get("_align") in ("STRONG", "LEAN")]
        counter = [t for t in pool if t.get("_align") == "COUNTER-TREND"]
        _, sw, _ = _stats(strong)
        _, cw, _ = _stats(counter)
        if strong and counter:
            verdict = "CONFIRMS (with-trend wins more)" if sw > cw else "NO win-rate edge"
            print(f"  -> with-trend win {sw}% vs counter-trend win {cw}%  ->  {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

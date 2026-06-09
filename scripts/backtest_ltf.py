"""Validate the admin-only LTF (M5 'micro') alert-validation feature.

Answers the user's shower idea honestly: does constant M5 behaviour at signal time
actually tell us whether an alert will play out? Generates the flagship trades,
computes each trade's M5 confirmation AT SIGNAL TIME (same ltf_confirmation() the
live analyst would use, strictly past M5 candles - no lookahead), buckets by
CONFIRMS/NEUTRAL/DENIES, and reports win rate + expectancy per bucket.

If CONFIRMS does not clearly beat DENIES, the feature is noise and we drop it.
Live use: MOMENTUM route only (HTF_MOMENTUM inverts the signal — excluded).

Example:
  python scripts/backtest_ltf.py --start 2024-06-01 --end 2025-06-01
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
from fx_annotation.trade_score import ltf_confirmation

from backtest_trade_filter import WATCHLIST, collect

ORDER = ["CONFIRMS", "NEUTRAL", "DENIES", "UNKNOWN"]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate the LTF (M5) alert-validation feature vs win rate.")
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
    return f"  {label:<12} n={n:<5} win={wr:>5}%   exp={exp:+.3f}R/trade"


def _date(value: str) -> datetime:
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
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

    print("Loading M5 candles per instrument (this is the slow part on first run)...")
    m5_by: dict[str, list] = {}
    fetch_start = start - timedelta(days=10)
    for ins in instruments:
        m5_by[ins] = _load_or_fetch_candles(client, ins, "M5", fetch_start, end + timedelta(days=3), cache)
        print(f"  {ins}: {len(m5_by[ins])} M5 candles")

    tagged = 0
    for t in trades:
        ins = str(t.get("instrument", ""))
        m5 = m5_by.get(ins, [])
        st = _date(str(t.get("signal_time", ""))) if t.get("signal_time") else None
        if st is None or not m5:
            t["_ltf"] = "UNKNOWN"
            continue
        idx = _first_index_at_or_after(m5, st)  # candles strictly before the signal
        win = m5[max(0, idx - 24):idx]          # ~2h of M5 history
        label, _e, _d = ltf_confirmation(str(t.get("side", "")),
                                         [c.close for c in win], [c.open for c in win])
        t["_ltf"] = label
        if label != "UNKNOWN":
            tagged += 1

    routes = sorted({str(t.get("route", "")) for t in trades})
    print(f"\nTagged {tagged}/{len(trades)} trades with M5 confirmation. Headline metric = WIN RATE.")
    for r in routes + (["ALL POOLED"] if len(routes) > 1 else []):
        pool = trades if r == "ALL POOLED" else [t for t in trades if str(t.get("route", "")) == r]
        print(f"\n[{r}]  ({len(pool)} trades)")
        for label in ORDER:
            print(_line(label, [t for t in pool if t.get("_ltf") == label]))
        conf = [t for t in pool if t.get("_ltf") == "CONFIRMS"]
        deny = [t for t in pool if t.get("_ltf") == "DENIES"]
        _, cw, ce = _stats(conf)
        _, dw, de = _stats(deny)
        if conf and deny:
            ok = cw > dw and ce > de
            print(f"  -> CONFIRMS win {cw}% ({ce:+.3f}R) vs DENIES win {dw}% ({de:+.3f}R)  ->  "
                  f"{'USEFUL' if ok else 'NOT a reliable validator'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

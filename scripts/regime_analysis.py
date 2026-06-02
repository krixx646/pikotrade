"""Does the market regime at entry separate runners from chop in our own trades?

Re-derives the M15 regime (the same detect_regime the agent uses) at each trade's
signal time and buckets outcomes. If regime predicts runners, trending buckets will
show bigger best_r / realized than ranging or unclear.
"""
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fx_annotation.config import load_oanda_config
from fx_annotation.oanda_client import OandaClient
from fx_annotation.dynamic_scoring import detect_regime
from fx_annotation.backtesting import _load_or_fetch_candles
from fx_annotation.route_backtesting import DEFAULT_CACHE_DIR, _first_index_at_or_after


def bucket_of(regime: str) -> str:
    if regime in ("trending_up", "trending_down"):
        return "trending"
    return regime


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else r"outputs/backtests/m5entry_4yr.json"
    trades = json.load(open(path))["trades"]
    by_inst: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_inst[t["instrument"]].append(t)

    client = OandaClient(load_oanda_config())
    tagged: list[tuple[str, dict]] = []
    for inst, rows in by_inst.items():
        times = [datetime.fromisoformat(r["signal_time"]) for r in rows]
        start = min(times) - timedelta(days=90)
        end = max(times) + timedelta(days=3)
        m15 = _load_or_fetch_candles(client, inst, "M15", start, end, DEFAULT_CACHE_DIR)
        for r in rows:
            idx = _first_index_at_or_after(m15, datetime.fromisoformat(r["signal_time"]))
            regime = detect_regime(m15[: idx + 1]) if idx < len(m15) else "unclear"
            tagged.append((bucket_of(regime), r))

    buckets: dict[str, list[dict]] = defaultdict(list)
    for b, r in tagged:
        buckets[b].append(r)

    print(f"{len(trades)} trades from {path}\n")
    print(f"{'Regime at entry':<18}{'n':>5}{'win%':>7}{'avg realized':>14}{'avg best_r':>12}{'%>=2R':>8}")
    for name in ["trending", "ranging", "high_volatility", "unclear"]:
        rows = buckets.get(name, [])
        if not rows:
            continue
        n = len(rows)
        rr = [x["realized_r"] for x in rows if x.get("realized_r") is not None]
        br = [x.get("best_r", 0.0) for x in rows]
        wins = sum(1 for r in rr if r > 0.05)
        big = sum(1 for b in br if b >= 2.0)
        print(f"{name:<18}{n:>5}{100*wins/n:>6.0f}%{sum(rr)/n:>+13.3f}R{sum(br)/n:>+11.2f}R{100*big/n:>7.0f}%")


if __name__ == "__main__":
    main()

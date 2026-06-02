"""Does directional efficiency (momentum vs chop) at entry predict bigger runs?

Buckets trades by the directional efficiency of price just before entry. If
'enter when it's already moving cleanly' predicts runners, the high-efficiency
bucket will show bigger best_r / realized than the low (choppy) bucket.
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
from fx_annotation.dynamic_scoring import _directional_efficiency
from fx_annotation.backtesting import _load_or_fetch_candles
from fx_annotation.route_backtesting import DEFAULT_CACHE_DIR, _first_index_at_or_after

LOOKBACK = 48  # ~12h of M15: recent momentum into the entry


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else r"outputs/backtests/m5entry_4yr.json"
    trades = json.load(open(path))["trades"]
    by_inst: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_inst[t["instrument"]].append(t)

    client = OandaClient(load_oanda_config())
    scored: list[tuple[float, dict]] = []
    for inst, rows in by_inst.items():
        times = [datetime.fromisoformat(r["signal_time"]) for r in rows]
        m15 = _load_or_fetch_candles(client, inst, "M15", min(times) - timedelta(days=90), max(times) + timedelta(days=3), DEFAULT_CACHE_DIR)
        for r in rows:
            idx = _first_index_at_or_after(m15, datetime.fromisoformat(r["signal_time"]))
            eff = _directional_efficiency(m15[: idx + 1], LOOKBACK) if idx < len(m15) else 0.0
            scored.append((eff, r))

    scored.sort(key=lambda x: x[0])
    n = len(scored)
    thirds = {"Low eff (choppy)": scored[: n // 3], "Mid eff": scored[n // 3 : 2 * n // 3], "High eff (clean move)": scored[2 * n // 3 :]}
    print(f"{n} trades from {path}  (efficiency lookback {LOOKBACK} bars)\n")
    print(f"{'Bucket':<24}{'n':>5}{'eff range':>14}{'win%':>7}{'avg realized':>14}{'avg best_r':>12}{'%>=2R':>8}")
    for name, items in thirds.items():
        if not items:
            continue
        rows = [x[1] for x in items]
        effs = [x[0] for x in items]
        rr = [x["realized_r"] for x in rows if x.get("realized_r") is not None]
        br = [x.get("best_r", 0.0) for x in rows]
        wins = sum(1 for r in rr if r > 0.05)
        big = sum(1 for b in br if b >= 2.0)
        rng = f"{min(effs):.2f}-{max(effs):.2f}"
        print(f"{name:<24}{len(rows):>5}{rng:>14}{100*wins/len(rows):>6.0f}%{sum(rr)/len(rows):>+13.3f}R{sum(br)/len(rows):>+11.2f}R{100*big/len(rows):>7.0f}%")


if __name__ == "__main__":
    main()

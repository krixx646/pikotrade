"""Do certain conditions separate 'runner' trades from chop in our own backtest?

Buckets existing trades by trading session (from signal_time UTC hour) and reports
how far they ran (best_r) and what they realized. If a condition genuinely predicts
runners, high-R outcomes will concentrate in some buckets and not others.
"""
import json
import sys
from datetime import datetime


def session_of(hour: int) -> str:
    if 0 <= hour < 7:
        return "Asian (00-07 UTC)"
    if 7 <= hour < 12:
        return "London (07-12 UTC)"
    if 12 <= hour < 16:
        return "NY overlap (12-16 UTC)"
    return "Late/off (16-24 UTC)"


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else r"outputs/backtests/m5entry_4yr.json"
    trades = json.load(open(path))["trades"]
    buckets: dict[str, list[dict]] = {}
    for t in trades:
        h = datetime.fromisoformat(t["signal_time"]).hour
        buckets.setdefault(session_of(h), []).append(t)

    print(f"{len(trades)} trades from {path}\n")
    header = f"{'Session':<22}{'n':>5}{'win%':>7}{'avg realized':>14}{'avg best_r':>12}{'%>=2R':>8}"
    print(header)
    order = ["Asian (00-07 UTC)", "London (07-12 UTC)", "NY overlap (12-16 UTC)", "Late/off (16-24 UTC)"]
    for name in order:
        rows = buckets.get(name, [])
        if not rows:
            continue
        n = len(rows)
        rr = [x["realized_r"] for x in rows if x.get("realized_r") is not None]
        br = [x.get("best_r", 0.0) for x in rows]
        wins = sum(1 for r in rr if r > 0.05)
        big = sum(1 for b in br if b >= 2.0)
        print(f"{name:<22}{n:>5}{100*wins/n:>6.0f}%{sum(rr)/n:>+13.3f}R{sum(br)/n:>+11.2f}R{100*big/n:>7.0f}%")


if __name__ == "__main__":
    main()

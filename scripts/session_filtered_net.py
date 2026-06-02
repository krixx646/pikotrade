"""Net expectancy after dropping dead-session trades, priced at a raw-account spread."""
import json
import sys
from datetime import datetime

PIP = {"XAU_USD": 0.1, "GBP_USD": 0.0001, "EUR_USD": 0.0001, "USD_JPY": 0.01}


def net_at(trades: list[dict], gold_pips: float, fx_pips: float) -> tuple[float, float, int]:
    nets = []
    for x in trades:
        risk, inst = x["risk"], x["instrument"]
        spread = gold_pips if inst == "XAU_USD" else fx_pips
        cost = (spread * PIP.get(inst, 0.0001)) / risk if risk > 0 else 0.0
        nets.append(x["realized_r"] - cost)
    return (sum(nets) / len(nets), sum(nets), len(nets)) if nets else (0.0, 0.0, 0)


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else r"outputs/backtests/m5entry_4yr.json"
    trades = json.load(open(path))["trades"]
    active = [t for t in trades if datetime.fromisoformat(t["signal_time"]).hour >= 7]
    print(f"All trades: {len(trades)}  |  Active-session only (drop 00-07 UTC): {len(active)}\n")
    for label, ts in [("ALL trades", trades), ("ACTIVE session only", active)]:
        gross = sum(x["realized_r"] for x in ts) / len(ts)
        print(f"--- {label} (gross {gross:+.3f}R) ---")
        for sc, g, fx in [("Standard ($0.25)", 2.5, 1.3), ("Raw/Zero ($0.12)", 1.2, 0.9), ("Raw best ($0.08)", 0.8, 0.6)]:
            e, tot, n = net_at(ts, g, fx)
            print(f"   {sc:<20} net {e:+.3f}R   total {tot:+.1f}R")
        print()


if __name__ == "__main__":
    main()

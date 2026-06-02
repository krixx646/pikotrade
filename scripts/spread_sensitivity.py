"""Recompute net expectancy of an existing route backtest at different broker spreads.

Cost scales linearly with spread, and each trade stores its own ``risk``, so we can
re-price every trade at any spread without re-running the backtest.
"""
import json
import sys

PIP = {"XAU_USD": 0.1, "GBP_USD": 0.0001, "EUR_USD": 0.0001, "USD_JPY": 0.01}


def load_trades(path: str) -> list[dict]:
    data = json.load(open(path))
    return data["trades"]


def net_expectancy(trades: list[dict], gold_pips: float, fx_pips: float) -> tuple[float, float]:
    nets = []
    for x in trades:
        risk = x["risk"]
        inst = x["instrument"]
        spread = gold_pips if inst == "XAU_USD" else fx_pips
        cost = (spread * PIP.get(inst, 0.0001)) / risk if risk > 0 else 0.0
        nets.append(x["realized_r"] - cost)
    return sum(nets) / len(nets), sum(nets)


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else r"outputs/backtests/m5entry.json"
    trades = load_trades(path)
    gross = sum(x["realized_r"] for x in trades) / len(trades)
    print(f"Sample: {len(trades)} trades from {path}")
    print(f"Gross expectancy: {gross:+.3f}R/trade\n")
    print(f"{'Exness scenario':<32}{'net/trade':>12}{'total R':>12}")
    scenarios = [
        ("my test default ($0.40 / 1.4p)", 4.0, 1.4),
        ("Standard ($0.25 / 1.3p)", 2.5, 1.3),
        ("Standard tight ($0.18 / 1.1p)", 1.8, 1.1),
        ("Raw/Zero ($0.12 / 0.9p)", 1.2, 0.9),
        ("Raw best ($0.08 / 0.6p)", 0.8, 0.6),
    ]
    for label, g, fx in scenarios:
        e, tot = net_expectancy(trades, g, fx)
        print(f"{label:<32}{e:>+11.3f}R{tot:>+11.1f}R")

    lo, hi = 0.0, 6.0
    for _ in range(50):
        mid = (lo + hi) / 2
        e, _ = net_expectancy(trades, mid, 1.0)
        if e > 0:
            lo = mid
        else:
            hi = mid
    print(f"\nBreakeven gold cost (GBP held ~1.0p): ~{lo:.2f} pips = ${lo * 0.1:.2f} spread")


if __name__ == "__main__":
    main()

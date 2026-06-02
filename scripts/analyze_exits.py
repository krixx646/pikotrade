import json
import sys
from datetime import timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from fx_annotation.config import load_oanda_config
from fx_annotation.oanda_client import OandaClient


def parse_dt(value):
    from datetime import datetime

    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch_m15_range(client, instrument, start, end):
    # OANDA caps a single candle request; M15 = 96 candles/day, so chunk by ~30 days.
    candles = []
    window = timedelta(days=30)
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + window, end)
        try:
            candles.extend(client.fetch_candles_range(instrument, "M15", cursor, chunk_end))
        except Exception:
            pass
        cursor = chunk_end
    seen = set()
    unique = []
    for candle in candles:
        if not candle.complete or candle.time in seen:
            continue
        seen.add(candle.time)
        unique.append(candle)
    return unique


def main() -> int:
    tests = json.loads((PROJECT_ROOT / "outputs" / "forward_tests.json").read_text(encoding="utf-8"))
    client = OandaClient(load_oanda_config())

    rows = []
    for key, test in tests.items():
        if not isinstance(test, dict) or test.get("status") != "closed":
            continue
        if test.get("model") == "partial_trail":
            status = str(test.get("outcome", ""))
            if status not in {"loss", "partial_only", "timeout"}:
                continue
        else:
            target = (test.get("targets") or {}).get("3R") or {}
            status = target.get("status")
            if status not in {"sl_hit", "sl_hit_ambiguous", "breakeven", "timeout"}:
                continue
        side = test.get("side")
        entry = float(test.get("entry_price", 0.0))
        sl = float(test.get("stop_loss", 0.0))
        risk = float(test.get("risk", 0.0)) or abs(entry - sl)
        entry_time = parse_dt(test.get("entry_time"))
        exit_time = parse_dt(test.get("exit_time"))
        if not entry_time or not exit_time or risk <= 0:
            continue
        candles = fetch_m15_range(
            client, test.get("instrument"), entry_time - timedelta(minutes=15), exit_time + timedelta(minutes=15)
        )
        if not candles:
            continue
        if side == "BUY":
            best = max(c.high for c in candles)
            worst = min(c.low for c in candles)
            mfe_r = (best - entry) / risk
            mae_r = (entry - worst) / risk
        else:
            best = min(c.low for c in candles)
            worst = max(c.high for c in candles)
            mfe_r = (entry - best) / risk
            mae_r = (worst - entry) / risk
        rows.append(
            {
                "instrument": test.get("instrument"),
                "route": test.get("route"),
                "side": side,
                "status": status,
                "mfe_r": round(mfe_r, 2),
                "mae_r": round(mae_r, 2),
            }
        )

    rows.sort(key=lambda r: r["mfe_r"], reverse=True)
    print(f"{'PAIR':9} {'ROUTE':28} {'SIDE':4} {'RESULT':18} {'MFE_R':>6} {'MAE_R':>6}")
    for r in rows:
        print(
            f"{r['instrument']:9} {r['route']:28} {r['side']:4} {r['status']:18} {r['mfe_r']:6.2f} {r['mae_r']:6.2f}"
        )

    if rows:
        n = len(rows)
        avg_mfe = sum(r["mfe_r"] for r in rows) / n
        near_tp = [r for r in rows if r["mfe_r"] >= 2.0]
        reached_1r = [r for r in rows if r["mfe_r"] >= 1.0]
        be = [r for r in rows if r["status"] == "breakeven"]
        be_high_mfe = [r for r in be if r["mfe_r"] >= 1.5]
        print("\nSUMMARY")
        print(f"- trades analyzed (non-TP closes): {n}")
        print(f"- average MFE toward TP: {avg_mfe:.2f}R out of 3R")
        print(f"- reached >= 1R in favor before exit: {len(reached_1r)}/{n}")
        print(f"- reached >= 2R in favor before exit: {len(near_tp)}/{n}")
        print(f"- breakeven trades: {len(be)}; of those that ran >=1.5R first: {len(be_high_mfe)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

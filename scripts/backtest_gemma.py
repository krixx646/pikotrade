"""EXPERIMENTAL point-in-time backtest for the Gemma AI route (delete-safe).

This is the ONLY valid way to test an AI route: at each historical step we rebuild
the market state seeing ONLY the past (via a replay client that hides future candles),
call the real Gemma model exactly as the live agent does, then simulate the outcome on
forward candles with the same scale-trail exit the other routes use.

It touches no live code. Delete this file to remove the feature.

Runtime note: every Gemma call hits the local Ollama model (slow). We gate calls to bars
where a real setup exists, and cap total calls with --max-gemma-calls to bound runtime.
"""
import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fx_annotation.ai_strategy import analyze_state_with_gemma
from fx_annotation.backtesting import _load_or_fetch_candles
from fx_annotation.candles import Candle
from fx_annotation.config import GeminiConfig, load_gemma_reviewer_config, load_oanda_config
from fx_annotation.forward_testing import SignalCandidate
from fx_annotation.market_watch import scan_instrument
from fx_annotation.oanda_client import OandaClient
from fx_annotation.route_backtesting import (
    DEFAULT_CACHE_DIR,
    RouteBacktestConfig,
    _first_index_at_or_after,
    _simulate_scale_trail,
    summarize_route_backtest,
)

GRANULARITIES = ("H4", "H1", "M15")


class ReplayClient:
    """Wraps cached candles and answers fetch_candles() with only bars up to `now`."""

    def __init__(self, real: OandaClient, instruments: list[str], start: datetime, end: datetime, cache_dir: Path):
        self.now: datetime = start
        self._data: dict[tuple[str, str], list[Candle]] = {}
        lookback = start - timedelta(days=160)
        tail = end + timedelta(days=3)
        for inst in instruments:
            for gran in GRANULARITIES:
                self._data[(inst, gran)] = _load_or_fetch_candles(real, inst, gran, lookback, tail, cache_dir)

    def fetch_candles(self, instrument: str, granularity: str, count: int = 300, price: str = "M") -> list[Candle]:
        candles = self._data.get((instrument, granularity), [])
        upto = [c for c in candles if c.time <= self.now]
        return upto[-count:]

    def m15(self, instrument: str) -> list[Candle]:
        return self._data.get((instrument, "M15"), [])


def _date(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _candidate_from_analysis(inst: str, a: object, signal_time: str) -> SignalCandidate | None:
    side = str(getattr(a, "side", "")).upper()
    low = getattr(a, "entry_zone_low", None)
    high = getattr(a, "entry_zone_high", None)
    if side not in {"BUY", "SELL"} or low is None or high is None:
        return None
    lo, hi = min(low, high), max(low, high)
    return SignalCandidate(
        route="Gemma",
        instrument=inst,
        side=side,
        status="ENTRY_NOW",
        entry_low=round(lo, 5),
        entry_high=round(hi, 5),
        source="Gemma AI entry zone",
        signal_time=signal_time,
        sweep_price=round(lo if side == "BUY" else hi, 5),
        bos_time=signal_time,
        notes=f"Gemma conf {getattr(a, 'confidence', '')}: {getattr(a, 'chart_notes', '')[:120]}",
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Point-in-time backtest of the Gemma AI route.")
    p.add_argument("--instruments", default="EUR_USD,GBP_USD,USD_JPY")
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--end", default="2024-03-01")
    p.add_argument("--scan-step", type=int, default=16, help="M15 bars between scans (16 = 4h).")
    p.add_argument("--max-gemma-calls", type=int, default=150, help="Cap total Gemma calls to bound runtime.")
    p.add_argument("--model", default="", help="Override model id (Gemini or Ollama). Blank = config default.")
    p.add_argument("--json-output", default="outputs/backtests/gemma_replay.json")
    args = p.parse_args()

    instruments = [s.strip() for s in args.instruments.split(",") if s.strip()]
    start, end = _date(args.start), _date(args.end)
    real = OandaClient(load_oanda_config())
    reviewer = load_gemma_reviewer_config()
    if args.model:
        reviewer = replace(reviewer, model=args.model)
    provider = "Gemini" if isinstance(reviewer, GeminiConfig) else "Ollama"
    print(f"Using {provider} model: {reviewer.model}")
    print(f"Loading candles for {instruments} ...")
    replay = ReplayClient(real, instruments, start, end, DEFAULT_CACHE_DIR)

    trades: list[dict[str, object]] = []
    diag = {"gemma_calls": 0, "setups_seen": 0, "entry_now": 0, "statuses": {}, "per_instrument": {}}

    for inst in instruments:
        m15 = replay.m15(inst)
        if not m15:
            continue
        cfg = RouteBacktestConfig(
            instrument=inst, start=start, end=end, routes=("Gemma",),
            exit_model="scale_trail", stop_mode="zone",
        )
        s_idx = _first_index_at_or_after(m15, start)
        e_idx = _first_index_at_or_after(m15, end)
        blocked_until = 0
        calls = 0
        ent = 0
        for index in range(s_idx, e_idx, max(1, args.scan_step)):
            if diag["gemma_calls"] >= args.max_gemma_calls:
                break
            if index < blocked_until:
                continue
            replay.now = m15[index].time
            state = scan_instrument(replay, inst, "")
            if getattr(state, "primary_setup", None) is None:
                continue
            diag["setups_seen"] += 1
            try:
                analyses = analyze_state_with_gemma(reviewer, state)
            except Exception as err:  # model/parse hiccup — skip this point, keep going
                diag["statuses"]["error"] = diag["statuses"].get("error", 0) + 1
                continue
            calls += 1
            diag["gemma_calls"] += 1
            for a in analyses:
                st = str(getattr(a, "status", "")).upper()
                diag["statuses"][st] = diag["statuses"].get(st, 0) + 1
                if st != "ENTRY_NOW":
                    continue
                cand = _candidate_from_analysis(inst, a, m15[index].time.isoformat())
                if cand is None:
                    continue
                diag["entry_now"] += 1
                ent += 1
                trade = _simulate_scale_trail(cand, m15, index, cfg)
                if trade.get("result") == "no_fill":
                    continue
                trades.append(trade)
                blocked_until = int(trade.get("exit_index", index + 1)) + 1
                break
        diag["per_instrument"][inst] = {"gemma_calls": calls, "entry_now": ent}

    result = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "engine": "gemma_point_in_time_replay",
        "model": reviewer.model,
        "trades": trades,
        "summary": summarize_route_backtest(trades),
        "diagnostics": diag,
    }
    out = PROJECT_ROOT / args.json_output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    rs = [t["realized_r"] for t in trades if t.get("realized_r") is not None]
    nets = [t.get("realized_r_net") for t in trades if t.get("realized_r_net") is not None]
    n = len(rs)
    wins = [r for r in rs if r > 0.05]
    print("\n=== Gemma point-in-time backtest ===")
    print(f"Gemma calls: {diag['gemma_calls']} | setups seen: {diag['setups_seen']} | ENTRY_NOW: {diag['entry_now']}")
    print(f"Status mix: {diag['statuses']}")
    if n:
        print(f"Trades: {n} | win {100*len(wins)/n:.1f}% | gross {sum(rs)/n:+.3f}R | net {sum(nets)/n:+.3f}R "
              f"| avg winner {sum(wins)/len(wins) if wins else 0:+.2f}R")
    else:
        print("No ENTRY_NOW trades were simulated (Gemma stayed in FORMING/WAIT, or no fills).")
    print(f"JSON: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

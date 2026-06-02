"""Diagnose why the Rule route fires so rarely.

Walks historical M15 bars, rebuilds the full replay state each bar, and tallies
the exact rejection reason returned by the rule candidate gate. This shows which
gate is killing setups (over-gating) versus genuine absence of setups.

Usage:
    python scripts/diagnose_rule_route.py --instruments EUR_USD,GBP_USD \
        --start 2025-01-01 --end 2025-04-01 --scan-interval-bars 2
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fx_annotation.backtesting import (  # noqa: E402
    DEFAULT_CACHE_DIR,
    BacktestConfig,
    _candidate_from_replay_state,
    _load_or_fetch_candles,
    _replay_state,
)
from fx_annotation.candles import Candle  # noqa: E402
from fx_annotation.config import load_oanda_config  # noqa: E402
from fx_annotation.oanda_client import OandaClient  # noqa: E402

_NUM = re.compile(r"-?\d+\.?\d*")


def _bucket(reason: str) -> str:
    """Collapse value-bearing reasons into a stable gate name."""
    if reason == "":
        return "ACCEPTED"
    cleaned = _NUM.sub("N", reason)
    return cleaned.rstrip("_")


def _first_index_at_or_after(candles: list[Candle], when: datetime) -> int:
    for index, candle in enumerate(candles):
        if candle.time >= when:
            return index
    return len(candles)


def _rule_config(instrument: str, start: datetime, end: datetime, a_grade: bool, min_score: int) -> BacktestConfig:
    return BacktestConfig(
        instrument=instrument,
        start=start,
        end=end,
        strategy_mode="mtf_sniper",
        rr=3.0,
        require_a_grade_confluence=a_grade,
        a_grade_min_score=min_score,
    )


def _run(client: OandaClient, instrument: str, start: datetime, end: datetime,
         scan_interval: int, a_grade: bool, min_score: int, cache_dir: Path) -> Counter:
    fetch_start = start - timedelta(days=90)
    h4 = _load_or_fetch_candles(client, instrument, "H4", fetch_start, end, cache_dir)
    h1 = _load_or_fetch_candles(client, instrument, "H1", fetch_start, end, cache_dir)
    m15 = _load_or_fetch_candles(client, instrument, "M15", fetch_start, end + timedelta(days=3), cache_dir)
    config = _rule_config(instrument, start, end, a_grade, min_score)

    counts: Counter = Counter()
    start_index = _first_index_at_or_after(m15, start)
    end_index = _first_index_at_or_after(m15, end)
    for index in range(start_index, end_index, max(1, scan_interval)):
        now = m15[index].time
        state = _replay_state(config, h4, h1, m15[: index + 1], [], now)
        _candidate, reason = _candidate_from_replay_state(config, state, now)
        counts[_bucket(reason)] += 1
    return counts


def _print_funnel(title: str, counts: Counter) -> None:
    total = sum(counts.values())
    print(f"\n=== {title} (bars evaluated: {total}) ===")
    accepted = counts.get("ACCEPTED", 0)
    print(f"ACCEPTED (rule setup fired): {accepted} ({accepted / total * 100:.2f}%)" if total else "no bars")
    for reason, n in counts.most_common():
        if reason == "ACCEPTED":
            continue
        print(f"  {reason:<40} {n:>6} ({n / total * 100:5.1f}%)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instruments", default="EUR_USD,GBP_USD,USD_JPY,XAU_USD")
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", default="2025-04-01")
    parser.add_argument("--scan-interval-bars", type=int, default=2)
    parser.add_argument("--min-score", type=int, default=5)
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    return parser.parse_args()


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def main() -> int:
    args = parse_args()
    instruments = [s.strip() for s in args.instruments.split(",") if s.strip()]
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    cache_dir = Path(args.cache_dir)
    client = OandaClient(load_oanda_config())

    strict_total: Counter = Counter()
    relaxed_total: Counter = Counter()
    for instrument in instruments:
        print(f"\n##### {instrument} #####", flush=True)
        strict = _run(client, instrument, start, end, args.scan_interval_bars, True, args.min_score, cache_dir)
        relaxed = _run(client, instrument, start, end, args.scan_interval_bars, False, args.min_score, cache_dir)
        _print_funnel(f"{instrument} STRICT (A-grade ON)", strict)
        _print_funnel(f"{instrument} RELAXED (A-grade OFF)", relaxed)
        strict_total.update(strict)
        relaxed_total.update(relaxed)

    _print_funnel("ALL INSTRUMENTS — STRICT (A-grade ON)", strict_total)
    _print_funnel("ALL INSTRUMENTS — RELAXED (A-grade OFF)", relaxed_total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

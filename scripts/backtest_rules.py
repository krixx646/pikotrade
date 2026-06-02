import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from fx_annotation.backtesting import (
    DEFAULT_CACHE_DIR,
    DEFAULT_OUTPUT_JSON,
    DEFAULT_OUTPUT_MD,
    BacktestConfig,
    run_rule_backtest,
    save_backtest_outputs,
)
from fx_annotation.config import load_oanda_config
from fx_annotation.oanda_client import OandaClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run rule-only historical backtests.")
    parser.add_argument("--instruments", default="EUR_JPY,USD_JPY,BTC_USD")
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", default="2025-03-01")
    parser.add_argument("--strategy-mode", choices=("mtf_sniper", "m15_simplified"), default="mtf_sniper")
    parser.add_argument("--rr", type=float, default=3.0)
    parser.add_argument("--timeout-bars", type=int, default=48)
    parser.add_argument("--scan-interval-bars", type=int, default=1)
    parser.add_argument("--max-trades-per-instrument", type=int, default=0)
    parser.add_argument("--max-bos-age-hours", type=float, default=6.0)
    parser.add_argument("--min-room-to-active-extreme-r", type=float, default=0.0)
    parser.add_argument("--require-h1-alignment", action="store_true")
    parser.add_argument("--require-htf-poi-touched-now", action="store_true")
    parser.add_argument("--require-entry-reaction-candle", action="store_true")
    parser.add_argument("--require-refined-entry", action="store_true")
    parser.add_argument("--require-premium-discount", action="store_true")
    parser.add_argument("--premium-discount-edge", type=float, default=0.5)
    parser.add_argument("--require-market-regime", action="store_true")
    parser.add_argument("--regime-min-range-atr", type=float, default=6.0)
    parser.add_argument("--regime-min-directional-efficiency", type=float, default=0.0)
    parser.add_argument("--allow-continuation-phase", action="store_true")
    parser.add_argument("--require-a-grade-confluence", action="store_true")
    parser.add_argument("--a-grade-min-score", type=int, default=5)
    parser.add_argument("--one-trade-per-htf-zone", action="store_true")
    parser.add_argument("--breakeven-after-r", type=float, default=0.0)
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--json-output", default=str(DEFAULT_OUTPUT_JSON))
    parser.add_argument("--markdown-output", default=str(DEFAULT_OUTPUT_MD))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    instruments = [item.strip() for item in args.instruments.split(",") if item.strip()]
    configs = [
        BacktestConfig(
            instrument=instrument,
            start=start,
            end=end,
            strategy_mode=args.strategy_mode,
            rr=args.rr,
            timeout_bars=args.timeout_bars,
            scan_interval_bars=args.scan_interval_bars,
            max_trades=args.max_trades_per_instrument,
            max_bos_age_hours=args.max_bos_age_hours,
            min_room_to_active_extreme_r=args.min_room_to_active_extreme_r,
            require_h1_alignment=args.require_h1_alignment,
            require_htf_poi_touched_now=args.require_htf_poi_touched_now,
            require_entry_reaction_candle=args.require_entry_reaction_candle,
            require_refined_entry=args.require_refined_entry,
            require_premium_discount=args.require_premium_discount,
            premium_discount_edge=args.premium_discount_edge,
            require_market_regime=args.require_market_regime,
            regime_min_range_atr=args.regime_min_range_atr,
            regime_min_directional_efficiency=args.regime_min_directional_efficiency,
            regime_require_pullback_phase=not args.allow_continuation_phase,
            require_a_grade_confluence=args.require_a_grade_confluence,
            a_grade_min_score=args.a_grade_min_score,
            one_trade_per_htf_zone=args.one_trade_per_htf_zone,
            breakeven_after_r=args.breakeven_after_r,
        )
        for instrument in instruments
    ]
    client = OandaClient(load_oanda_config())
    result = run_rule_backtest(client, configs, cache_dir=Path(args.cache_dir))
    save_backtest_outputs(
        result,
        json_path=Path(args.json_output),
        markdown_path=Path(args.markdown_output),
    )
    print(f"Trades: {result['summary']['trades']}")
    print(f"JSON: {args.json_output}")
    print(f"Markdown: {args.markdown_output}")
    return 0


def _parse_date(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())

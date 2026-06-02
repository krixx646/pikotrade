import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from fx_annotation.config import load_oanda_config
from fx_annotation.oanda_client import OandaClient
from fx_annotation.route_backtesting import (
    DEFAULT_CACHE_DIR,
    DEFAULT_OUTPUT_JSON,
    DEFAULT_OUTPUT_MD,
    SUPPORTED_ROUTES,
    RouteBacktestConfig,
    parse_ladder,
    run_route_backtest,
    save_route_backtest_outputs,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backtest the non-rule routes (M15_SIMPLE, DYNAMIC_SCORE, REGIME_RANGE) with the partial-then-trail exit."
    )
    parser.add_argument("--instruments", default="EUR_USD,GBP_USD,USD_JPY,XAU_USD")
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", default="2025-04-01")
    parser.add_argument("--routes", default=",".join(SUPPORTED_ROUTES))
    parser.add_argument("--timeout-bars", type=int, default=48)
    parser.add_argument("--scan-interval-bars", type=int, default=1)
    parser.add_argument("--max-wait-bars", type=int, default=12)
    parser.add_argument("--spread-pips", type=float, default=None, help="Override per-instrument round-trip cost in pips.")
    parser.add_argument("--rule-a-grade-min-score", type=int, default=5)
    parser.add_argument("--rule-require-a-grade", default="true", choices=["true", "false"], help="Toggle the A-grade confluence gate for the Rule route.")
    parser.add_argument("--rule-max-bos-age-hours", type=float, default=6.0, help="How long after BOS the Rule entry stays valid.")
    parser.add_argument("--rule-min-setup-quality", type=int, default=3, help="Minimum setup quality score (max is 3) for the Rule route.")
    parser.add_argument("--rule-refined-entry", default="false", choices=["true", "false"], help="GATE: require an M5-confirmed entry (lower-TF turn) for the Rule route — reduces frequency.")
    parser.add_argument("--rule-m5-stop", default="false", choices=["true", "false"], help="MILK (stop): keep the M15 entry, only tighten the stop to M5 micro-structure.")
    parser.add_argument("--rule-m5-entry", default="false", choices=["true", "false"], help="MILK (entry+stop): relocate the entry to the M5 reaction in the zone with a tight M5 stop (more R per move).")
    parser.add_argument("--exit-model", default="partial_trail", choices=["partial_trail", "scale_trail"], help="Exit model: legacy partial_trail or day-trade scale_trail.")
    parser.add_argument("--stop-mode", default="sweep", choices=["sweep", "zone"], help="scale_trail stop: wide (sweep) or tight (zone).")
    parser.add_argument("--stop-buffer-atr", type=float, default=0.1)
    parser.add_argument("--ladder", default="1:0.25,2:0.25,3:0.25", help="Profit ladder, e.g. '1:0.25,2:0.25,3:0.25' (rest is trailed).")
    parser.add_argument("--trail-distance-r", type=float, default=1.0)
    parser.add_argument("--max-hold-bars", type=int, default=96, help="Day-trade window in M15 bars (~96 = 1 day). Raise for swing.")
    parser.add_argument("--entry-confirmation", default="touch", choices=["touch", "rejection"], help="Fill on zone tap (touch) or wait for a reaction close (rejection).")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--json-output", default=str(DEFAULT_OUTPUT_JSON))
    parser.add_argument("--markdown-output", default=str(DEFAULT_OUTPUT_MD))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    instruments = [item.strip() for item in args.instruments.split(",") if item.strip()]
    routes = tuple(item.strip() for item in args.routes.split(",") if item.strip())
    unknown = [route for route in routes if route not in SUPPORTED_ROUTES]
    if unknown:
        print(f"Unknown routes: {unknown}. Supported: {SUPPORTED_ROUTES}")
        return 2
    configs = [
        RouteBacktestConfig(
            instrument=instrument,
            start=start,
            end=end,
            routes=routes,
            timeout_bars=args.timeout_bars,
            scan_interval_bars=args.scan_interval_bars,
            max_wait_bars=args.max_wait_bars,
            spread_pips=args.spread_pips,
            rule_require_a_grade=args.rule_require_a_grade == "true",
            rule_a_grade_min_score=args.rule_a_grade_min_score,
            rule_max_bos_age_hours=args.rule_max_bos_age_hours,
            rule_min_setup_quality=args.rule_min_setup_quality,
            rule_refined_entry=args.rule_refined_entry == "true",
            rule_m5_stop=args.rule_m5_stop == "true",
            rule_m5_entry=args.rule_m5_entry == "true",
            exit_model=args.exit_model,
            stop_mode=args.stop_mode,
            stop_buffer_atr=args.stop_buffer_atr,
            ladder=parse_ladder(args.ladder),
            trail_distance_r=args.trail_distance_r,
            max_hold_bars=args.max_hold_bars,
            entry_confirmation=args.entry_confirmation,
        )
        for instrument in instruments
    ]
    client = OandaClient(load_oanda_config())
    result = run_route_backtest(client, configs, cache_dir=Path(args.cache_dir))
    save_route_backtest_outputs(
        result,
        json_path=Path(args.json_output),
        markdown_path=Path(args.markdown_output),
    )
    summary = result["summary"]
    overall = summary["overall"]
    print(
        f"Total trades: {overall['trades']} | expectancy gross {overall['expectancy_r']}R "
        f"| net {overall['expectancy_r_net']}R | total net {overall['total_r_net']}R"
    )
    for route, stats in sorted(summary["by_route"].items()):
        print(
            f"  {route}: {stats['trades']} trades | gross {stats['expectancy_r']}R "
            f"| net {stats['expectancy_r_net']}R | win {stats['win_rate']} "
            f"| total net {stats['total_r_net']}R | max best {stats['max_best_r']}R"
        )
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

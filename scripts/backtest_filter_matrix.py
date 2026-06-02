import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from fx_annotation.backtesting import BacktestConfig, run_rule_backtest, save_backtest_outputs
from fx_annotation.config import load_oanda_config
from fx_annotation.oanda_client import OandaClient


WATCHLIST = (
    "EUR_USD",
    "GBP_USD",
    "USD_JPY",
    "USD_CAD",
    "AUD_USD",
    "NZD_USD",
    "EUR_JPY",
    "GBP_JPY",
    "XAU_USD",
    "BTC_USD",
)


VARIANTS = {
    "base": {},
    "premium_discount": {"require_premium_discount": True},
    "premium_discount_extreme": {
        "require_premium_discount": True,
        "premium_discount_edge": 0.45,
    },
    "entry_reaction": {"require_entry_reaction_candle": True},
    "premium_discount__entry_reaction": {
        "require_premium_discount": True,
        "require_entry_reaction_candle": True,
    },
    "premium_discount_extreme__entry_reaction": {
        "require_premium_discount": True,
        "premium_discount_edge": 0.45,
        "require_entry_reaction_candle": True,
    },
    "premium_discount_extreme__entry_reaction__regime": {
        "require_premium_discount": True,
        "premium_discount_edge": 0.45,
        "require_entry_reaction_candle": True,
        "require_market_regime": True,
    },
    "a_grade_confluence": {
        "require_a_grade_confluence": True,
        "premium_discount_edge": 0.45,
    },
    "a_grade_confluence_lenient": {
        "require_a_grade_confluence": True,
        "a_grade_min_score": 4,
        "premium_discount_edge": 0.45,
    },
    "premium_discount_extreme__entry_reaction__regime__a_grade": {
        "require_premium_discount": True,
        "premium_discount_edge": 0.45,
        "require_entry_reaction_candle": True,
        "require_market_regime": True,
        "require_a_grade_confluence": True,
    },
    "h1_alignment": {"require_h1_alignment": True},
    "htf_poi_touched_now": {"require_htf_poi_touched_now": True},
    "refined_entry": {"require_refined_entry": True},
    "premium_discount__h1_alignment": {
        "require_premium_discount": True,
        "require_h1_alignment": True,
    },
    "premium_discount__refined_entry": {
        "require_premium_discount": True,
        "require_refined_entry": True,
    },
    "strict_stack": {
        "require_premium_discount": True,
        "require_entry_reaction_candle": True,
        "require_h1_alignment": True,
        "require_htf_poi_touched_now": True,
        "require_refined_entry": True,
        "one_trade_per_htf_zone": True,
    },
}

INCOMPATIBLE_VARIANTS_BY_MODE = {
    "m15_simplified": {
        "htf_poi_touched_now",
        "refined_entry",
        "premium_discount__refined_entry",
        "strict_stack",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a filter-by-filter Trading Geek backtest matrix.")
    parser.add_argument("--instruments", default=",".join(WATCHLIST))
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", default="2025-02-01")
    parser.add_argument("--strategy-mode", choices=("mtf_sniper", "m15_simplified"), default="m15_simplified")
    parser.add_argument("--timeout-bars", type=int, default=192)
    parser.add_argument("--max-bos-age-hours", type=float, default=6.0)
    parser.add_argument("--min-room-to-active-extreme-r", type=float, default=3.0)
    parser.add_argument("--output-prefix", default="outputs/backtests/filter_matrix")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = OandaClient(load_oanda_config())
    instruments = [item.strip() for item in args.instruments.split(",") if item.strip()]
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    output_prefix = Path(args.output_prefix)

    rows: list[dict[str, object]] = []
    for variant, overrides in VARIANTS.items():
        if variant in INCOMPATIBLE_VARIANTS_BY_MODE.get(args.strategy_mode, set()):
            rows.append(_skipped_row(args.strategy_mode, variant, "Filter belongs to the MTF sniper model, not M15 simplified."))
            continue
        print(f"Running {args.strategy_mode}:{variant}...", flush=True)
        base_configs = [
            BacktestConfig(
                instrument=instrument,
                start=start,
                end=end,
                strategy_mode=args.strategy_mode,
                timeout_bars=args.timeout_bars,
                max_bos_age_hours=args.max_bos_age_hours,
                min_room_to_active_extreme_r=args.min_room_to_active_extreme_r,
            )
            for instrument in instruments
        ]
        configs = [replace(config, **overrides) for config in base_configs]
        result = run_rule_backtest(client, configs)
        variant_prefix = output_prefix.with_name(f"{output_prefix.name}_{args.strategy_mode}_{variant}")
        save_backtest_outputs(
            result,
            json_path=variant_prefix.with_suffix(".json"),
            markdown_path=variant_prefix.with_suffix(".md"),
        )
        rows.append(_matrix_row(args.strategy_mode, variant, result, variant_prefix))

    matrix = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "strategy_mode": args.strategy_mode,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "rows": rows,
    }
    matrix_json = output_prefix.with_name(f"{output_prefix.name}_{args.strategy_mode}_summary.json")
    matrix_md = output_prefix.with_name(f"{output_prefix.name}_{args.strategy_mode}_summary.md")
    matrix_json.parent.mkdir(parents=True, exist_ok=True)
    matrix_json.write_text(json.dumps(matrix, indent=2, sort_keys=True), encoding="utf-8")
    matrix_md.write_text(_render_matrix_markdown(matrix), encoding="utf-8")
    print(f"Summary JSON: {matrix_json}")
    print(f"Summary Markdown: {matrix_md}")
    return 0


def _matrix_row(strategy_mode: str, variant: str, result: dict[str, object], prefix: Path) -> dict[str, object]:
    summary = result.get("summary", {})
    if not isinstance(summary, dict):
        summary = {}
    return {
        "strategy_mode": strategy_mode,
        "variant": variant,
        "trades": summary.get("trades", 0),
        "tp_hit": summary.get("tp_hit", 0),
        "sl_hit": summary.get("sl_hit", 0),
        "timeout": summary.get("timeout", 0),
        "no_fill": summary.get("no_fill", 0),
        "win_rate_resolved": summary.get("win_rate_resolved", "n/a"),
        "average_r": summary.get("average_r", 0.0),
        "average_mfe_r": summary.get("average_mfe_r", 0.0),
        "average_mae_r": summary.get("average_mae_r", 0.0),
        "failure_tags": summary.get("failure_tags", {}),
        "json": str(prefix.with_suffix(".json")),
        "markdown": str(prefix.with_suffix(".md")),
    }


def _skipped_row(strategy_mode: str, variant: str, reason: str) -> dict[str, object]:
    return {
        "strategy_mode": strategy_mode,
        "variant": variant,
        "skipped": True,
        "skip_reason": reason,
        "trades": 0,
        "tp_hit": 0,
        "sl_hit": 0,
        "timeout": 0,
        "no_fill": 0,
        "win_rate_resolved": "skipped",
        "average_r": 0.0,
        "average_mfe_r": 0.0,
        "average_mae_r": 0.0,
        "failure_tags": {},
        "json": "",
        "markdown": "",
    }


def _render_matrix_markdown(matrix: dict[str, object]) -> str:
    rows = [row for row in matrix.get("rows", []) if isinstance(row, dict)]
    lines = [
        "# Trading Geek Filter Matrix",
        "",
        f"- strategy_mode: `{matrix.get('strategy_mode')}`",
        f"- start: `{matrix.get('start')}`",
        f"- end: `{matrix.get('end')}`",
        "",
        "## Results",
        "",
    ]
    for row in rows:
        if row.get("skipped"):
            lines.append(f"- `{row.get('variant')}`: skipped - {row.get('skip_reason')}")
        else:
            lines.append(
                "- "
                f"`{row.get('variant')}`: trades `{row.get('trades')}`, "
                f"TP `{row.get('tp_hit')}`, SL `{row.get('sl_hit')}`, "
                f"timeout `{row.get('timeout')}`, no_fill `{row.get('no_fill')}`, "
                f"win `{row.get('win_rate_resolved')}`, avgR `{row.get('average_r')}`, "
                f"MFE `{row.get('average_mfe_r')}`, MAE `{row.get('average_mae_r')}`"
            )
    lines.extend(["", "## Top Failure Tags", ""])
    for row in rows:
        tags = row.get("failure_tags", {})
        if not isinstance(tags, dict) or not tags:
            continue
        top_tags = sorted(tags.items(), key=lambda item: item[1], reverse=True)[:5]
        tag_text = ", ".join(f"{tag}: {count}" for tag, count in top_tags)
        lines.append(f"- `{row.get('variant')}`: {tag_text}")
    lines.append("")
    return "\n".join(lines)


def _parse_date(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())

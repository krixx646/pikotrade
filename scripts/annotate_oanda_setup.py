import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from fx_annotation.bias import detect_bias
from fx_annotation.ai_strategy import analyze_state_with_ai
from fx_annotation.candles import Candle
from fx_annotation.config import load_deepseek_config, load_oanda_config
from fx_annotation.market_watch import InstrumentState, classify_state
from fx_annotation.oanda_client import OandaClient
from fx_annotation.report import render_setup_report
from fx_annotation.setups import find_recent_setups
from fx_annotation.structure import detect_fair_value_gaps
from fx_annotation.svg_renderer import render_setup_svg


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create the first OANDA-based annotated setup chart."
    )
    parser.add_argument("--instrument", default="EUR_USD")
    parser.add_argument("--bias-granularity", default="H4")
    parser.add_argument("--entry-granularity", default="M15")
    parser.add_argument("--bias-count", type=int, default=200)
    parser.add_argument("--entry-count", type=int, default=400)
    parser.add_argument("--setup-limit", type=int, default=5)
    parser.add_argument("--use-ai", action="store_true")
    parser.add_argument("--fundamentals-file", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--report-output", default="")
    return parser.parse_args()


def main() -> int:
    _configure_stdout()
    args = parse_args()
    config = load_oanda_config()
    client = OandaClient(config)

    bias_candles = _completed(
        client.fetch_candles(
            instrument=args.instrument,
            granularity=args.bias_granularity,
            count=args.bias_count,
        )
    )
    entry_candles = _completed(
        client.fetch_candles(
            instrument=args.instrument,
            granularity=args.entry_granularity,
            count=args.entry_count,
        )
    )

    bias = detect_bias(bias_candles)
    recent_setups, swings, sweeps = find_recent_setups(
        entry_candles,
        bias,
        limit=args.setup_limit,
    )
    fair_value_gaps = detect_fair_value_gaps(entry_candles)
    setup = recent_setups[0] if recent_setups else None
    ai_analysis = None
    if args.use_ai:
        deepseek_config = load_deepseek_config()
        if deepseek_config is None:
            print("DeepSeek key is not configured. Add it to .env.deepseek.")
        else:
            status, action = classify_state(bias, setup)
            ai_analysis = analyze_state_with_ai(
                deepseek_config,
                InstrumentState(
                    instrument=args.instrument,
                    status=status,
                    action=action,
                    bias=bias,
                    primary_setup=setup,
                    recent_setups=recent_setups,
                    htf_narrative=None,
                    htf_pois=[],
                    zone_ladder=[],
                    relevant_htf_poi=None,
                    htf_poi_sequence="unknown",
                    swings=swings,
                    sweeps=sweeps,
                    fair_value_gaps=fair_value_gaps,
                    entry_candles=entry_candles,
                    ai_context="",
                ),
                fundamentals=_read_fundamentals(args.fundamentals_file),
            )

    output_path = Path(args.output) if args.output else _default_output_path(args)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path = (
        Path(args.report_output)
        if args.report_output
        else output_path.with_suffix(".md")
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)

    title = (
        f"{args.instrument} {args.entry_granularity} annotation "
        f"with {args.bias_granularity} bias"
    )
    output_path.write_text(
        render_setup_svg(
            candles=entry_candles,
            swings=swings,
            setup=setup,
            title=title,
            ai_analysis=ai_analysis,
        ),
        encoding="utf-8",
    )
    report_path.write_text(
        render_setup_report(
            instrument=args.instrument,
            bias_granularity=args.bias_granularity,
            entry_granularity=args.entry_granularity,
            bias=bias,
            entry_candles=entry_candles,
            swings=swings,
            sweeps=sweeps,
            setup=setup,
            recent_setups=recent_setups,
            chart_path=str(output_path),
            ai_analysis=ai_analysis,
        ),
        encoding="utf-8",
    )

    print(f"Instrument: {args.instrument}")
    print(f"Bias timeframe: {args.bias_granularity}")
    print(f"Entry timeframe: {args.entry_granularity}")
    print(f"Higher-timeframe bias: {bias.direction}")
    print(f"Bias reason: {bias.reason}")
    print(f"Entry candles: {len(entry_candles)}")
    print(f"Swing points: {len(swings)}")
    print(f"Liquidity sweeps: {len(sweeps)}")
    print(f"Recent setup candidates: {len(recent_setups)}")

    if setup is None:
        print("Setup: none found")
    else:
        sweep_candle = entry_candles[setup.sweep.index]
        bos_candle = entry_candles[setup.bos.index]
        print(f"Setup side: {setup.side.upper()}")
        print(f"Setup status: {setup.status}")
        print(f"Setup reason: {setup.reason}")
        print(
            "Sweep: "
            f"{setup.sweep.kind} at {sweep_candle.time.isoformat()} "
            f"swept {setup.sweep.swept_price}"
        )
        print(
            "BOS: "
            f"{setup.bos.direction} at {bos_candle.time.isoformat()} "
            f"broke {setup.bos.broken_price}"
        )
        print(
            "Entry zone: "
            f"{setup.entry_zone.low:.5f} - {setup.entry_zone.high:.5f} "
            f"({setup.entry_zone.source})"
        )
        print(f"Pullback touched zone: {setup.entry_zone.touched_after_bos}")
    if ai_analysis is not None:
        print(f"AI side: {ai_analysis.side}")
        print(f"AI status: {ai_analysis.status}")
        print(f"AI confidence: {ai_analysis.confidence}")

    print(f"Chart written: {output_path}")
    print(f"Report written: {report_path}")
    return 0


def _completed(candles: list[Candle]) -> list[Candle]:
    return [candle for candle in candles if candle.complete]


def _read_fundamentals(path_value: str) -> str:
    if not path_value:
        return ""
    path = Path(path_value)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _default_output_path(args: argparse.Namespace) -> Path:
    name = (
        f"{args.instrument}_{args.bias_granularity}_"
        f"{args.entry_granularity}_annotation.svg"
    )
    return PROJECT_ROOT / "outputs" / name


if __name__ == "__main__":
    raise SystemExit(main())

import argparse
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from fx_annotation.alerts import render_alerts_markdown
from fx_annotation.ai_strategy import (
    analyze_state_with_gemma,
    analyze_state_with_ai,
    render_ai_alerts_markdown,
    render_ai_strategy_report,
    render_gemma_alerts_markdown,
    render_gemma_strategy_report,
    select_ai_review_states,
    update_ai_memory,
)
from fx_annotation.chart_pack import render_instrument_chart_pack
from fx_annotation.config import (
    load_deepseek_config,
    load_gemma_reviewer_config,
    load_oanda_config,
)
from fx_annotation.live_memory import (
    due_instruments,
    next_due_summary,
    render_memory_updates,
    update_live_memory,
)
from fx_annotation.market_watch import (
    DEFAULT_WATCHLIST,
    render_market_watch_report,
    scan_market,
)
from fx_annotation.oanda_client import OandaClient


DEEPSEEK_DISABLED = True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run due-only live monitoring based on stored next-check times."
    )
    parser.add_argument(
        "--instruments",
        default=",".join(DEFAULT_WATCHLIST),
        help="Comma-separated OANDA instruments.",
    )
    parser.add_argument("--bias-granularity", default="H4")
    parser.add_argument("--refinement-granularity", default="H1")
    parser.add_argument("--entry-granularity", default="M15")
    parser.add_argument("--setup-limit", type=int, default=5)
    parser.add_argument("--fundamentals-file", default="")
    parser.add_argument("--force-all", action="store_true")
    parser.add_argument("--use-ai", action="store_true")
    parser.add_argument("--use-gemma", action="store_true")
    parser.add_argument("--no-chart-images", action="store_true")
    parser.add_argument(
        "--ai-limit",
        type=int,
        default=3,
        help="Deprecated while DeepSeek is disabled; kept for command compatibility.",
    )
    parser.add_argument(
        "--gemma-limit",
        type=int,
        default=1,
        help="Maximum number of due instruments local Gemma should analyze per run. Use 0 for all.",
    )
    parser.add_argument("--loop-seconds", type=int, default=0)
    parser.add_argument(
        "--memory-output",
        default=str(PROJECT_ROOT / "outputs" / "live_memory.json"),
    )
    parser.add_argument(
        "--alerts-output",
        default=str(PROJECT_ROOT / "outputs" / "alerts.json"),
    )
    parser.add_argument(
        "--alerts-md-output",
        default=str(PROJECT_ROOT / "outputs" / "alerts.md"),
    )
    parser.add_argument(
        "--ai-output",
        default=str(PROJECT_ROOT / "outputs" / "ai_strategy_analysis.md"),
    )
    parser.add_argument(
        "--ai-memory-output",
        default=str(PROJECT_ROOT / "outputs" / "ai_memory.json"),
    )
    parser.add_argument(
        "--ai-alerts-output",
        default=str(PROJECT_ROOT / "outputs" / "ai_alerts.json"),
    )
    parser.add_argument(
        "--ai-alerts-md-output",
        default=str(PROJECT_ROOT / "outputs" / "ai_alerts.md"),
    )
    parser.add_argument(
        "--gemma-output",
        default=str(PROJECT_ROOT / "outputs" / "gemma_strategy_analysis.md"),
    )
    parser.add_argument(
        "--gemma-memory-output",
        default=str(PROJECT_ROOT / "outputs" / "gemma_memory.json"),
    )
    parser.add_argument(
        "--gemma-alerts-output",
        default=str(PROJECT_ROOT / "outputs" / "gemma_alerts.json"),
    )
    parser.add_argument(
        "--gemma-alerts-md-output",
        default=str(PROJECT_ROOT / "outputs" / "gemma_alerts.md"),
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "outputs" / "live_monitor.md"),
    )
    return parser.parse_args()


def main() -> int:
    _configure_stdout()
    args = parse_args()
    config = load_oanda_config()
    client = OandaClient(config)
    instruments = [
        item.strip()
        for item in args.instruments.split(",")
        if item.strip()
    ]

    while True:
        report = run_once(args, client, instruments)
        print(report)

        if args.loop_seconds <= 0:
            return 0

        time.sleep(args.loop_seconds)


def run_once(
    args: argparse.Namespace,
    client: OandaClient,
    instruments: list[str],
) -> str:
    memory_path = Path(args.memory_output)
    alerts_path = Path(args.alerts_output)
    due = instruments if args.force_all else due_instruments(instruments, path=memory_path)

    if not due:
        report = "\n".join(
            [
                "# Live Monitor",
                "",
                "No instruments are due right now.",
                "",
                next_due_summary(path=memory_path),
                "",
            ]
        )
        _write_report(Path(args.output), report)
        return report

    fundamentals = _read_fundamentals(args.fundamentals_file)
    states = scan_market(
        client=client,
        instruments=due,
        fundamentals=fundamentals,
        bias_granularity=args.bias_granularity,
        refinement_granularity=args.refinement_granularity,
        entry_granularity=args.entry_granularity,
        setup_limit=args.setup_limit,
    )
    updates = update_live_memory(states, path=memory_path, alerts_path=alerts_path)
    report = "\n".join(
        [
            "# Live Monitor",
            "",
            f"Checked instruments: {', '.join(due)}",
            "",
            render_market_watch_report(states),
            render_memory_updates(updates),
        ]
    )
    if args.use_ai:
        report = f"{report}\n{run_ai_review(args, client, states, updates)}"
    if args.use_gemma:
        report = f"{report}\n{run_gemma_review(args, client, states, updates)}"

    _write_report(Path(args.output), report)
    _write_report(Path(args.alerts_md_output), render_alerts_markdown(alerts_path))
    return report


def run_ai_review(
    args: argparse.Namespace,
    client: OandaClient,
    states: object,
    updates: object,
) -> str:
    if DEEPSEEK_DISABLED:
        review = (
            "# DeepSeek AI Strategy Analysis\n\n"
            "DeepSeek is disabled to stop API spend. This project no longer uses DeepSeek "
            "for live analysis, route generation, or forward-test candidates.\n"
        )
        _write_report(Path(args.ai_output), review)
        return review

    config = load_deepseek_config()
    if config is None:
        return (
            "## DeepSeek AI Route\n\n"
            "DeepSeek key is not configured. Add it to `.env.deepseek` to enable live v4pro review.\n"
        )

    del updates
    fundamentals = _read_fundamentals(args.fundamentals_file)
    analyses = []
    errors = []
    for state in select_ai_review_states(states, args.ai_limit):
        try:
            image_paths = _chart_image_paths(args, client, state)
            analyses.extend(
                analyze_state_with_ai(
                    config,
                    state,
                    fundamentals=fundamentals,
                    image_paths=image_paths,
                )
            )
        except Exception as error:
            instrument = getattr(state, "instrument", "unknown")
            errors.append(f"- `{instrument}`: {error}")

    if not analyses and errors:
        review = "# DeepSeek AI Strategy Analysis\n\nDeepSeek live strategy analysis failed:\n" + "\n".join(errors) + "\n"
        _write_report(Path(args.ai_output), review)
        return review

    update_ai_memory(
        analyses,
        memory_path=Path(args.ai_memory_output),
        alerts_path=Path(args.ai_alerts_output),
    )
    _write_report(
        Path(args.ai_alerts_md_output),
        render_ai_alerts_markdown(Path(args.ai_alerts_output)),
    )
    review = render_ai_strategy_report(analyses)
    if errors:
        review = f"{review}\n## DeepSeek Route Errors\n\n" + "\n".join(errors) + "\n"
    _write_report(Path(args.ai_output), review)
    return review


def run_gemma_review(
    args: argparse.Namespace,
    client: OandaClient,
    states: object,
    updates: object,
) -> str:
    config = load_gemma_reviewer_config()
    del client, updates
    fundamentals = _read_fundamentals(args.fundamentals_file)
    analyses = []
    errors = []
    for state in select_ai_review_states(states, args.gemma_limit):
        try:
            analyses.extend(
                analyze_state_with_gemma(
                    config,
                    state,
                    fundamentals=fundamentals,
                )
            )
        except Exception as error:
            instrument = getattr(state, "instrument", "unknown")
            errors.append(f"- `{instrument}`: {error}")

    if not analyses and errors:
        review = "# Gemma AI Strategy Analysis\n\nGemma local strategy analysis failed:\n" + "\n".join(errors) + "\n"
        _write_report(Path(args.gemma_output), review)
        return review

    update_ai_memory(
        analyses,
        memory_path=Path(args.gemma_memory_output),
        alerts_path=Path(args.gemma_alerts_output),
    )
    _write_report(
        Path(args.gemma_alerts_md_output),
        render_gemma_alerts_markdown(Path(args.gemma_alerts_output)),
    )
    review = render_gemma_strategy_report(analyses)
    if errors:
        review = f"{review}\n## Gemma Route Errors\n\n" + "\n".join(errors) + "\n"
    _write_report(Path(args.gemma_output), review)
    return review


def _read_fundamentals(path_value: str) -> str:
    if not path_value:
        return ""
    path = Path(path_value)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _chart_image_paths(
    args: argparse.Namespace,
    client: OandaClient,
    state: object,
) -> list[Path]:
    if args.no_chart_images:
        return []
    try:
        return render_instrument_chart_pack(
            client=client,
            instrument=state.instrument,
            bias_granularity=args.bias_granularity,
            refinement_granularity=args.refinement_granularity,
            entry_granularity=args.entry_granularity,
        )
    except Exception:
        return []


def _write_report(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())

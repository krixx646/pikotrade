import argparse
import json
from pathlib import Path

import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from fx_annotation.pair_value import pair_value_for_instrument

DEFAULT_OPENCLAW_MEMORY = Path.home() / ".openclaw" / "workspace" / "memory" / "forex-chart-agent-state.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export forex chart agent state for external orchestration."
    )
    parser.add_argument(
        "--output-json",
        default=str(PROJECT_ROOT / "outputs" / "orchestration" / "market_agent_state.json"),
    )
    parser.add_argument(
        "--output-md",
        default=str(PROJECT_ROOT / "outputs" / "orchestration" / "market_agent_state.md"),
    )
    parser.add_argument(
        "--openclaw-memory",
        default=str(DEFAULT_OPENCLAW_MEMORY),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state = build_state()
    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    openclaw_memory = Path(args.openclaw_memory)

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")

    markdown = render_markdown(state)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(markdown, encoding="utf-8")

    openclaw_memory.parent.mkdir(parents=True, exist_ok=True)
    openclaw_memory.write_text(markdown, encoding="utf-8")

    print(f"Project JSON: {output_json}")
    print(f"Project Markdown: {output_md}")
    print(f"OpenClaw memory: {openclaw_memory}")
    return 0


def build_state() -> dict[str, object]:
    return {
        "project": "forex-chart-annotation-agent",
        "agent_version": "advanced_confluence_pair_value",
        "agent_capabilities": {
            "shared_a_grade_confluence": True,
            "reaction_confirmed_entries": True,
            "pair_value_alert_labels": True,
            "deepseek_disabled": True,
            "ai_consensus_override_forward_tests": False,
            "opportunity_forward_tests": False,
            "high_value_ai_opportunity_forward_tests": False,
            "split_ai_route_forward_tests": True,
            "gemma_smc_rag_new_tests_frozen": False,
            "m15_simple_forward_tests": True,
            "dynamic_score_forward_tests": True,
            "regime_range_forward_tests": True,
            "exit_model": "partial_then_trail",
            "partial_target_r": 1.5,
            "partial_fraction": 0.5,
            "runner_trail_r": 1.0,
            "runner_uncapped": True,
            "forward_test_timeout_bars": 48,
            "result_metric": "realized_r_expectancy",
            "real_trade_execution": False,
        },
        "wrong_system_guard": [
            "Do not use Quant Trading System V2 or V3.",
            "Do not use Hyperliquid DataAgent or FactorAgent.",
            "Do not start or rely on a localhost:5000 dashboard.",
            "Do not merge demo strategy systems into this OANDA forex agent.",
            "Continue OANDA-based forex forward testing with scripts/run_always_on.py.",
        ],
        "openclaw_schedule": {
            "job_name": "Trading Agent - OANDA Forex Forward Testing",
            "status": "old cron deleted; create exactly one new scheduled job",
            "interval_seconds": 300,
            "working_directory": str(PROJECT_ROOT),
            "command": 'python "scripts\\run_always_on.py" --once --use-gemma --gemma-limit 1',
            "chat_trigger": "Trading Agent",
            "chat_behavior": "Start or resume continuous chat updates for meaningful OANDA forex agent changes only.",
        },
        "ai_consensus_policy": {
            "route": "AI_CONSENSUS_OVERRIDE",
            "status": "disabled because DeepSeek is disabled to stop API spend",
            "max_ai_age_minutes": 240,
            "max_ai_gap_minutes": 240,
            "near_zone_tolerance_pips": 3,
            "requires_same_side": True,
            "requires_overlapping_or_near_matching_entry_zone": True,
            "requires_current_m15_zone_reaction": True,
            "stats_must_remain_separate_from_rule_route": True,
        },
        "opportunity_route_policy": {
            "route_suffix": "_OPPORTUNITY",
            "status": "disabled for new Rule/RULE_STALE_BOS/AI_CONSENSUS opportunity tests after poor results",
            "purpose": "Historical paper-test route for valid zones that strict reaction confirmation blocked.",
            "disabled_new_routes": ["Rule_OPPORTUNITY", "RULE_STALE_BOS_OPPORTUNITY", "AI_CONSENSUS_OVERRIDE_OPPORTUNITY"],
            "stats_must_remain_separate_from_rule_route": True,
        },
        "split_ai_route_policy": {
            "routes": [
                "GEMMA_SMC_RAG_OPPORTUNITY",
                "DEEPSEEK_SMC_RAG_OPPORTUNITY",
                "GEMMA_M15_MECHANICAL_OPPORTUNITY",
                "DEEPSEEK_M15_MECHANICAL_OPPORTUNITY",
            ],
            "status": "GEMMA_SMC_RAG_OPPORTUNITY re-enabled for observation under the new exit model; DeepSeek split routes remain historical only while DeepSeek is disabled",
            "frozen_new_routes": ["DEEPSEEK_SMC_RAG_OPPORTUNITY", "DEEPSEEK_M15_MECHANICAL_OPPORTUNITY"],
            "memory_routes": ["smc_rag", "m15_mechanical"],
            "min_confidence": 70,
            "max_distance_from_zone": "3 average M15 candle ranges",
            "stats_must_remain_separate_from_rule_route": True,
        },
        "m15_simple_policy": {
            "route": "M15_SIMPLE",
            "purpose": "Paper-test simplified Trading Geek-style M15 setups without requiring the full H4/H1 sniper POI sequence.",
            "requires_m15_bias": True,
            "requires_liquidity_sweep": True,
            "requires_bos": True,
            "requires_compact_base_zone": True,
            "requires_premium_discount_location": True,
            "requires_clean_chart_filter": True,
            "stats_must_remain_separate_from_rule_route": True,
        },
        "dynamic_score_policy": {
            "route": "DYNAMIC_SCORE",
            "purpose": "Paper-test weighted non-SMC strategy modules beside the original rule engine.",
            "strategies": ["trend_continuation", "breakout_continuation", "range_reversal"],
            "min_score": 5.8,
            "duplicate_guard": "Do not open a same-side DYNAMIC_SCORE test on the same instrument when an active near-identical entry already exists.",
            "factors": [
                "EMA trend/slope",
                "RSI momentum or exhaustion",
                "candle body/wick quality",
                "volatility",
                "tick-volume expansion",
                "recent swing structure",
                "compression",
                "range-edge location",
            ],
            "stats_must_remain_separate_from_rule_route": True,
        },
        "regime_range_policy": {
            "route": "REGIME_RANGE",
            "purpose": "Paper-test regime-gated range fades to add quality trade frequency without loosening the strict rule funnel.",
            "regime_classifier": "ATR-vs-baseline volatility, EMA50/EMA200 stack and price location, directional efficiency; regimes: trending_up, trending_down, ranging, high_volatility, unclear.",
            "fires_only_when": "regime == ranging",
            "suppressed_in": ["trends", "high_volatility"],
            "min_score": 4.8,
            "session_weight": "Soft only: score minimum is raised slightly during the London/New York overlap; session never hard-blocks a signal.",
            "duplicate_guard": "Do not open a same-side REGIME_RANGE test on the same instrument when an active near-identical entry already exists.",
            "stats_must_remain_separate_from_rule_route": True,
        },
        "exit_model_policy": {
            "model": "partial_then_trail",
            "partial_target_r": 1.5,
            "partial_fraction": 0.5,
            "runner_trail_r": 1.0,
            "runner_uncapped": True,
            "breakeven_after_partial": True,
            "timeout_bars": 48,
            "timeout_rationale": "Intraday M15 style, no overnight holds; runner is marked to market at timeout.",
            "result_metric": "realized R expectancy (average realized R per closed trade)",
            "realized_r_formula": "0.75 + 0.5 * runner_exit_r; full -1R if original stop hits before 1.5R",
            "legacy_tests_reported_separately": True,
        },
        "experimental_route_conflict_policy": {
            "applies_to": ["M15_SIMPLE", "DYNAMIC_SCORE", "REGIME_RANGE", "RULE_STALE_BOS", "AI_CONSENSUS_OVERRIDE", "*_OPPORTUNITY"],
            "behavior": "Do not open a new opposite-side paper test when the same instrument already has an active forward test.",
            "rule_route_is_anchor": True,
        },
        "alert_delivery_state": {
            "path": str(PROJECT_ROOT / "outputs" / "delivered_alerts.json"),
            "behavior": "Only deliver alert IDs not already recorded in delivered_alerts.json.",
        },
        "pair_value_policy": {
            "high_value": ["GBP_USD", "GBP_JPY", "USD_CAD", "USD_JPY", "XAU_USD"],
            "low_value_caution": ["AUD_USD", "BTC_USD", "EUR_USD", "NZD_USD"],
            "unvalidated": "Any pair not listed above.",
        },
        "rule_memory": _load_json(PROJECT_ROOT / "outputs" / "live_memory.json"),
        "rule_alerts": _load_json(PROJECT_ROOT / "outputs" / "alerts.json"),
        "ai_memory": _load_json(PROJECT_ROOT / "outputs" / "ai_memory.json"),
        "ai_alerts": _load_json(PROJECT_ROOT / "outputs" / "ai_alerts.json"),
        "gemma_memory": _load_json(PROJECT_ROOT / "outputs" / "gemma_memory.json"),
        "gemma_alerts": _load_json(PROJECT_ROOT / "outputs" / "gemma_alerts.json"),
        "forward_tests": _load_json(PROJECT_ROOT / "outputs" / "forward_tests.json"),
        "latest_rule_report": _load_text(PROJECT_ROOT / "outputs" / "live_monitor.md"),
        "latest_ai_report": _load_text(PROJECT_ROOT / "outputs" / "ai_strategy_analysis.md"),
        "latest_gemma_report": _load_text(PROJECT_ROOT / "outputs" / "gemma_strategy_analysis.md"),
        "latest_forward_tests": _load_text(PROJECT_ROOT / "outputs" / "forward_tests.md"),
    }


def render_markdown(state: dict[str, object]) -> str:
    lines = [
        "# Forex Chart Agent State",
        "",
        "This file is exported for OpenClaw/OpenWork-style orchestration.",
        "",
        "## Agent Version",
        "",
        "- Version: `advanced_confluence_pair_value`",
        "- Shared A-grade confluence is used by live scanning and backtesting.",
        "- Entry alerts include pair-value labels: `HIGH-VALUE PAIR`, `LOW-VALUE PAIR - CAUTION`, or `UNVALIDATED PAIR`.",
        "- DeepSeek is disabled to stop API spend; old DeepSeek memory is historical only.",
        "- `AI_CONSENSUS_OVERRIDE` is disabled while DeepSeek is disabled.",
        "- Exit model is partial-then-trail: bank 50% at 1.5R, runner trails 1R behind peak (uncapped), breakeven after the partial; results judged by realized-R expectancy.",
        "- Active forward-test timeout is 48 M15 bars (12h) to stay intraday with no overnight holds; the runner is marked to market at timeout. Legacy pre-redesign tests are reported separately and use the old 3R TP/SL counts.",
        "- Forward tests can include `M15_SIMPLE` as a separate simplified M15 route that does not force the full H4/H1 sniper POI sequence.",
        "- Forward tests can include `DYNAMIC_SCORE` as a separate weighted scoring experiment using non-SMC strategy modules.",
        "- Forward tests can include `REGIME_RANGE` as a separate regime-gated range route that only fades range edges when the market is classified as ranging.",
        "- New `Rule_OPPORTUNITY`, `RULE_STALE_BOS_OPPORTUNITY`, and AI-consensus opportunity tests are disabled after poor results.",
        "- Gemma route outputs can include `smc_rag` and `m15_mechanical`; DeepSeek split routes are historical only while disabled.",
        "- New `GEMMA_SMC_RAG_OPPORTUNITY` tests are frozen; historical results remain in reports.",
        "- Forward tests can include split AI routes historically: `GEMMA_SMC_RAG_OPPORTUNITY`, `DEEPSEEK_SMC_RAG_OPPORTUNITY`, `GEMMA_M15_MECHANICAL_OPPORTUNITY`, and `DEEPSEEK_M15_MECHANICAL_OPPORTUNITY`.",
        "- `GEMMA_HIGH_VALUE_OPPORTUNITY` and `DEEPSEEK_HIGH_VALUE_OPPORTUNITY` are disabled and purged from forward-test reporting.",
        "- Low-value pair means technically valid setups may appear, but Q1 tested edge was weak on that pair.",
        "- This agent is paper/alert only and must not place real trades.",
        "",
        "## OpenClaw Schedule",
        "",
        "- The old cron job was deleted.",
        "- Create exactly one new scheduled job named `Trading Agent - OANDA Forex Forward Testing`.",
        "- Run every `300` seconds / 5 minutes.",
        "- Working directory: `C:\\Users\\ADMIN\\Desktop\\signal`.",
        "- If a matching job already exists, update it instead of creating a duplicate.",
        "",
        "## Chat Trigger",
        "",
        "When the user says `Trading Agent`, OpenClaw should:",
        "",
        "1. Confirm the scheduled job exists and uses this project.",
        "2. Run one immediate cycle if outputs are stale.",
        "3. Continue chat updates for meaningful forex-agent changes only.",
        "4. Never report crypto auto-trader signals, dashboards, positions, or balances.",
        "",
        "## Read First",
        "",
        "OpenClaw must read this before taking action:",
        "",
        "```text",
        "C:\\Users\\ADMIN\\Desktop\\signal\\docs\\OPENCLAW_READ_THIS_FIRST.md",
        "```",
        "",
        "## Wrong-System Guard",
        "",
        "- Do not use `Quant Trading System V2` or `Quant Trading System V3`.",
        "- Do not use Hyperliquid `DataAgent` or `FactorAgent`.",
        "- Do not start or rely on a Flask dashboard at `http://localhost:5000`.",
        "- Do not merge demo momentum/MACD/supertrend systems into this project.",
        "- Continue this OANDA forex forward-testing agent using `scripts\\run_always_on.py`.",
        "",
        "## AI Consensus Override",
        "",
        "- Experimental route: `AI_CONSENSUS_OVERRIDE`.",
        "- Disabled because DeepSeek is no longer called.",
        "- Historical rule: DeepSeek and Gemma had to agree on the same side.",
        "- Entry zones must overlap or nearly match within `3` pips.",
        "- Both AI opinions must be within `240` minutes and no more than `240` minutes apart.",
        "- Current M15 price must still be at the consensus zone with a reaction candle.",
        "- Report override stats separately from strict `Rule` stats.",
        "",
        "## Opportunity Route",
        "",
        "- Experimental suffix: `*_OPPORTUNITY`.",
        "- Disabled for new `Rule_OPPORTUNITY`, `RULE_STALE_BOS_OPPORTUNITY`, and AI-consensus opportunity tests after poor results.",
        "- Historical records remain visible for performance review.",
        "- Report opportunity stats separately from strict `Rule` stats.",
        "",
        "## Split AI Opportunity Routes",
        "",
        "- Gemma can produce two independent route verdicts per analyzed pair.",
        "- DeepSeek is disabled and must not be called for new route verdicts.",
        "- `smc_rag` is the original SMC/RAG sniper workflow using HTF narrative, H4/H1 POI ladder, sweep, BOS, reaction, and target room.",
        "- `m15_mechanical` is the simplified M15 workflow using M15 trend/range, premium/discount, sweep, BOS, compact base zone, price return/near-return, and clean/ugly filtering.",
        "- New `GEMMA_SMC_RAG_OPPORTUNITY` tests are frozen after weak results.",
        "- Active split AI forward-test routes should be Gemma-only if enabled, with `GEMMA_M15_MECHANICAL_OPPORTUNITY` still unproven.",
        "- DeepSeek split routes are historical only: `DEEPSEEK_SMC_RAG_OPPORTUNITY` and `DEEPSEEK_M15_MECHANICAL_OPPORTUNITY`.",
        "- Report each route separately so weak performance can be traced to the original SMC/RAG route, the M15 mechanical route, a specific AI model, or the mechanical algo.",
        "",
        "## M15 Simple Route",
        "",
        "- Route: `M15_SIMPLE`.",
        "- Uses M15 trend, premium/discount, liquidity sweep, BOS, compact base zone, price return/near-return, 3R target, and clean/ugly chart filtering.",
        "- Does not require the full H4/H1 sniper POI sequence.",
        "- Rejects opposite-side M15 simplified tests when another active forward test already exists on the same instrument.",
        "- Report M15 simple stats separately from strict `Rule`, stale-BOS, AI consensus, and AI opportunity routes.",
        "- Non-`Rule` experimental routes are conflict-guarded against opening opposite-side paper tests on the same pair.",
        "",
        "## Exit Model (Partial-Then-Trail)",
        "",
        "- Every new forward test banks 50% at 1.5R (locks +0.75R), then trails the runner 1R behind the peak with no upper cap.",
        "- Runner goes to breakeven after the partial; a full -1R only happens if the original stop is hit before 1.5R.",
        "- `realized_r = 0.75 + 0.5 * runner_exit_r`. Judge routes by realized-R expectancy, not TP/SL counts.",
        "- Active-test timeout is 48 M15 bars (12h) to stay intraday (no overnight holds); the runner is marked to market at timeout. Legacy pre-redesign tests are reported in a separate block.",
        "",
        "## Dynamic Score Route",
        "",
        "- Route: `DYNAMIC_SCORE`.",
        "- Scores trend continuation, breakout continuation, and range reversal modules, then tests only the highest current score per pair.",
        "- Current minimum score: `5.8/10`.",
        "- Duplicate guard: do not stack same-side near-identical active `DYNAMIC_SCORE` tests on the same pair.",
        "- Factors include EMA trend/slope, RSI momentum or exhaustion, candle body/wick quality, volatility, tick volume, recent structure, compression, and range-edge location.",
        "- This is a new paper-only experiment and must be reported separately from `Rule`, `M15_SIMPLE`, AI routes, and opportunity routes.",
        "",
        "## Regime Range Route",
        "",
        "- Route: `REGIME_RANGE`.",
        "- Classifies the M15 regime (trending/ranging/high-volatility/unclear) from ATR-vs-baseline volatility, the EMA50/EMA200 stack, and directional efficiency.",
        "- Only opens a range-reversal fade when the regime is `ranging`; it is suppressed in trends and volatility spikes.",
        "- Current minimum score: `4.8/10`.",
        "- Session is a soft weight only: the score minimum is raised slightly during the London/New York overlap; session never hard-blocks a signal.",
        "- Duplicate guard: do not stack same-side near-identical active `REGIME_RANGE` tests on the same pair.",
        "- This is a new paper-only experiment and must be reported separately from `Rule`, `M15_SIMPLE`, `DYNAMIC_SCORE`, AI routes, and opportunity routes.",
        "",
        "## Alert Delivery State",
        "",
        "- Delivered alert IDs are stored in `outputs/delivered_alerts.json`.",
        "- OpenClaw should not resend historical alerts already recorded there.",
        "- If the first run after dedupe sends old alerts, treat it as one-time catch-up.",
        "",
        "## Pair Value Policy",
        "",
        "- High-value: `GBP_USD`, `GBP_JPY`, `USD_CAD`, `USD_JPY`, `XAU_USD`",
        "- Low-value caution: `AUD_USD`, `BTC_USD`, `EUR_USD`, `NZD_USD`",
        "- Unvalidated: any other pair",
        "",
        "## Rule Route",
        "",
        _summary_from_memory(state.get("rule_memory", {})),
        "",
        "## Decision Dashboard",
        "",
        _decision_dashboard(state.get("rule_memory", {})),
        "",
        "## DeepSeek AI Route",
        "",
        "DeepSeek is disabled to stop API spend. The memory below is historical only and must not trigger new API calls.",
        "",
        _summary_from_memory(state.get("ai_memory", {})),
        "",
        "## Gemma AI Route",
        "",
        _summary_from_memory(state.get("gemma_memory", {})),
        "",
        "## Alert Files",
        "",
        "- Rule alerts: `outputs/alerts.md`",
        "- AI alerts: `outputs/ai_alerts.md`",
        "- Gemma alerts: `outputs/gemma_alerts.md`",
        "",
        "## Forward Testing",
        "",
        _forward_test_summary(state.get("forward_tests", {})),
        "",
        "## Operator Commands",
        "",
        "```powershell",
        'cd "C:\\Users\\ADMIN\\Desktop\\signal"',
        '$env:PYTHONPATH="C:\\Users\\ADMIN\\Desktop\\signal\\src"',
        'python "scripts\\run_always_on.py" --once --use-gemma --gemma-limit 1',
        "```",
        "",
    ]
    return "\n".join(lines)


def _summary_from_memory(memory: object) -> str:
    if not isinstance(memory, dict) or not memory:
        return "No memory records available."

    lines: list[str] = []
    for instrument, record in sorted(memory.items()):
        if not isinstance(record, dict):
            continue
        status = record.get("status", "")
        side = record.get("primary_side", record.get("side", ""))
        pair_value = record.get("pair_value_label") or pair_value_for_instrument(str(instrument)).label
        next_check = record.get("next_check_time", "")
        reason = _short_text(str(record.get("next_check_reason", record.get("reasoning", ""))))
        lines.append(f"- `{instrument}` [{pair_value}]: {side} {status}, next `{next_check}` - {reason}")
    return "\n".join(lines) if lines else "No memory records available."


def _decision_dashboard(memory: object) -> str:
    if not isinstance(memory, dict) or not memory:
        return "No rule memory records available."

    lines = [
        "| Pair | Decision | Pair Value | Side | Reason |",
        "|---|---|---|---|---|",
    ]
    for instrument, record in sorted(memory.items()):
        if not isinstance(record, dict):
            continue
        decision = _decision_label(record)
        pair_value = record.get("pair_value_label") or pair_value_for_instrument(str(instrument)).label
        side = str(record.get("primary_side") or record.get("side") or "").upper()
        reason = _short_text(_decision_reason(record), limit=120)
        lines.append(f"| `{instrument}` | `{decision}` | {pair_value} | {side} | {reason} |")
    return "\n".join(lines)


def _decision_label(record: dict[str, object]) -> str:
    status = str(record.get("status", ""))
    htf_sequence = str(record.get("htf_poi_sequence", ""))
    current_state = str(record.get("current_state", ""))
    quality = record.get("quality_score")
    distance = record.get("distance_in_ranges")

    if status == "entry_candidate_now":
        return "ENTRY_NOW"
    if isinstance(distance, int | float) and float(distance) <= 0.75:
        return "NEAR_ENTRY"
    if htf_sequence not in {"", "valid", "no_m15_setup"}:
        return "BLOCKED_BY_POI_SEQUENCE"
    if status == "no_clear_state":
        return "BLOCKED_BY_HTF_BIAS"
    if status == "low_quality" or (isinstance(quality, int | float) and int(quality) < 3):
        return "LOW_QUALITY"
    if status == "expired" or current_state == "expired_after_bos":
        return "EXPIRED"
    if current_state == "waiting_for_first_pullback" or status == "wait_for_pullback":
        return "WAITING_FOR_PULLBACK"
    story = record.get("story")
    if isinstance(story, dict):
        phase = str(story.get("phase", ""))
        if phase == "waiting_for_liquidity_sweep":
            return "WAITING_FOR_SWEEP"
        if phase == "waiting_for_15m_market_shift":
            return "WAITING_FOR_BOS"
    if status == "potential_future_setup":
        return "WATCHING"
    return status.upper() if status else "UNKNOWN"


def _decision_reason(record: dict[str, object]) -> str:
    story = record.get("story")
    if isinstance(story, dict) and story.get("note"):
        return str(story.get("note"))
    return str(record.get("next_check_reason") or record.get("action") or "")


def _forward_test_summary(tests: object) -> str:
    if not isinstance(tests, dict) or not tests:
        return "No forward tests recorded yet."

    open_count = 0
    closed_count = 0
    deduped: dict[tuple[object, ...], dict[str, object]] = {}
    raw_tests = []
    for test in tests.values():
        if not isinstance(test, dict):
            continue
        raw_tests.append(test)
        if test.get("status") == "closed":
            closed_count += 1
        else:
            open_count += 1
        key = (
            test.get("route"),
            test.get("instrument"),
            test.get("side"),
            _rounded(test.get("entry_price")),
            _rounded(test.get("stop_loss")),
            test.get("entry_time") or "no_entry",
        )
        deduped.setdefault(key, test)

    raw_wins, raw_losses = _target_counts(raw_tests)
    deduped_values = list(deduped.values())
    deduped_wins, deduped_losses = _target_counts(deduped_values)
    non_gold_wins, non_gold_losses = _target_counts(
        [test for test in deduped_values if test.get("instrument") != "XAU_USD"]
    )
    gold_wins, gold_losses = _target_counts(
        [test for test in deduped_values if test.get("instrument") == "XAU_USD"]
    )

    return "\n".join(
        [
            f"- Open or pending tests: `{open_count}`",
            f"- Closed tests: `{closed_count}`",
            f"- Primary target: `3R`",
            f"- Raw 3R wins: `{raw_wins}`",
            f"- Raw 3R losses: `{raw_losses}`",
            f"- Deduped 3R wins: `{deduped_wins}`",
            f"- Deduped 3R losses: `{deduped_losses}`",
            f"- Deduped non-gold 3R wins/losses: `{non_gold_wins}` / `{non_gold_losses}`",
            f"- Deduped gold-only 3R wins/losses: `{gold_wins}` / `{gold_losses}`",
            "- Details: `outputs/forward_tests.md` and `outputs/forward_tests.json`",
        ]
    )


def _target_counts(tests: list[dict[str, object]]) -> tuple[int, int]:
    wins = 0
    losses = 0
    for test in tests:
        targets = test.get("targets", {})
        if not isinstance(targets, dict):
            continue
        for target in targets.values():
            if not isinstance(target, dict) or target.get("rr") not in {3, 3.0}:
                continue
            status = str(target.get("status", ""))
            if status == "tp_hit":
                wins += 1
            if status in {"sl_hit", "sl_hit_ambiguous"}:
                losses += 1
    return wins, losses


def _rounded(value: object) -> float:
    try:
        return round(float(value), 5)
    except (TypeError, ValueError):
        return 0.0


def _short_text(value: str, limit: int = 180) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _load_json(path: Path) -> object:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())

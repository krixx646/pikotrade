from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter
import json

from fx_annotation.candles import Candle
from fx_annotation.config import PROJECT_ROOT
from fx_annotation.bias import detect_bias
from fx_annotation.htf_momentum import HtfMomentumParams, htf_momentum_signal
try:  # delete-safe: removing htf_zone.py just disables the HTF_ZONE route, nothing else breaks
    from fx_annotation.htf_zone import HtfZoneParams, htf_zone_signal
    HTF_ZONE_AVAILABLE = True
except Exception:  # pragma: no cover
    HTF_ZONE_AVAILABLE = False
from fx_annotation.dynamic_scoring import (
    DynamicScoreSignal,
    best_dynamic_score,
    best_regime_range_signal,
    detect_regime,
)
from fx_annotation.oanda_client import OandaClient
from fx_annotation.pair_value import pair_value_for_instrument
from fx_annotation.scheduler import market_session
from fx_annotation.setups import SetupCandidate, find_recent_setups
from fx_annotation.structure import average_range
from fx_annotation.trade_targets import TradeTarget, available_r


DEFAULT_TESTS_PATH = PROJECT_ROOT / "outputs" / "forward_tests.json"
DEFAULT_TESTS_MD_PATH = PROJECT_ROOT / "outputs" / "forward_tests.md"
DEFAULT_DIAGNOSTICS_PATH = PROJECT_ROOT / "outputs" / "forward_test_diagnostics.json"
DEFAULT_OPENCLAW_TESTS_PATH = Path.home() / ".openclaw" / "workspace" / "memory" / "forex-forward-tests.md"
DEFAULT_MEMORY_PATHS = {
    "Rule": PROJECT_ROOT / "outputs" / "live_memory.json",
    "DeepSeek": PROJECT_ROOT / "outputs" / "ai_memory.json",
    "Gemma": PROJECT_ROOT / "outputs" / "gemma_memory.json",
}
DEFAULT_WATCHLIST = (
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
MIN_FORWARD_TEST_QUALITY = 3
MAX_BOS_AGE_HOURS = 6
# Live Rule route. NOTE: this live path is the RELAXED rule variant (quality>=3, stale-BOS tracked,
# no strict A-grade>=5 gate) — i.e. the ~546-trade / 51% / +0.096R "relaxed" engine, NOT the strict
# A-grade 7-trade backtest config. Re-enabled at the user's request to run alongside MOMENTUM.
# Set to False to suppress the live Rule route's own signals (rule_memory still feeds AI-route context).
STRICT_RULE_ENGINE_ENABLED = True
# MOMENTUM route (impulse-continuation entry). Self-contained + delete-safe: remove this block,
# the _momentum_* functions, and the one extend() line in run_forward_testing to fully remove it.
# Backtested best at 3.5xATR (56.9% win, +0.232R gross on gold+cable). Fires across full watchlist.
MOMENTUM_ENABLED = True
MOMENTUM_IMPULSE_LOOKBACK = 10
MOMENTUM_IMPULSE_ATR_MULT = 3.5
MOMENTUM_RECENT_EXTREME_WITHIN = 6
MOMENTUM_SHALLOW_RETRACE = 0.33
MOMENTUM_DEEP_RETRACE = 0.55
MOMENTUM_BIAS_LOOKBACK = 120
# HTF_MOMENTUM route (day-trade): detect an impulse move on H1, hand a tight entry zone to M15
# so the small M15 stop rides the long HTF continuation for max R. Detection lives in
# htf_momentum.py (delete-safe). Backtested (Jan-May 2025, 4 pairs, 1pip spread): 195 trades,
# 75.9% win, +0.641R net/trade, +125R total. Remove this block + _htf_momentum_* + the one
# extend() line in run_forward_testing to fully remove it.
HTF_MOMENTUM_ENABLED = True
# Profile A: target = the H1 impulse high (target_ext=0.0), M15-structure stop, ride to target.
# Backtest (Jan-May 2025, 4 pairs): 64% win, +0.73R/trade, +140R, 192 trades.
HTF_MOMENTUM_PARAMS = HtfMomentumParams(impulse_atr_mult=2.0, bias_lookback=80, target_ext=0.0)
HTF_MOMENTUM_MAX_DISTANCE_RANGES = 4.0
# HTF_ZONE route (EXPERIMENTAL, delete-safe): H4 bias + H1 SMC zone -> M15 reaction, trailing
# exit (the config that backtested 83% win / +1.25R per trade - but rare, ~6 trades/4mo).
# Detection in htf_zone.py. To remove: delete htf_zone.py + backtest_htf_zone.py + this block +
# the one extend() line in run_forward_testing (the guarded import auto-disables it regardless).
HTF_ZONE_ENABLED = True
HTF_ZONE_MAX_DISTANCE_RANGES = 4.0
HTF_ZONE_PARAMS = HtfZoneParams() if HTF_ZONE_AVAILABLE else None

# Alert hierarchy: routes ranked by their measured edge (2024 backtests, scale-trail exit).
# Lower rank = higher priority. Used to tier and sort live alerts for OpenClaw + the trader.
# (rank, label, one-line rationale shown in the alert legend)
TIER_ORDER: tuple[tuple[int, str, str], ...] = (
    (1, "HTF-MOMENTUM", "HTF_MOMENTUM - H1 day-trade momentum -> M15 entry, ride to H1 target (64% win / +0.73R / +140R backtest)."),
    (2, "HTF-ZONE", "HTF_ZONE - H4/H1 SMC reaction -> M15 entry, trailing (83% win / +1.25R per trade, but rare)."),
    (3, "PREMIUM", "MOMENTUM - M15 impulse-continuation, best M15 edge."),
    (4, "HIGH", "M15_SIMPLE - net-positive on trendy pairs (BTC, USD_JPY, EUR_JPY)."),
    (5, "MEDIUM", "DYNAMIC_SCORE - real gross edge, spread-sensitive. Best on a raw-spread account."),
    (6, "LOW", "REGIME_RANGE / relaxed Rule - thin or net-negative blended. Confirmation only."),
    (7, "WATCH", "AI routes (DeepSeek/Gemma) and opportunity variants - observe, do not trade blindly."),
)
TIER_LABELS = {rank: label for rank, label, _desc in TIER_ORDER}
AI_CONSENSUS_MAX_AGE_MINUTES = 240
AI_CONSENSUS_MAX_GAP_MINUTES = 240
AI_CONSENSUS_ZONE_TOLERANCE_PIPS = 3.0
AI_HIGH_VALUE_MIN_CONFIDENCE = 70.0
AI_HIGH_VALUE_MAX_DISTANCE_RANGES = 3.0
M15_SIMPLE_MAX_DISTANCE_RANGES = 4.0
DYNAMIC_SCORE_MINIMUM = 5.8
DYNAMIC_SCORE_MAX_DISTANCE_RANGES = 1.5
DYNAMIC_SCORE_DUPLICATE_TOLERANCE_PIPS = 5.0
REGIME_RANGE_MINIMUM = 4.8
REGIME_RANGE_MAX_DISTANCE_RANGES = 1.5
REGIME_RANGE_OVERLAP_PENALTY = 0.4
SCORE_DUPLICATE_ROUTES = {"DYNAMIC_SCORE", "REGIME_RANGE"}
PARTIAL_TARGET_R = 1.5
PARTIAL_FRACTION = 0.5
RUNNER_TRAIL_R = 1.0
FROZEN_AI_SPLIT_ROUTES: set[tuple[str, str]] = set()
DISABLED_OPPORTUNITY_SOURCE_ROUTES = {"Rule", "RULE_STALE_BOS", "AI_CONSENSUS_OVERRIDE"}
AI_SPLIT_ROUTE_KEYS = {
    "smc_rag": "SMC_RAG",
    "m15_mechanical": "M15_MECHANICAL",
}


@dataclass(frozen=True)
class SignalCandidate:
    route: str
    instrument: str
    side: str
    status: str
    entry_low: float
    entry_high: float
    source: str
    signal_time: str
    sweep_price: float | None
    bos_time: str
    notes: str
    htf_range_low: float | None = None
    htf_range_high: float | None = None
    target_price: float | None = None
    target_timeframe: str = ""
    target_reason: str = ""
    available_r: float | None = None
    entry_timeframe: str = "M15"


def run_forward_testing(
    client: OandaClient,
    tests_path: Path = DEFAULT_TESTS_PATH,
    markdown_path: Path = DEFAULT_TESTS_MD_PATH,
    openclaw_path: Path = DEFAULT_OPENCLAW_TESTS_PATH,
    rr_values: tuple[float, ...] = (3.0,),
    timeout_bars: int = 48,
    max_signal_age_minutes: int = 30,
    track_m5_variant: bool = True,
) -> dict[str, object]:
    tests = _load_json(tests_path)
    diagnostics: list[dict[str, object]] = []
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    candidates = _load_signal_candidates()
    candidates.extend(_m15_simple_candidates(client, now))
    candidates.extend(_dynamic_score_candidates(client, now))
    candidates.extend(_regime_range_candidates(client, now))
    candidates.extend(_momentum_candidates(client, now))
    candidates.extend(_htf_momentum_candidates(client, now))
    candidates.extend(_htf_zone_candidates(client, now))

    for candidate in candidates:
        if not _fresh_signal(candidate.signal_time, now_dt, max_signal_age_minutes):
            diagnostics.append(_candidate_diagnostic(candidate, "signal_not_fresh"))
            continue
        if candidate.route != "Rule" and _conflicts_with_active_test(candidate, tests):
            diagnostics.append(_candidate_diagnostic(candidate, "conflicts_existing_active_route"))
            continue
        if _duplicates_active_test(candidate, tests):
            diagnostics.append(_candidate_diagnostic(candidate, "duplicates_existing_active_route"))
            continue
        key = _candidate_key(candidate)
        if key in tests:
            diagnostics.append(_candidate_diagnostic(candidate, "already_tracking"))
            continue
        candles = _fetch_m15(client, candidate.instrument)
        if not candles:
            diagnostics.append(_candidate_diagnostic(candidate, "no_m15_candles"))
            continue
        m5_candles = _fetch_m5(client, candidate.instrument)
        rejection = _strict_live_entry_rejection(candidate, candles, rr_values)
        if rejection:
            diagnostics.append(_candidate_diagnostic(candidate, rejection))
            opportunity = _opportunity_candidate(candidate, rejection, candles)
            if opportunity is None:
                continue
            opportunity_key = _candidate_key(opportunity)
            if opportunity_key in tests:
                diagnostics.append(_candidate_diagnostic(opportunity, "already_tracking"))
                continue
            opportunity_test = _new_test(opportunity, candles, rr_values, now, m5_candles)
            tests[opportunity_key] = opportunity_test
            _maybe_add_m5_sibling(tests, opportunity_test, track_m5_variant)
            diagnostics.append(_candidate_diagnostic(opportunity, "opened_opportunity_forward_test"))
            continue
        base_test = _new_test(candidate, candles, rr_values, now, m5_candles)
        tests[key] = base_test
        _maybe_add_m5_sibling(tests, base_test, track_m5_variant)
        diagnostics.append(_candidate_diagnostic(candidate, "opened_forward_test"))

    m15_cache: dict[str, list[Candle]] = {}
    m5_cache: dict[str, list[Candle]] = {}
    for key, test in list(tests.items()):
        if not isinstance(test, dict) or test.get("status") == "closed":
            continue
        instrument = str(test.get("instrument", ""))
        if test.get("timeframe") == "M5":
            m5c = m5_cache.get(instrument)
            if m5c is None:
                m5c = _fetch_m5(client, instrument)
                m5_cache[instrument] = m5c
            if not m5c:
                continue
            _update_m5_entry_test(test, m5c, timeout_bars * 3, now)
        else:
            candles = m15_cache.get(instrument)
            if candles is None:
                candles = _fetch_m15(client, instrument)
                m15_cache[instrument] = candles
            if not candles:
                continue
            _update_test(test, candles, timeout_bars, now)

    tests_path.parent.mkdir(parents=True, exist_ok=True)
    tests_path.write_text(json.dumps(tests, indent=2, sort_keys=True), encoding="utf-8")
    _save_forward_test_diagnostics(diagnostics)
    markdown = render_forward_tests_markdown(tests)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(markdown, encoding="utf-8")
    openclaw_path.parent.mkdir(parents=True, exist_ok=True)
    openclaw_path.write_text(markdown, encoding="utf-8")
    return tests


def render_forward_tests_markdown(tests: dict[str, object]) -> str:
    open_tests = []
    closed_tests = []
    for key, value in sorted(tests.items()):
        if not isinstance(value, dict):
            continue
        (closed_tests if value.get("status") == "closed" else open_tests).append((key, value))

    lines = [
        "# Forex Forward Tests",
        "",
        "Paper-only live signal testing. No execution, no broker orders.",
        "",
        "## Summary",
        "",
    ]
    lines.extend(_summary_lines(tests))
    lines.append("")
    lines.extend(_tier_legend_lines())
    lines.extend(
        [
            "",
            "## Live Signal Alerts (priority order)",
            "",
        ]
    )
    lines.extend(_alert_lines(open_tests))
    lines.extend(["", "## Candidate Diagnostics", ""])
    lines.extend(_diagnostic_lines() or ["No candidate diagnostics recorded this cycle."])
    lines.extend(["", "## Closed", ""])
    lines.extend(_test_lines(closed_tests[-30:]) or ["No closed forward tests yet."])
    lines.append("")
    return "\n".join(lines)


def _save_forward_test_diagnostics(diagnostics: list[dict[str, object]]) -> None:
    DEFAULT_DIAGNOSTICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_DIAGNOSTICS_PATH.write_text(json.dumps(diagnostics, indent=2, sort_keys=True), encoding="utf-8")


def _diagnostic_lines() -> list[str]:
    diagnostics = _load_json(DEFAULT_DIAGNOSTICS_PATH)
    if not isinstance(diagnostics, list):
        return []
    lines: list[str] = []
    for item in diagnostics[-20:]:
        if not isinstance(item, dict):
            continue
        lines.append(
            "- "
            f"`{item.get('instrument', '')}` {item.get('route', '')} {item.get('side', '')}: "
            f"{item.get('decision', '')} zone {item.get('entry_low', '')}-{item.get('entry_high', '')}"
        )
    return lines


def _candidate_diagnostic(candidate: SignalCandidate, decision: str) -> dict[str, object]:
    return {
        "route": candidate.route,
        "instrument": candidate.instrument,
        "side": candidate.side,
        "decision": decision,
        "signal_time": candidate.signal_time,
        "entry_low": candidate.entry_low,
        "entry_high": candidate.entry_high,
        "available_r": candidate.available_r,
        "notes": candidate.notes,
    }


def _conflicts_with_active_test(candidate: SignalCandidate, tests: dict[str, object]) -> bool:
    for value in tests.values():
        if not isinstance(value, dict):
            continue
        if value.get("status") == "closed":
            continue
        if value.get("instrument") != candidate.instrument:
            continue
        side = str(value.get("side", "")).upper()
        if side and side != candidate.side.upper():
            return True
    return False


def _duplicates_active_test(candidate: SignalCandidate, tests: dict[str, object]) -> bool:
    if candidate.route not in SCORE_DUPLICATE_ROUTES:
        return False
    candidate_entry = _entry_price(candidate)
    tolerance = _pip_size(candidate.instrument) * DYNAMIC_SCORE_DUPLICATE_TOLERANCE_PIPS
    for value in tests.values():
        if not isinstance(value, dict):
            continue
        if value.get("status") == "closed":
            continue
        if value.get("route") != candidate.route:
            continue
        if value.get("instrument") != candidate.instrument:
            continue
        if str(value.get("side", "")).upper() != candidate.side.upper():
            continue
        existing_entry = _float_or_none(value.get("entry_price"))
        if existing_entry is not None and abs(candidate_entry - existing_entry) <= tolerance:
            return True
    return False


def _summary_lines(tests: dict[str, object]) -> list[str]:
    values = [value for value in tests.values() if isinstance(value, dict)]
    new_values = [value for value in values if value.get("model") == "partial_trail"]
    legacy_values = [value for value in values if value.get("model") != "partial_trail"]
    deduped_new = list(_unique_tests(new_values).values())

    lines = [
        "- Exit model: bank 50% at 1.5R, runner trails 1R behind peak (uncapped), breakeven after the partial.",
        "- Metric: realized R per closed trade. Win = realized R above 0; expectancy = average realized R.",
    ]
    lines.extend(_realized_summary_block("All new-model (deduped)", deduped_new))
    lines.append("")
    lines.append("### Per-route (new model, deduped)")
    lines.extend(_route_realized_lines(deduped_new))

    if legacy_values:
        legacy_counts = _target_counts(legacy_values)
        legacy_losses = legacy_counts["sl_hit"] + legacy_counts["sl_hit_ambiguous"]
        lines.append("")
        lines.append(
            f"### Legacy (old 3R model): {len(legacy_values)} tests, not comparable to new realized-R stats."
        )
        lines.append(
            f"- Legacy 3R: `{legacy_counts['tp_hit']}` TP, `{legacy_losses}` SL, "
            f"`{legacy_counts['breakeven']}` BE, win rate `{_closed_rate(legacy_counts)}`."
        )
    return lines


def _realized_stats(tests: list[dict[str, object]]) -> dict[str, object]:
    closed = [
        test
        for test in tests
        if test.get("status") == "closed" and _float_or_none(test.get("realized_r")) is not None
    ]
    realized = [float(test["realized_r"]) for test in closed]
    count = len(realized)
    wins = sum(1 for value in realized if value > 0)
    losses = sum(1 for value in realized if value < 0)
    scratches = sum(1 for value in realized if value == 0)
    total = sum(realized)
    return {
        "count": count,
        "wins": wins,
        "losses": losses,
        "scratches": scratches,
        "total": total,
        "avg": total / count if count else 0.0,
        "best": max(realized) if realized else 0.0,
        "win_rate": (wins / count * 100) if count else None,
    }


def _realized_summary_block(label: str, tests: list[dict[str, object]]) -> list[str]:
    stats = _realized_stats(tests)
    open_count = sum(1 for test in tests if test.get("status") != "closed")
    win_rate = "n/a" if stats["win_rate"] is None else f"{stats['win_rate']:.1f}%"
    return [
        f"- {label}: `{stats['count']}` closed (`{open_count}` open). "
        f"Win rate `{win_rate}`, expectancy `{stats['avg']:+.2f}R`, "
        f"total `{stats['total']:+.2f}R`, best `{stats['best']:+.2f}R`."
    ]


def _route_realized_lines(tests: list[dict[str, object]]) -> list[str]:
    lines: list[str] = []
    routes = sorted({str(test.get("route", "")) for test in tests if test.get("route")})
    for route in routes:
        route_tests = [test for test in tests if test.get("route") == route]
        stats = _realized_stats(route_tests)
        if stats["count"] == 0:
            continue
        lines.append(
            f"- `{route}`: `{stats['count']}` closed, win `{stats['win_rate']:.1f}%`, "
            f"expectancy `{stats['avg']:+.2f}R`, total `{stats['total']:+.2f}R`, best `{stats['best']:+.2f}R`."
        )
    return lines or ["- No closed new-model trades yet."]


def _unique_tests(tests: list[dict[str, object]]) -> dict[tuple[object, ...], dict[str, object]]:
    unique: dict[tuple[object, ...], dict[str, object]] = {}

    for value in tests:
        key = (
            value.get("route"),
            value.get("instrument"),
            value.get("side"),
            round(float(value.get("entry_price", 0.0)), 5),
            round(float(value.get("stop_loss", 0.0)), 5),
            value.get("entry_time") or "no_entry",
        )
        unique.setdefault(key, value)
    return unique


def _target_counts(tests: list[dict[str, object]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for value in tests:
        for target in (value.get("targets") or {}).values():
            if isinstance(target, dict):
                rr = target.get("rr")
                if rr == 3 or rr == 3.0:
                    counts[str(target.get("status", ""))] += 1
    return counts


def _closed_rate(counts: Counter[str]) -> str:
    wins = counts["tp_hit"]
    total = counts["tp_hit"] + counts["sl_hit"] + counts["sl_hit_ambiguous"]
    if total <= 0:
        return "n/a"
    return f"{wins / total * 100:.1f}%"


def _route_summary_lines(tests: list[dict[str, object]]) -> list[str]:
    lines: list[str] = []
    routes = sorted({str(test.get("route", "")) for test in tests if test.get("route")})
    for route in routes:
        counts = _target_counts([test for test in tests if test.get("route") == route])
        wins = counts["tp_hit"]
        losses = counts["sl_hit"] + counts["sl_hit_ambiguous"]
        if wins or losses:
            lines.append(f"- `{route}` 3R: `{wins}` TP, `{losses}` SL, win rate `{_closed_rate(counts)}`.")
    return lines


def _route_tier(route: str) -> tuple[int, str]:
    """Map a route name (incl. variants like *_OPPORTUNITY) to its alert tier (rank, label)."""
    r = str(route or "").upper()
    if r.startswith("HTF_MOMENTUM"):  # includes the HTF_MOMENTUM_M5 entry variant
        return (1, "HTF-MOMENTUM")
    if r.startswith("HTF_ZONE"):
        return (2, "HTF-ZONE")
    if r.startswith("MOMENTUM"):
        return (3, "PREMIUM")
    if r.startswith("M15"):
        return (4, "HIGH")
    if r.startswith("DYNAMIC"):
        return (5, "MEDIUM")
    if r.startswith("REGIME") or r.startswith("RULE") or r == "RULE":
        return (6, "LOW")
    # AI routes (DeepSeek/Gemma), consensus overrides, split-opportunity variants, anything else.
    return (7, "WATCH")


def _tier_legend_lines() -> list[str]:
    lines = ["### Alert hierarchy (priority order)"]
    for rank, label, desc in TIER_ORDER:
        lines.append(f"- **Tier {rank} - {label}**: {desc}")
    lines.append("- Every alert below carries both an M15 entry plan and a tighter M5 entry plan so you can choose.")
    return lines


def _test_lines(items: list[tuple[str, dict[str, object]]]) -> list[str]:
    lines: list[str] = []
    for _key, test in items:
        if test.get("model") == "partial_trail":
            lines.append(_partial_trail_line(test))
        else:
            lines.append(_legacy_test_line(test))
        if test.get("route") in {"DYNAMIC_SCORE", "REGIME_RANGE"} and test.get("notes"):
            lines.append(f"  - {test.get('notes', '')}")
    return lines


def _alert_lines(items: list[tuple[str, dict[str, object]]]) -> list[str]:
    """Render open signals grouped by tier (premium first) — the OpenClaw alert feed."""
    by_tier: dict[int, list[tuple[str, dict[str, object]]]] = {}
    for key, test in items:
        rank, _label = _route_tier(test.get("route", ""))
        by_tier.setdefault(rank, []).append((key, test))
    lines: list[str] = []
    emitted = False
    for rank, label, desc in TIER_ORDER:
        group = by_tier.get(rank)
        if not group:
            continue
        emitted = True
        lines.append(f"### Tier {rank} - {label} ({len(group)})")
        lines.append(f"_{desc}_")
        lines.append("")
        for _key, test in sorted(group, key=lambda kv: str(kv[1].get("instrument", ""))):
            if test.get("model") == "partial_trail":
                lines.append(_partial_trail_line(test))
            else:
                lines.append(_legacy_test_line(test))
            if test.get("route") in {"DYNAMIC_SCORE", "REGIME_RANGE"} and test.get("notes"):
                lines.append(f"  - {test.get('notes', '')}")
        lines.append("")
    if not emitted:
        return ["No open forward tests."]
    return lines


def _partial_trail_line(test: dict[str, object]) -> str:
    _rank, _label = _route_tier(test.get("route", ""))
    parts = [
        f"[T{_rank} {_label}]",
        f"`{test.get('instrument', '')}` {test.get('route', '')} {test.get('side', '')} {test.get('status', '')}",
        f"entry {test.get('entry_price', '')}",
        f"SL {test.get('stop_loss', '')}",
    ]
    if test.get("partial_taken"):
        parts.append("partial@1.5R booked")
    else:
        parts.append("partial pending")
    realized = _float_or_none(test.get("realized_r"))
    if realized is not None:
        parts.append(f"realized {realized:+.2f}R ({test.get('outcome', '')})")
    parts.append(str(test.get("pair_value_label", "")))
    line = "- " + " | ".join(parts)
    plan = _plan_sublines(test)
    if plan:
        line = line + "\n" + "\n".join(plan)
    return line


def _plan_sublines(test: dict[str, object]) -> list[str]:
    """Human-facing dual-timeframe trade plan: both M15 and M5, with TP and trailing."""
    target = _float_or_none(test.get("trade_target_price"))
    tf = test.get("trade_target_timeframe", "")
    m15_rr = test.get("m15_rr_to_target")
    m5_sl = test.get("m5_stop_loss")
    m5_rr = test.get("m5_rr_to_target")
    lines: list[str] = []
    tp_text = f"{target} ({tf})" if target is not None else "trail / open-ended"
    lines.append(
        f"  - M15 plan: entry `{test.get('entry_price', '')}` | SL `{test.get('stop_loss', '')}`"
        f" | TP `{tp_text}`" + (f" | ~{m15_rr}R" if m15_rr is not None else "")
    )
    if m5_sl is not None:
        lines.append(
            f"  - M5 plan: entry `{test.get('entry_price', '')}` | tighter SL `{m5_sl}`"
            + (f" | ~{m5_rr}R to same TP" if m5_rr is not None else "")
            + " (more R, smaller stop)"
        )
    trail = test.get("trail_levels")
    if isinstance(trail, dict):
        lines.append(
            f"  - Trailing TP: partial @ `{trail.get('partial_1_5R', '')}` (1.5R),"
            f" 3R @ `{trail.get('milestone_3R', '')}`, then trail. {trail.get('note', '')}"
        )
    return lines


def _legacy_test_line(test: dict[str, object]) -> str:
    targets = test.get("targets", {})
    target_text = ""
    if isinstance(targets, dict):
        target_text = ", ".join(
            f"{rr}: {target.get('status', '')}"
            for rr, target in sorted(targets.items())
            if isinstance(target, dict)
        )
    rank, label = _route_tier(test.get("route", ""))
    return (
        "- "
        f"[T{rank} {label}] "
        f"`{test.get('instrument', '')}` {test.get('route', '')} {test.get('side', '')} "
        f"{test.get('status', '')} | entry {test.get('entry_price', '')} "
        f"SL {test.get('stop_loss', '')} | {target_text} | {test.get('pair_value_label', '')}"
    )


def _load_signal_candidates() -> list[SignalCandidate]:
    candidates: list[SignalCandidate] = []
    now = datetime.now(timezone.utc)
    memories = {
        route: _load_json(path)
        for route, path in DEFAULT_MEMORY_PATHS.items()
    }
    rule_memory = memories.get("Rule", {})
    for route in DEFAULT_MEMORY_PATHS:
        memory = memories.get(route, {})
        if not isinstance(memory, dict):
            continue
        for instrument, record in memory.items():
            if not isinstance(record, dict):
                continue
            rule_record = rule_memory.get(instrument) if isinstance(rule_memory, dict) else None
            candidate = _candidate_from_record(
                route,
                str(instrument),
                record,
                rule_record if isinstance(rule_record, dict) else None,
            )
            if candidate is not None:
                candidates.append(candidate)
    candidates.extend(_ai_consensus_override_candidates(memories, rule_memory if isinstance(rule_memory, dict) else {}, now))
    candidates.extend(_ai_split_opportunity_candidates(memories, rule_memory if isinstance(rule_memory, dict) else {}))
    return candidates


def _ai_split_opportunity_candidates(
    memories: dict[str, object],
    rule_memory: dict[str, object],
) -> list[SignalCandidate]:
    candidates: list[SignalCandidate] = []
    for source in ("Gemma", "DeepSeek"):
        memory = memories.get(source, {})
        if not isinstance(memory, dict):
            continue
        for instrument, record in memory.items():
            if not isinstance(record, dict):
                continue
            routes = record.get("routes")
            if not isinstance(routes, dict):
                continue
            for route_key, route_record in routes.items():
                if not isinstance(route_key, str) or not isinstance(route_record, dict):
                    continue
                candidate = _ai_split_route_candidate(
                    source=source,
                    route_key=route_key,
                    instrument=str(instrument),
                    record=route_record,
                    rule_record=rule_memory.get(instrument) if isinstance(rule_memory.get(instrument), dict) else None,
                )
                if candidate is not None:
                    candidates.append(candidate)
    return candidates


def _ai_split_route_candidate(
    source: str,
    route_key: str,
    instrument: str,
    record: dict[str, object],
    rule_record: dict[str, object] | None,
) -> SignalCandidate | None:
    if (source, route_key) in FROZEN_AI_SPLIT_ROUTES:
        return None
    route_suffix = AI_SPLIT_ROUTE_KEYS.get(route_key)
    if route_suffix is None:
        return None
    if not _ai_record_has_usable_setup(record):
        return None
    confidence = _float_or_none(record.get("confidence")) or 0.0
    if confidence < AI_HIGH_VALUE_MIN_CONFIDENCE:
        return None
    side = str(record.get("side", "")).upper()
    if route_key == "smc_rag" and rule_record is not None:
        rule_bias = str(rule_record.get("bias", "")).lower()
        if rule_bias == "bullish" and side != "BUY":
            return None
        if rule_bias == "bearish" and side != "SELL":
            return None
    low = _float_or_none(record.get("entry_zone_low"))
    high = _float_or_none(record.get("entry_zone_high"))
    if side not in {"BUY", "SELL"} or low is None or high is None:
        return None
    pair_value = pair_value_for_instrument(instrument)
    route_name = f"{source.upper()}_{route_suffix}_OPPORTUNITY"
    route_label = str(record.get("route_label") or route_suffix.replace("_", " "))
    return SignalCandidate(
        route=route_name,
        instrument=instrument,
        side=side,
        status=str(record.get("status") or route_name),
        entry_low=min(low, high),
        entry_high=max(low, high),
        source=f"{source} {route_label} opportunity zone",
        signal_time=str(record.get("updated_at") or ""),
        sweep_price=_float_or_none((rule_record or {}).get("sweep_price")),
        bos_time=str((rule_record or {}).get("bos_time") or record.get("updated_at") or ""),
        notes=(
            f"{source} {route_label} opportunity: {side} {record.get('status', '')} "
            f"with {confidence:.0f}% confidence. Pair value: {pair_value.label}. "
            f"AI route key={route_key}; tracked separately from strict Rule and legacy AI routes."
        ),
        htf_range_low=_float_or_none((rule_record or {}).get("htf_narrative", {}).get("range_low"))
        if isinstance((rule_record or {}).get("htf_narrative"), dict)
        else None,
        htf_range_high=_float_or_none((rule_record or {}).get("htf_narrative", {}).get("range_high"))
        if isinstance((rule_record or {}).get("htf_narrative"), dict)
        else None,
        target_price=None,
        target_timeframe="fixed",
        target_reason=f"Fixed 3R forward-test target for {source} {route_label} AI opportunity.",
        available_r=3.0,
    )


def _m15_simple_candidates(client: OandaClient, signal_time: str) -> list[SignalCandidate]:
    candidates: list[SignalCandidate] = []
    for instrument in DEFAULT_WATCHLIST:
        candles = _fetch_m15(client, instrument)
        if len(candles) < 80:
            continue
        bias = detect_bias(candles)
        if bias.direction not in {"bullish", "bearish"}:
            continue
        setups, _swings, _sweeps = find_recent_setups(candles, bias, limit=5)
        for setup in _m15_directional_setups(setups, bias):
            candidate = _m15_simple_candidate(instrument, setup, candles, signal_time)
            if candidate is not None:
                candidates.append(candidate)
                break
    return candidates


def _m15_directional_setups(
    setups: list[SetupCandidate],
    bias: object,
) -> list[SetupCandidate]:
    direction = getattr(bias, "direction", "")
    if direction == "bullish":
        return [setup for setup in setups if setup.side == "buy"]
    if direction == "bearish":
        return [setup for setup in setups if setup.side == "sell"]
    return []


def _dynamic_score_candidates(client: OandaClient, signal_time: str) -> list[SignalCandidate]:
    candidates: list[SignalCandidate] = []
    for instrument in DEFAULT_WATCHLIST:
        candles = _fetch_m15(client, instrument)
        signal = best_dynamic_score(candles)
        if signal is None or signal.score < DYNAMIC_SCORE_MINIMUM:
            continue
        candidates.append(_dynamic_score_candidate(instrument, signal, signal_time))
    return candidates


def _regime_range_candidates(client: OandaClient, signal_time: str) -> list[SignalCandidate]:
    candidates: list[SignalCandidate] = []
    session = _session_for_signal_time(signal_time)
    minimum = REGIME_RANGE_MINIMUM
    if session == "london_new_york_overlap":
        minimum += REGIME_RANGE_OVERLAP_PENALTY
    for instrument in DEFAULT_WATCHLIST:
        candles = _fetch_m15(client, instrument)
        signal = best_regime_range_signal(candles)
        if signal is None or signal.score < minimum:
            continue
        candidates.append(_regime_range_candidate(instrument, signal, signal_time, session))
    return candidates


def _momentum_candidates(client: OandaClient, signal_time: str) -> list[SignalCandidate]:
    """MOMENTUM route: enter on a shallow continuation pullback after a fresh M15 impulse.

    Self-contained port of the validated momentum_entry backtest (3.5xATR gate). Kept inline
    here (not imported from momentum_entry) to avoid a circular import and stay delete-safe.
    """
    if not MOMENTUM_ENABLED:
        return []
    candidates: list[SignalCandidate] = []
    for instrument in DEFAULT_WATCHLIST:
        candles = _fetch_m15(client, instrument)
        candidate = _momentum_signal_live(candles, instrument, signal_time)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _momentum_signal_live(
    window: list[Candle],
    instrument: str,
    signal_time: str,
) -> SignalCandidate | None:
    if len(window) < max(MOMENTUM_BIAS_LOOKBACK, MOMENTUM_IMPULSE_LOOKBACK + 2, 40):
        return None
    atr = average_range(window[-30:], period=30)
    if atr <= 0:
        return None
    leg = window[-MOMENTUM_IMPULSE_LOOKBACK:]
    highs = [c.high for c in leg]
    lows = [c.low for c in leg]
    imp_high = max(highs)
    imp_low = min(lows)
    hi_idx = highs.index(imp_high)
    lo_idx = lows.index(imp_low)
    rng = imp_high - imp_low
    if rng < atr * MOMENTUM_IMPULSE_ATR_MULT:
        return None
    bias = detect_bias(window[-MOMENTUM_BIAS_LOOKBACK:]).direction
    n = len(leg)
    latest_close = window[-1].close
    if bias == "bullish" and lo_idx < hi_idx and (n - 1 - hi_idx) <= MOMENTUM_RECENT_EXTREME_WITHIN:
        entry_high = imp_high - MOMENTUM_SHALLOW_RETRACE * rng
        entry_low = imp_high - MOMENTUM_DEEP_RETRACE * rng
        if entry_low < latest_close:
            return _momentum_candidate(instrument, "BUY", entry_low, entry_high, imp_low, signal_time, rng, atr)
    if bias == "bearish" and hi_idx < lo_idx and (n - 1 - lo_idx) <= MOMENTUM_RECENT_EXTREME_WITHIN:
        entry_low = imp_low + MOMENTUM_SHALLOW_RETRACE * rng
        entry_high = imp_low + MOMENTUM_DEEP_RETRACE * rng
        if entry_high > latest_close:
            return _momentum_candidate(instrument, "SELL", entry_low, entry_high, imp_high, signal_time, rng, atr)
    return None


def _momentum_candidate(
    instrument: str,
    side: str,
    entry_low: float,
    entry_high: float,
    sweep_price: float,
    signal_time: str,
    rng: float,
    atr: float,
) -> SignalCandidate:
    strength = rng / atr if atr > 0 else 0.0
    return SignalCandidate(
        route="MOMENTUM",
        instrument=instrument,
        side=side,
        status=f"impulse:{strength:.1f}xATR",
        entry_low=round(min(entry_low, entry_high), 5),
        entry_high=round(max(entry_low, entry_high), 5),
        source="M15 impulse continuation",
        signal_time=signal_time,
        sweep_price=round(sweep_price, 5),
        bos_time=signal_time,
        notes=(
            f"MOMENTUM: {side} continuation entry on shallow pullback after a {strength:.1f}xATR "
            f"M15 impulse (3.5xATR gate). Pair value: {pair_value_for_instrument(instrument).label}."
        ),
        target_price=None,
        target_timeframe="fixed",
        target_reason="Trailing runner (scale-and-trail), no fixed cap.",
        available_r=3.0,
    )


def _htf_momentum_candidates(client: OandaClient, signal_time: str) -> list[SignalCandidate]:
    """HTF_MOMENTUM route: detect an impulse move on H1, enter the tight M15 zone for max R.

    Day-trade angle: the move is found on the 1-hour chart (hours of room), but the entry zone
    handed to M15 is narrow so the M15 stop is small and the runner can ride the HTF
    continuation. Detection lives in htf_momentum.py (delete-safe, no circular import).
    """
    if not HTF_MOMENTUM_ENABLED:
        return []
    candidates: list[SignalCandidate] = []
    for instrument in DEFAULT_WATCHLIST:
        h1 = _fetch_h1(client, instrument)
        sig = htf_momentum_signal(h1, HTF_MOMENTUM_PARAMS)
        if sig is None:
            continue
        pair_value = pair_value_for_instrument(instrument)
        # Profile A: M15-structure stop = just past the entry-zone edge (room to breathe via the
        # _stop_loss buffer), NOT the far H1 impulse low. Encode it by pointing sweep_price at the
        # zone edge so _stop_loss hugs the band instead of the impulse origin.
        stop_ref = sig.entry_low if sig.side == "BUY" else sig.entry_high
        entry_px = sig.entry_high if sig.side == "BUY" else sig.entry_low
        risk = abs(entry_px - stop_ref)
        planned_r = round(abs(sig.target_price - entry_px) / risk, 2) if risk > 0 else None
        candidates.append(
            SignalCandidate(
                route="HTF_MOMENTUM",
                instrument=instrument,
                side=sig.side,
                status=f"htf_impulse:{sig.strength:.1f}xATR",
                entry_low=sig.entry_low,
                entry_high=sig.entry_high,
                source="H1 impulse continuation -> M15 entry (ride to H1 target)",
                signal_time=signal_time,
                sweep_price=round(stop_ref, 5),
                bos_time=signal_time,
                notes=(
                    f"HTF_MOMENTUM: {sig.side} day-trade. {sig.note}. Enter on M15, M15-structure stop, "
                    f"ride to the H1 impulse target {sig.target_price:g}. Pair value: {pair_value.label}."
                ),
                target_price=sig.target_price,
                target_timeframe="H1",
                target_reason="H1 impulse high (measured target) - ride 100% to it (Profile A).",
                available_r=planned_r,
                entry_timeframe="M15",
            )
        )
    return candidates


def _htf_zone_candidates(client: OandaClient, signal_time: str) -> list[SignalCandidate]:
    """HTF_ZONE route (delete-safe): H4 bias + H1 SMC zone -> M15 reaction, trailing exit.

    Wired exactly as it backtested (zone-edge stop, partial-then-trail). High per-trade quality
    but low frequency, so it surfaces as a HIGH-tier alert, below the proven PREMIUM momentum.
    """
    if not (HTF_ZONE_ENABLED and HTF_ZONE_AVAILABLE):
        return []
    candidates: list[SignalCandidate] = []
    for instrument in DEFAULT_WATCHLIST:
        h4 = _fetch_h4(client, instrument)
        h1 = _fetch_h1(client, instrument)
        sig = htf_zone_signal(h4, h1, HTF_ZONE_PARAMS)
        if sig is None:
            continue
        pair_value = pair_value_for_instrument(instrument)
        stop_ref = sig.entry_low if sig.side == "BUY" else sig.entry_high
        candidates.append(
            SignalCandidate(
                route="HTF_ZONE",
                instrument=instrument,
                side=sig.side,
                status=f"smc_zone:Q{sig.strength:.0f}",
                entry_low=sig.entry_low,
                entry_high=sig.entry_high,
                source="H4 bias + H1 SMC zone -> M15 reaction",
                signal_time=signal_time,
                sweep_price=round(stop_ref, 5),
                bos_time=signal_time,
                notes=(
                    f"HTF_ZONE: {sig.side} SMC day-trade. {sig.note}. Enter the M15 reaction at the H1 zone, "
                    f"zone-edge stop, bank ~50% at 1.5R then trail. Pair value: {pair_value.label}."
                ),
                target_price=None,
                target_timeframe="fixed",
                target_reason="Trailing runner (partial then trail), no fixed cap.",
                available_r=3.0,
                entry_timeframe="M15",
            )
        )
    return candidates


def _regime_range_candidate(
    instrument: str,
    signal: DynamicScoreSignal,
    signal_time: str,
    session: str,
) -> SignalCandidate:
    factor_text = "; ".join(
        f"{factor.name}=+{factor.points:g} ({factor.note})"
        for factor in signal.factors
        if factor.points > 0
    )
    pair_value = pair_value_for_instrument(instrument)
    return SignalCandidate(
        route="REGIME_RANGE",
        instrument=instrument,
        side=signal.side,
        status=f"ranging:{signal.score:.1f}",
        entry_low=signal.entry_low,
        entry_high=signal.entry_high,
        source="regime range reversal",
        signal_time=signal_time,
        sweep_price=signal.stop_reference,
        bos_time=signal_time,
        notes=(
            f"REGIME_RANGE: {signal.side} range fade score {signal.score:.1f}/10 in ranging regime "
            f"({session}). Pair value: {pair_value.label}. Factors: {factor_text}"
        ),
        target_price=None,
        target_timeframe="fixed",
        target_reason="Fixed 3R forward-test target for regime-gated range reversal.",
        available_r=3.0,
    )


def _session_for_signal_time(signal_time: str) -> str:
    try:
        return market_session(datetime.fromisoformat(signal_time))
    except ValueError:
        return market_session(datetime.now(timezone.utc))


def _dynamic_score_candidate(
    instrument: str,
    signal: DynamicScoreSignal,
    signal_time: str,
) -> SignalCandidate:
    factor_text = "; ".join(
        f"{factor.name}=+{factor.points:g} ({factor.note})"
        for factor in signal.factors
        if factor.points > 0
    )
    pair_value = pair_value_for_instrument(instrument)
    return SignalCandidate(
        route="DYNAMIC_SCORE",
        instrument=instrument,
        side=signal.side,
        status=f"{signal.strategy}:{signal.score:.1f}",
        entry_low=signal.entry_low,
        entry_high=signal.entry_high,
        source=f"dynamic score {signal.strategy}",
        signal_time=signal_time,
        sweep_price=signal.stop_reference,
        bos_time=signal_time,
        notes=(
            f"DYNAMIC_SCORE {signal.strategy}: {signal.side} score {signal.score:.1f}/10. "
            f"Pair value: {pair_value.label}. Factors: {factor_text}"
        ),
        target_price=None,
        target_timeframe="fixed",
        target_reason=f"Fixed 3R forward-test target for dynamic score strategy {signal.strategy}.",
        available_r=3.0,
    )


def _m15_simple_candidate(
    instrument: str,
    setup: SetupCandidate,
    candles: list[Candle],
    signal_time: str,
) -> SignalCandidate | None:
    if setup.status != "candidate":
        return None
    if setup.current_state == "expired_after_bos":
        return None
    if setup.entry_zone.source == "50-70 percent impulse retracement":
        return None
    if setup.quality_score < _m15_min_quality(instrument):
        return None
    if not _m15_simple_range_location_ok(setup, candles):
        return None
    if not _m15_simple_path_quality_ok(setup, candles):
        return None
    side = setup.side.upper()
    if side not in {"BUY", "SELL"}:
        return None
    bos_time = candles[setup.bos.index].time.isoformat() if 0 <= setup.bos.index < len(candles) else signal_time
    pair_value = pair_value_for_instrument(instrument)
    return SignalCandidate(
        route="M15_SIMPLE",
        instrument=instrument,
        side=side,
        status=f"M15_SIMPLE_{setup.current_state}",
        entry_low=setup.entry_zone.low,
        entry_high=setup.entry_zone.high,
        source=f"M15 simple {setup.entry_zone.source}",
        signal_time=signal_time,
        sweep_price=setup.sweep.swept_price,
        bos_time=bos_time,
        notes=(
            "M15_SIMPLE route: M15 bias, premium/discount, liquidity sweep, BOS, "
            f"and compact base zone. quality={setup.quality_score}; state={setup.current_state}; "
            f"pair_value={pair_value.label}."
        ),
        target_price=None,
        target_timeframe="fixed",
        target_reason="Fixed 3R forward-test target for M15 simplified opportunity.",
        available_r=3.0,
    )


def _m15_min_quality(instrument: str) -> int:
    pair_value = pair_value_for_instrument(instrument)
    return 2 if pair_value.tier == "high_value" else 3


def _m15_simple_range_location_ok(setup: SetupCandidate, candles: list[Candle]) -> bool:
    sample = candles[-96:] if len(candles) > 96 else candles
    if not sample:
        return False
    range_high = max(candle.high for candle in sample)
    range_low = min(candle.low for candle in sample)
    active_range = range_high - range_low
    if active_range <= 0:
        return False
    zone_mid = (setup.entry_zone.low + setup.entry_zone.high) / 2
    position = (zone_mid - range_low) / active_range
    if setup.side == "buy":
        return position <= 0.55
    return position >= 0.45


def _m15_simple_path_quality_ok(setup: SetupCandidate, candles: list[Candle]) -> bool:
    if setup.bos.index <= setup.sweep.index:
        return False
    impulse = candles[setup.sweep.index : setup.bos.index + 1]
    if not impulse:
        return False
    avg_range = average_range(candles[max(0, setup.sweep.index - 30) : setup.bos.index + 1], period=30)
    if avg_range <= 0:
        return False
    impulse_range = max(candle.high for candle in impulse) - min(candle.low for candle in impulse)
    zone_width = setup.entry_zone.high - setup.entry_zone.low
    if zone_width > avg_range * 1.8:
        return False
    return impulse_range >= avg_range * 1.1


def _ai_consensus_override_candidates(
    memories: dict[str, object],
    rule_memory: dict[str, object],
    now: datetime,
) -> list[SignalCandidate]:
    deepseek = memories.get("DeepSeek", {})
    gemma = memories.get("Gemma", {})
    if not isinstance(deepseek, dict) or not isinstance(gemma, dict):
        return []

    candidates: list[SignalCandidate] = []
    for instrument in sorted(set(deepseek) & set(gemma)):
        deepseek_record = deepseek.get(instrument)
        gemma_record = gemma.get(instrument)
        if not isinstance(deepseek_record, dict) or not isinstance(gemma_record, dict):
            continue
        candidate = _ai_consensus_candidate(
            str(instrument),
            deepseek_record,
            gemma_record,
            rule_memory.get(instrument) if isinstance(rule_memory.get(instrument), dict) else None,
            now,
        )
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _ai_consensus_candidate(
    instrument: str,
    deepseek_record: dict[str, object],
    gemma_record: dict[str, object],
    rule_record: dict[str, object] | None,
    now: datetime,
) -> SignalCandidate | None:
    deepseek_side = str(deepseek_record.get("side", "")).upper()
    gemma_side = str(gemma_record.get("side", "")).upper()
    if deepseek_side not in {"BUY", "SELL"} or deepseek_side != gemma_side:
        return None
    if not _ai_record_has_usable_setup(deepseek_record) or not _ai_record_has_usable_setup(gemma_record):
        return None

    zone = _consensus_zone(instrument, deepseek_record, gemma_record)
    if zone is None:
        return None
    low, high = zone
    timing = _consensus_timing(
        str(deepseek_record.get("updated_at") or ""),
        str(gemma_record.get("updated_at") or ""),
        now,
    )
    if timing is None:
        return None
    signal_time, deepseek_age, gemma_age, gap_minutes = timing

    rule_side = str((rule_record or {}).get("primary_side", "")).upper()
    rule_bias = str((rule_record or {}).get("bias", "")).lower()
    if rule_bias == "bullish" and deepseek_side != "BUY":
        return None
    if rule_bias == "bearish" and deepseek_side != "SELL":
        return None

    pair_value = pair_value_for_instrument(instrument)
    notes = (
        "AI consensus override: DeepSeek and Gemma agree on "
        f"{deepseek_side}. Rule route remains strict and this result is tracked separately. "
        f"Pair value: {pair_value.label}. "
        f"Consensus window: DeepSeek age={deepseek_age:.0f}m, Gemma age={gemma_age:.0f}m, gap={gap_minutes:.0f}m. "
        f"DeepSeek status={deepseek_record.get('status', '')} confidence={deepseek_record.get('confidence', '')}; "
        f"Gemma status={gemma_record.get('status', '')} confidence={gemma_record.get('confidence', '')}."
    )
    return SignalCandidate(
        route="AI_CONSENSUS_OVERRIDE",
        instrument=instrument,
        side=deepseek_side,
        status="AI_CONSENSUS_OVERRIDE",
        entry_low=low,
        entry_high=high,
        source="DeepSeek + Gemma consensus entry zone",
        signal_time=signal_time,
        sweep_price=_float_or_none((rule_record or {}).get("sweep_price")),
        bos_time=str((rule_record or {}).get("bos_time") or signal_time),
        notes=notes,
        htf_range_low=_float_or_none((rule_record or {}).get("htf_narrative", {}).get("range_low"))
        if isinstance((rule_record or {}).get("htf_narrative"), dict)
        else None,
        htf_range_high=_float_or_none((rule_record or {}).get("htf_narrative", {}).get("range_high"))
        if isinstance((rule_record or {}).get("htf_narrative"), dict)
        else None,
        target_price=None,
        target_timeframe="fixed",
        target_reason="Fixed 3R forward-test target for AI consensus override.",
        available_r=3.0,
    )


def _ai_record_has_usable_setup(record: dict[str, object]) -> bool:
    status = str(record.get("status", "")).upper()
    if status not in {"ENTRY_NOW", "FORMING", "WAIT"}:
        return False
    low = _float_or_none(record.get("entry_zone_low"))
    high = _float_or_none(record.get("entry_zone_high"))
    if low is None or high is None:
        return False
    confidence = _float_or_none(record.get("confidence"))
    return confidence is None or confidence >= 50


def _consensus_zone(
    instrument: str,
    deepseek_record: dict[str, object],
    gemma_record: dict[str, object],
) -> tuple[float, float] | None:
    deep_low = _float_or_none(deepseek_record.get("entry_zone_low"))
    deep_high = _float_or_none(deepseek_record.get("entry_zone_high"))
    gemma_low = _float_or_none(gemma_record.get("entry_zone_low"))
    gemma_high = _float_or_none(gemma_record.get("entry_zone_high"))
    if None in {deep_low, deep_high, gemma_low, gemma_high}:
        return None
    first_low, first_high = sorted((float(deep_low), float(deep_high)))
    second_low, second_high = sorted((float(gemma_low), float(gemma_high)))
    overlap_low = max(first_low, second_low)
    overlap_high = min(first_high, second_high)
    if overlap_low <= overlap_high:
        return overlap_low, overlap_high
    gap = max(first_low, second_low) - min(first_high, second_high)
    tolerance = _pip_size(instrument) * AI_CONSENSUS_ZONE_TOLERANCE_PIPS
    if 0 < gap <= tolerance:
        return min(first_low, second_low), max(first_high, second_high)
    return None


def _consensus_timing(
    first: str,
    second: str,
    now: datetime,
) -> tuple[str, float, float, float] | None:
    first_time = _parse_datetime(first)
    second_time = _parse_datetime(second)
    if first_time is None or second_time is None:
        return None
    first_age = (now - first_time).total_seconds() / 60
    second_age = (now - second_time).total_seconds() / 60
    if first_age < 0 or second_age < 0:
        return None
    if first_age > AI_CONSENSUS_MAX_AGE_MINUTES or second_age > AI_CONSENSUS_MAX_AGE_MINUTES:
        return None
    gap = abs((first_time - second_time).total_seconds()) / 60
    if gap > AI_CONSENSUS_MAX_GAP_MINUTES:
        return None
    latest = first if first_time >= second_time else second
    return latest, first_age, second_age, gap


def _candidate_from_record(
    route: str,
    instrument: str,
    record: dict[str, object],
    rule_record: dict[str, object] | None = None,
) -> SignalCandidate | None:
    status = str(record.get("status", ""))
    if route == "Rule":
        if not STRICT_RULE_ENGINE_ENABLED:
            return None
        if status != "entry_candidate_now":
            return None
        if record.get("htf_poi_sequence") not in {None, "", "valid"}:
            return None
        if str(record.get("current_state", "")) != "at_entry_zone_now":
            return None
        if int(record.get("quality_score") or 0) < MIN_FORWARD_TEST_QUALITY:
            return None
        route_name = route
        if not _fresh_bos(record):
            route_name = "RULE_STALE_BOS"
        side = str(record.get("primary_side", "")).upper()
        signal_time = str(record.get("updated_at") or "")
        source = str(record.get("entry_zone_source", ""))
        notes = str(record.get("action") or record.get("next_check_reason") or "")
        if route_name == "RULE_STALE_BOS":
            notes = f"{notes} Forward-tested separately because entry is active now but BOS is older than {MAX_BOS_AGE_HOURS}h."
    else:
        if isinstance(record.get("routes"), dict):
            return None
        route_name = route
        if status.upper() != "ENTRY_NOW":
            return None
        if not _ai_has_confirmed_entry(record):
            return None
        if rule_record is not None and not _rule_context_allows_ai_test(record, rule_record):
            return None
        side = str(record.get("side", "")).upper()
        signal_time = str(record.get("updated_at") or "")
        source = f"{route} AI entry zone"
        notes = str(record.get("chart_notes") or record.get("next_check_reason") or "")

    low = _float_or_none(record.get("entry_zone_low"))
    high = _float_or_none(record.get("entry_zone_high"))
    if side not in {"BUY", "SELL"} or low is None or high is None:
        return None
    if route == "Rule" and not _latest_price_inside_zone(record, min(low, high), max(low, high)):
        return None
    if route != "Rule" and rule_record is not None:
        rule_price = rule_record.get("latest_price")
        if not _price_inside_zone(rule_price, min(low, high), max(low, high)):
            return None

    narrative_record = record.get("htf_narrative") if route == "Rule" else None
    if route != "Rule" and rule_record is not None:
        narrative_record = rule_record.get("htf_narrative")
    htf_range_low, htf_range_high = _narrative_range(narrative_record)
    trade_target_record = record.get("trade_target") if route == "Rule" else None
    if route != "Rule" and rule_record is not None:
        trade_target_record = rule_record.get("trade_target")
    target_price, target_timeframe, target_reason, target_available_r = _trade_target_fields(trade_target_record)

    return SignalCandidate(
        route=route_name,
        instrument=instrument,
        side=side,
        status=status,
        entry_low=min(low, high),
        entry_high=max(low, high),
        source=source,
        signal_time=signal_time,
        sweep_price=_float_or_none(record.get("sweep_price")),
        bos_time=str(record.get("bos_time") or signal_time),
        notes=notes,
        htf_range_low=htf_range_low,
        htf_range_high=htf_range_high,
        target_price=target_price,
        target_timeframe=target_timeframe,
        target_reason=target_reason,
        available_r=target_available_r,
    )


def _ai_has_confirmed_entry(record: dict[str, object]) -> bool:
    text = " ".join(
        str(record.get(key, ""))
        for key in ("status", "next_action", "chart_notes", "alert")
    ).lower()
    if any(term in text for term in ("forming", "waiting", "wait for", "await")):
        return False
    return "entry_now" in text or "entry now" in text or "confirmed" in text


def _rule_context_allows_ai_test(
    ai_record: dict[str, object],
    rule_record: dict[str, object],
) -> bool:
    side = str(ai_record.get("side", "")).upper()
    bias = str(rule_record.get("bias", "")).lower()
    if bias == "bullish" and side != "BUY":
        return False
    if bias == "bearish" and side != "SELL":
        return False
    if rule_record.get("htf_poi_sequence") not in {None, "", "valid"}:
        return False
    if str(rule_record.get("status", "")) != "entry_candidate_now":
        return False
    if str(rule_record.get("current_state", "")) != "at_entry_zone_now":
        return False
    if int(rule_record.get("quality_score") or 0) < MIN_FORWARD_TEST_QUALITY:
        return False
    if not _fresh_bos(rule_record):
        return False
    if _float_or_none(rule_record.get("available_r")) is None or float(rule_record.get("available_r") or 0.0) < 3.0:
        return False
    return True


def _fresh_bos(record: dict[str, object]) -> bool:
    signal_time = _parse_datetime(str(record.get("updated_at") or ""))
    bos_time = _parse_datetime(str(record.get("bos_time") or ""))
    if signal_time is None or bos_time is None:
        return False
    age_seconds = (signal_time - bos_time).total_seconds()
    return 0 <= age_seconds <= MAX_BOS_AGE_HOURS * 60 * 60


def _latest_price_inside_zone(record: dict[str, object], low: float, high: float) -> bool:
    return _price_inside_zone(record.get("latest_price"), low, high)


def _price_inside_zone(value: object, low: float, high: float) -> bool:
    price = _float_or_none(value)
    if price is None:
        return False
    return low <= price <= high


def _passes_strict_live_entry_filters(
    candidate: SignalCandidate,
    candles: list[Candle],
    rr_values: tuple[float, ...],
) -> bool:
    minimum_rr = max(rr_values) if rr_values else 3.0
    return not _strict_live_entry_rejection(candidate, candles, rr_values)


def _strict_live_entry_rejection(
    candidate: SignalCandidate,
    candles: list[Candle],
    rr_values: tuple[float, ...],
) -> str:
    if _is_ai_split_opportunity_route(candidate.route):
        if not _price_near_zone(candidate, candles, AI_HIGH_VALUE_MAX_DISTANCE_RANGES):
            return "ai_split_opportunity_price_too_far"
        return ""
    if candidate.route == "HTF_MOMENTUM":
        # Profile A pullback-continuation: fills on touch, near H1 target (~1-2R), so it
        # skips the strict 3R / reaction-candle gate. Only require price to be within reach.
        if not _price_near_zone(candidate, candles, HTF_MOMENTUM_MAX_DISTANCE_RANGES):
            return "htf_momentum_price_too_far"
        return ""
    if candidate.route == "HTF_ZONE":
        if not _price_near_zone(candidate, candles, HTF_ZONE_MAX_DISTANCE_RANGES):
            return "htf_zone_price_too_far"
        return ""
    if candidate.route == "M15_SIMPLE":
        if not _price_near_zone(candidate, candles, M15_SIMPLE_MAX_DISTANCE_RANGES):
            return "m15_simple_price_too_far"
        return ""
    if candidate.route == "DYNAMIC_SCORE":
        if not _price_near_zone(candidate, candles, DYNAMIC_SCORE_MAX_DISTANCE_RANGES):
            return "dynamic_score_price_too_far"
        return ""
    if candidate.route == "REGIME_RANGE":
        if not _price_near_zone(candidate, candles, REGIME_RANGE_MAX_DISTANCE_RANGES):
            return "regime_range_price_too_far"
        return ""
    if candidate.route == "AI_CONSENSUS_OVERRIDE" and not _latest_candle_touches_zone(candidate, candles):
        return "consensus_price_not_at_zone"
    if not _entry_reaction_candle(candidate, candles):
        return "no_entry_reaction_candle"
    minimum_rr = max(rr_values) if rr_values else 3.0
    if candidate.available_r is None or candidate.available_r < minimum_rr:
        return f"available_r_below_{minimum_rr:g}"
    return ""


def _is_ai_split_opportunity_route(route: str) -> bool:
    return any(
        route.endswith(f"_{suffix}_OPPORTUNITY")
        for suffix in AI_SPLIT_ROUTE_KEYS.values()
    )


def _price_near_zone(candidate: SignalCandidate, candles: list[Candle], max_ranges: float) -> bool:
    if not candles:
        return False
    latest = candles[-1].close
    if candidate.entry_low <= latest <= candidate.entry_high:
        return True
    distance = min(abs(latest - candidate.entry_low), abs(latest - candidate.entry_high))
    avg_range = average_range(candles, period=30)
    if avg_range <= 0:
        return False
    return distance / avg_range <= max_ranges


def _latest_candle_touches_zone(candidate: SignalCandidate, candles: list[Candle]) -> bool:
    if not candles:
        return False
    latest = candles[-1]
    return latest.low <= candidate.entry_high and latest.high >= candidate.entry_low


def _opportunity_candidate(
    candidate: SignalCandidate,
    rejection: str,
    candles: list[Candle],
) -> SignalCandidate | None:
    if rejection != "no_entry_reaction_candle":
        return None
    if candidate.route in DISABLED_OPPORTUNITY_SOURCE_ROUTES:
        return None
    if candidate.route not in {"Rule", "RULE_STALE_BOS", "AI_CONSENSUS_OVERRIDE"}:
        return None
    if not _latest_candle_touches_zone(candidate, candles):
        return None
    return SignalCandidate(
        route=f"{candidate.route}_OPPORTUNITY",
        instrument=candidate.instrument,
        side=candidate.side,
        status=candidate.status,
        entry_low=candidate.entry_low,
        entry_high=candidate.entry_high,
        source=f"{candidate.source} opportunity route",
        signal_time=candidate.signal_time,
        sweep_price=candidate.sweep_price,
        bos_time=candidate.bos_time,
        notes=(
            f"{candidate.notes} Opportunity paper route opened because price is at the zone, "
            "but strict reaction confirmation has not appeared yet. Track separately."
        ),
        htf_range_low=candidate.htf_range_low,
        htf_range_high=candidate.htf_range_high,
        target_price=candidate.target_price,
        target_timeframe=candidate.target_timeframe,
        target_reason=candidate.target_reason,
        available_r=candidate.available_r,
        entry_timeframe=candidate.entry_timeframe,
    )


def _entry_reaction_candle(candidate: SignalCandidate, candles: list[Candle]) -> bool:
    if not candles:
        return False
    latest = candles[-1]
    zone_mid = (candidate.entry_low + candidate.entry_high) / 2
    if candidate.side == "BUY":
        return latest.close > latest.open and latest.close >= zone_mid
    if candidate.side == "SELL":
        return latest.close < latest.open and latest.close <= zone_mid
    return False


def _room_to_active_extreme_r(candidate: SignalCandidate, entry_price: float, risk: float) -> float | None:
    if risk <= 0:
        return None
    if candidate.side == "BUY" and candidate.htf_range_high is not None:
        return (candidate.htf_range_high - entry_price) / risk
    if candidate.side == "SELL" and candidate.htf_range_low is not None:
        return (entry_price - candidate.htf_range_low) / risk
    return None


def candidate_with_trade_target(
    candidate: SignalCandidate,
    target: TradeTarget | None,
    entry_price: float,
    risk: float,
) -> SignalCandidate:
    if target is None:
        return candidate
    return SignalCandidate(
        route=candidate.route,
        instrument=candidate.instrument,
        side=candidate.side,
        status=candidate.status,
        entry_low=candidate.entry_low,
        entry_high=candidate.entry_high,
        source=candidate.source,
        signal_time=candidate.signal_time,
        sweep_price=candidate.sweep_price,
        bos_time=candidate.bos_time,
        notes=candidate.notes,
        htf_range_low=candidate.htf_range_low,
        htf_range_high=candidate.htf_range_high,
        target_price=target.price,
        target_timeframe=target.timeframe,
        target_reason=target.reason,
        available_r=available_r(target, entry_price, risk),
        entry_timeframe=candidate.entry_timeframe,
    )


def _narrative_range(record: object) -> tuple[float | None, float | None]:
    if not isinstance(record, dict):
        return None, None
    return _float_or_none(record.get("range_low")), _float_or_none(record.get("range_high"))


def _trade_target_fields(record: object) -> tuple[float | None, str, str, float | None]:
    if not isinstance(record, dict):
        return None, "", "", None
    return (
        _float_or_none(record.get("price")),
        str(record.get("timeframe") or ""),
        str(record.get("reason") or ""),
        _float_or_none(record.get("available_r")),
    )


def _new_test(
    candidate: SignalCandidate,
    candles: list[Candle],
    rr_values: tuple[float, ...],
    created_at: str,
    m5_candles: list[Candle] | None = None,
) -> dict[str, object]:
    entry_price = _entry_price(candidate)
    stop_loss = _stop_loss(candidate, candles)
    risk = abs(entry_price - stop_loss)
    room_to_active_extreme_r = _room_to_active_extreme_r(candidate, entry_price, risk)
    pair_value = pair_value_for_instrument(candidate.instrument)
    partial_target_price = _target_price(entry_price, stop_loss, candidate.side, PARTIAL_TARGET_R)
    # --- dual-timeframe trade plan for the human trader ---
    target_price = candidate.target_price
    m15_rr_to_target = None
    if target_price is not None and risk > 0:
        m15_rr_to_target = round(
            (target_price - entry_price) / risk if candidate.side == "BUY" else (entry_price - target_price) / risk,
            2,
        )
    m5_plan = _m5_plan(candidate.side, entry_price, stop_loss, target_price, m5_candles or [])
    # Trailing-TP milestones: bank a partial, then trail the runner uncapped.
    trail_levels = {
        "partial_1_5R": round(partial_target_price, 5),
        "milestone_3R": round(_target_price(entry_price, stop_loss, candidate.side, 3.0), 5),
        "note": "Bank ~50% at 1.5R, move SL to breakeven, then trail 1R behind the peak (uncapped).",
    }
    return {
        "model": "partial_trail",
        # Profile A routes ride 100% to the fixed HTF target (no partial/trail); everything else
        # uses the standard bank-at-1.5R-then-trail model.
        "exit_model": "ride_target" if candidate.route == "HTF_MOMENTUM" else "partial_trail",
        "route": candidate.route,
        "instrument": candidate.instrument,
        "side": candidate.side,
        "status": "waiting_entry",
        "created_at": created_at,
        "monitor_from": created_at,
        "signal_time": candidate.signal_time,
        "bos_time": candidate.bos_time,
        "entry_low": candidate.entry_low,
        "entry_high": candidate.entry_high,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "risk": risk,
        "partial_r": PARTIAL_TARGET_R,
        "partial_fraction": PARTIAL_FRACTION,
        "runner_trail_r": RUNNER_TRAIL_R,
        "partial_target_price": partial_target_price,
        "partial_taken": False,
        "partial_time": "",
        "runner_active": False,
        "runner_peak": None,
        "breakeven_active": False,
        "realized_r": None,
        "runner_exit_r": None,
        "outcome": "open",
        "room_to_active_extreme_r": room_to_active_extreme_r,
        "trade_target_price": candidate.target_price,
        "trade_target_timeframe": candidate.target_timeframe,
        "trade_target_reason": candidate.target_reason,
        "available_r": candidate.available_r,
        "pair_value_tier": pair_value.tier,
        "pair_value_label": pair_value.label,
        "pair_value_note": pair_value.note,
        "entry_timeframe": candidate.entry_timeframe,
        "source": candidate.source,
        "notes": candidate.notes,
        "entry_time": "",
        "exit_time": "",
        "targets": {},
        # --- dual-timeframe plan (trader decides which to take) ---
        "m15_rr_to_target": m15_rr_to_target,
        "m5_stop_loss": m5_plan["m5_stop_loss"],
        "m5_risk": m5_plan["m5_risk"],
        "m5_rr_to_target": m5_plan["m5_rr_to_target"],
        "trail_levels": trail_levels,
    }


def _test_key_for(test: dict[str, object]) -> str:
    """Stable tracking key for an already-built test dict (mirror of _candidate_key)."""
    zone = f"{float(test.get('entry_low', 0.0)):.5f}-{float(test.get('entry_high', 0.0)):.5f}"
    return f"{test.get('route', '')}:{test.get('instrument', '')}:{test.get('side', '')}:{zone}"


def _m5_sibling_test(base: dict[str, object]) -> dict[str, object] | None:
    """Build a parallel "scale down to M5" paper trade from a freshly opened M15 test.

    Goal: maximize R via a *better entry*, not reconfirmation. It places the entry deeper
    in the zone (the midpoint) for a better fill price, while keeping the same wide M15
    structural stop so the trade still has breathing room. Better entry + same safe stop =
    smaller risk and more distance to target = more R, without the noise-stop fragility of
    a tight M5 stop. It fills when price trades down/up to the midpoint (no extra candle
    confirmation); if price only taps the M15 edge and runs, this variant simply misses that
    fill (the M15 trade still takes it). Tracked under ``{route}_M5`` (inherits the parent's
    tier) and managed on M5 candles, so the scoreboard shows M15-edge vs M5-midzone entries.
    """
    side = str(base.get("side", ""))
    if side not in {"BUY", "SELL"}:
        return None
    if base.get("status") != "waiting_entry":
        return None  # only mirror brand-new, not-yet-filled signals
    entry_low = _float_or_none(base.get("entry_low"))
    entry_high = _float_or_none(base.get("entry_high"))
    m15_stop = _float_or_none(base.get("stop_loss"))
    if entry_low is None or entry_high is None or m15_stop is None:
        return None
    entry_mid = round((entry_low + entry_high) / 2.0, 5)
    risk = abs(entry_mid - m15_stop)
    if risk <= 0:
        return None
    partial_target = _target_price(entry_mid, m15_stop, side, PARTIAL_TARGET_R)
    target = _float_or_none(base.get("trade_target_price"))
    rr_to_target = None
    if target is not None:
        rr_to_target = round((target - entry_mid) / risk if side == "BUY" else (entry_mid - target) / risk, 2)
    sibling = dict(base)
    sibling.update(
        {
            "route": f"{base.get('route', '')}_M5",
            "entry_timeframe": "M5",
            "timeframe": "M5",
            "entry_model": "m5_midzone",
            "status": "waiting_entry",
            "entry_price": entry_mid,
            "stop_loss": m15_stop,
            "risk": round(risk, 5),
            "m15_fallback_stop": m15_stop,
            "partial_target_price": round(partial_target, 5),
            "m15_rr_to_target": rr_to_target,
            "trail_levels": {
                "partial_1_5R": round(partial_target, 5),
                "milestone_3R": round(_target_price(entry_mid, m15_stop, side, 3.0), 5),
                "note": "M5 variant: deeper mid-zone entry, wide M15 structural stop (room). Bank ~50% at 1.5R, BE after, trail 1R behind peak.",
            },
            "partial_taken": False,
            "partial_time": "",
            "runner_active": False,
            "runner_peak": None,
            "breakeven_active": False,
            "realized_r": None,
            "runner_exit_r": None,
            "outcome": "open",
            "entry_time": "",
            "exit_time": "",
            "targets": {},
            "source": (str(base.get("source", "")) + " | M5 mid-zone entry variant").strip(" |"),
            # this row IS the M5 trade; clear the advisory nested M5 plan fields
            "m5_stop_loss": None,
            "m5_risk": None,
            "m5_rr_to_target": None,
        }
    )
    return sibling


def _maybe_add_m5_sibling(tests: dict[str, object], base_test: dict[str, object], enabled: bool) -> None:
    if not enabled:
        return
    sibling = _m5_sibling_test(base_test)
    if sibling is None:
        return
    sibling_key = _test_key_for(sibling)
    if sibling_key not in tests:
        tests[sibling_key] = sibling


def _m5_midzone_fill(test: dict[str, object], m5: list[Candle]) -> str | None:
    """Fill the M5 variant when price trades to the deeper mid-zone entry — no confirmation.

    Fires on the first M5 candle that touches the pre-computed mid-zone entry price. Returns
    the fill time, or None until price reaches that level. Re-derivable each cycle, so safe to
    call repeatedly. If price only taps the shallow M15 edge and runs, this never fills (the
    M15 trade still takes it) — that is the accepted fills-vs-R trade-off.
    """
    entry_price = _float_or_none(test.get("entry_price"))
    if entry_price is None or not m5:
        return None
    start = _first_candle_index_after(m5, str(test.get("monitor_from") or test.get("created_at") or ""))
    for j in range(start, len(m5)):
        c = m5[j]
        if c.low <= entry_price <= c.high:
            return c.time.isoformat()
    return None


def _update_m5_entry_test(test: dict[str, object], m5: list[Candle], timeout_bars_m5: int, now: str) -> None:
    """Fill (when price reaches the mid-zone entry) and manage the M5 variant on M5 candles.

    Entry/stop/risk are fixed by the builder (mid-zone entry + wide M15 stop); here we just
    detect the fill and hand off to the shared partial-trail manager. No reconfirmation.
    """
    if test.get("status") == "closed" or not m5:
        return
    if test.get("status") == "waiting_entry":
        fill_time = _m5_midzone_fill(test, m5)
        if fill_time is None:
            test["last_checked_at"] = now
            return
        if _float_or_none(test.get("risk")) in (None, 0.0):
            test["last_checked_at"] = now
            return
        test["status"] = "active"
        test["entry_time"] = fill_time
        test["runner_active"] = True
        test["runner_peak"] = test.get("entry_price")
    _update_partial_trail_test(test, m5, timeout_bars_m5, now)


def _update_test(test: dict[str, object], candles: list[Candle], timeout_bars: int, now: str) -> None:
    if test.get("model") == "partial_trail":
        _update_partial_trail_test(test, candles, timeout_bars, now)
        return
    _update_legacy_test(test, candles, timeout_bars, now)


def _update_partial_trail_test(
    test: dict[str, object], candles: list[Candle], timeout_bars: int, now: str
) -> None:
    side = str(test.get("side", ""))
    entry_low = float(test.get("entry_low", 0.0))
    entry_high = float(test.get("entry_high", 0.0))

    if test.get("status") == "waiting_entry":
        start_time = str(test.get("monitor_from") or test.get("created_at") or "")
        start_index = _first_candle_index_after(candles, start_time)
        entry_price = _entry_price_from_test(test, entry_low, entry_high)
        touch_index = _first_price_touch(candles, start_index, entry_price)
        if touch_index is None and _active_now(test, candles, entry_low, entry_high):
            test["status"] = "active"
            test["entry_time"] = now
            test["runner_active"] = True
            test["runner_peak"] = test.get("entry_price")
            test["last_checked_at"] = now
            return
        if touch_index is None:
            test["last_checked_at"] = now
            return
        test["status"] = "active"
        test["entry_time"] = candles[touch_index].time.isoformat()
        test["runner_active"] = True
        test["runner_peak"] = test.get("entry_price")

    entry_time = str(test.get("entry_time") or "")
    start_index = _first_candle_index_after(candles, entry_time)
    entry_price = float(test.get("entry_price", 0.0))
    risk = float(test.get("risk", 0.0))
    if risk <= 0:
        test["last_checked_at"] = now
        return

    if str(test.get("exit_model")) == "ride_target":
        _manage_ride_target(test, candles, start_index, entry_price, risk, side, timeout_bars, entry_time, now)
        return

    partial_r = float(test.get("partial_r", PARTIAL_TARGET_R))
    partial_fraction = float(test.get("partial_fraction", PARTIAL_FRACTION))
    trail_r = float(test.get("runner_trail_r", RUNNER_TRAIL_R))
    stop_loss = float(test.get("stop_loss", 0.0))
    peak = _float_or_none(test.get("runner_peak"))
    if peak is None:
        peak = entry_price
    partial_level = entry_price + partial_r * risk if side == "BUY" else entry_price - partial_r * risk

    for candle_index in range(start_index, len(candles)):
        candle = candles[candle_index]
        peak = max(peak, candle.high) if side == "BUY" else min(peak, candle.low)

        if not bool(test.get("partial_taken")):
            hit_partial = candle.high >= partial_level if side == "BUY" else candle.low <= partial_level
            hit_stop = candle.low <= stop_loss if side == "BUY" else candle.high >= stop_loss
            if hit_stop and not hit_partial:
                test["runner_peak"] = peak
                _close_partial_trail(test, candle.time.isoformat(), -1.0, -1.0, "loss")
                test["last_checked_at"] = now
                return
            if hit_partial:
                test["partial_taken"] = True
                test["partial_time"] = candle.time.isoformat()
                test["breakeven_active"] = True
                if hit_stop:
                    test["runner_peak"] = peak
                    realized = partial_fraction * partial_r
                    _close_partial_trail(test, candle.time.isoformat(), realized, 0.0, "partial_only")
                    test["last_checked_at"] = now
                    return
                continue
        else:
            if side == "BUY":
                trail_stop = max(entry_price, peak - trail_r * risk)
                hit_runner_stop = candle.low <= trail_stop
            else:
                trail_stop = min(entry_price, peak + trail_r * risk)
                hit_runner_stop = candle.high >= trail_stop
            if hit_runner_stop:
                runner_exit_r = (
                    (trail_stop - entry_price) / risk if side == "BUY" else (entry_price - trail_stop) / risk
                )
                realized = partial_fraction * partial_r + (1 - partial_fraction) * runner_exit_r
                outcome = "partial_only" if runner_exit_r <= 0.05 else "runner_win"
                test["runner_peak"] = peak
                _close_partial_trail(test, candle.time.isoformat(), realized, runner_exit_r, outcome)
                test["last_checked_at"] = now
                return

    test["runner_peak"] = peak

    if candles and _bars_since_entry(candles, entry_time) >= timeout_bars:
        last_close = candles[-1].close
        mark_r = (last_close - entry_price) / risk if side == "BUY" else (entry_price - last_close) / risk
        exit_time = candles[-1].time.isoformat()
        if bool(test.get("partial_taken")):
            runner_exit_r = max(mark_r, 0.0)
            realized = partial_fraction * partial_r + (1 - partial_fraction) * runner_exit_r
            _close_partial_trail(test, exit_time, realized, runner_exit_r, "timeout")
        else:
            _close_partial_trail(test, exit_time, mark_r, mark_r, "timeout")
        test["last_checked_at"] = now
        return

    test["last_checked_at"] = now


def _manage_ride_target(
    test: dict[str, object],
    candles: list[Candle],
    start_index: int,
    entry_price: float,
    risk: float,
    side: str,
    timeout_bars: int,
    entry_time: str,
    now: str,
) -> None:
    """Profile A exit: ride the full position to the fixed H1 target, M15 stop, no partial.

    Win = price reaches ``trade_target_price`` (realized = planned R); loss = M15 stop (-1R);
    both in one candle = conservatively the stop. Timeout marks at the last close.
    """
    target = _float_or_none(test.get("trade_target_price"))
    stop_loss = float(test.get("stop_loss", 0.0))
    if target is None:
        test["last_checked_at"] = now
        return
    planned_r = (target - entry_price) / risk if side == "BUY" else (entry_price - target) / risk
    peak = _float_or_none(test.get("runner_peak")) or entry_price
    for candle_index in range(start_index, len(candles)):
        candle = candles[candle_index]
        peak = max(peak, candle.high) if side == "BUY" else min(peak, candle.low)
        hit_stop = candle.low <= stop_loss if side == "BUY" else candle.high >= stop_loss
        hit_target = candle.high >= target if side == "BUY" else candle.low <= target
        if hit_stop:  # conservative: stop checked first when both touch the same candle
            test["runner_peak"] = peak
            _close_partial_trail(test, candle.time.isoformat(), -1.0, -1.0, "loss")
            test["last_checked_at"] = now
            return
        if hit_target:
            test["runner_peak"] = peak
            _close_partial_trail(test, candle.time.isoformat(), round(planned_r, 4), round(planned_r, 4), "target_win")
            test["last_checked_at"] = now
            return
    test["runner_peak"] = peak
    if candles and _bars_since_entry(candles, entry_time) >= timeout_bars:
        last_close = candles[-1].close
        mark_r = (last_close - entry_price) / risk if side == "BUY" else (entry_price - last_close) / risk
        _close_partial_trail(test, candles[-1].time.isoformat(), round(mark_r, 4), round(mark_r, 4), "timeout")
    test["last_checked_at"] = now


def _close_partial_trail(
    test: dict[str, object],
    exit_time: str,
    realized_r: float,
    runner_exit_r: float,
    outcome: str,
) -> None:
    test["status"] = "closed"
    test["exit_time"] = exit_time
    test["realized_r"] = round(realized_r, 4)
    test["runner_exit_r"] = round(runner_exit_r, 4)
    test["outcome"] = outcome
    test["runner_active"] = False


def _update_legacy_test(test: dict[str, object], candles: list[Candle], timeout_bars: int, now: str) -> None:
    side = str(test.get("side", ""))
    entry_low = float(test.get("entry_low", 0.0))
    entry_high = float(test.get("entry_high", 0.0))
    start_time = str(test.get("entry_time") or test.get("monitor_from") or test.get("created_at") or "")
    start_index = _first_candle_index_after(candles, start_time)

    if test.get("status") == "waiting_entry":
        entry_price = _entry_price_from_test(test, entry_low, entry_high)
        touch_index = _first_price_touch(candles, start_index, entry_price)
        if touch_index is None and _active_now(test, candles, entry_low, entry_high):
            _activate_test(test, now)
            test["last_checked_at"] = now
            return
        if touch_index is None:
            test["last_checked_at"] = now
            return
        test["status"] = "active"
        test["entry_time"] = candles[touch_index].time.isoformat()
        start_index = touch_index + 1
        _activate_targets(test)

    entry_time = str(test.get("entry_time") or "")
    start_index = _first_candle_index_after(candles, entry_time)
    targets = test.get("targets", {})
    if not isinstance(targets, dict):
        return

    for candle_index in range(start_index, len(candles)):
        candle = candles[candle_index]
        _update_breakeven_protection(test, candle)
        for target in targets.values():
            if not isinstance(target, dict) or target.get("status") not in {"active", "waiting_entry"}:
                continue
            sl = _effective_stop_loss(test)
            tp = float(target.get("price", 0.0))
            hit_sl = candle.low <= sl if side == "BUY" else candle.high >= sl
            hit_tp = candle.high >= tp if side == "BUY" else candle.low <= tp
            if hit_sl and hit_tp:
                target["status"] = "sl_hit_ambiguous"
                target["result_time"] = candle.time.isoformat()
                target["diagnostic"] = _loss_diagnostic(test, candle, ambiguous=True)
            elif hit_sl:
                target["status"] = "breakeven" if test.get("breakeven_active") else "sl_hit"
                target["result_time"] = candle.time.isoformat()
                if target["status"] == "sl_hit":
                    target["diagnostic"] = _loss_diagnostic(test, candle, ambiguous=False)
            elif hit_tp:
                target["status"] = "tp_hit"
                target["result_time"] = candle.time.isoformat()

    if _bars_since_entry(candles, entry_time) >= timeout_bars:
        for target in targets.values():
            if isinstance(target, dict) and target.get("status") == "active":
                target["status"] = "timeout"
                target["result_time"] = now

    open_targets = [
        target
        for target in targets.values()
        if isinstance(target, dict) and target.get("status") in {"active", "waiting_entry"}
    ]
    if not open_targets:
        test["status"] = "closed"
        test["exit_time"] = now
    test["last_checked_at"] = now


def _update_breakeven_protection(test: dict[str, object], candle: Candle) -> None:
    if test.get("breakeven_active"):
        return
    side = str(test.get("side", ""))
    entry_price = float(test.get("entry_price", 0.0))
    risk = float(test.get("risk", 0.0))
    trigger_r = float(test.get("breakeven_after_r") if test.get("breakeven_after_r") is not None else 1.0)
    if risk <= 0:
        return
    if trigger_r <= 0:
        return
    if side == "BUY" and candle.high >= entry_price + risk * trigger_r:
        test["breakeven_active"] = True
        test["breakeven_time"] = candle.time.isoformat()
    if side == "SELL" and candle.low <= entry_price - risk * trigger_r:
        test["breakeven_active"] = True
        test["breakeven_time"] = candle.time.isoformat()


def _effective_stop_loss(test: dict[str, object]) -> float:
    if test.get("breakeven_active"):
        return float(test.get("entry_price", 0.0))
    return float(test.get("stop_loss", 0.0))


def _fetch_m15(client: OandaClient, instrument: str) -> list[Candle]:
    if not instrument:
        return []
    try:
        return [candle for candle in client.fetch_candles(instrument, "M15", count=500) if candle.complete]
    except Exception:
        return []


def _fetch_m5(client: OandaClient, instrument: str) -> list[Candle]:
    if not instrument:
        return []
    try:
        return [candle for candle in client.fetch_candles(instrument, "M5", count=500) if candle.complete]
    except Exception:
        return []


def _fetch_h1(client: OandaClient, instrument: str) -> list[Candle]:
    if not instrument:
        return []
    try:
        return [candle for candle in client.fetch_candles(instrument, "H1", count=400) if candle.complete]
    except Exception:
        return []


def _fetch_h4(client: OandaClient, instrument: str) -> list[Candle]:
    if not instrument:
        return []
    try:
        return [candle for candle in client.fetch_candles(instrument, "H4", count=200) if candle.complete]
    except Exception:
        return []


def _m5_swing_stop(side: str, entry_price: float, m15_stop: float, m5_candles: list[Candle]) -> float | None:
    """Tightest structural M5 stop: hide just beyond the nearest M5 swing inside the zone.

    Returns a stop that sits between the entry and the (wider) M15 stop, parked just
    past a real recent M5 swing extreme. None if no suitable swing is found.
    """
    window = m5_candles[-60:]
    if len(window) < 5:
        return None
    buffer = average_range(window, period=14) * 0.1
    best: float | None = None
    for i in range(1, len(window) - 1):
        c = window[i]
        if side == "BUY":
            is_swing = c.low < window[i - 1].low and c.low < window[i + 1].low
            if is_swing and m15_stop < c.low < entry_price:
                best = c.low if best is None else max(best, c.low)
        else:
            is_swing = c.high > window[i - 1].high and c.high > window[i + 1].high
            if is_swing and entry_price < c.high < m15_stop:
                best = c.high if best is None else min(best, c.high)
    if best is None:
        return None
    return best - buffer if side == "BUY" else best + buffer


def _m5_plan(side: str, entry_price: float, m15_stop: float, target_price: float | None, m5_candles: list[Candle]) -> dict[str, object]:
    """Build the tighter M5 alternative (same entry, structural M5 stop) for the trader."""
    m5_stop = _m5_swing_stop(side, entry_price, m15_stop, m5_candles)
    if m5_stop is None:
        # Fall back to a generic tighter stop (40% of the M15 risk) when no clean swing.
        m5_stop = entry_price - (entry_price - m15_stop) * 0.4 if side == "BUY" else entry_price + (m15_stop - entry_price) * 0.4
    m5_risk = abs(entry_price - m5_stop)
    m5_rr = None
    if target_price is not None and m5_risk > 0:
        m5_rr = (target_price - entry_price) / m5_risk if side == "BUY" else (entry_price - target_price) / m5_risk
    return {
        "m5_stop_loss": round(m5_stop, 5),
        "m5_risk": round(m5_risk, 5),
        "m5_rr_to_target": round(m5_rr, 2) if m5_rr is not None else None,
    }


def _loss_diagnostic(test: dict[str, object], candle: Candle, ambiguous: bool) -> str:
    instrument = str(test.get("instrument", ""))
    notes = str(test.get("notes", "")).lower()
    reasons: list[str] = []
    if ambiguous:
        reasons.append("SL and TP touched inside the same candle; counted conservatively as SL first")
    if instrument == "XAU_USD":
        reasons.append("gold volatility can require a wider confirmation/SL rule")
    if "forming" in notes or "wait" in notes or "await" in notes:
        reasons.append("signal text looked like an unconfirmed forming/waiting setup")
    if "not current" in notes or "recheck" in notes:
        reasons.append("entry may have been stale or not current")
    if not reasons:
        reasons.append("price invalidated the test-only stop before reaching 3R")
    return f"{'; '.join(reasons)} at {candle.time.isoformat()}"


def _candidate_key(candidate: SignalCandidate) -> str:
    zone = f"{candidate.entry_low:.5f}-{candidate.entry_high:.5f}"
    return f"{candidate.route}:{candidate.instrument}:{candidate.side}:{zone}"


def _fresh_signal(signal_time: str, now: datetime, max_age_minutes: int) -> bool:
    parsed = _parse_datetime(signal_time)
    if parsed is None:
        return False
    age_seconds = (now - parsed).total_seconds()
    return 0 <= age_seconds <= max_age_minutes * 60


def _entry_price(candidate: SignalCandidate) -> float:
    if candidate.side == "BUY":
        return candidate.entry_high
    return candidate.entry_low


def _stop_loss(candidate: SignalCandidate, candles: list[Candle]) -> float:
    avg_range = average_range(candles, period=30)
    buffer = avg_range * _stop_buffer_multiplier(candidate.instrument)
    if candidate.side == "BUY":
        base = min(value for value in (candidate.entry_low, candidate.sweep_price) if value is not None)
        return base - buffer
    base = max(value for value in (candidate.entry_high, candidate.sweep_price) if value is not None)
    return base + buffer


def _stop_buffer_multiplier(instrument: str) -> float:
    if instrument == "XAU_USD":
        return 0.35
    if instrument == "BTC_USD":
        return 0.35
    return 0.15


def _target_price(entry_price: float, stop_loss: float, side: str, rr: float) -> float:
    risk = abs(entry_price - stop_loss)
    return entry_price + risk * rr if side == "BUY" else entry_price - risk * rr


def _pip_size(instrument: str) -> float:
    if instrument.endswith("_JPY"):
        return 0.01
    if instrument == "XAU_USD":
        return 0.1
    if instrument == "BTC_USD":
        return 1.0
    return 0.0001


def _first_candle_index_after(candles: list[Candle], iso_time: str) -> int:
    if not iso_time:
        return 0
    start = _parse_datetime(iso_time)
    if start is None:
        return 0
    for index, candle in enumerate(candles):
        if candle.time >= start:
            return index
    return len(candles)


def _first_price_touch(candles: list[Candle], start_index: int, price: float) -> int | None:
    for index in range(start_index, len(candles)):
        candle = candles[index]
        if candle.low <= price <= candle.high:
            return index
    return None


def _active_now(test: dict[str, object], candles: list[Candle], low: float, high: float) -> bool:
    if not candles or str(test.get("status", "")).upper() not in {"WAITING_ENTRY"}:
        return False
    latest = candles[-1]
    return latest.low <= _entry_price_from_test(test, low, high) <= latest.high


def _entry_price_from_test(test: dict[str, object], low: float, high: float) -> float:
    value = _float_or_none(test.get("entry_price"))
    return value if value is not None else (low + high) / 2


def _activate_test(test: dict[str, object], entry_time: str) -> None:
    test["status"] = "active"
    test["entry_time"] = entry_time
    _activate_targets(test)


def _activate_targets(test: dict[str, object]) -> None:
    targets = test.get("targets", {})
    if not isinstance(targets, dict):
        return
    for target in targets.values():
        if isinstance(target, dict):
            target["status"] = "active"


def _bars_since_entry(candles: list[Candle], entry_time: str) -> int:
    start = _first_candle_index_after(candles, entry_time)
    return max(0, len(candles) - start)


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def _float_or_none(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _load_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))

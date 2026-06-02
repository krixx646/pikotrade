from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import re
from typing import Any

from fx_annotation.candles import Candle
from fx_annotation.config import DeepSeekConfig, GeminiConfig, OllamaConfig
from fx_annotation.deepseek_client import call_deepseek_text
from fx_annotation.gemini_client import call_gemini_text
from fx_annotation.knowledge import load_strategy_knowledge
from fx_annotation.ollama_client import call_ollama_text
from fx_annotation.pair_value import pair_value_for_instrument


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AI_MEMORY_PATH = PROJECT_ROOT / "outputs" / "ai_memory.json"
DEFAULT_AI_ALERTS_PATH = PROJECT_ROOT / "outputs" / "ai_alerts.json"
DEFAULT_GEMMA_MEMORY_PATH = PROJECT_ROOT / "outputs" / "gemma_memory.json"
DEFAULT_GEMMA_ALERTS_PATH = PROJECT_ROOT / "outputs" / "gemma_alerts.json"
AI_ROUTE_DEFINITIONS = {
    "smc_rag": "SMC/RAG Sniper",
    "m15_mechanical": "M15 Mechanical",
}


@dataclass(frozen=True)
class AiStrategyAnalysis:
    instrument: str
    route_key: str
    route_label: str
    side: str
    status: str
    entry_zone_low: float | None
    entry_zone_high: float | None
    confidence: int
    reasoning: str
    chart_notes: str
    next_action: str
    alert: str
    htf_direction: str = ""


def analyze_state_with_ai(
    config: DeepSeekConfig,
    state: Any,
    fundamentals: str = "",
    image_paths: list[Path] | None = None,
) -> list[AiStrategyAnalysis]:
    prompt = build_strategy_analysis_prompt(state, fundamentals)
    try:
        response = call_deepseek_text(config, prompt, json_mode=True, image_paths=image_paths)
    except Exception:
        if not image_paths:
            raise
        response = call_deepseek_text(config, prompt, json_mode=True)
    if not _has_split_route_payload(response):
        response = call_deepseek_text(
            config,
            _deepseek_split_retry_prompt(state, response),
            json_mode=True,
        )
    analyses = parse_ai_strategy_analyses(state.instrument, response)
    return [_enforce_ai_directional_gate(analysis, state) for analysis in analyses]


def analyze_state_with_gemma(
    config: "OllamaConfig | GeminiConfig",
    state: Any,
    fundamentals: str = "",
) -> list[AiStrategyAnalysis]:
    prompt = build_gemma_strategy_analysis_prompt(state, fundamentals)
    # The "Gemma" reviewer slot: free Gemini API when configured, else local Ollama.
    if isinstance(config, GeminiConfig):
        response = call_gemini_text(config, prompt)
    else:
        response = call_ollama_text(config, prompt)
    analyses = [
        _repair_ai_fallback_analysis(analysis, state)
        for analysis in parse_ai_strategy_analyses(state.instrument, response)
    ]
    return [_enforce_ai_directional_gate(analysis, state) for analysis in analyses]


def build_gemma_strategy_analysis_prompt(state: Any, fundamentals: str = "") -> str:
    return (
        "You are the local Gemma strategy reviewer. Return exactly one compact JSON object. "
        "Do not explain outside JSON. Be conservative: only ENTRY_NOW when the setup is confirmed now; "
        "use FORMING or WAIT when monitoring a concrete zone.\n\n"
        + build_strategy_analysis_prompt(
            state,
            fundamentals,
            strategy_limit=450,
            candle_limit=5,
            swing_limit=3,
            sweep_limit=3,
            fvg_limit=2,
        )
    )


def _has_split_route_payload(raw_response: str) -> bool:
    data = _extract_json(raw_response)
    if not data:
        return False
    return all(isinstance(data.get(route_key), dict) for route_key in AI_ROUTE_DEFINITIONS)


def _deepseek_split_retry_prompt(state: Any, bad_response: str) -> str:
    clipped = bad_response.strip().replace("\n", " ")[:1200]
    return f"""Your previous response was invalid because it did not return the required two-route JSON object.

Do not explain. Do not write prose. Return exactly one valid JSON object with both keys:
- smc_rag
- m15_mechanical

Do not use RSI, MACD, moving-average crossovers, generic indicators, or any strategy outside the user's Trading Geek-style SMC workflow.
For any route with status ENTRY_NOW, FORMING, or WAIT, you must provide numeric entry_zone_low and entry_zone_high.
If a route has no concrete zone, return status NO_SETUP with null entry zones.

smc_rag route rules:
- Use HTF direction, H4/H1 POI ladder, liquidity sweep, BOS/market shift, reaction confirmation, and 3R room.
- Follow the effective HTF direction only.

m15_mechanical route rules:
- Use M15 trend/range, premium/discount, liquidity sweep, BOS/market shift, compact base zone, price return/near-return, and clean/ugly filtering.
- Do not require the full H4/H1 sniper POI sequence.

Instrument: {state.instrument}
Effective HTF direction: {state.bias.direction if state.bias else "unknown"}
Latest M15 candles:
{_recent_candles_text(state.entry_candles, limit=8)}

Required output shape:
{{
  "smc_rag": {{
    "side": "BUY, SELL, NEUTRAL, or NO_TRADE",
    "status": "ENTRY_NOW, FORMING, WAIT, STALE, INVALID, or NO_SETUP",
    "entry_zone_low": number or null,
    "entry_zone_high": number or null,
    "confidence": integer from 0 to 100,
    "reasoning": "short SMC/RAG route reasoning",
    "chart_notes": "short chart-facing note",
    "next_action": "alert, wait, revisit, or ignore",
    "alert": "alert text or empty string"
  }},
  "m15_mechanical": {{
    "side": "BUY, SELL, NEUTRAL, or NO_TRADE",
    "status": "ENTRY_NOW, FORMING, WAIT, STALE, INVALID, or NO_SETUP",
    "entry_zone_low": number or null,
    "entry_zone_high": number or null,
    "confidence": integer from 0 to 100,
    "reasoning": "short M15 mechanical route reasoning",
    "chart_notes": "short chart-facing note",
    "next_action": "alert, wait, revisit, or ignore",
    "alert": "alert text or empty string"
  }}
}}

Bad previous response excerpt, do not repeat this style:
{clipped}
"""


def select_ai_review_states(states: list[Any], limit: int) -> list[Any]:
    candidates = [
        state
        for state in states
        if not getattr(state, "error", "") and getattr(state, "entry_candles", [])
    ]
    if limit <= 0:
        return candidates
    return sorted(candidates, key=_ai_state_rank, reverse=True)[:limit]


def update_ai_memory(
    analyses: list[AiStrategyAnalysis],
    memory_path: Path = DEFAULT_AI_MEMORY_PATH,
    alerts_path: Path = DEFAULT_AI_ALERTS_PATH,
    now: datetime | None = None,
) -> None:
    current_time = now or datetime.now(timezone.utc)
    memory = _load_json(memory_path)
    alerts = _load_json(alerts_path)

    for analysis in analyses:
        record = asdict(analysis)
        alert = ai_alert_for_analysis(analysis)
        next_check_time, next_check_reason = schedule_ai_next_check(analysis, current_time)
        record["alert"] = alert
        record["updated_at"] = current_time.isoformat()
        record["next_check_time"] = next_check_time.isoformat()
        record["next_check_reason"] = next_check_reason
        existing = memory.get(analysis.instrument)
        instrument_record = existing if isinstance(existing, dict) else {}
        routes = instrument_record.get("routes")
        if not isinstance(routes, dict):
            routes = {}
        routes[analysis.route_key] = record
        instrument_record["instrument"] = analysis.instrument
        instrument_record["routes"] = routes
        instrument_record["updated_at"] = current_time.isoformat()
        if analysis.route_key == "smc_rag":
            instrument_record.update(record)
        memory[analysis.instrument] = instrument_record

        if alert:
            alerts[f"{analysis.instrument}:{analysis.route_key}:{current_time.isoformat()}"] = {
                "instrument": analysis.instrument,
                "route_key": analysis.route_key,
                "route_label": analysis.route_label,
                "created_at": current_time.isoformat(),
                "side": analysis.side,
                "status": analysis.status,
                "alert": alert,
                "entry_zone_low": analysis.entry_zone_low,
                "entry_zone_high": analysis.entry_zone_high,
                "confidence": analysis.confidence,
                "source": _analysis_source(memory_path),
            }

    _save_json(memory_path, memory)
    _save_json(alerts_path, alerts)


def render_ai_strategy_report(analyses: list[AiStrategyAnalysis]) -> str:
    return render_strategy_report("DeepSeek AI Strategy Analysis", "DeepSeek", analyses)


def render_gemma_strategy_report(analyses: list[AiStrategyAnalysis]) -> str:
    return render_strategy_report("Gemma AI Strategy Analysis", "Gemma", analyses)


def render_strategy_report(
    title: str,
    label: str,
    analyses: list[AiStrategyAnalysis],
) -> str:
    lines = [f"# {title}", ""]
    if not analyses:
        lines.append(f"No {label} analyses were produced.")
        return "\n".join(lines) + "\n"

    for analysis in analyses:
        alert = ai_alert_for_analysis(analysis)
        zone = "none"
        if analysis.entry_zone_low is not None and analysis.entry_zone_high is not None:
            zone = f"{analysis.entry_zone_low:.5f} - {analysis.entry_zone_high:.5f}"
        lines.extend(
            [
                f"## {analysis.instrument} - {analysis.route_label}",
                "",
                f"- {label} route: {analysis.route_key}",
                f"- {label} side: {analysis.side}",
                f"- {label} effective HTF direction: {analysis.htf_direction or 'unknown'}",
                f"- {label} status: {analysis.status}",
                f"- {label} entry zone: {zone}",
                f"- {label} confidence: {analysis.confidence}",
                f"- {label} next action: {analysis.next_action}",
                f"- {label} alert: {alert or 'none'}",
                "",
                f"### {label} {analysis.route_label} Reasoning",
                "",
                analysis.reasoning or "No reasoning returned.",
                "",
                f"### {label} {analysis.route_label} Chart Notes",
                "",
                analysis.chart_notes or "No chart notes returned.",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def schedule_ai_next_check(
    analysis: AiStrategyAnalysis,
    now: datetime,
) -> tuple[datetime, str]:
    status = analysis.status.upper()
    action = analysis.next_action.lower()

    if status == "ENTRY_NOW" or action == "alert":
        return now + timedelta(minutes=5), "AI route sees immediate attention."
    if status == "FORMING":
        return now + timedelta(minutes=30), "AI route sees a forming setup."
    if status == "WAIT":
        return now + timedelta(minutes=60), "AI route is waiting for confirmation."
    if status == "STALE":
        return now + timedelta(hours=3), "AI route sees a stale setup."
    if status in {"INVALID", "NO_SETUP"} or action == "ignore":
        return now + timedelta(hours=4), "AI route sees no active setup."

    if analysis.confidence >= 70:
        return now + timedelta(minutes=30), "AI route has higher confidence and should revisit soon."
    if analysis.confidence >= 40:
        return now + timedelta(minutes=60), "AI route has moderate confidence."
    return now + timedelta(hours=2), "AI route has low confidence."


def ai_alert_for_analysis(analysis: AiStrategyAnalysis) -> str:
    if analysis.alert:
        return analysis.alert

    status = analysis.status.upper()
    action = analysis.next_action.lower()
    side = analysis.side.upper()
    route = analysis.route_label

    if status == "ENTRY_NOW" or action == "alert":
        return f"AI {route} route wants attention now for {side}."
    if status == "FORMING" and analysis.confidence >= 60:
        return f"AI {route} route sees a forming {side} setup with {analysis.confidence}% confidence."
    if status == "WAIT" and analysis.entry_zone_low is not None and analysis.entry_zone_high is not None:
        return f"AI {route} route is waiting for price near {analysis.entry_zone_low:.5f}-{analysis.entry_zone_high:.5f}."

    return ""


def render_ai_alerts_markdown(path: Path = DEFAULT_AI_ALERTS_PATH) -> str:
    return render_alerts_markdown("DeepSeek AI Alert Log", "DeepSeek AI", path)


def render_gemma_alerts_markdown(path: Path = DEFAULT_GEMMA_ALERTS_PATH) -> str:
    return render_alerts_markdown("Gemma AI Alert Log", "Gemma AI", path)


def render_alerts_markdown(title: str, label: str, path: Path) -> str:
    alerts = _load_json(path)
    lines = [f"# {title}", ""]
    if not alerts:
        lines.append(f"No {label} alerts recorded.")
        return "\n".join(lines) + "\n"

    for key, alert in sorted(alerts.items(), reverse=True):
        if not isinstance(alert, dict):
            continue
        lines.extend(
            [
                f"## {alert.get('instrument', key)}",
                "",
                f"- Created at: {alert.get('created_at', '')}",
                f"- AI route: {alert.get('route_label', alert.get('route_key', 'legacy'))}",
                f"- AI side: {alert.get('side', '')}",
                f"- AI status: {alert.get('status', '')}",
                f"- AI confidence: {alert.get('confidence', '')}",
                f"- AI alert: {alert.get('alert', '')}",
                f"- AI entry zone: {alert.get('entry_zone_low', '')} - {alert.get('entry_zone_high', '')}",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def _analysis_source(memory_path: Path) -> str:
    if memory_path == DEFAULT_GEMMA_MEMORY_PATH:
        return "gemma_ai"
    return "deepseek_ai"


def build_strategy_analysis_prompt(
    state: Any,
    fundamentals: str = "",
    strategy_limit: int = 4500,
    candle_limit: int = 20,
    swing_limit: int = 12,
    sweep_limit: int = 10,
    fvg_limit: int = 10,
) -> str:
    return f"""You are an independent forex technical analyst trained on the user's strategy.

You are NOT reviewing, approving, filtering, or confirming a rule engine.
You are running two independent strategy analyses from the provided candles and strategy knowledge.

Boundaries:
- Do not place trades.
- Do not provide stop loss, take profit, lot size, or execution instructions.
- Do not combine your answer with any rule-engine result.
- Return only your own AI analysis.
- You must keep the two route decisions independent. Do not let one route approve or reject the other route.
- You must follow the trend-following directional gate:
  - If the effective higher-timeframe direction is bullish, analyze BUY/demand opportunities only. Ignore SELL/supply opportunities.
  - If the effective higher-timeframe direction is bearish, analyze SELL/supply opportunities only. Ignore BUY/demand opportunities.
  - For the smc_rag route, if the effective higher-timeframe direction is neutral/unknown, return NO_SETUP unless H1 refinement clearly resolves direction from the provided evidence.
  - For the m15_mechanical route, derive the local M15 trend/range from the latest M15 candles and structure evidence. It may disagree with the SMC/RAG route, but must explain why.
  - Opposite-side zones can exist on the chart, but they are not actionable under this strategy because the user rides the trend instead of trading against it.
- You must follow the active-story rule:
  - Older candles are allowed only to determine HTF direction and locate the highest high / lowest low anchors.
  - After HH and LL are known, find which anchor came first from left to right.
  - The active story starts from that first anchor.
  - Do not use candles before the active story start to choose demand/supply zones, liquidity targets, entry zones, alerts, or chart annotations.
  - Use the provided active_from_anchor and active_from_time as the boundary for current analysis.

Strategy knowledge:
{_strategy_knowledge_text(strategy_limit)}

Fundamental notes:
{fundamentals or "No fundamental notes provided."}

Market evidence:
Instrument: {state.instrument}
Pair value context: {_pair_value_text(getattr(state, "pair_value", None))}
Effective higher-timeframe direction fact from OANDA structure scan: {state.bias.direction if state.bias else "unknown"}
Known higher-timeframe context note: {state.bias.reason if state.bias else "unknown"}
Timeframe process for smc_rag: H4 narrative first; use H1 as refinement when H4 is broad/noisy; use 15M for execution confirmation only.
Timeframe process for m15_mechanical: M15 chart first; use the higher-timeframe facts only as context/warnings, not as a forced POI sequence.
HTF narrative:
{_htf_narrative_text(getattr(state, "htf_narrative", None))}

Rule-route trade target:
{_trade_target_text(getattr(state, "trade_target", None), getattr(state, "available_r", None))}

Recent extracted swing points:
{_swing_evidence_text(getattr(state, "swings", []), limit=swing_limit)}

Recent extracted liquidity sweeps:
{_sweep_evidence_text(getattr(state, "sweeps", []), limit=sweep_limit)}

Recent extracted fair value gaps:
{_fvg_evidence_text(getattr(state, "fair_value_gaps", []), limit=fvg_limit)}

Higher-timeframe points of interest:
{_poi_evidence_text(getattr(state, "htf_pois", []))}

HTF/1H zone ladder:
{_zone_ladder_text(getattr(state, "zone_ladder", []))}

Relevant HTF POI for current route setup:
{_single_poi_text(getattr(state, "relevant_htf_poi", None))}

Rule-route HTF POI sequence state:
{getattr(state, "htf_poi_sequence", "unknown")}

Latest candles:
{_recent_candles_text(state.entry_candles, limit=candle_limit)}

Visual context:
If images are attached, use them as chart context generated from the same OANDA candles:
- H4 image = narrative.
- H1 image = zone ladder refinement.
- M15 image = execution confirmation.
Treat images as supporting evidence beside the numeric facts, not as a replacement for the facts.

Analyze silently. Do not restate the candles. Do not write step-by-step reasoning.
Each route output must be useful to a TradingView user:
- If there is a current route entry, return side BUY or SELL, status ENTRY_NOW, and exact entry_zone_low/high.
- If there is a future potential entry area, return side BUY or SELL, status FORMING or WAIT, and exact entry_zone_low/high for the zone to monitor.
- If a route cannot name a concrete price zone, return NO_SETUP with null entry zones for that route.
- Keep chart_notes short and chart-facing: explain why the BUY/SELL entry or coming-soon setup is valid in one sentence.
- Do not put generic text like "watch for BOS", "no clean setup yet", "NEUTRAL", "NO_SETUP", or "WAIT" in chart_notes.

SMC/RAG route priority:
- Direction comes before zone selection. Bullish means demand/BUY only. Bearish means supply/SELL only. Do not mark an opposite-side AI route.
- Active story comes before zone selection. Old data behind active_from_time is background context only; do not mark zones from it.
- Demand is not any random green reaction. Treat demand as a drop-base-rally or rally-base-rally area: a compact 1-3 candle base/order-block cluster where buyers caused a meaningful bullish impulse or BOS. Supply is the bearish mirror: rally-base-drop or drop-base-drop.
- Order blocks are narrower candle-level refinements inside a broader supply/demand base. Do not treat every last opposing candle as a valid zone unless it belongs to a base that caused displacement.
- Prefer fresh or lightly tested zones. Repeatedly mitigated/respected zones are weaker context/ladder zones, not fresh entries by themselves.
- Liquidity sweep means price raids a prior swing high/low or equal-high/equal-low pool and closes back through the level with rejection. A sweep alone is not entry.
- BOS confirms continuation by closing beyond a meaningful swing level in the intended trend direction. Opposite-side structure damage is a reversal warning/CHOCH, not a trend-following entry.
- Do not enter just because price is in an HTF/1H zone.
- First identify which H4/H1 zone price is approaching, testing, respecting, or failing.
- If a zone fails, ignore it and reason from the next valid zone in the ladder.
- If a zone is respected, require 15M liquidity sweep first and 15M Market Shift/BOS second before calling ENTRY_NOW.
- Before calling ENTRY_NOW, identify the HTF/H1 liquidity target first: last swing high for BUY or last swing low for SELL. If target-to-risk is under 3R, return WAIT or NO_SETUP instead of ENTRY_NOW.
- Use lower-timeframe entry precision only to improve risk-to-target math; do not move the target to fit the entry.
- A monitored future zone can be FORMING/WAIT only if it has a concrete price range from the ladder.

M15 mechanical route priority:
- Judge the latest M15 chart as its own simplified Trading Geek route.
- Require local M15 trend/range context, premium/discount location within the recent M15 range, liquidity sweep, BOS/market shift, and the compact base zone that caused BOS.
- The entry zone must be a real supply/demand base, not a random candle and not a broad 50-70 percent retracement-only area.
- Price should be at, returning to, or reasonably close to the M15 base zone. If price is far away, use WAIT/FORMING with the zone to monitor.
- Reject ugly charts: compressed/noisy ranges, weak displacement from sweep to BOS, oversized base zones, and zones in the middle of the range.
- Do not require the full H4/H1 sniper POI sequence for this route.
- Still mark clear higher-timeframe conflict or low pair value in reasoning if it exists.

Return one compact JSON object only:
{{
  "smc_rag": {{
    "side": "BUY, SELL, NEUTRAL, or NO_TRADE",
    "status": "ENTRY_NOW, FORMING, WAIT, STALE, INVALID, or NO_SETUP",
    "entry_zone_low": number or null,
    "entry_zone_high": number or null,
    "confidence": integer from 0 to 100,
    "reasoning": "independent SMC/RAG route reasoning",
    "chart_notes": "what should be shown on the chart for this route",
    "next_action": "alert, wait, revisit, or ignore",
    "alert": "alert text if this route wants attention now, otherwise empty string"
  }},
  "m15_mechanical": {{
    "side": "BUY, SELL, NEUTRAL, or NO_TRADE",
    "status": "ENTRY_NOW, FORMING, WAIT, STALE, INVALID, or NO_SETUP",
    "entry_zone_low": number or null,
    "entry_zone_high": number or null,
    "confidence": integer from 0 to 100,
    "reasoning": "independent M15 mechanical route reasoning",
    "chart_notes": "what should be shown on the chart for this route",
    "next_action": "alert, wait, revisit, or ignore",
    "alert": "alert text if this route wants attention now, otherwise empty string"
  }}
}}

Do not write analysis outside the JSON object. Put all reasoning inside each route's "reasoning" field.
"""


def parse_ai_strategy_analyses(instrument: str, raw_response: str) -> list[AiStrategyAnalysis]:
    data = _extract_json(raw_response)
    if not data and raw_response.strip():
        data = _fallback_strategy_data(raw_response)
    if not data and not raw_response.strip():
        return [
            _empty_route_analysis(
                instrument,
                route_key,
                "AI returned no visible strategy analysis.",
            )
            for route_key in AI_ROUTE_DEFINITIONS
        ]

    if any(route_key in data for route_key in AI_ROUTE_DEFINITIONS):
        return [
            _analysis_from_route_data(
                instrument,
                route_key,
                data.get(route_key) if isinstance(data.get(route_key), dict) else {},
                raw_response,
            )
            for route_key in AI_ROUTE_DEFINITIONS
        ]

    legacy = _analysis_from_route_data(instrument, "smc_rag", data, raw_response)
    return [legacy, _empty_route_analysis(instrument, "m15_mechanical", "No separate M15 mechanical route returned.")]


def parse_ai_strategy_analysis(instrument: str, raw_response: str) -> AiStrategyAnalysis:
    return parse_ai_strategy_analyses(instrument, raw_response)[0]


def _analysis_from_route_data(
    instrument: str,
    route_key: str,
    data: dict[str, object],
    raw_response: str,
) -> AiStrategyAnalysis:
    if not data:
        return _empty_route_analysis(instrument, route_key, f"No {AI_ROUTE_DEFINITIONS[route_key]} route returned.")
    return AiStrategyAnalysis(
        instrument=instrument,
        route_key=route_key,
        route_label=AI_ROUTE_DEFINITIONS[route_key],
        side=_text_value(data, "side", "NEUTRAL"),
        status=_text_value(data, "status", "NO_SETUP"),
        entry_zone_low=_float_or_none(data.get("entry_zone_low")),
        entry_zone_high=_float_or_none(data.get("entry_zone_high")),
        confidence=max(0, min(100, int(_float_or_none(data.get("confidence")) or 0))),
        reasoning=_text_value(data, "reasoning", raw_response.strip()),
        chart_notes=_text_value(data, "chart_notes", _extract_chart_notes(raw_response)),
        next_action=_text_value(data, "next_action", ""),
        alert=_text_value(data, "alert", ""),
    )


def _empty_route_analysis(instrument: str, route_key: str, reason: str) -> AiStrategyAnalysis:
    return AiStrategyAnalysis(
        instrument=instrument,
        route_key=route_key,
        route_label=AI_ROUTE_DEFINITIONS[route_key],
        side="NEUTRAL",
        status="NO_SETUP",
        entry_zone_low=None,
        entry_zone_high=None,
        confidence=0,
        reasoning=reason,
        chart_notes="",
        next_action="ignore",
        alert="",
    )


def _enforce_ai_directional_gate(analysis: AiStrategyAnalysis, state: Any) -> AiStrategyAnalysis:
    direction = _state_direction(state)
    if analysis.route_key == "m15_mechanical":
        return replace(analysis, htf_direction=direction)
    side = analysis.side.upper()
    allowed_side = _allowed_side(direction)

    if allowed_side is None:
        return replace(
            analysis,
            side="NEUTRAL",
            status="NO_SETUP",
            entry_zone_low=None,
            entry_zone_high=None,
            confidence=0,
            reasoning=(
                f"Directional gate blocked AI route because effective HTF direction is {direction}. "
                f"{analysis.reasoning}"
            ),
            chart_notes="",
            next_action="ignore",
            alert="",
            htf_direction=direction,
        )

    if side in {"BUY", "SELL"} and side != allowed_side:
        return replace(
            analysis,
            side="NEUTRAL",
            status="INVALID",
            entry_zone_low=None,
            entry_zone_high=None,
            confidence=0,
            reasoning=(
                f"Directional gate invalidated {side}: effective HTF direction is {direction}, "
                f"so this strategy only allows {allowed_side}. {analysis.reasoning}"
            ),
            chart_notes="",
            next_action="ignore",
            alert="",
            htf_direction=direction,
        )

    return replace(analysis, htf_direction=direction)


def _repair_ai_fallback_analysis(analysis: AiStrategyAnalysis, state: Any) -> AiStrategyAnalysis:
    if analysis.confidence != 45 or analysis.side.upper() not in {"BUY", "SELL"}:
        return analysis

    side = analysis.side.upper()
    status = analysis.status.upper()
    setup = getattr(state, "primary_setup", None)
    target_r = getattr(state, "available_r", None)
    has_valid_target = target_r is not None and float(target_r) >= 3.0
    if (
        setup is not None
        and str(getattr(setup, "side", "")).upper() == side
        and getattr(setup, "current_state", "") == "at_entry_zone_now"
        and has_valid_target
    ):
        zone = getattr(setup, "entry_zone", None)
        if zone is not None:
            return replace(
                analysis,
                status="ENTRY_NOW",
                entry_zone_low=getattr(zone, "low", None),
                entry_zone_high=getattr(zone, "high", None),
                confidence=_fallback_setup_confidence(state, "ENTRY_NOW"),
                chart_notes=(
                    f"{side} ENTRY_NOW: watch {getattr(zone, 'low', 0.0):.5f}-"
                    f"{getattr(zone, 'high', 0.0):.5f}; liquidity sweep and Market Shift/BOS confirmed."
                ),
                next_action="alert",
            )

    zone = _fallback_context_zone(state, side)
    if zone is None:
        return analysis

    low = getattr(zone, "low", None)
    high = getattr(zone, "high", None)
    if low is None or high is None:
        return analysis

    repaired_status = "WAIT" if status == "WAIT" else "FORMING"
    return replace(
        analysis,
        status=repaired_status,
        entry_zone_low=low,
        entry_zone_high=high,
        confidence=_fallback_setup_confidence(state, repaired_status),
        chart_notes=(
            f"{side} {repaired_status}: watch {low:.5f}-{high:.5f}; "
            "waiting for 15M liquidity sweep and Market Shift/BOS."
        ),
        next_action="revisit",
        alert="",
    )


def _fallback_context_zone(state: Any, side: str) -> Any:
    expected_zone_side = "demand" if side == "BUY" else "supply"
    poi = getattr(state, "relevant_htf_poi", None)
    if poi is not None and getattr(poi, "side", "") == expected_zone_side:
        return poi
    for zone in getattr(state, "zone_ladder", []):
        if getattr(zone, "side", "") == expected_zone_side and getattr(zone, "state", "") != "failed":
            return zone
    return None


def _fallback_setup_confidence(state: Any, status: str) -> int:
    setup = getattr(state, "primary_setup", None)
    quality = int(getattr(setup, "quality_score", 0) or 0) if setup is not None else 0
    base = 58 if status == "ENTRY_NOW" else 50
    sequence_bonus = 7 if getattr(state, "htf_poi_sequence", "") == "valid" else 0
    active_bonus = 8 if setup is not None and getattr(setup, "current_state", "") == "at_entry_zone_now" else 0
    return max(35, min(82, base + quality * 4 + sequence_bonus + active_bonus))


def _state_direction(state: Any) -> str:
    bias = getattr(state, "bias", None)
    direction = getattr(bias, "direction", None)
    return str(direction or "unknown").lower()


def _allowed_side(direction: str) -> str | None:
    if direction == "bullish":
        return "BUY"
    if direction == "bearish":
        return "SELL"
    return None


def _strategy_knowledge_text(limit: int = 4500) -> str:
    text = load_strategy_knowledge([PROJECT_ROOT / "docs" / "strategy-knowledge-base.md"])
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n\n[Strategy knowledge truncated for prompt size.]"


def _ai_state_rank(state: Any) -> int:
    ranks = {
        "entry_candidate_now": 100,
        "wait_for_pullback": 90,
        "potential_future_setup": 80,
        "watchlist": 40,
        "low_quality": 20,
        "conflict": 20,
        "no_clear_state": 10,
    }
    score = ranks.get(getattr(state, "status", ""), 0)
    setup = getattr(state, "primary_setup", None)
    pair_value = pair_value_for_instrument(str(getattr(state, "instrument", "")))
    if pair_value.tier == "high_value":
        score += 12
    elif pair_value.tier == "low_value":
        score -= 3
    if setup is not None:
        score += int(getattr(setup, "quality_score", 0) or 0)
        if getattr(setup, "current_state", "") == "at_entry_zone_now":
            score += 15
    return score


def _recent_candles_text(candles: list[Candle], limit: int = 20) -> str:
    lines: list[str] = []
    for candle in candles[-limit:]:
        lines.append(
            f"{candle.time.isoformat()} O={candle.open:.5f} H={candle.high:.5f} "
            f"L={candle.low:.5f} C={candle.close:.5f} V={candle.volume}"
        )
    return "\n".join(lines)


def _swing_evidence_text(swings: list[Any], limit: int = 12) -> str:
    if not swings:
        return "No extracted swing points provided."
    lines: list[str] = []
    for swing in swings[-limit:]:
        lines.append(
            f"index={getattr(swing, 'index', '')} kind={getattr(swing, 'kind', '')} "
            f"price={getattr(swing, 'price', 0.0):.5f}"
        )
    return "\n".join(lines)


def _sweep_evidence_text(sweeps: list[Any], limit: int = 10) -> str:
    if not sweeps:
        return "No extracted liquidity sweeps provided."
    lines: list[str] = []
    for sweep in sweeps[-limit:]:
        lines.append(
            f"index={getattr(sweep, 'index', '')} kind={getattr(sweep, 'kind', '')} "
            f"swept_price={getattr(sweep, 'swept_price', 0.0):.5f}"
        )
    return "\n".join(lines)


def _fvg_evidence_text(gaps: list[Any], limit: int = 10) -> str:
    if not gaps:
        return "No extracted fair value gaps provided."
    lines: list[str] = []
    for gap in gaps[-limit:]:
        lines.append(
            f"index={getattr(gap, 'index', '')} direction={getattr(gap, 'direction', '')} "
            f"zone={getattr(gap, 'low', 0.0):.5f}-{getattr(gap, 'high', 0.0):.5f}"
        )
    return "\n".join(lines)


def _poi_evidence_text(pois: list[Any], limit: int = 8) -> str:
    if not pois:
        return "No HTF POIs provided."
    return "\n".join(_single_poi_text(poi) for poi in pois[:limit])


def _zone_ladder_text(zones: list[Any], limit: int = 10) -> str:
    if not zones:
        return "No HTF/1H zone ladder provided."
    lines: list[str] = []
    for zone in zones[:limit]:
        lines.append(
            f"timeframe={getattr(zone, 'timeframe', '')} side={getattr(zone, 'side', '')} "
            f"state={getattr(zone, 'state', '')} zone={getattr(zone, 'low', 0.0):.5f}-"
            f"{getattr(zone, 'high', 0.0):.5f} distance={getattr(zone, 'distance_to_price', 0.0):.5f} "
            f"reason={getattr(zone, 'reason', '')}"
        )
    return "\n".join(lines)


def _htf_narrative_text(narrative: Any) -> str:
    if narrative is None:
        return "No HTF narrative provided."
    pools = getattr(narrative, "liquidity_pools", ())
    pool_text = "; ".join(
        f"{getattr(pool, 'kind', '')}={getattr(pool, 'price', 0.0):.5f}"
        for pool in pools[:6]
    )


def _trade_target_text(target: Any, target_r: Any) -> str:
    if target is None:
        return "No HTF/H1 swing target selected. Do not call ENTRY_NOW without a named target and at least 3R available."
    return (
        f"target_price={getattr(target, 'price', 0.0):.5f} "
        f"timeframe={getattr(target, 'timeframe', '')} "
        f"swing_kind={getattr(target, 'swing_kind', '')} "
        f"candle_time={getattr(target, 'candle_time', '')} "
        f"available_r={target_r} "
        f"reason={getattr(target, 'reason', '')}"
    )


def _pair_value_text(pair_value: Any) -> str:
    if pair_value is None:
        return "UNVALIDATED PAIR - no pair tier available."
    return (
        f"{getattr(pair_value, 'label', 'UNVALIDATED PAIR')} "
        f"({getattr(pair_value, 'tier', 'unvalidated')}): "
        f"{getattr(pair_value, 'note', '')}"
    )
    zones = getattr(narrative, "zones_inside_range", ())
    zone_text = "; ".join(str(zone) for zone in zones[:8])
    return (
        f"timeframe={getattr(narrative, 'timeframe', '')} "
        f"direction={getattr(narrative, 'direction', '')} "
        f"phase={getattr(narrative, 'phase', '')} "
        f"highest_high={getattr(narrative, 'highest_high', 0.0):.5f} "
        f"lowest_low={getattr(narrative, 'lowest_low', 0.0):.5f} "
        f"active_from_anchor={getattr(narrative, 'active_from_anchor', '')} "
        f"active_from_time={getattr(narrative, 'active_from_time', '')} "
        f"active_range={getattr(narrative, 'range_low', 0.0):.5f}-"
        f"{getattr(narrative, 'range_high', 0.0):.5f} "
        f"last_line_of_defense={getattr(narrative, 'last_line_of_defense', '') or 'none'} "
        f"liquidity_pools={pool_text or 'none'} "
        f"zones_inside_range={zone_text or 'none'}"
    )


def _single_poi_text(poi: Any) -> str:
    if poi is None:
        return "No relevant HTF POI."
    touched = "yes" if getattr(poi, "touched_now", False) else "no"
    return (
        f"side={getattr(poi, 'side', '')} zone={getattr(poi, 'low', 0.0):.5f}-"
        f"{getattr(poi, 'high', 0.0):.5f} source={getattr(poi, 'source', '')} "
        f"touched_now={touched} distance={getattr(poi, 'distance_to_price', 0.0):.5f}"
    )


def _extract_json(raw_response: str) -> dict[str, object]:
    text = raw_response.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _fallback_strategy_data(raw_response: str) -> dict[str, object]:
    text = raw_response.strip()
    lowered = text.lower()
    side = _fallback_side_from_context(text, lowered)
    if side == "NEUTRAL":
        side = _fallback_side(lowered)
    status = _fallback_status(lowered, side)
    zone_low, zone_high = _fallback_zone(text, side)
    confidence = _fallback_confidence(lowered)

    if side in {"BUY", "SELL"} and zone_low is not None and zone_high is not None:
        return {
            "side": side,
            "status": status,
            "entry_zone_low": zone_low,
            "entry_zone_high": zone_high,
            "confidence": confidence,
            "reasoning": text,
            "chart_notes": _fallback_chart_note(side, status, zone_low, zone_high, text),
            "next_action": "revisit" if status in {"FORMING", "WAIT"} else "alert",
            "alert": "",
        }

    return {
        "side": "NEUTRAL",
        "status": "NO_SETUP",
        "entry_zone_low": None,
        "entry_zone_high": None,
        "confidence": 0,
        "reasoning": text,
        "chart_notes": _extract_chart_notes(text),
        "next_action": "ignore",
        "alert": "",
    }


def _fallback_side(lowered: str) -> str:
    buy_markers = (
        "potential buy",
        "buy setup",
        "buy idea",
        "looking for a buy",
        "possible buy",
        "buy entry",
        "bullish setup",
        "bullish entry",
    )
    sell_markers = (
        "potential sell",
        "sell setup",
        "sell idea",
        "looking for a sell",
        "possible sell",
        "sell entry",
        "bearish setup",
        "bearish entry",
    )
    if any(marker in lowered for marker in buy_markers):
        return "BUY"
    if any(marker in lowered for marker in sell_markers):
        return "SELL"
    return "NEUTRAL"


def _fallback_side_from_context(text: str, lowered: str) -> str:
    buy_context = (
        "htf direction is bullish",
        "effective htf direction: bullish",
        "higher-timeframe direction: bullish",
        "direction: bullish",
    )
    sell_context = (
        "htf direction is bearish",
        "effective htf direction: bearish",
        "higher-timeframe direction: bearish",
        "direction: bearish",
    )
    if any(marker in lowered for marker in buy_context) and _fallback_zone(text, "BUY") != (None, None):
        return "BUY"
    if any(marker in lowered for marker in sell_context) and _fallback_zone(text, "SELL") != (None, None):
        return "SELL"
    return "NEUTRAL"


def _fallback_status(lowered: str, side: str) -> str:
    if side == "NEUTRAL":
        return "NO_SETUP"
    if '"status": "entry_now"' in lowered or '"status":"entry_now"' in lowered:
        return "ENTRY_NOW"
    if "forming" in lowered or "setup in the making" in lowered:
        return "FORMING"
    if "zone under test" in lowered or "touched_now=yes" in lowered or "price is inside" in lowered:
        return "FORMING"
    if "no bos" in lowered or "bos missing" in lowered or "market shift" in lowered:
        return "FORMING"
    if "wait" in lowered or "await" in lowered or "monitor" in lowered:
        return "WAIT"
    return "FORMING"


def _fallback_zone(text: str, side: str) -> tuple[float | None, float | None]:
    if side == "NEUTRAL":
        return None, None

    candidates: list[tuple[int, float, float]] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text.replace("\n", " ")):
        lowered = sentence.lower()
        matches = re.findall(r"(\d+\.\d+)\s*[-–]\s*(\d+\.\d+)", sentence)
        if not matches:
            continue
        score = 0
        if "entry" in lowered:
            score += 5
        if "respected" in lowered:
            score += 4
        if side == "BUY" and "demand" in lowered:
            score += 3
        if side == "SELL" and "supply" in lowered:
            score += 3
        if "zone" in lowered:
            score += 1
        for low_raw, high_raw in matches:
            low = float(low_raw)
            high = float(high_raw)
            candidates.append((score, min(low, high), max(low, high)))

    if not candidates:
        return None, None
    _, low, high = sorted(candidates, key=lambda item: (item[0], -(item[2] - item[1])), reverse=True)[0]
    return low, high


def _fallback_confidence(lowered: str) -> int:
    match = re.search(r"confidence[^0-9]{0,20}(\d{1,3})", lowered)
    if match:
        return max(0, min(100, int(match.group(1))))
    if "moderate" in lowered:
        return 55
    if "strong" in lowered:
        return 70
    return 45


def _fallback_chart_note(
    side: str,
    status: str,
    zone_low: float,
    zone_high: float,
    text: str,
) -> str:
    lowered = text.lower()
    confirmation = "waiting for 15M liquidity sweep and Market Shift/BOS"
    if "sweep" in lowered and ("no bos" in lowered or "bos missing" in lowered):
        confirmation = "liquidity sweep seen; waiting for 15M Market Shift/BOS"
    if status == "ENTRY_NOW":
        confirmation = "liquidity sweep and Market Shift/BOS confirmed"
    return f"{side} {status}: watch {zone_low:.5f}-{zone_high:.5f}; {confirmation}."


def _extract_chart_notes(raw_response: str) -> str:
    if not raw_response.strip():
        return ""

    sentences = re.split(r"(?<=[.!?])\s+", raw_response.replace("\n", " "))
    notes: list[str] = []
    keywords = ("sweep", "bos", "break", "supply", "demand", "zone", "below", "above")
    for sentence in sentences:
        lowered = sentence.lower()
        price_count = len(re.findall(r"\d+\.\d+", sentence))
        if len(sentence) > 280 or price_count > 4:
            continue
        if any(term in lowered for term in ("open", "close", "volume", "candles:")):
            continue
        if not any(keyword in lowered for keyword in keywords):
            continue
        if price_count == 0:
            continue
        notes.append(sentence.strip())
        if len(notes) >= 4:
            break

    return " ".join(notes)


def _text_value(data: dict[str, object], key: str, default: str) -> str:
    value = data.get(key)
    return value.strip() if isinstance(value, str) else default


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _load_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")

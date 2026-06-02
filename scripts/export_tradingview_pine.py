import argparse
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export current agent zones as a TradingView Pine Script indicator."
    )
    parser.add_argument(
        "--rule-memory",
        default=str(PROJECT_ROOT / "outputs" / "live_memory.json"),
    )
    parser.add_argument(
        "--deepseek-memory",
        default=str(PROJECT_ROOT / "outputs" / "ai_memory.json"),
    )
    parser.add_argument(
        "--gemma-memory",
        default=str(PROJECT_ROOT / "outputs" / "gemma_memory.json"),
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "outputs" / "tradingview" / "market_agent_zones.pine"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    zones = []
    rule_memory = _load_json(Path(args.rule_memory))
    zones.extend(rule_zones(rule_memory))
    zones.extend(ai_zones(_load_json(Path(args.deepseek_memory)), "DeepSeek", "deepseek", rule_memory))
    zones.extend(ai_zones(_load_json(Path(args.gemma_memory)), "Gemma", "gemma", rule_memory))

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_pine(zones), encoding="utf-8")

    print(f"Pine zones: {len(zones)}")
    print(f"Pine script: {output}")
    return 0


def rule_zones(memory: object) -> list[dict[str, object]]:
    if not isinstance(memory, dict):
        return []

    zones: list[dict[str, object]] = []
    for instrument, record in memory.items():
        if not isinstance(record, dict):
            continue
        low = _float_or_none(record.get("entry_zone_low"))
        high = _float_or_none(record.get("entry_zone_high"))
        if low is None or high is None:
            continue
        if record.get("htf_poi_sequence") not in {None, "", "valid"}:
            continue
        display = _rule_display(record)
        if display is None:
            continue
        zones.append(
            {
                "instrument": str(record.get("instrument", instrument)),
                "route": "Rule",
                "route_id": "rule",
                "side": display["side"],
                "status": display["label"],
                "low": min(low, high),
                "high": max(low, high),
                "note": display["reason"],
            }
        )
    return zones


def ai_zones(
    memory: object,
    route: str,
    route_id: str,
    rule_memory: object,
) -> list[dict[str, object]]:
    if not isinstance(memory, dict):
        return []

    zones: list[dict[str, object]] = []
    for instrument, record in memory.items():
        if not isinstance(record, dict):
            continue
        low = _float_or_none(record.get("entry_zone_low"))
        high = _float_or_none(record.get("entry_zone_high"))
        if low is None or high is None:
            continue
        if not _ai_side_allowed(record, rule_memory, str(record.get("instrument", instrument))):
            continue
        display = _ai_display(record, route)
        if display is None:
            continue
        zones.append(
            {
                "instrument": str(record.get("instrument", instrument)),
                "route": route,
                "route_id": route_id,
                "side": display["side"],
                "status": display["label"],
                "low": min(low, high),
                "high": max(low, high),
                "note": display["reason"],
            }
        )
    return zones


def render_pine(zones: list[dict[str, object]]) -> str:
    lines = [
        "//@version=5",
        'indicator("Forex Agent Zones", overlay=true, max_boxes_count=200, max_labels_count=200)',
        "",
        "showRule = input.bool(true, \"Show Rule Route\")",
        "showDeepSeek = input.bool(true, \"Show DeepSeek Route\")",
        "showGemma = input.bool(true, \"Show Gemma Route\")",
        "extendBars = input.int(120, \"Extend zones right/left bars\", minval=10, maxval=500)",
        "",
        "var box[] agentBoxes = array.new_box()",
        "var label[] agentLabels = array.new_label()",
        "",
        "clearDrawings() =>",
        "    while array.size(agentBoxes) > 0",
        "        box.delete(array.pop(agentBoxes))",
        "    while array.size(agentLabels) > 0",
        "        label.delete(array.pop(agentLabels))",
        "",
        "routeColor(routeId, side) =>",
        "    routeId == \"rule\" and side == \"BUY\" ? color.new(color.lime, 82) :",
        "     routeId == \"rule\" and side == \"SELL\" ? color.new(color.red, 82) :",
        "     routeId == \"deepseek\" ? color.new(color.blue, 84) :",
        "     color.new(color.orange, 84)",
        "",
        "routeBorder(routeId, side) =>",
        "    routeId == \"rule\" and side == \"BUY\" ? color.lime :",
        "     routeId == \"rule\" and side == \"SELL\" ? color.red :",
        "     routeId == \"deepseek\" ? color.blue :",
        "     color.orange",
        "",
        "drawZone(symbolValue, routeName, routeId, side, status, note, lowPrice, highPrice) =>",
        "    enabled = routeId == \"rule\" ? showRule : routeId == \"deepseek\" ? showDeepSeek : showGemma",
        "    if enabled and syminfo.ticker == symbolValue",
        "        b = box.new(bar_index - extendBars, highPrice, bar_index + extendBars, lowPrice, bgcolor=routeColor(routeId, side), border_color=routeBorder(routeId, side), border_width=2)",
        "        array.push(agentBoxes, b)",
        "        reasonText = note == \"\" ? \"\" : \"\\n\" + note",
        "        labelText = status + \"\\n\" + str.tostring(lowPrice) + \" - \" + str.tostring(highPrice) + reasonText",
        "        l = label.new(bar_index, highPrice, labelText, style=label.style_label_down, textcolor=color.white, color=routeBorder(routeId, side))",
        "        array.push(agentLabels, l)",
        "",
        "if barstate.islast",
        "    clearDrawings()",
    ]

    if not zones:
        lines.extend(
            [
                "    label.new(bar_index, high, \"No forex agent zones exported\", style=label.style_label_down, textcolor=color.white, color=color.gray)",
                "",
            ]
        )
        return "\n".join(lines) + "\n"

    for zone in zones:
        lines.append(
            "    drawZone("
            f"\"{_pine_string(_tv_symbol(str(zone['instrument'])))}\", "
            f"\"{_pine_string(str(zone['route']))}\", "
            f"\"{_pine_string(str(zone['route_id']))}\", "
            f"\"{_pine_string(str(zone['side']))}\", "
            f"\"{_pine_string(str(zone['status']))}\", "
            f"\"{_pine_string(str(zone['note']))}\", "
            f"{float(zone['low']):.8f}, "
            f"{float(zone['high']):.8f})"
        )

    lines.append("")
    return "\n".join(lines) + "\n"


def _tv_symbol(instrument: str) -> str:
    return instrument.replace("_", "")


def _pine_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _ai_display(record: dict[str, object], route: str) -> dict[str, str] | None:
    side = str(record.get("side", "")).upper()
    status = str(record.get("status", "")).upper()
    action = str(record.get("next_action", "")).lower()

    if side not in {"BUY", "SELL"}:
        return None
    if status in {"NO_SETUP", "INVALID", "STALE"} or action == "ignore":
        return None

    if status == "ENTRY_NOW" or action == "alert":
        label = f"{route} {side} entry"
    elif status in {"FORMING", "WAIT"}:
        label = f"{route} {side} setup coming soon"
    else:
        return None

    return {
        "side": side,
        "label": label,
        "reason": _short_ai_reason(record),
    }


def _ai_side_allowed(
    record: dict[str, object],
    rule_memory: object,
    instrument: str,
) -> bool:
    side = str(record.get("side", "")).upper()
    direction = str(record.get("htf_direction", "")).lower()
    if not direction and isinstance(rule_memory, dict):
        rule_record = rule_memory.get(instrument)
        if isinstance(rule_record, dict):
            direction = str(rule_record.get("bias", "")).lower()
    if direction == "bullish":
        return side == "BUY"
    if direction == "bearish":
        return side == "SELL"
    return False


def _rule_display(record: dict[str, object]) -> dict[str, str] | None:
    side = str(record.get("primary_side", "")).upper()
    status = str(record.get("status", ""))
    if side not in {"BUY", "SELL"}:
        return None
    if status == "entry_candidate_now":
        label = f"Rule {side} entry"
    elif status in {"wait_for_pullback", "potential_future_setup"}:
        label = f"Rule {side} setup coming soon"
    else:
        return None

    return {
        "side": side,
        "label": label,
        "reason": _short_rule_reason(record),
    }


def _short_rule_reason(record: dict[str, object], limit: int = 95) -> str:
    raw = str(record.get("next_check_reason") or record.get("action") or "")
    text = " ".join(raw.replace("\n", " ").split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _short_ai_reason(record: dict[str, object], limit: int = 95) -> str:
    raw = str(
        record.get("chart_notes")
        or record.get("alert")
        or record.get("reasoning")
        or ""
    )
    text = " ".join(raw.replace("\n", " ").split())
    blocked_phrases = (
        "watch for bos",
        "no clean setup",
        "no setup",
        "neutral",
    )
    if not text or any(phrase in text.lower() for phrase in blocked_phrases):
        text = "AI marked a strategy zone from sweep, structure, and POI evidence."
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _load_json(path: Path) -> object:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())

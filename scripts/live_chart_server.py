import json
import mimetypes
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
WEB_ROOT = PROJECT_ROOT / "web" / "live_chart"
sys.path.insert(0, str(SRC_DIR))

from fx_annotation.config import load_oanda_config
from fx_annotation.market_watch import DEFAULT_WATCHLIST
from fx_annotation.oanda_client import OandaClient


MEMORY_PATHS = {
    "rule": PROJECT_ROOT / "outputs" / "live_memory.json",
    "deepseek": PROJECT_ROOT / "outputs" / "ai_memory.json",
    "gemma": PROJECT_ROOT / "outputs" / "gemma_memory.json",
}


def main() -> int:
    host = "127.0.0.1"
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    server = ThreadingHTTPServer((host, port), LiveChartHandler)
    print(f"Live chart viewer: http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping live chart viewer.")
    finally:
        server.server_close()
    return 0


class LiveChartHandler(BaseHTTPRequestHandler):
    client: OandaClient | None = None

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._handle_api(parsed.path, parse_qs(parsed.query))
            return
        self._serve_static(parsed.path)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _handle_api(self, path: str, query: dict[str, list[str]]) -> None:
        try:
            if path == "/api/instruments":
                self._json_response({"instruments": _instruments()})
                return
            if path == "/api/candles":
                self._json_response(_candles_payload(self._client(), query))
                return
            if path == "/api/overlays":
                self._json_response(_overlays_payload(query))
                return
            self._json_response({"error": "Unknown API route."}, status=404)
        except Exception as error:
            self._json_response({"error": str(error)}, status=500)

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        file_path = (WEB_ROOT / relative).resolve()
        if not str(file_path).startswith(str(WEB_ROOT.resolve())) or not file_path.exists():
            self.send_error(404)
            return

        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json_response(self, payload: object, status: int = 200) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    @classmethod
    def _client(cls) -> OandaClient:
        if cls.client is None:
            cls.client = OandaClient(load_oanda_config())
        return cls.client


def _candles_payload(client: OandaClient, query: dict[str, list[str]]) -> dict[str, object]:
    instrument = _query_value(query, "instrument", "XAU_USD")
    granularity = _query_value(query, "granularity", "M15")
    count = int(_query_value(query, "count", "300"))
    candles = client.fetch_candles(instrument, granularity, count=count)
    return {
        "instrument": instrument,
        "granularity": granularity,
        "candles": [
            {
                "time": int(candle.time.timestamp()),
                "iso_time": candle.time.isoformat(),
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
                "complete": candle.complete,
            }
            for candle in candles
        ],
    }


def _overlays_payload(query: dict[str, list[str]]) -> dict[str, object]:
    instrument = _query_value(query, "instrument", "XAU_USD")
    mode = _query_value(query, "mode", "actionable")
    rule_memory = _load_json(MEMORY_PATHS["rule"])
    rule_record = _record_for(rule_memory, instrument)
    overlays = {
        "zones": [],
        "price_lines": [],
        "markers": [],
        "state": _state_payload(rule_record),
    }

    if rule_record:
        overlays["zones"].extend(_rule_zones(rule_record, mode=mode))
        overlays["price_lines"].extend(_narrative_lines(rule_record))
        overlays["markers"].extend(_sequence_markers(rule_record))
        overlays["markers"].extend(_reversal_markers(rule_record))

    ai_routes: list[dict[str, object]] = []
    for route_id, route_name in (("deepseek", "DeepSeek"), ("gemma", "Gemma")):
        record = _record_for(_load_json(MEMORY_PATHS[route_id]), instrument)
        if record:
            ai_routes.append(_ai_route_summary(route_name, record))
            zone = _ai_zone(record, rule_record, route_name)
            if zone:
                overlays["zones"].append(zone)
    overlays["state"]["ai_routes"] = ai_routes

    return {"instrument": instrument, **overlays}


def _rule_zones(record: dict[str, object], mode: str = "actionable") -> list[dict[str, object]]:
    ladder_zones: list[dict[str, object]] = []
    for zone in record.get("zone_ladder", []):
        if not isinstance(zone, dict):
            continue
        low = _float_or_none(zone.get("low"))
        high = _float_or_none(zone.get("high"))
        if low is None or high is None:
            continue
        side = str(zone.get("side", ""))
        timeframe = str(zone.get("timeframe", ""))
        state = str(zone.get("state", ""))
        if state in {"failed", "below_untouched", "above_untouched"}:
            continue
        ladder_zones.append(
            {
                "route": "Rule",
                "kind": "zone",
                "side": side.upper(),
                "low": min(low, high),
                "high": max(low, high),
                "start_time": _timestamp(str(zone.get("candle_time", ""))),
                "label": f"{timeframe} {side} {state}".strip(),
                "source": str(zone.get("source", "")),
                "note": _zone_note(zone),
            }
        )

    zones = (
        _context_presentation_zones(ladder_zones, record)
        if mode == "context"
        else _presentation_zones(ladder_zones, record)
    )
    entry_low = _float_or_none(record.get("entry_zone_low"))
    entry_high = _float_or_none(record.get("entry_zone_high"))
    side = str(record.get("primary_side", "")).upper()
    status = str(record.get("status", ""))
    if (
        entry_low is not None
        and entry_high is not None
        and side in {"BUY", "SELL"}
        and _active_rule_status(status)
        and not _record_is_stale(record)
    ):
        zones.append(
            {
                "route": "Rule",
                "kind": "entry",
                "side": side,
                "low": min(entry_low, entry_high),
                "high": max(entry_low, entry_high),
                "start_time": _timestamp(str(record.get("bos_time") or record.get("updated_at", ""))),
                "label": _rule_entry_label(side, status),
                "source": str(record.get("entry_zone_source", "")),
                "note": _entry_note(record),
            }
        )
    return zones


def _presentation_zones(
    zones: list[dict[str, object]],
    record: dict[str, object],
    limit: int = 6,
) -> list[dict[str, object]]:
    story = record.get("story")
    active_low = _float_or_none(story.get("active_zone_low")) if isinstance(story, dict) else None
    active_high = _float_or_none(story.get("active_zone_high")) if isinstance(story, dict) else None
    latest_price = _float_or_none(record.get("latest_price"))

    selected: list[dict[str, object]] = []
    for zone in zones:
        if _same_price_zone(zone, active_low, active_high):
            selected.append(zone)

    for state in ("inside", "approaching"):
        selected.extend(zone for zone in zones if f" {state}" in str(zone.get("label", "")))

    selected.extend(_next_valid_ladder_zones(zones, latest_price, limit=4))

    deduped: list[dict[str, object]] = []
    seen: set[tuple[float, float, str]] = set()
    for zone in selected:
        low = _float_or_none(zone.get("low"))
        high = _float_or_none(zone.get("high"))
        side = str(zone.get("side", ""))
        if low is None or high is None:
            continue
        key = (round(low, 6), round(high, 6), side)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(zone)
        if len(deduped) >= limit:
            break
    return deduped


def _next_valid_ladder_zones(
    zones: list[dict[str, object]],
    latest_price: float | None,
    limit: int,
) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    for zone in zones:
        label = str(zone.get("label", ""))
        if " failed" in label or "below_untouched" in label or "above_untouched" in label:
            continue
        low = _float_or_none(zone.get("low"))
        high = _float_or_none(zone.get("high"))
        side = str(zone.get("side", "")).upper()
        if low is None or high is None:
            continue
        if latest_price is not None:
            if side == "DEMAND" and high > latest_price:
                continue
            if side == "SUPPLY" and low < latest_price:
                continue
        candidates.append(zone)

    return sorted(
        candidates,
        key=lambda zone: abs(((_float_or_none(zone.get("high")) or 0.0) + (_float_or_none(zone.get("low")) or 0.0)) / 2 - (latest_price or 0.0)),
    )[:limit]


def _context_presentation_zones(
    zones: list[dict[str, object]],
    record: dict[str, object],
    limit: int = 10,
) -> list[dict[str, object]]:
    selected = _presentation_zones(zones, record, limit=6)
    last_line = _last_line_zone(zones, record)
    if last_line is not None:
        last_line = {
            **last_line,
            "label": f"Last defense {last_line.get('label', '')}".strip(),
            "note": f"Last line of defense context. {last_line.get('note', '')}".strip(),
        }
        selected.append(last_line)

    origin_zones: list[dict[str, object]] = []
    for zone in zones:
        source = str(zone.get("source", ""))
        if "origin base" in source:
            origin_zones.append(zone)
    selected.extend(origin_zones)

    deduped: list[dict[str, object]] = []
    seen: set[tuple[float, float, str]] = set()
    for zone in selected:
        low = _float_or_none(zone.get("low"))
        high = _float_or_none(zone.get("high"))
        side = str(zone.get("side", ""))
        if low is None or high is None:
            continue
        key = (round(low, 6), round(high, 6), side)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(zone)
        if len(deduped) >= limit:
            break
    return deduped


def _last_line_zone(
    zones: list[dict[str, object]],
    record: dict[str, object],
) -> dict[str, object] | None:
    narrative = record.get("htf_narrative")
    if not isinstance(narrative, dict):
        return None
    last_line = str(narrative.get("last_line_of_defense", ""))
    if not last_line:
        return None
    for zone in zones:
        low = _float_or_none(zone.get("low"))
        high = _float_or_none(zone.get("high"))
        if low is None or high is None:
            continue
        if f"{low:.5f}-{high:.5f}" in last_line:
            return zone
    return None


def _zone_note(zone: dict[str, object]) -> str:
    source = str(zone.get("source", "")).strip()
    reason = str(zone.get("reason", "")).strip()
    if source and reason:
        return f"{source}. {reason}"
    return reason or source


def _same_price_zone(
    zone: dict[str, object],
    active_low: float | None,
    active_high: float | None,
) -> bool:
    low = _float_or_none(zone.get("low"))
    high = _float_or_none(zone.get("high"))
    if low is None or high is None or active_low is None or active_high is None:
        return False
    return abs(low - active_low) <= 0.00001 and abs(high - active_high) <= 0.00001


def _ai_zone(
    record: dict[str, object],
    rule_record: dict[str, object],
    route: str,
) -> dict[str, object] | None:
    low = _float_or_none(record.get("entry_zone_low"))
    high = _float_or_none(record.get("entry_zone_high"))
    side = str(record.get("side", "")).upper()
    status = str(record.get("status", "")).upper()
    if low is None or high is None or side not in {"BUY", "SELL"}:
        return None
    if status not in {"ENTRY_NOW", "FORMING", "WAIT"}:
        return None
    if _record_is_stale(record):
        return None
    if not _side_allowed(side, record, rule_record):
        return None
    label_status = "entry" if status == "ENTRY_NOW" else "setup coming soon"
    return {
        "route": route,
        "kind": "ai",
        "side": side,
        "low": min(low, high),
        "high": max(low, high),
        "start_time": _timestamp(str(record.get("updated_at", ""))),
        "label": f"{route} {side} {label_status}",
        "note": str(record.get("chart_notes", "")),
    }


def _ai_route_summary(route: str, record: dict[str, object]) -> dict[str, object]:
    low = _float_or_none(record.get("entry_zone_low"))
    high = _float_or_none(record.get("entry_zone_high"))
    return {
        "route": route,
        "status": record.get("status", ""),
        "side": record.get("side", ""),
        "confidence": record.get("confidence"),
        "htf_direction": record.get("htf_direction", ""),
        "updated_at": record.get("updated_at", ""),
        "is_stale": _record_is_stale(record),
        "entry_zone": f"{low:.5f}-{high:.5f}" if low is not None and high is not None else "",
        "note": _short_text(record.get("chart_notes") or record.get("next_check_reason") or "", limit=360),
    }


def _short_text(value: object, limit: int = 360) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _sequence_markers(record: dict[str, object]) -> list[dict[str, object]]:
    markers: list[dict[str, object]] = []
    side = str(record.get("primary_side", "")).upper()
    sweep_time = _timestamp(str(record.get("sweep_time", "")))
    sweep_price = _float_or_none(record.get("sweep_price"))
    if sweep_time is not None and sweep_price is not None:
        markers.append(
            {
                "time": sweep_time,
                "price": sweep_price,
                "kind": "sweep",
                "side": side,
                "label": "Liquidity sweep",
                "note": _sweep_note(record),
            }
        )

    bos_time = _timestamp(str(record.get("bos_time", "")))
    bos_price = _float_or_none(record.get("bos_price"))
    if bos_time is not None and bos_price is not None:
        markers.append(
            {
                "time": bos_time,
                "price": bos_price,
                "kind": "bos",
                "side": side,
                "label": "Market Shift / BOS",
                "note": _bos_note(record),
            }
        )
    return markers


def _reversal_markers(record: dict[str, object]) -> list[dict[str, object]]:
    warning = record.get("reversal_warning")
    if not isinstance(warning, dict):
        return []
    markers: list[dict[str, object]] = []
    side = str(warning.get("side", "")).upper()
    for key, label in (("sweep", "Opposite sweep"), ("bos", "Opposite Market Shift")):
        time = _timestamp(str(warning.get(f"{key}_time", "")))
        price = _float_or_none(warning.get(f"{key}_price"))
        if time is None or price is None:
            continue
        markers.append(
            {
                "time": time,
                "price": price,
                "kind": f"reversal-{key}",
                "side": "SELL" if side == "SELL" else "BUY",
                "label": label,
                "note": str(warning.get("message", "")),
            }
        )
    return markers


def _narrative_lines(record: dict[str, object]) -> list[dict[str, object]]:
    narrative = record.get("htf_narrative")
    if not isinstance(narrative, dict):
        return []
    lines: list[dict[str, object]] = []
    for key, label in (("highest_high", "HH"), ("lowest_low", "LL")):
        price = _float_or_none(narrative.get(key))
        if price is not None:
            lines.append({"price": price, "label": f"{label} {price:.5f}", "kind": key})
    return lines


def _state_payload(record: dict[str, object]) -> dict[str, object]:
    if not record:
        return {}
    updated_at = str(record.get("updated_at", ""))
    next_check_time = str(record.get("next_check_time", ""))
    stale = _record_is_stale(record)
    return {
        "status": record.get("status", ""),
        "action": record.get("action", ""),
        "bias": record.get("bias", ""),
        "latest_price": record.get("latest_price"),
        "next_check_time": next_check_time,
        "updated_at": updated_at,
        "is_stale": stale,
        "freshness": _freshness_label(updated_at, next_check_time, stale),
        "story": record.get("story", {}),
        "htf_narrative": record.get("htf_narrative", {}),
        "reversal_warning": record.get("reversal_warning"),
    }


def _instruments() -> list[str]:
    instruments = set(DEFAULT_WATCHLIST)
    for path in MEMORY_PATHS.values():
        memory = _load_json(path)
        if isinstance(memory, dict):
            instruments.update(str(key) for key in memory)
    return sorted(instruments)


def _rule_entry_label(side: str, status: str) -> str:
    if status == "entry_candidate_now":
        return f"Rule {side} entry"
    if status in {"wait_for_pullback", "potential_future_setup"}:
        return f"Rule {side} setup coming soon"
    return f"Rule {side} zone"


def _active_rule_status(status: str) -> bool:
    return status in {"entry_candidate_now", "wait_for_pullback", "potential_future_setup"}


def _entry_note(record: dict[str, object]) -> str:
    story = record.get("story")
    if isinstance(story, dict):
        note = str(story.get("note", ""))
        if note:
            return note
    return str(record.get("action", ""))


def _sweep_note(record: dict[str, object]) -> str:
    side = str(record.get("primary_side", "")).upper()
    kind = str(record.get("sweep_kind", "")).replace("_", " ")
    price = _float_or_none(record.get("sweep_price"))
    return f"{side}: {kind} swept at {price:.5f}." if price is not None else f"{side}: liquidity sweep."


def _bos_note(record: dict[str, object]) -> str:
    side = str(record.get("primary_side", "")).upper()
    direction = str(record.get("bos_direction", "")).upper()
    price = _float_or_none(record.get("bos_price"))
    return (
        f"{side}: {direction} market shift/BOS broke {price:.5f}."
        if price is not None
        else f"{side}: market shift/BOS confirmed."
    )


def _side_allowed(side: str, record: dict[str, object], rule_record: dict[str, object]) -> bool:
    direction = str(record.get("htf_direction") or rule_record.get("bias", "")).lower()
    if direction == "bullish":
        return side == "BUY"
    if direction == "bearish":
        return side == "SELL"
    return False


def _record_for(memory: object, instrument: str) -> dict[str, object]:
    if not isinstance(memory, dict):
        return {}
    record = memory.get(instrument)
    return record if isinstance(record, dict) else {}


def _load_json(path: Path) -> object:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _record_is_stale(record: dict[str, object]) -> bool:
    status = str(record.get("status", ""))
    if status in {"expired", "low_quality", "no_clear_state", "error"}:
        return True
    next_check = _parse_datetime(str(record.get("next_check_time", "")))
    if next_check is None:
        return False
    return datetime.now(timezone.utc) > next_check


def _freshness_label(updated_at: str, next_check_time: str, stale: bool) -> str:
    parts = []
    if updated_at:
        parts.append(f"updated {updated_at}")
    if next_check_time:
        parts.append(f"next check {next_check_time}")
    if stale:
        parts.append("STALE/RECHECK NEEDED")
    return " | ".join(parts)


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _timestamp(value: str) -> int | None:
    parsed = _parse_datetime(value)
    return int(parsed.timestamp()) if parsed is not None else None


def _query_value(query: dict[str, list[str]], key: str, default: str) -> str:
    values = query.get(key)
    return values[0] if values else default


def _float_or_none(value: object) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())

"""Owner-only AI second-opinion analyst (admin privilege).

When the agent opens a new high-tier forward test, this script pulls fresh OANDA
multi-timeframe context, asks the free Gemini model for an independent verdict
(TAKE / CAUTION / SKIP) with reasoning, and pushes it to the OWNER ONLY via the
PicoClaw token-free /send endpoint. Guests never receive these - they are the
admin's edge for deciding which signals are worth taking.

Each trade is analyzed once (state-tracked). Verdicts are also written to a file
PicoClaw can read so the owner can ask follow-ups ("why skip EURUSD?"). Designed
to run after forward_test_signals in the 5-minute loop. Fully delete-safe: remove
this file and the one call in run_always_on.run_cycle.

Config (environment variables, all optional):
  PICOTRADE_ANALYST_OWNER     owner JID (default: first whatsapp_push recipient)
  PICOTRADE_ANALYST_MIN_TIER  default 3 (analyze tier <= this; 1 HTF-MOM, 2 HTF-ZONE, 3 PREMIUM)
  PICOTRADE_ANALYST_MAX       default 3 (verdicts per run; rest deferred to next run)
  PICOTRADE_ANALYST_MD        default ~/.picoclaw/workspace/memory/TRADE_VERDICTS.md
  PICOTRADE_ANALYST_DRY_RUN   set to 1 to print instead of sending (or pass --dry-run)
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SRC_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from fx_annotation.config import load_gemini_config, load_oanda_config
from fx_annotation.gemini_client import call_gemini_text
from fx_annotation.oanda_client import OandaClient

import whatsapp_push as wa

FORWARD_TESTS_PATH = PROJECT_ROOT / "outputs" / "forward_tests.json"
STATE_PATH = PROJECT_ROOT / "outputs" / "trade_analyst_state.json"
VERDICTS_PATH = PROJECT_ROOT / "outputs" / "trade_verdicts.json"
DEFAULT_MD = "~/.picoclaw/workspace/memory/TRADE_VERDICTS.md"

VERDICT_EMOJI = {"TAKE": "\u2705", "CAUTION": "\u26a0\ufe0f", "SKIP": "\u274c"}


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _save_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def _float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _trend(closes: list[float]) -> str:
    if len(closes) < 5:
        return "unclear"
    first = sum(closes[: max(1, len(closes) // 4)]) / max(1, len(closes) // 4)
    last = sum(closes[-max(1, len(closes) // 4):]) / max(1, len(closes) // 4)
    span = max(abs(c) for c in closes) or 1.0
    diff = (last - first) / span
    if diff > 0.0008:
        return "up"
    if diff < -0.0008:
        return "down"
    return "ranging"


def _oanda_context(client: OandaClient, instrument: str) -> dict:
    """Compact, robust multi-timeframe snapshot. Never raises."""
    ctx: dict[str, object] = {}
    try:
        h4 = [c for c in client.fetch_candles(instrument, "H4", count=120) if c.complete]
        h1 = [c for c in client.fetch_candles(instrument, "H1", count=180) if c.complete]
        m15 = [c for c in client.fetch_candles(instrument, "M15", count=120) if c.complete]
    except Exception:
        return ctx
    if not m15:
        return ctx
    price = m15[-1].close
    ctx["price"] = round(price, 5)
    if h4:
        ctx["h4_trend"] = _trend([c.close for c in h4[-30:]])
        ctx["h4_high"] = round(max(c.high for c in h4[-30:]), 5)
        ctx["h4_low"] = round(min(c.low for c in h4[-30:]), 5)
    if h1:
        ctx["h1_trend"] = _trend([c.close for c in h1[-40:]])
        ctx["h1_high"] = round(max(c.high for c in h1[-40:]), 5)
        ctx["h1_low"] = round(min(c.low for c in h1[-40:]), 5)
        ctx["h1_recent"] = [round(c.close, 5) for c in h1[-8:]]
    ctx["m15_recent"] = [round(c.close, 5) for c in m15[-10:]]
    return ctx


def _build_prompt(test: dict, ctx: dict) -> str:
    side = test.get("side")
    entry = _float(test.get("entry_price"))
    stop = _float(test.get("stop_loss"))
    target = _float(test.get("trade_target_price"))
    risk = abs(entry - stop) if entry is not None and stop is not None else None
    avail = _float(test.get("available_r"))
    rank, tier_label = wa._tier(str(test.get("route", "")))
    session_name, prime = wa._session_quality(test)
    price = ctx.get("price")
    r_to_target = None
    if price is not None and target is not None and risk:
        r_to_target = round((target - price) / risk if side == "BUY" else (price - target) / risk, 2)
    facts = {
        "instrument": test.get("instrument"),
        "route": test.get("route"),
        "tier": tier_label,
        "side": side,
        "entry": entry,
        "stop_loss": stop,
        "target": target,
        "planned_R": avail,
        "current_price": price,
        "R_left_to_target_from_now": r_to_target,
        "session": session_name,
        "prime_session": prime,
        "pair_value": test.get("pair_value_label"),
        "h4_trend": ctx.get("h4_trend"),
        "h1_trend": ctx.get("h1_trend"),
        "h4_range": [ctx.get("h4_low"), ctx.get("h4_high")],
        "h1_range": [ctx.get("h1_low"), ctx.get("h1_high")],
        "h1_recent_closes": ctx.get("h1_recent"),
        "m15_recent_closes": ctx.get("m15_recent"),
    }
    return (
        "You are a disciplined intraday forex risk manager giving a private second opinion to the "
        "desk owner on ONE trade the system just found. The strategy is trend-following SMC: only "
        "trade WITH the higher-timeframe direction, prefer reactions from fresh demand/supply, and "
        "require clean room to target.\n\n"
        "Judge whether this specific trade is worth taking. Reward: H4 and H1 agreeing with the trade "
        "side; price reacting from a sensible level; >=2R clean room to target; prime session "
        "(London/NY/overlap). Penalize: trading against H4/H1; thin off-hours liquidity; little room "
        "left to target; price already extended.\n\n"
        f"Trade + market facts (JSON):\n{json.dumps(facts, indent=2)}\n\n"
        "Return ONE compact JSON object only, no prose outside it:\n"
        "{\n"
        '  "verdict": "TAKE | CAUTION | SKIP",\n'
        '  "conviction": integer 0-100,\n'
        '  "reason": "one or two sentences, concrete and chart-facing",\n'
        '  "risk_note": "the main thing that would invalidate it, one sentence",\n'
        '  "key_levels": "support/resistance to watch, short"\n'
        "}\n"
    )


def _extract_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _verdict_message(test: dict, ctx: dict, verdict: dict) -> str:
    rank, tier_label = wa._tier(str(test.get("route", "")))
    v = str(verdict.get("verdict", "CAUTION")).upper()
    emoji = VERDICT_EMOJI.get(v, "\u26a0\ufe0f")
    conv = verdict.get("conviction", "?")
    entry = wa._fmt_num(test.get("entry_price"))
    sl = wa._fmt_num(test.get("stop_loss"))
    tp = wa._fmt_num(test.get("trade_target_price")) if _float(test.get("trade_target_price")) is not None else "3R"
    avail = _float(test.get("available_r"))
    session_name, prime = wa._session_quality(test)
    lines = [
        "\U0001f9e0 ANALYST (owner only)",
        f"{test.get('instrument','')} {test.get('route','')} {test.get('side','')} - T{rank} {tier_label}",
        f"Verdict: {v} {emoji} (conviction {conv}/100)",
        f"Plan: entry {entry} | SL {sl} | TP {tp}" + (f" (~{avail:g}R)" if avail else ""),
        f"Session: {session_name}" + (" (prime)" if prime else " (off-hours)"),
        f"Why: {str(verdict.get('reason','')).strip() or 'n/a'}",
        f"Risk: {str(verdict.get('risk_note','')).strip() or 'n/a'}",
    ]
    levels = str(verdict.get("key_levels", "")).strip()
    if levels:
        lines.append(f"Levels: {levels}")
    return "\n".join(lines)


def _is_tradeable(test: dict, min_tier: int) -> bool:
    if not isinstance(test, dict):
        return False
    if test.get("ledger") == "full_target":
        return False
    if str(test.get("status")) not in ("waiting_entry", "active"):
        return False
    if str(test.get("timeframe")) == "M5":
        return False
    rank, _ = wa._tier(str(test.get("route", "")))
    return rank <= min_tier


def _owner() -> str:
    explicit = os.environ.get("PICOTRADE_ANALYST_OWNER", "").strip()
    if explicit:
        return explicit
    recipients = wa._recipients()
    return recipients[0] if recipients else wa.DEFAULT_TO


def _write_md(verdicts: dict, md_path: Path) -> None:
    if not md_path.parent.exists():
        return
    items = [v for v in verdicts.values() if isinstance(v, dict)]
    items.sort(key=lambda x: str(x.get("analyzed_at", "")), reverse=True)
    lines = [
        "# Trade Verdicts (OWNER ONLY)",
        "",
        "Private AI second opinion for the desk owner. Do NOT share these with guests.",
        "",
    ]
    for v in items[:40]:
        lines.append(
            f"- {v.get('analyzed_at','')[:16]} `{v.get('instrument','')}` {v.get('route','')} "
            f"{v.get('side','')}: **{v.get('verdict','')}** ({v.get('conviction','?')}/100) - "
            f"{v.get('reason','')}"
        )
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Owner-only AI second-opinion analyst for new trades.")
    parser.add_argument("--dry-run", action="store_true", help="Print verdicts instead of sending.")
    parser.add_argument("--limit", type=int, default=None, help="Override max verdicts this run.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    gemini = load_gemini_config()
    if gemini is None:
        print("trade_analyst: no Gemini config (.env.gemini); skipping.")
        return 0

    tests = _load_json(FORWARD_TESTS_PATH)
    if not tests:
        print("trade_analyst: no forward tests; nothing to analyze.")
        return 0

    state = _load_json(STATE_PATH)
    analyzed = state.get("analyzed") if isinstance(state.get("analyzed"), dict) else {}
    verdicts = _load_json(VERDICTS_PATH)

    min_tier = int(os.environ.get("PICOTRADE_ANALYST_MIN_TIER", "3"))
    max_sends = args.limit if args.limit is not None else int(os.environ.get("PICOTRADE_ANALYST_MAX", "3"))
    dry_run = args.dry_run or os.environ.get("PICOTRADE_ANALYST_DRY_RUN") == "1"
    owner = _owner()
    md_path = Path(os.path.expanduser(os.environ.get("PICOTRADE_ANALYST_MD", DEFAULT_MD)))

    pending = [
        (key, test)
        for key, test in tests.items()
        if key not in analyzed and _is_tradeable(test, min_tier)
    ]
    pending.sort(key=lambda kv: (wa._tier(str(kv[1].get("route", "")))[0], str(kv[1].get("created_at", ""))))

    client = OandaClient(load_oanda_config())
    sent = 0
    for key, test in pending:
        if sent >= max_sends:
            break
        ctx = _oanda_context(client, str(test.get("instrument", "")))
        try:
            raw = call_gemini_text(gemini, _build_prompt(test, ctx))
        except Exception as exc:
            print(f"trade_analyst: Gemini error for {key}: {exc}")
            continue
        verdict = _extract_json(raw)
        if not verdict.get("verdict"):
            print(f"trade_analyst: unparseable verdict for {key}; skipping.")
            continue
        message = _verdict_message(test, ctx, verdict)
        delivered = True
        if dry_run:
            print("--- DRY RUN ---")
            print(message)
        else:
            delivered = wa._send_one(message, owner)
        if not delivered:
            print(f"trade_analyst: send failed for {key}; will retry next run.")
            continue
        now = datetime.now(timezone.utc).isoformat()
        analyzed[key] = now
        verdicts[key] = {
            "instrument": test.get("instrument"),
            "route": test.get("route"),
            "side": test.get("side"),
            "verdict": str(verdict.get("verdict", "")).upper(),
            "conviction": verdict.get("conviction"),
            "reason": str(verdict.get("reason", "")).strip(),
            "risk_note": str(verdict.get("risk_note", "")).strip(),
            "key_levels": str(verdict.get("key_levels", "")).strip(),
            "analyzed_at": now,
        }
        sent += 1

    state["analyzed"] = analyzed
    _save_json(STATE_PATH, state)
    _save_json(VERDICTS_PATH, verdicts)
    _write_md(verdicts, md_path)
    print(f"trade_analyst: {sent} verdict(s) {'printed' if dry_run else 'sent to owner'}, "
          f"{len(pending)} pending candidate(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

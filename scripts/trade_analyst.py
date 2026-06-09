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
  PICOTRADE_ANALYST_MAX_AGE_MIN  default 60 (only analyze freshly found trades, not the backlog)
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
from fx_annotation.trade_score import alignment, band_stats, conviction, ltf_confirmation, trend_of

import whatsapp_push as wa

FORWARD_TESTS_PATH = PROJECT_ROOT / "outputs" / "forward_tests.json"
STATE_PATH = PROJECT_ROOT / "outputs" / "trade_analyst_state.json"
VERDICTS_PATH = PROJECT_ROOT / "outputs" / "trade_verdicts.json"
DEFAULT_MD = "~/.picoclaw/workspace/memory/TRADE_VERDICTS.md"

VERDICT_EMOJI = {"TAKE": "\u2705", "CAUTION": "\u26a0\ufe0f", "SKIP": "\u274c"}

# M5 micro-validation is backtest-validated on MOMENTUM only (+11pp win when CONFIRMS).
# HTF_MOMENTUM inverts the signal (pullback entry) — do not apply micro there.
MICRO_ROUTE = "MOMENTUM"


def _use_micro(route: str) -> bool:
    return str(route or "").upper() == MICRO_ROUTE


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
    return trend_of(closes)


def _oanda_context(client: OandaClient, instrument: str, *, need_m5: bool = False) -> dict:
    """Compact, robust multi-timeframe snapshot. Never raises."""
    ctx: dict[str, object] = {}
    try:
        h4 = [c for c in client.fetch_candles(instrument, "H4", count=120) if c.complete]
        h1 = [c for c in client.fetch_candles(instrument, "H1", count=180) if c.complete]
        m15 = [c for c in client.fetch_candles(instrument, "M15", count=120) if c.complete]
        m5 = [c for c in client.fetch_candles(instrument, "M5", count=48) if c.complete] if need_m5 else []
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
    if m5:
        ctx["m5_closes"] = [c.close for c in m5[-30:]]
        ctx["m5_opens"] = [c.open for c in m5[-30:]]
    return ctx


def _build_note_prompt(test: dict, ctx: dict, verdict: str) -> str:
    """Gemini is NOT the decision-maker. The deterministic score already decided
    (TAKE/CAUTION). This asks only for a short plain-English note + key levels."""
    side = test.get("side")
    entry = _float(test.get("entry_price"))
    stop = _float(test.get("stop_loss"))
    target = _float(test.get("trade_target_price"))
    risk = abs(entry - stop) if entry is not None and stop is not None else None
    price = ctx.get("price")
    r_to_target = None
    if price is not None and target is not None and risk:
        r_to_target = round((target - price) / risk if side == "BUY" else (price - target) / risk, 2)
    facts = {
        "instrument": test.get("instrument"),
        "route": test.get("route"),
        "side": side,
        "entry": entry,
        "stop_loss": stop,
        "target": target,
        "planned_R": _float(test.get("available_r")),
        "current_price": price,
        "R_left_to_target_from_now": r_to_target,
        "pair_value": test.get("pair_value_label"),
        "h4_trend": ctx.get("h4_trend"),
        "h1_trend": ctx.get("h1_trend"),
        "h4_range": [ctx.get("h4_low"), ctx.get("h4_high")],
        "h1_range": [ctx.get("h1_low"), ctx.get("h1_high")],
        "h1_recent_closes": ctx.get("h1_recent"),
        "m15_recent_closes": ctx.get("m15_recent"),
    }
    return (
        "You are a forex analyst writing a SHORT private note for the desk owner about a trade the "
        f"system already rated **{verdict}** with its own deterministic score. Do NOT re-decide or "
        "give your own verdict. Just add plain-English colour: in one or two sentences say what makes "
        "the trade reasonable (HTF alignment, the level it reacts from, room to target) and the single "
        "biggest risk. Use the facts; do not invent prices.\n\n"
        f"Trade + market facts (JSON):\n{json.dumps(facts, indent=2)}\n\n"
        "Return ONE compact JSON object only:\n"
        "{\n"
        '  "note": "one or two sentences, concrete and chart-facing",\n'
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


def _verdict_message(test: dict, score, note: dict, ctx: dict | None = None) -> str:
    rank, tier_label = wa._tier(str(test.get("route", "")))
    emoji = VERDICT_EMOJI.get(score.verdict, "\u26a0\ufe0f")
    entry = wa._fmt_num(test.get("entry_price"))
    sl = wa._fmt_num(test.get("stop_loss"))
    tp = wa._fmt_num(test.get("trade_target_price")) if _float(test.get("trade_target_price")) is not None else "3R"
    avail = _float(test.get("available_r"))
    time_lines = wa._time_lines(test, warn_stale=True)
    status = str(test.get("status", ""))
    phase = "setup forming" if status == "waiting_entry" else ("entry filled" if status == "active" else status)
    lines = [
        "\U0001f9e0 ANALYST (owner only)",
        f"{test.get('instrument','')} {test.get('route','')} {test.get('side','')} - T{rank} {tier_label}",
        f"Phase: {phase}",
        *time_lines,
        f"Score: {score.score}/100 -> {score.verdict} {emoji}",
        f"Edge: {', '.join(score.reasons)}",
        f"Plan: entry {entry} | SL {sl} | TP {tp}" + (f" (~{avail:g}R)" if avail else ""),
        f"Session: {score.session}" + (" (prime)" if score.prime else " (off-hours)"),
    ]
    if ctx:
        label, al_emoji, detail = alignment(str(test.get("side", "")), ctx.get("h4_trend"), ctx.get("h1_trend"))
        lines.append(f"Confirmation: {label} {al_emoji} ({detail})")
        if _use_micro(str(test.get("route", ""))):
            m5c, m5o = ctx.get("m5_closes"), ctx.get("m5_opens")
            if m5c and m5o:
                ml, me, md = ltf_confirmation(str(test.get("side", "")), m5c, m5o)
                lines.append(f"Micro (M5): {ml} {me} ({md})")
    band, wr, ar, n = band_stats(score.score)
    if n:
        lines.append(f"History (band {band}): ~{wr:.0f}% win, {ar:+.2f}R/trade avg (n={n}, backtest)")
    n = str(note.get("note", "")).strip()
    if n:
        lines.append(f"Note: {n}")
    levels = str(note.get("key_levels", "")).strip()
    if levels:
        lines.append(f"Levels: {levels}")
    return "\n".join(lines)


def _age_minutes(test: dict) -> float | None:
    raw = str(test.get("created_at", "")).strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0


def _is_tradeable(test: dict, min_tier: int, max_age_min: float) -> bool:
    if not isinstance(test, dict):
        return False
    if test.get("ledger") == "full_target":
        return False
    if str(test.get("status")) not in ("waiting_entry", "active"):
        return False
    if str(test.get("timeframe")) == "M5":
        return False
    # Only weigh in on freshly found trades, not the historical backlog of open trades.
    age = _age_minutes(test)
    if age is None or age > max_age_min:
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
        note = str(v.get("note", "")).strip()
        lines.append(
            f"- {str(v.get('analyzed_at',''))[:16]} `{v.get('instrument','')}` {v.get('route','')} "
            f"{v.get('side','')}: **{v.get('verdict','')}** ({v.get('score','?')}/100)"
            + (f" - {note}" if note else f" - {v.get('edge','')}")
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
    max_age_min = float(os.environ.get("PICOTRADE_ANALYST_MAX_AGE_MIN", "60"))
    max_sends = args.limit if args.limit is not None else int(os.environ.get("PICOTRADE_ANALYST_MAX", "3"))
    dry_run = args.dry_run or os.environ.get("PICOTRADE_ANALYST_DRY_RUN") == "1"
    # Which verdicts get pushed to the owner. The deterministic score is the gate; SKIP is recorded
    # silently (queryable) and only TAKE/CAUTION are pushed (and only those call Gemini for a note).
    push_verdicts = {v.strip().upper() for v in os.environ.get("PICOTRADE_ANALYST_PUSH", "TAKE,CAUTION").split(",")}
    owner = _owner()
    md_path = Path(os.path.expanduser(os.environ.get("PICOTRADE_ANALYST_MD", DEFAULT_MD)))

    pending = [
        (key, test)
        for key, test in tests.items()
        if key not in analyzed and _is_tradeable(test, min_tier, max_age_min)
    ]
    def _priority(item: tuple[str, dict]) -> tuple[int, int, str]:
        test = item[1]
        # waiting_entry (setup just spotted) beats active (already filled) so the
        # owner gets the verdict before/at the same time as the NEW alert, not on fill.
        status_pri = 0 if str(test.get("status")) == "waiting_entry" else 1
        rank, _ = wa._tier(str(test.get("route", "")))
        return (status_pri, rank, str(test.get("created_at", "")))

    pending.sort(key=_priority)

    client = OandaClient(load_oanda_config())
    sent = 0
    for key, test in pending:
        # 1) Deterministic, backtested gate decides the verdict (no LLM in the decision).
        score = conviction(
            str(test.get("route", "")),
            str(test.get("instrument", "")),
            str(test.get("signal_time") or test.get("created_at") or ""),
            _float(test.get("available_r")),
            str(test.get("pair_value_tier") or "") or None,
        )
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "instrument": test.get("instrument"),
            "route": test.get("route"),
            "side": test.get("side"),
            "verdict": score.verdict,
            "score": score.score,
            "session": score.session,
            "prime": score.prime,
            "edge": ", ".join(score.reasons),
            "note": "",
            "key_levels": "",
            "analyzed_at": now,
        }

        if score.verdict not in push_verdicts:
            # SKIP (or filtered): record silently, no push, no Gemini spend.
            verdicts[key] = record
            analyzed[key] = now
            continue

        if sent >= max_sends:
            continue  # defer remaining pushes (and their Gemini calls) to the next run

        # 2) Trade passed the gate -> Gemini writes only the plain-English note (optional, best-effort).
        ctx = _oanda_context(client, str(test.get("instrument", "")), need_m5=_use_micro(str(test.get("route", ""))))
        note: dict = {}
        try:
            note = _extract_json(call_gemini_text(gemini, _build_note_prompt(test, ctx, score.verdict)))
        except Exception as exc:
            print(f"trade_analyst: Gemini note error for {key} (sending without note): {exc}")
        record["note"] = str(note.get("note", "")).strip()
        record["key_levels"] = str(note.get("key_levels", "")).strip()
        al_label, _al_emoji, al_detail = alignment(str(test.get("side", "")), ctx.get("h4_trend"), ctx.get("h1_trend"))
        record["alignment"] = al_label
        record["alignment_detail"] = al_detail
        if _use_micro(str(test.get("route", ""))) and ctx.get("m5_closes") and ctx.get("m5_opens"):
            ltf_label, _lt_e, ltf_detail = ltf_confirmation(str(test.get("side", "")), ctx["m5_closes"], ctx["m5_opens"])
            record["micro_m5"] = ltf_label
            record["micro_m5_detail"] = ltf_detail

        message = _verdict_message(test, score, note, ctx)
        if dry_run:
            print("--- DRY RUN ---")
            print(message)
        elif not wa._send_one(message, owner):
            print(f"trade_analyst: send failed for {key}; will retry next run.")
            continue
        verdicts[key] = record
        analyzed[key] = now
        sent += 1

    state["analyzed"] = analyzed
    _save_json(STATE_PATH, state)
    _save_json(VERDICTS_PATH, verdicts)
    _write_md(verdicts, md_path)
    print(f"trade_analyst: {sent} verdict(s) {'printed' if dry_run else 'pushed to owner'}, "
          f"{len(pending)} fresh candidate(s) evaluated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

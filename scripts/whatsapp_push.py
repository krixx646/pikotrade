"""Deterministic, LLM-free WhatsApp push for forward-test signal changes.

Reads outputs/forward_tests.json each run, diffs it against a stored state file,
and pushes a concise WhatsApp message ONLY when a tracked trade changes state
(new setup, entry filled, partial banked, closed). Delivery goes through the
PicoClaw gateway's token-free POST /send endpoint -> no DeepSeek tokens are
spent for routine monitoring. DeepSeek is only used when you chat the bot.

Designed to run as the last step of the 5-minute run_always_on loop. Safe to
remove: delete this file and the one call in run_always_on.run_cycle.

Config (environment variables, all optional):
  PICOTRADE_SEND_URL    default http://localhost:18790/send
  PICOTRADE_WA_CHANNEL  default whatsapp
  PICOTRADE_WA_TO       default 249812612050953@lid
  PICOTRADE_PID_FILE    default ~/.picoclaw/.picoclaw.pid  (gateway auth token)
  PICOTRADE_SEND_TOKEN  explicit bearer token (overrides pid file)
  PICOTRADE_MIN_TIER    default 3  (announce new setups only for tier <= this)
  PICOTRADE_MAX_SENDS   default 8  (per run; extra events deferred to next run)
  PICOTRADE_OPEN_TRADES_MD  default ~/.picoclaw/workspace/memory/OPEN_TRADES.md
                            (concise live snapshot the PicoClaw bot reads to answer
                            "which trades are open?" - only written if its dir exists)
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORWARD_TESTS_PATH = PROJECT_ROOT / "outputs" / "forward_tests.json"
STATE_PATH = PROJECT_ROOT / "outputs" / "whatsapp_push_state.json"

DEFAULT_SEND_URL = "http://localhost:18790/send"
DEFAULT_CHANNEL = "whatsapp"
DEFAULT_TO = "249812612050953@lid"
DEFAULT_PID_FILE = "~/.picoclaw/.picoclaw.pid"
DEFAULT_OPEN_TRADES_MD = "~/.picoclaw/workspace/memory/OPEN_TRADES.md"


def _tier(route: str) -> tuple[int, str]:
    """Mirror of forward_testing._route_tier so alerts carry the same priority."""
    r = str(route or "").upper()
    if r.startswith("MOMENTUM"):
        return (1, "PREMIUM")
    if r.startswith("M15"):
        return (2, "HIGH")
    if r.startswith("DYNAMIC"):
        return (3, "MEDIUM")
    if r.startswith("REGIME") or r.startswith("RULE"):
        return (4, "LOW")
    return (5, "WATCH")


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _fingerprint(test: dict) -> dict:
    return {
        "status": str(test.get("status", "")),
        "partial_taken": bool(test.get("partial_taken")),
        "outcome": str(test.get("outcome", "")),
    }


def _resolve_token() -> str:
    explicit = os.environ.get("PICOTRADE_SEND_TOKEN", "").strip()
    if explicit:
        return explicit
    pid_path = Path(os.path.expanduser(os.environ.get("PICOTRADE_PID_FILE", DEFAULT_PID_FILE)))
    data = _load_json(pid_path)
    return str(data.get("token", "")).strip()


def _send(text: str) -> bool:
    url = os.environ.get("PICOTRADE_SEND_URL", DEFAULT_SEND_URL)
    payload = {
        "channel": os.environ.get("PICOTRADE_WA_CHANNEL", DEFAULT_CHANNEL),
        "to": os.environ.get("PICOTRADE_WA_TO", DEFAULT_TO),
        "text": text,
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    token = _resolve_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = request.Request(url, data=body, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=10) as response:
            response.read()
            return 200 <= response.status < 300
    except error.HTTPError as exc:
        print(f"whatsapp_push: send failed HTTP {exc.code}: {exc.read()[:200]!r}")
        return False
    except (error.URLError, OSError) as exc:
        print(f"whatsapp_push: send failed: {exc}")
        return False


def _fmt_num(value: object) -> str:
    number = _float_or_none(value)
    if number is None:
        return str(value if value not in (None, "") else "?")
    text = f"{number:.5f}".rstrip("0").rstrip(".")
    return text or "0"


def _float_or_none(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _trim(text: str, limit: int = 140) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def _plan_block(test: dict) -> list[str]:
    """The full dual-timeframe trade plan, mirroring the OpenClaw forward-test report:
    entry zone, M15 plan, tighter M5 plan, and the trailing-TP milestones."""
    entry = _fmt_num(test.get("entry_price"))
    sl = _fmt_num(test.get("stop_loss"))
    entry_low = test.get("entry_low")
    entry_high = test.get("entry_high")
    target = _float_or_none(test.get("trade_target_price"))
    tf = test.get("trade_target_timeframe", "")
    m15_rr = test.get("m15_rr_to_target")
    m5_sl = test.get("m5_stop_loss")
    m5_rr = test.get("m5_rr_to_target")
    trail = test.get("trail_levels") if isinstance(test.get("trail_levels"), dict) else {}

    tp_text = f"{_fmt_num(target)} ({tf})" if target is not None else "trail / open-ended"
    is_m5_variant = str(test.get("timeframe")) == "M5" or str(test.get("entry_model")) == "m5_midzone"
    lines: list[str] = []
    if entry_low is not None and entry_high is not None:
        lines.append(f"Zone: {_fmt_num(entry_low)} - {_fmt_num(entry_high)}")
    if is_m5_variant:
        # This row IS the M5 trade: deeper mid-zone entry, wide M15 structural stop.
        lines.append(
            f"M5 entry (mid-zone): {entry} | SL {sl} (M15 structure) | TP {tp_text}"
            + (f" | ~{m15_rr}R" if m15_rr is not None else "")
            + " (better entry, more R)"
        )
    else:
        lines.append(
            f"M15: entry {entry} | SL {sl} | TP {tp_text}"
            + (f" | ~{m15_rr}R" if m15_rr is not None else "")
        )
        if m5_sl is not None:
            lines.append(
                f"M5:  entry {entry} | tighter SL {_fmt_num(m5_sl)}"
                + (f" | ~{m5_rr}R to same TP" if m5_rr is not None else "")
                + " (more R, smaller stop)"
            )
    if trail:
        partial = trail.get("partial_1_5R")
        milestone = trail.get("milestone_3R")
        lines.append(
            f"Trail: partial @ {_fmt_num(partial)} (1.5R), 3R @ {_fmt_num(milestone)}, "
            "then trail 1R behind peak (uncapped)"
        )
    pair = str(test.get("pair_value_label", "")).strip()
    if pair:
        lines.append(f"Pair: {pair}")
    return lines


def _new_signal_message(test: dict, tier_label: str, rank: int) -> str:
    head = (
        f"[NEW][T{rank} {tier_label}] {test.get('instrument', '')} {test.get('route', '')} "
        f"{test.get('side', '')} - setup forming"
    )
    lines = [head, *_plan_block(test)]
    notes = str(test.get("notes", "")).strip()
    if notes:
        lines.append("note: " + _trim(notes))
    return "\n".join(lines)


def _entry_message(test: dict, tier_label: str, rank: int) -> str:
    head = (
        f"[FILLED][T{rank} {tier_label}] {test.get('instrument', '')} {test.get('route', '')} "
        f"{test.get('side', '')} - entry filled @ {_fmt_num(test.get('entry_price'))}"
    )
    lines = [head, *_plan_block(test), "Manage: bank ~50% at 1.5R, move SL to breakeven, trail the runner."]
    return "\n".join(lines)


def _partial_message(test: dict, tier_label: str, rank: int) -> str:
    return (
        f"[PARTIAL][T{rank} {tier_label}] {test.get('instrument', '')} {test.get('route', '')} "
        f"{test.get('side', '')} - banked ~50% at 1.5R, SL -> breakeven, runner trailing"
    )


def _closed_message(test: dict, tier_label: str, rank: int) -> str:
    realized = _float_or_none(test.get("realized_r"))
    realized_text = f"{realized:+.2f}R" if realized is not None else "n/a"
    tag = "WIN" if (realized is not None and realized > 0) else "LOSS"
    return (
        f"[{tag}][T{rank} {tier_label}] {test.get('instrument', '')} {test.get('route', '')} "
        f"{test.get('side', '')} - closed {test.get('outcome', '')}, realized {realized_text}"
    )


def _write_open_trades_md(tests: dict) -> None:
    """Write a concise live snapshot the PicoClaw bot can read on demand to answer
    "which trades are open?". Only writes where PicoClaw's memory dir exists (the box)."""
    path = Path(os.path.expanduser(os.environ.get("PICOTRADE_OPEN_TRADES_MD", DEFAULT_OPEN_TRADES_MD)))
    if not path.parent.exists():
        return

    open_tests: list[tuple[int, str, dict]] = []
    closed: list[dict] = []
    for test in tests.values():
        if not isinstance(test, dict):
            continue
        if test.get("status") == "closed":
            closed.append(test)
        else:
            rank, label = _tier(test.get("route", ""))
            open_tests.append((rank, label, test))

    realized = [r for r in (_float_or_none(t.get("realized_r")) for t in closed) if r is not None]
    n = len(realized)
    wins = sum(1 for r in realized if r > 0)
    total = sum(realized)

    lines = [
        "# PikoTrade - Live Open Trades",
        "",
        f"_Auto-refreshed every ~5 minutes. Last update: {datetime.now(timezone.utc).isoformat()}_",
        "",
        "## Summary",
        f"- Open now: {len(open_tests)}",
    ]
    if n:
        lines.append(
            f"- Closed (all-time): {n} | win rate {100 * wins / n:.0f}% | "
            f"expectancy {total / n:+.2f}R | total {total:+.2f}R"
        )
    else:
        lines.append("- Closed (all-time): 0")
    lines += ["", "## Open trades (priority order)", ""]
    if not open_tests:
        lines.append("None right now.")
    else:
        for rank, label, test in sorted(open_tests, key=lambda x: (x[0], str(x[2].get("instrument", "")))):
            lines.append(
                f"- [T{rank} {label}] {test.get('instrument', '')} {test.get('route', '')} "
                f"{test.get('side', '')} - {test.get('status', '')}"
            )
            for plan_line in _plan_block(test):
                lines.append(f"  - {plan_line}")

    lines += ["", "## Recent closes", ""]
    recent = sorted(closed, key=lambda t: str(t.get("exit_time", "")))[-8:]
    if not recent:
        lines.append("None yet.")
    for test in recent:
        realized_r = _float_or_none(test.get("realized_r"))
        realized_text = f"{realized_r:+.2f}R" if realized_r is not None else "n/a"
        lines.append(
            f"- {test.get('instrument', '')} {test.get('route', '')} {test.get('side', '')} - "
            f"{test.get('outcome', '')} {realized_text}"
        )

    try:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"whatsapp_push: could not write open-trades snapshot: {exc}")


def main() -> int:
    min_tier = int(os.environ.get("PICOTRADE_MIN_TIER", "3") or "3")
    max_sends = int(os.environ.get("PICOTRADE_MAX_SENDS", "8") or "8")

    tests = _load_json(FORWARD_TESTS_PATH)
    _write_open_trades_md(tests)

    # Cold start: no state file yet. Seed from the current trades WITHOUT sending,
    # so we never flood you with a backlog of pre-existing signals on first deploy.
    # Future state changes on tradeable-tier trades will then alert normally.
    if not STATE_PATH.exists():
        seeded = {
            key: {**_fingerprint(test), "announced": _tier(test.get("route", ""))[0] <= min_tier}
            for key, test in tests.items()
            if isinstance(test, dict)
        }
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(
            json.dumps({"tests": seeded}, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(f"whatsapp_push: cold start, seeded {len(seeded)} trade(s) silently (no sends).")
        return 0

    state = _load_json(STATE_PATH)
    tracked = state.get("tests") if isinstance(state.get("tests"), dict) else {}

    new_tracked: dict[str, dict] = {}
    sends = 0

    for key, test in tests.items():
        if not isinstance(test, dict):
            continue
        rank, label = _tier(test.get("route", ""))
        cur = _fingerprint(test)
        prev = tracked.get(key) if isinstance(tracked.get(key), dict) else None

        messages: list[str] = []
        announced_after = False
        if prev is None:
            if rank > min_tier:
                # Not a tradeable tier: track silently so we never alert on it.
                new_tracked[key] = {**cur, "announced": False}
                continue
            if cur["status"] == "waiting_entry":
                messages.append(_new_signal_message(test, label, rank))
            elif cur["status"] == "active":
                messages.append(_entry_message(test, label, rank))
            elif cur["status"] == "closed":
                messages.append(_closed_message(test, label, rank))
            announced_after = True
        else:
            announced_after = bool(prev.get("announced"))
            if announced_after:
                if cur["status"] != prev.get("status"):
                    if cur["status"] == "active":
                        messages.append(_entry_message(test, label, rank))
                    elif cur["status"] == "closed":
                        messages.append(_closed_message(test, label, rank))
                if cur["partial_taken"] and not prev.get("partial_taken"):
                    messages.append(_partial_message(test, label, rank))

        # Deliver. Only advance this trade's fingerprint when every message for
        # it was sent, so a transient failure is re-detected and retried next run.
        all_sent = True
        for message in messages:
            if sends >= max_sends:
                all_sent = False
                break
            if _send(message):
                sends += 1
                print(f"whatsapp_push: sent -> {message.splitlines()[0]}")
            else:
                all_sent = False
                break

        if all_sent:
            new_tracked[key] = {**cur, "announced": announced_after}
        elif prev is not None:
            # Keep the old fingerprint so the change is re-detected next run.
            new_tracked[key] = prev
        # else: brand-new key whose send failed -> omit so it's first-seen again.

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps({"tests": new_tracked}, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(f"whatsapp_push: {sends} message(s) sent, {len(new_tracked)} trade(s) tracked.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # never break the trading loop
        print(f"whatsapp_push: unexpected error: {exc}")
        raise SystemExit(0)

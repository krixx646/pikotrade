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
  PICOTRADE_MIN_TIER    default 5  (announce new setups only for tier <= this;
                        tiers: 1 HTF-MOMENTUM, 2 HTF-ZONE, 3 PREMIUM/MOMENTUM,
                        4 HIGH/M15, 5 MEDIUM/DYNAMIC, 6 LOW, 7 WATCH)
  PICOTRADE_MAX_SENDS   default 8  (per run; extra events deferred to next run)
  PICOTRADE_OPEN_TRADES_MD  default ~/.picoclaw/workspace/memory/OPEN_TRADES.md
                            (concise live snapshot the PicoClaw bot reads to answer
                            "which trades are open?" - only written if its dir exists)
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
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
    """Mirror of forward_testing._route_tier so alerts carry the same priority.

    Each HTF day-trade strategy is its own tier (1, 2); the legacy routes follow.
    """
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
    if r.startswith("REGIME") or r.startswith("RULE"):
        return (6, "LOW")
    return (7, "WATCH")


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


def _recipients() -> list[str]:
    """Recipient list. The FIRST entry is the primary (owner) and gates dedup; any
    additional entries are best-effort read-only signal guests. Override with the
    PICOTRADE_WA_TO env var (comma-separated) if needed."""
    env = os.environ.get("PICOTRADE_WA_TO", "").strip()
    if env:
        return [r.strip() for r in env.split(",") if r.strip()]
    return [
        DEFAULT_TO,                      # owner (admin) - primary, gates dedup
        "2348146310043@s.whatsapp.net",  # guest: read-only signal recipient (no command access)
    ]


def _send_one(text: str, to: str) -> bool:
    url = os.environ.get("PICOTRADE_SEND_URL", DEFAULT_SEND_URL)
    payload = {
        "channel": os.environ.get("PICOTRADE_WA_CHANNEL", DEFAULT_CHANNEL),
        "to": to,
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
        print(f"whatsapp_push: send to {to} failed HTTP {exc.code}: {exc.read()[:200]!r}")
        return False
    except (error.URLError, OSError) as exc:
        print(f"whatsapp_push: send to {to} failed: {exc}")
        return False


def _send(text: str) -> bool:
    """Deliver to all recipients. Only the primary (owner) gates the return value /
    dedup state, so a failing guest recipient can never cause duplicate owner alerts."""
    recipients = _recipients()
    if not recipients:
        return False
    primary_ok = False
    for idx, to in enumerate(recipients):
        ok = _send_one(text, to)
        if idx == 0:
            primary_ok = ok
        elif not ok:
            print(f"whatsapp_push: guest recipient {to} did not receive (best-effort)")
    return primary_ok


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


# Distinct marker emoji per tier so the two HTF day-trade routes are spottable at a glance.
TIER_EMOJI = {1: "\U0001F680", 2: "\U0001F3AF"}  # 1 HTF-MOMENTUM rocket, 2 HTF-ZONE target


def _route_emoji(rank: int) -> str:
    return TIER_EMOJI.get(rank, "")


def _parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _signal_dt(test: dict) -> datetime | None:
    """When the setup was found (prefer signal_time, else created_at)."""
    return _parse_dt(test.get("signal_time") or test.get("created_at"))


def _time_lines(test: dict, *, warn_stale: bool = False) -> list[str]:
    """Human-readable signal time on every alert. Optionally flag stale setups."""
    dt = _signal_dt(test)
    if dt is None:
        return []
    wat = dt.astimezone(ZoneInfo("Africa/Lagos"))
    lines = [f"Time: {dt.strftime('%d %b %Y %H:%M')} UTC ({wat.strftime('%H:%M')} WAT)"]
    if warn_stale:
        age_min = (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
        limit = float(os.environ.get("PICOTRADE_STALE_MIN", "20") or "20")
        if age_min > limit:
            lines.append(
                f"\u26a0\ufe0f STALE ({int(age_min)} min old) - verify price before entry"
            )
    return lines


def _session_quality(test: dict) -> tuple[str, bool]:
    """Classify the signal time by FX session (UTC). London/New York and their
    overlap are the high-liquidity windows; everything else is flagged low quality.

    Windows (UTC, summer/DST-aligned): London 07-16, New York 12-21,
    overlap 12-16 (the golden window). Off-hours = Asian / late-US drift.
    """
    dt = _parse_dt(test.get("entry_time") or test.get("signal_time") or test.get("created_at"))
    if dt is None:
        return ("unknown", True)
    h = dt.hour
    if 12 <= h < 16:
        return ("London/New York overlap", True)
    if 7 <= h < 12:
        return ("London", True)
    if 16 <= h < 21:
        return ("New York", True)
    return ("off-hours (Asian/late-US)", False)


def _confidence(test: dict, rank: int) -> tuple[int, str, str]:
    """A route-agnostic 0-100 confidence blend: strategy edge (tier) + session
    quality + reward (available R) + pair-value tier. Returns (score, label, emoji)."""
    score = {1: 35, 2: 32, 3: 30, 4: 25, 5: 20, 6: 12, 7: 8}.get(rank, 8)

    session_name, _ok = _session_quality(test)
    if session_name == "London/New York overlap":
        score += 30
    elif session_name in ("London", "New York"):
        score += 22
    elif session_name == "unknown":
        score += 15

    available_r = _float_or_none(test.get("available_r"))
    if available_r is None:
        score += 8
    elif available_r >= 3:
        score += 20
    elif available_r >= 2:
        score += 14
    elif available_r >= 1.5:
        score += 8
    else:
        score += 3

    pair_tier = str(test.get("pair_value_tier", "")).lower()
    if pair_tier in ("high_value", "core", "high"):
        score += 15
    elif pair_tier in ("low_value", "low"):
        score += 0
    else:
        score += 8

    score = max(0, min(100, score))
    if score >= 75:
        return (score, "High", "\U0001F7E2")  # green
    if score >= 55:
        return (score, "Medium", "\U0001F7E1")  # yellow
    return (score, "Low", "\U0001F534")  # red


def _decision_lines(test: dict, rank: int) -> list[str]:
    """Confidence + session quality lines shown on actionable (NEW/FILLED) alerts."""
    score, label, emoji = _confidence(test, rank)
    session_name, ok = _session_quality(test)
    lines = [f"Confidence: {score}/100 ({label} {emoji})"]
    if ok:
        lines.append(f"Session: {session_name} \u2705")
    else:
        lines.append(
            f"Session: {session_name} \u274C LOW-QUALITY TIME (outside London/NY) "
            "- excluded from headline stats"
        )
    return lines


def _head_prefix(test: dict, rank: int) -> str:
    """Leading markers for an alert head: a cross for off-hours (bad-session) trades,
    then the route emoji (rocket/target for the HTF day-trade routes)."""
    parts: list[str] = []
    _name, ok = _session_quality(test)
    if not ok:
        parts.append("\u274C")  # off-session / low-quality time
    marker = _route_emoji(rank)
    if marker:
        parts.append(marker)
    return (" ".join(parts) + " ") if parts else ""


PRIME_SESSIONS = ("London/New York overlap", "London", "New York")
# A trade is only a real WIN/LOSS if it closes beyond this band. Inside +-SCRATCH_R it is a
# scratch (typically a timeout that marked a hair off breakeven) - not a "win" worth celebrating.
SCRATCH_R = 0.25


def _session_groups(closed: list[dict]) -> dict[str, list[float]]:
    """Realized-R lists keyed by FX-session name, for closed trades only."""
    groups: dict[str, list[float]] = {}
    for test in closed:
        realized = _float_or_none(test.get("realized_r"))
        if realized is None:
            continue
        name, _ok = _session_quality(test)
        groups.setdefault(name, []).append(realized)
    return groups


def _prime_values(groups: dict[str, list[float]]) -> list[float]:
    return [v for name in PRIME_SESSIONS for v in groups.get(name, [])]


def _stat_str(values: list[float]) -> str:
    n = len(values)
    if n == 0:
        return "0 trades"
    wins = sum(1 for v in values if v > SCRATCH_R)
    scratch = sum(1 for v in values if -SCRATCH_R <= v <= SCRATCH_R)
    total = sum(values)
    scratch_text = f" | {scratch} scratch" if scratch else ""
    return (
        f"{n} trades | win {100 * wins / n:.0f}% | exp {total / n:+.2f}R | "
        f"total {total:+.2f}R{scratch_text}"
    )


def _session_breakdown(closed: list[dict]) -> list[str]:
    """Segment closed trades by FX session so performance in prime hours
    (London / New York / overlap) is reported separately from off-hours (Asian/late-US)."""
    groups = _session_groups(closed)
    _stat = _stat_str
    prime = _prime_values(groups)
    off_hours = groups.get("off-hours (Asian/late-US)", [])
    unknown = groups.get("unknown", [])

    lines = [
        "## Performance by session (closed trades)",
        f"- PRIME (London/NY/overlap): {_stat(prime)}",
        f"  - overlap: {_stat(groups.get('London/New York overlap', []))}",
        f"  - London: {_stat(groups.get('London', []))}",
        f"  - New York: {_stat(groups.get('New York', []))}",
        f"- OFF-HOURS (Asian/late-US): {_stat(off_hours)}",
    ]
    if unknown:
        lines.append(f"- (untimed): {_stat(unknown)}")
    return lines


def _rr_price(test: dict, r_mult: float) -> float | None:
    """Price at r_mult R from entry, on the profit side (for a concrete TP)."""
    entry = _float_or_none(test.get("entry_price"))
    stop = _float_or_none(test.get("stop_loss"))
    side = str(test.get("side", "")).upper()
    if entry is None or stop is None:
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    return entry + r_mult * risk if side == "BUY" else entry - r_mult * risk


def _order_type(test: dict) -> str:
    """Classify the pending order so the user can log it: buy/sell limit vs stop vs market.

    limit  = entry sits beyond current price in the pullback direction (wait for a retrace).
    stop   = entry is through current price in the trade direction (breakout/continuation).
    market = entry is essentially at the current price.
    Uses ref_price (market price when the signal formed). Falls back to LIMIT, since these
    are predominantly pullback-into-zone entries, when ref_price is absent (older trades)."""
    side = str(test.get("side", "")).upper()
    entry = _float_or_none(test.get("entry_price"))
    ref = _float_or_none(test.get("ref_price"))
    stop = _float_or_none(test.get("stop_loss"))
    if side not in ("BUY", "SELL") or entry is None:
        return side or "?"
    if ref is None:
        return f"{side} LIMIT"
    tol = abs(entry - stop) * 0.05 if stop is not None else 0.0
    if side == "BUY":
        if entry < ref - tol:
            return "BUY LIMIT"
        if entry > ref + tol:
            return "BUY STOP"
        return "BUY (market now)"
    if entry > ref + tol:
        return "SELL LIMIT"
    if entry < ref - tol:
        return "SELL STOP"
    return "SELL (market now)"


def _tp_line(test: dict) -> str:
    """A concrete take-profit the user can set if trading the alert manually.

    Prefers the structural target; otherwise a fixed target at the route's available R
    (default 3R). Always a real price, never "open-ended"."""
    target = _float_or_none(test.get("trade_target_price"))
    tf = str(test.get("trade_target_timeframe", "")).strip()
    m15_rr = test.get("m15_rr_to_target")
    if target is not None:
        rtxt = f" ~{m15_rr}R" if m15_rr is not None else ""
        ttxt = f" ({tf})" if tf and tf != "fixed" else ""
        return f"TP: {_fmt_num(target)}{ttxt}{rtxt}"
    avail = _float_or_none(test.get("available_r")) or 3.0
    tp = _rr_price(test, avail)
    return f"TP: {_fmt_num(tp)} (~{avail:g}R)" if tp is not None else "TP: (set at 3R)"


def _plan_block(test: dict) -> list[str]:
    """Actionable order ticket for the user: order type, entry, SL, a concrete TP, plus the
    system's own bank-and-trail management and the tighter M5 option when present."""
    entry = _fmt_num(test.get("entry_price"))
    sl = _fmt_num(test.get("stop_loss"))
    entry_low = test.get("entry_low")
    entry_high = test.get("entry_high")
    m5_sl = test.get("m5_stop_loss")
    m5_rr = test.get("m5_rr_to_target")
    trail = test.get("trail_levels") if isinstance(test.get("trail_levels"), dict) else {}
    order = _order_type(test)
    is_m5_variant = str(test.get("timeframe")) == "M5" or str(test.get("entry_model")) == "m5_midzone"

    lines: list[str] = []
    if entry_low is not None and entry_high is not None:
        lines.append(f"Zone: {_fmt_num(entry_low)} - {_fmt_num(entry_high)}")
    if is_m5_variant:
        lines.append(f"Order: {order} @ {entry} (M5 mid-zone, M15 structural stop)")
    else:
        lines.append(f"Order: {order} @ {entry}")
    lines.append(f"SL: {sl}")
    lines.append(_tp_line(test))
    if not is_m5_variant and m5_sl is not None:
        lines.append(
            f"M5 option: tighter SL {_fmt_num(m5_sl)}"
            + (f" (~{m5_rr}R to TP)" if m5_rr is not None else "")
            + " - smaller stop, more R"
        )
    if str(test.get("exit_model")) == "ride_target":
        lines.append("System manages it as: ride 100% to TP, M15 structural stop (no partial)")
    elif trail:
        partial = trail.get("partial_price", trail.get("partial_1_5R"))
        partial_r = trail.get("partial_r", 2.0)
        lines.append(
            f"System manages it as: bank ~50% @ {_fmt_num(partial)} ({partial_r:g}R), "
            "then trail 1R behind peak"
        )
    pair = str(test.get("pair_value_label", "")).strip()
    if pair:
        lines.append(f"Pair: {pair}")
    return lines


def _new_signal_message(test: dict, tier_label: str, rank: int) -> str:
    prefix = _head_prefix(test, rank)
    head = (
        f"{prefix}[NEW][T{rank} {tier_label}] {test.get('instrument', '')} {test.get('route', '')} "
        f"{test.get('side', '')} - setup forming"
    )
    lines = [head, *_time_lines(test, warn_stale=True), *_decision_lines(test, rank), *_plan_block(test)]
    notes = str(test.get("notes", "")).strip()
    if notes:
        lines.append("note: " + _trim(notes))
    return "\n".join(lines)


def _entry_message(test: dict, tier_label: str, rank: int) -> str:
    prefix = _head_prefix(test, rank)
    head = (
        f"{prefix}[FILLED][T{rank} {tier_label}] {test.get('instrument', '')} {test.get('route', '')} "
        f"{test.get('side', '')} - entry filled @ {_fmt_num(test.get('entry_price'))}"
    )
    lines = [
        head,
        *_time_lines(test, warn_stale=True),
        *_decision_lines(test, rank),
        *_plan_block(test),
    ]
    return "\n".join(lines)


def _partial_message(test: dict, tier_label: str, rank: int) -> str:
    prefix = _head_prefix(test, rank)
    head = (
        f"{prefix}[PARTIAL][T{rank} {tier_label}] {test.get('instrument', '')} {test.get('route', '')} "
        f"{test.get('side', '')} - banked ~50% at 2R, SL -> breakeven, runner trailing"
    )
    return "\n".join([head, *_time_lines(test), *_decision_lines(test, rank)])


def _closed_message(test: dict, tier_label: str, rank: int) -> str:
    prefix = _head_prefix(test, rank)
    realized = _float_or_none(test.get("realized_r"))
    realized_text = f"{realized:+.2f}R" if realized is not None else "n/a"
    if realized is None:
        tag = "CLOSED"
    elif realized > SCRATCH_R:
        tag = "WIN"
    elif realized < -SCRATCH_R:
        tag = "LOSS"
    else:
        tag = "SCRATCH"
    head = (
        f"{prefix}[{tag}][T{rank} {tier_label}] {test.get('instrument', '')} {test.get('route', '')} "
        f"{test.get('side', '')} - closed {test.get('outcome', '')}, realized {realized_text}"
    )
    return "\n".join([head, *_time_lines(test), *_decision_lines(test, rank)])


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

    groups = _session_groups(closed)
    prime = _prime_values(groups)

    lines = [
        "# PikoTrade - Live Open Trades",
        "",
        f"_Auto-refreshed every ~5 minutes. Last update: {datetime.now(timezone.utc).isoformat()}_",
        "",
        "## Summary (PRIME sessions only - London/NY/overlap)",
        f"- Open now: {len(open_tests)}",
        f"- Closed (PRIME sessions): {_stat_str(prime)}",
        "- NOTE: off-hours (Asian/late-US) trades are EXCLUDED from this headline because "
        "off-session liquidity is thin and has been net-negative. They are still tracked - "
        "see 'Performance by session' below, or ask explicitly for off-hours results.",
    ]
    lines += ["", *_session_breakdown(closed)]
    lines += ["", "## Open trades (priority order)", ""]
    if not open_tests:
        lines.append("None right now.")
    else:
        for rank, label, test in sorted(open_tests, key=lambda x: (x[0], str(x[2].get("instrument", "")))):
            prefix = _head_prefix(test, rank)
            score, conf_label, conf_emoji = _confidence(test, rank)
            session_name, session_ok = _session_quality(test)
            session_text = (
                f"{session_name}" if session_ok else f"{session_name} (LOW-QUALITY TIME)"
            )
            lines.append(
                f"- {prefix}[T{rank} {label}] {test.get('instrument', '')} {test.get('route', '')} "
                f"{test.get('side', '')} - {test.get('status', '')}"
            )
            lines.append(
                f"  - Confidence: {score}/100 ({conf_label} {conf_emoji}) | Session: {session_text}"
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
        session_name, session_ok = _session_quality(test)
        session_tag = session_name if session_ok else f"{session_name} [off-hours]"
        lines.append(
            f"- {test.get('instrument', '')} {test.get('route', '')} {test.get('side', '')} - "
            f"{test.get('outcome', '')} {realized_text} | {session_tag}"
        )

    try:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"whatsapp_push: could not write open-trades snapshot: {exc}")


def main() -> int:
    min_tier = int(os.environ.get("PICOTRADE_MIN_TIER", "5") or "5")
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

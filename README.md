# PikoTrade — Multi-Route Forex Signal & Forward-Testing Agent

PikoTrade is an autonomous, **paper-trading** forex signal engine. It scans a watchlist of
FX / metal / crypto instruments on multiple timeframes (H4 / H1 / M15 / M5) using OANDA data,
generates trade setups from several independent **strategy routes**, forward-tests every signal
with a realistic exit model, and pushes state-change alerts to **WhatsApp** (via a PicoClaw
gateway).

> **It places no real broker orders.** There is no exchange connection — this is signal
> generation, trade-plan alerting, and forward-test bookkeeping only.

---

## Table of contents
- [What it does](#what-it-does)
- [Strategy routes & alert tiers](#strategy-routes--alert-tiers)
- [Exit models](#exit-models)
- [Session quality & confidence scoring](#session-quality--confidence-scoring)
- [Per-route max hold (timeouts)](#per-route-max-hold-timeouts)
- [Architecture & run cycle](#architecture--run-cycle)
- [Project layout](#project-layout)
- [Setup](#setup)
- [Running it](#running-it)
- [Backtesting](#backtesting)
- [WhatsApp alerts & PicoClaw](#whatsapp-alerts--picoclaw)
- [Outputs](#outputs)
- [Deployment (Oracle Cloud)](#deployment-oracle-cloud)
- [Disclaimer](#disclaimer)

---

## What it does

Every cycle (default 5 minutes) the agent:

1. Pulls candles from OANDA for the watchlist (default 10 instruments — see below).
2. Runs each enabled **strategy route** to look for fresh setups.
3. Opens a forward-test record for any new signal (entry zone, stop, target / trail, planned R).
4. Updates open forward-tests against new price action — partial, breakeven, trail, target, stop, or timeout.
5. Writes reports (`outputs/forward_tests.md`, `forward_tests.json`) and a concise live snapshot.
6. Pushes a **WhatsApp message only when a tracked trade changes state** (new / filled / partial / win / loss).

Default watchlist (`DEFAULT_WATCHLIST` in `src/fx_annotation/forward_testing.py`):

```
EUR_USD  GBP_USD  USD_JPY  USD_CAD  AUD_USD
NZD_USD  EUR_JPY  GBP_JPY  XAU_USD  BTC_USD
```

---

## Strategy routes & alert tiers

Each route is **independent** — its own detector module, its own enable flag, its own scoreboard
row. The **tier** is just the alert priority (lower = higher priority / stronger measured edge).

| Tier | Label | Route | Idea |
|------|-------|-------|------|
| T1 | `HTF-MOMENTUM` | `HTF_MOMENTUM` | H1 impulse/continuation → **M15 entry**, M15 structural stop, **rides 100% to a fixed H1 target** (day-trade). |
| T2 | `HTF-ZONE` | `HTF_ZONE` | H4 bias + H1 SMC zone reaction → M15 entry, zone-edge stop, trailing (day-trade, rare/high-R). |
| T3 | `PREMIUM` | `MOMENTUM` | M15 impulse-continuation (best M15-only edge). |
| T4 | `HIGH` | `M15_SIMPLE` | Clean M15 structure: sweep → BOS → base zone. |
| T5 | `MEDIUM` | `DYNAMIC_SCORE` | Weighted multi-factor score (spread-sensitive). |
| T6 | `LOW` | `REGIME_RANGE`, relaxed `Rule`, `RULE_STALE_BOS` | Range fades / strict-rule variants — confirmation only. |
| T7 | `WATCH` | `GEMMA_*` / `DEEPSEEK_*` AI routes, `*_OPPORTUNITY` | Observe, don't trade blindly. |

Most routes (except the base M15 setup) also run an **M5 entry variant** (route name suffixed
`_M5`): a mid-zone entry that keeps the wide M15 structural stop, aiming for more R on the same
idea. It is a tracking variant of the same strategy, not an extra route.

Routes A/B detection modules: `src/fx_annotation/htf_momentum.py`, `src/fx_annotation/htf_zone.py`
(the latter uses a guarded import so it is delete-safe).

---

## Exit models

Exit handling is **not uniform** — it depends on the route:

- **Most routes (`partial_trail`)**: bank ~50% at **1.5R**, move stop to **breakeven**, then
  **trail the runner 1R behind its peak** (uncapped).
- **`HTF_MOMENTUM` (`ride_target`)**: ride the full position to a fixed H1 target with an M15
  structural stop — **no partial**. Win = target reached (planned R); loss = M15 stop (−1R).
- **`HTF_ZONE`**: partial-then-trail from the H1 zone.

"Realized R" is the blended result of a closed trade; **expectancy** is the average realized R.

---

## Session quality & confidence scoring

Alerts and the live snapshot are annotated to help decide quickly (`scripts/whatsapp_push.py`):

- **Emoji markers**: 🚀 `HTF_MOMENTUM` (T1), 🎯 `HTF_ZONE` (T2). Off-session trades are prefixed
  with ❌.
- **Confidence: N/100 (Low / Medium / High 🔴🟡🟢)** — a route-agnostic blend of strategy edge
  (tier) + session quality + reward (available R) + pair-value tier.
- **Session quality (UTC, DST-aligned)**: London `07–16`, New York `12–21`, and their **overlap
  `12–16`** are the high-liquidity windows. Anything else (Asian / late-US) is flagged
  **LOW-QUALITY TIME** and **excluded from the headline win/loss stats** (tracked separately,
  shown on request). See the "Performance by session" block in `OPEN_TRADES.md`.

---

## Per-route max hold (timeouts)

A stalled trade that never hits its partial or stop is force-closed at the current mark
(outcome `timeout`). The cap is **per route** (`_route_timeout_bars`, in M15 bars; M5 variants ×3):

- Intraday routes (`MOMENTUM`, `M15_SIMPLE`, `DYNAMIC_SCORE`, `REGIME_RANGE`, `Rule`): **~5h** (20 bars).
- HTF day-trade routes (`HTF_MOMENTUM`, `HTF_ZONE`): **~20h** (80 bars) to reach their H1 target.
- AI / other: 12h default.

---

## Architecture & run cycle

`scripts/run_always_on.py` is the managed loop. Each cycle (`run_cycle`) runs, in order:

1. `scripts/fetch_fundamentals.py` — refresh the fundamentals brief.
2. `scripts/live_monitor.py` — multi-timeframe scan + live memory / revisit scheduling
   (optionally `--use-gemma` for the AI reviewer).
3. `scripts/deliver_alerts.py` — emit alerts on the chosen channel.
4. `scripts/forward_test_signals.py` — open/update forward-tests (the core engine in
   `src/fx_annotation/forward_testing.py`).
5. `scripts/whatsapp_push.py` — token-free WhatsApp push on state change (if enabled).
6. `scripts/export_orchestration_state.py`, `scripts/export_tradingview_pine.py` — exports.

The DeepSeek path is disabled by default (`DEEPSEEK_DISABLED = True`) to avoid API spend; the
local-AI ("Gemma") reviewer slot prefers the **free Gemini API** when configured, else local Ollama.

---

## Project layout

```
src/fx_annotation/        # Core library
  forward_testing.py      # Routes, candidate generation, forward-test engine, tiers, timeouts
  htf_momentum.py         # Route A detector (HTF_MOMENTUM)
  htf_zone.py             # Route B detector (HTF_ZONE, delete-safe)
  momentum_entry.py       # MOMENTUM detector
  dynamic_scoring.py      # DYNAMIC_SCORE weighted bot
  market_watch.py         # Multi-timeframe scan / watchlist
  oanda_client.py         # OANDA candle fetch
  gemini_client.py        # Free Gemini API client (key rotation)
  ai_strategy.py          # AI reviewer dispatch (Gemini / Ollama)
  config.py               # .env loaders (OANDA / Gemini / DeepSeek / Ollama / OpenAI)
  ... (bias, structure, setups, confluence, trade_targets, pair_value, etc.)

scripts/                  # CLIs / orchestration
  run_always_on.py        # Managed 5-min loop
  forward_test_signals.py # Forward-test driver
  live_monitor.py         # Due-only live monitoring
  whatsapp_push.py        # WhatsApp state-change push + OPEN_TRADES.md snapshot
  backtest_*.py           # Per-strategy backtest harnesses
  ...

deploy/picoclaw/          # PicoClaw persona (SOUL.md) + deployment notes (README.md)
docs/                     # Strategy guides, handoff notes
outputs/                  # Generated reports & state (gitignored)
requirements.txt          # Runtime deps (Pillow; rest is stdlib). Python 3.10+
```

---

## Setup

**Requirements:** Python **3.10+** (uses `X | None` / `tuple[str, ...]` annotations).

```bash
pip install -r requirements.txt
```

Configuration is via dotenv files in the project root (loaders in `src/fx_annotation/config.py`).
Only `.env` (OANDA) is required; the rest are optional.

`.env` (required):
```
OANDA_API_TOKEN=your_oanda_token
OANDA_ACCOUNT_ID=your_account_id
OANDA_ENV=practice            # or "live"
```

`.env.gemini` (optional — free AI reviewer, supports up to 5 keys for rotation):
```
GEMINI_API_KEY=key1,key2
GEMINI_MODEL=gemini-3.1-flash-lite
```

Other optional files: `.env.deepseek`, `.env.ollama`, `.env.openai`. **All `.env*` files and the
`outputs/` directory are gitignored** — never commit secrets.

---

## Running it

One full cycle (no loop):
```bash
python scripts/run_always_on.py --once --use-gemma
```

Continuous loop (5-minute interval) with WhatsApp pushing:
```bash
python scripts/run_always_on.py --interval-seconds 300 --use-gemma --whatsapp-push
```

Just the forward-test engine:
```bash
PYTHONPATH=src python scripts/forward_test_signals.py
```

Key flags (`run_always_on.py`): `--interval-seconds`, `--once`, `--use-gemma`,
`--no-chart-images`, `--no-forward-test`, `--whatsapp-push` (or env `PICOTRADE_WHATSAPP_PUSH=1`).

---

## Backtesting

Per-strategy harnesses live in `scripts/` and import the shared simulator in
`src/fx_annotation/route_backtesting.py`:

```bash
PYTHONPATH=src python scripts/backtest_htf_momentum.py   # Route A (HTF_MOMENTUM)
PYTHONPATH=src python scripts/backtest_htf_zone.py        # Route B (HTF_ZONE)
PYTHONPATH=src python scripts/backtest_momentum.py        # MOMENTUM
PYTHONPATH=src python scripts/backtest_routes.py          # Multi-route comparison (incl. RULE M15/M5 arms)
PYTHONPATH=src python scripts/backtest_gemma.py           # AI route accuracy
```

Backtests can model spread costs and the scale-and-trail exit. Note: the standalone backtest
scripts carry their own timeout parameters and may differ slightly from the live per-route holds.

---

## WhatsApp alerts & PicoClaw

`scripts/whatsapp_push.py` is a deterministic, **LLM-free** pusher: it diffs
`outputs/forward_tests.json` against a stored state file and sends a WhatsApp message **only when a
tracked trade changes state**, through the PicoClaw gateway's token-free `POST /send` endpoint
(no LLM tokens spent for routine monitoring). It also maintains `OPEN_TRADES.md`, a concise live
snapshot the PicoClaw bot reads on demand.

Relevant environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `PICOTRADE_SEND_URL` | `http://localhost:18790/send` | PicoClaw gateway send endpoint |
| `PICOTRADE_WA_TO` | `249812612050953@lid` | Recipient WhatsApp ID |
| `PICOTRADE_MIN_TIER` | `5` | Announce new setups only for tier ≤ this |
| `PICOTRADE_MAX_SENDS` | `8` | Max messages per run |
| `PICOTRADE_OPEN_TRADES_MD` | `~/.picoclaw/workspace/memory/OPEN_TRADES.md` | Live snapshot path |
| `PICOTRADE_PID_FILE` / `PICOTRADE_SEND_TOKEN` | — | Gateway auth |

The PicoClaw bot persona is `deploy/picoclaw/SOUL.md` (route catalog, tiers, alert markers,
session rules, and a **sender-identity roster** — owner gets full access, other allowed numbers
are read-only guests). PicoClaw identifies each sender by their unique WhatsApp ID and keeps a
separate session per person.

---

## Outputs

Generated under `outputs/` (gitignored):

- `forward_tests.json` / `forward_tests.md` — full forward-test state and report.
- `whatsapp_push_state.json` — dedup state for the WhatsApp pusher.
- `OPEN_TRADES.md` — concise live snapshot (open trades, recent closes, win rate, expectancy,
  and the **per-session performance breakdown**).
- `live_memory.json`, `market_watch.md`, `alerts.json`, validation/chart artifacts.

---

## Deployment (Oracle Cloud)

The agent runs on a free Oracle Cloud VM as a `systemd` service (`pikotrade`) on a 5-minute loop,
with PicoClaw providing the WhatsApp bridge and an on-demand DeepSeek-backed assistant. Full
deployment steps — Go build of PicoClaw, the token-free `/send` patch, WhatsApp native pairing,
and the always-on service — are documented in `deploy/picoclaw/README.md`.

---

## Disclaimer

This software is for research and educational purposes. It is a **paper-trading / signal-alerting
system** and does **not** execute real trades. Forex/CFD trading carries substantial risk. Nothing
here is financial advice. Use at your own risk.

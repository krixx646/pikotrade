# Rule-Only Backtesting

This backtest is for the algorithm only. It does not call DeepSeek, Gemma, or any AI route.

The purpose is to test whether the rule engine is truly following the strategy:

- HTF direction and MA200 context.
- H4/H1 narrative and zone ladder.
- Relevant HTF POI sequence.
- M15 liquidity sweep.
- M15 Market Shift / BOS.
- Pullback into the M15 base that caused BOS.
- Quality score and quality notes.
- 3R, SL, timeout, MFE, and MAE.
- Failure tags such as `no_entry_reaction_candle`, `failed_before_1r`, `immediate_stop_pressure`, `no_clean_room_to_3r`, and `bos_older_than_6h`.

## Validation Run

Run a small validation first:

```powershell
cd "C:\Users\ADMIN\Desktop\signal"
$env:PYTHONPATH="C:\Users\ADMIN\Desktop\signal\src"
python "scripts\backtest_rules.py" --instruments EUR_JPY,USD_JPY,BTC_USD --start 2025-01-01 --end 2025-02-01 --max-trades-per-instrument 10 --json-output "outputs\backtests\validation_multi_rule_backtest.json" --markdown-output "outputs\backtests\validation_multi_rule_backtest.md"
```

Outputs:

```text
outputs\backtests\validation_multi_rule_backtest.json
outputs\backtests\validation_multi_rule_backtest.md
```

The JSON file is the source of truth. Every trade includes a `decision_snapshot` with the rule confluences that allowed the entry.

## Longer Runs

After validation, run longer windows in chunks. Do not start with all instruments from 2020 to 2026 in one command.

Example yearly run:

```powershell
cd "C:\Users\ADMIN\Desktop\signal"
$env:PYTHONPATH="C:\Users\ADMIN\Desktop\signal\src"
python "scripts\backtest_rules.py" --instruments EUR_JPY,USD_JPY,GBP_USD,AUD_USD,NZD_USD,XAU_USD,BTC_USD --start 2024-01-01 --end 2025-01-01 --json-output "outputs\backtests\rule_backtest_2024.json" --markdown-output "outputs\backtests\rule_backtest_2024.md"
```

For 2020-2026, run one year at a time:

- `2020-01-01` to `2021-01-01`
- `2021-01-01` to `2022-01-01`
- `2022-01-01` to `2023-01-01`
- `2023-01-01` to `2024-01-01`
- `2024-01-01` to `2025-01-01`
- `2025-01-01` to `2026-01-01`
- `2026-01-01` to the current date

## Current Validation Result

The latest tagged validation run covered `2025-01-01` to `2025-02-01` for `EUR_JPY`, `USD_JPY`, and `BTC_USD`.

It produced:

- `15` trades.
- `1` TP.
- `13` SL.
- `1` timeout.
- `7.1%` resolved win rate.
- `-0.667R` average result.

This is not a final verdict because the window is small, but it is a useful warning. The rule engine was finding strategy ingredients, but many accepted trades lacked enough human-style proof that the zone was actually reacting.

Top failure tags from the tagged baseline:

- `no_entry_reaction_candle`: `9`
- `failed_before_1r`: `7`
- `immediate_stop_pressure`: `6`
- `failed_between_1r_and_2r`: `5`
- `htf_poi_not_currently_touched`: `4`
- `bos_older_than_6h`: `4`
- `h1_bias_conflict`: `4`
- `no_clean_room_to_3r`: `2`

## Filter Experiments

All experiments below used the same validation window and instruments.

- Baseline: `15` trades, `1` TP, `13` SL, `1` timeout, `7.1%` resolved win rate, `-0.667R` average result.
- Minimum room to active HTF extreme of `3R`: `13` trades, `1` TP, `11` SL, `1` timeout, `8.3%` resolved win rate, `-0.615R` average result.
- Entry reaction candle required: `9` trades, `1` TP, `8` SL, `0` timeout, `11.1%` resolved win rate, `-0.556R` average result.
- Room plus reaction: `8` trades, `1` TP, `7` SL, `0` timeout, `12.5%` resolved win rate, `-0.500R` average result.
- Room plus reaction plus BOS age max `6h`: `5` trades, `1` TP, `4` SL, `20.0%` resolved win rate, `-0.200R` average result.
- Room plus reaction plus BOS age max `6h` plus one trade per HTF zone: `4` trades, `1` TP, `3` SL, `25.0%` resolved win rate, `0.000R` average result.
- Same as above with break-even protection after `1R`: `4` trades, `1` TP, `0` SL, `3` BE, `100.0%` resolved win rate, `+0.750R` average result.
- H1 alignment required: `12` trades, `0` TP, `11` SL, `1` timeout, `0.0%` resolved win rate, `-0.917R` average result.
- HTF POI touched now required: `12` trades, `0` TP, `11` SL, `1` timeout, `0.0%` resolved win rate, `-0.917R` average result.
- One trade per HTF zone: `13` trades, `1` TP, `11` SL, `1` timeout, `8.3%` resolved win rate, `-0.615R` average result.

Promoted for live forward testing:

- Require a confirming M15 reaction candle at the entry zone.
- Require enough HTF active-range room for the configured target, currently `3R`.
- Require fresher BOS by default, currently max `6h`.
- Protect forward tests at break-even once price reaches `1R`.

Not promoted yet:

- Mandatory H1 alignment. In this validation window it removed the only winner.
- Mandatory current HTF POI touch. In this validation window it also removed the only winner.
- One trade per HTF zone. It helped only slightly, so it remains a backtest switch until longer runs prove it.

## What To Inspect

Use the JSON `decision_snapshot` to inspect each trade:

- `bias`, `h4_bias`, and `h1_bias`.
- `narrative`.
- `relevant_htf_poi`.
- `zone_ladder`.
- `setup.sweep_time`, `setup.sweep_price`, and `setup.sweep_kind`.
- `setup.bos_time`, `setup.bos_price`, and `setup.bos_direction`.
- `setup.quality_score` and `setup.quality_notes`.
- `max_favorable_r` and `max_adverse_r`.
- `room_to_active_extreme_r`.
- `failure_tags`.
- `breakeven` result count.

Use `diagnostics.rejected_reasons` to see why the rule engine did not open trades on most scans.

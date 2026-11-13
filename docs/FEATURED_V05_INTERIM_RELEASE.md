# November 13, 2026 interim website model — possession Blend20

**Repository scope:** `The-Caves/cbb-model` only. This is a user-directed *website presentation and research release* of the previously built possession-event challenger; it is **not** a data-proven ATS/O-U promotion or an edit to the trained v0.4 algorithm.

## Website behavior

Starting with the date of this release (`2026-11-13T22:39:21Z`), for eligible D-I games that had not started by the release cutoff:

- Show the **original frozen `CBB_V05_POSSESSION_EVENTS_20261113_BLEND20`** as the **featured score, margin, total and winner** when a correctly matched source-verified pre-tip v0.4 forecast and immutable source-time-authenticated 20% simulator forecast exist. A 512-draw opponent-conditioned 2P/3P/FT/turnover/rebounding approximation contributes 20%, with the stronger lighter previous-season score ridge contributing 80%.
- If the v0.5 forecast is missing/invalid, **fall back to the original source-validated v0.4-era forecast**. Don't invent a new after-tip pick or backfill results. Never show either forecast for disputed or unverified non-D-I teams.
- Keep the original `data/forecast_ledger.jsonl` untouched, preserve `original_v04_projection` alongside the featured forecast, and keep original game-time win/loss/ATS records correctly attributed to the original versions. For games already started before rollout, show the originally featured forecast even if research files contain an earlier v0.5 shadow record.
- Show the model-specific status and an explicit **user-selected interim; ATS/O-U not yet independently verified** label. Distinguish original v0.4-era graded performance from the separate prospective v0.5 simulation report. The source quote must predate *both* captures if the site shows a v0.5/market spread or total difference.
- The main site metadata carries original champion version, featured model version, activation time, v0.5 shown count and v0.4 fallback count. No claim of calibrated probability, profit, actual FanDuel lines or verified closings.

## One follow-on improvement experiment — split total only

Backtesting previously found that the pure and larger simulation blends worsened margins; the 20% blend improved February totals marginally, while lighter carryover won the margin. The next **independent, immutable candidate** is `CBB_V05_POSSESSION_EVENTS_20261113_SPLIT_TOTAL20`:

- Margin: **100% lighter carryover** `prior_weight=0.15`.
- Total: **80% lighter carryover + 20% simulated total**.
- Predicted home/away scores derived consistently from margin and total.
- All existing blend20 historical and prospective predictions keep their original model ID and numbers, unchanged.
- This model is assessed on **identical saved games** in January validation and February *repeated retrospective* diagnostics, plus separately frozen 2026–27 real pre-tip predictions against identical source-observed market quotes.
- **Do not** switch the featured site model to the split candidate without performance evidence or another explicit user decision. Minimum 150 independent frozen matched games, stable margin/total and real book ATS/O-U evidence before considering fully validated promotion.

Historical 2025–26 ESPN boxscores were fetched retrospectively, and February 2026 has been examined before. Historical replay isn't proof of original source publication time or an untouched market holdout.

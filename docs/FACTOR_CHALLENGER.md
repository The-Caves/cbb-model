# CBB M2–M3 chronological factor challenger

**Repository:** `The-Caves/cbb-model` ONLY.

**Model:** `CBB_FACTOR_RESIDUAL_V0_1` is a *research-only* challenger to the live `CBB_SCORE_RIDGE_V0_4_D1_PRIOR30_A1_5` score model. It does not write a forecast ledger, alter website scores or replace live predictions.

## Method

- Read authenticated ESPN scoreboard finals, stable D-I team IDs, and source-verified two-sided ESPN boxscores from the existing archive.
- For each historical game day, fit the score model **only** to final games before 00:00 UTC on that date. Compute strength-of-schedule, strength-of-record (research proxy), last-five opponent-adjusted form and source-backed opponent-adjusted efficiency, pace and Four Factors from that exact earlier snapshot. Same-day and future final scores cannot enter features.
- Require both teams to have played at least three current-season D-I games and to have **at least two verified current-season boxscores each**, with all key factors present. Missing data causes PASS/exclusion from this matched challenger experiment, not a fabricated value.
- Train a regularized ridge residual correction separately for predicted margin and total, limited to +/-6 margin points and +/-8 total points. Predeclared `ridge_alpha=100`. Feature ablations: `form_only`, `factors_only`, `combined`.
- **Train:** November–December 2025. **Select feature group:** January 2026 only, with margin error prioritized. **Untouched holdout:** February 2026. No February results influence training, hyperparameter or feature-group selection. Report champion and challenger on **identical** heldout games; retain loss/accuracy, Brier score, and low-sample, large-gap and SOS-gap strata.
- Minimums: 80 matched training games, 30 January validation games, 30 February heldout games. If missing, report `INSUFFICIENT_...` and **do not** claim a gain.
- Train automatically on a source-code push to `main`, in the existing free GitHub Actions workflow. Result is committed at `data/reports/factor_challenger_2026.json`. Repeat deliberately; this historical experiment should not run four times daily or auto-promote.

## Important caveat

2025–26 ESPN boxscore statistics were acquired **retrospectively**. Temporal filtering prevents future *game outcomes* from contaminating earlier replay snapshots, but we have **no authenticated historical API publication timestamps**. The retrospective experiment is therefore useful for diagnosing model design, **not proof** of live deployable model improvement, positive betting ROI, or true ATS/O-U performance. Historical market prices/odds, verifiable transfers, lineup injuries, player-impact projections and game simulations are not supplied by this experiment. Retain frozen live v0.4 predictions while collecting independent prospective challenger forecasts.

## Run

```bash
python -m pytest -q
python factor_challenger.py --root data --season 2026
cat data/reports/factor_challenger_2026.json
```

Do not promote from one retrospective window alone; even if February performance improves, require separately frozen, truly pre-tip 2026–27 predictions with current known source times, and sufficient game count across different types of opponents.

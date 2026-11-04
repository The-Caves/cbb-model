# Possession and Four Factors data milestone (research-only)

## Why this exists

The original CBB v0.2 score model over-attributed weaker non-D-I visitors' losses to home court. v0.3 corrected its large home estimate and over-shrunk team ratings, but final-score modeling still misses pace, turnover, offensive rebounding, shot selection, and free throw tendencies.

## Implemented in this step

- Free, optional, rate-limited ESPN `summary?event=` collection of **two-sided verified final boxscores**; no API keys, billing, or paid services.
- Stable scoreboard game and team ID joins and independent final score comparison. Mismatches and missing statistics are **rejected**, not backfilled from guesswork.
- Estimate each team's possessions as `FGA - ORB + TO + 0.475 × FTA`. Also compute eFG%, turnover%, offensive rebound%, free-throw rate, three-point attempt rate, and offensive points/100 estimated possessions.
- Archive append-only game data and a compact compressed excerpt of original team-stat evidence, with raw response hash and capture time. Never claim these were captured before historical tipoff.
- Aggregate early 2026–27 exploratory per-team profiles **only for games involving two ESPN-directory-listed D-I opponents**. Profiles with fewer than five games are **INSUFFICIENT**, not meaningful scouting or validated betting inputs.
- Save explicit `data/advanced/boxscore_coverage.json` reporting successes/failures and missing source coverage.

## Safety rules

- **The active v0.3 score predictions remain unchanged.** This is a data-engineering milestone, not proof of improved predictive accuracy. No possession-derived weights will be promoted until independent chronological walk-forward and prospective comparison beat the existing score-only model.
- Historical boxscores discovered today are retrospective observations. As-of feature eligibility for future models must use the actual pregame decision time; previously archived historical games can support retrospective research only.
- No invented injury, starter, market, player or advanced metric values.
- ESPN endpoint is unofficial. Failures are reported. Normal model and website runs should remain functional when advanced source coverage is incomplete.

## Next experiment

1. Backfill historical game-level verified stats incrementally without exceeding free-run limits.
2. Fit opponent-adjusted **points per possession** and pace on time-eligible game slices.
3. Backtest separate margin, total, and straight-up errors with a forward-rolling cutoff, plus venue, non-D-I, and early-season ablations.
4. Freeze both baseline and challenger predictions before live games, then consider promotion only with reproducible evidence.

# M4 — Verified player minutes and roster-aware challenger

**Repo scope:** `The-Caves/cbb-model` ONLY. The active website and champion remain `CBB_SCORE_RIDGE_V0_4_D1_PRIOR30_A1_5`.

## Implemented, not assumed

- Reuse **the same verified ESPN game-summary responses** already collected for team boxscores, parsing both team's athlete tables and retaining stable ESPN player IDs, minutes, points, rebounds, and assists.
- Source reconciliation: exact ESPN game ID, both stable D-I team IDs from the verified scoreboard, five or more independently identified players per side, plausible team minutes, summed player points equal the source-verified final score, digest and real capture timestamp. Missing player data **does not become zero** or invalidate a genuine team boxscore; optional player parsing failures are reported separately.
- Player evidence ledger: `data/advanced/player_boxscore_games.jsonl`, one accepted entry per game. The player data comes from observed games, **not an inferred future lineup**.
- Free, rate-limited current-team roster retrieval from the ESPN team endpoint `teams/{id}?enable=roster`. Preserve stable player IDs, source hash and actual capture time in `data/advanced/roster_snapshots.jsonl`, with current membership never misrepresented as a historical roster.
- Rate-limited **historical player enrichment** re-fetches previously accepted game summaries, independently revalidating game ID, team identity and scoreboard final against stored team evidence. A retrospective fetch is always labeled as such and never treated as contemporaneous with a 2025 or early-2026 prediction.
- For a target current season, calculate the *observed prior-season sample* minutes/points attributable to players on a genuinely observed recent roster. Track returning-player count, observed transfers from other known team IDs, unobserved players, prior verified game count, source coverage and `UNKNOWN` when prior minutes are unavailable or too sparse. A partial historical sample is **not** a claimed full-season returning-production percentage.
- Model candidate `CBB_ROSTER_PRIOR_V0_5_RESEARCH`: only when both teams have verified recent roster and at least five prior-season sampled player games, scale previous-season game weights according to evidence-backed retention (a conservative confidence-capped multiplier from 0.4 to 1). Unverified teams preserve unchanged baseline team weights. Live `cbb.fit()` call sites do not provide this optional parameter; **v0.4 behavior is unchanged**.
- Create a separate append-only `data/roster_shadow_ledger.jsonl` if a real pre-tip roster-aware prediction is possible. Never rewrite `data/forecast_ledger.jsonl`. Grade candidate and original v0.4 on **identical** games after observed finishes in `data/reports/roster_challenger_prospective.json`.

## Correct validation gates

1. Unit tests for missing/unmatched source fields, future tip and roster capture, points and minutes reconciliation, duplicate player IDs, transfer attribution, current sample coverage, optional prior weight preserving champion defaults, and frozen pre-tip identity.
2. **Training/backtesting must not backdate current roster snapshots** to the 2025–26 season. We do not have authenticated historical rosters from those dates. Therefore historical roster-aware model MAE improvement is **not claimed**. Existing historical v0.4 vs lighter-carryover experiments remain useful separate diagnostics, not substitutes for true roster-aware validation.
3. Only a verified prospective head-to-head of at least 150 completed games with genuine pre-tip candidate and v0.4 snapshots can warrant review. More than one time period and early-season sample analysis are also required. No automatic promotion.
4. Current ESPN player/roster source coverage is measured, not fabricated; if zero teams qualify the candidate is explicitly marked `PASS_INSUFFICIENT_ROSTER_COVERAGE`.
5. These are no-cost public ESPN sources used with bounded requests. Do not create paid APIs, extra hosting infrastructure, sportsbook line assertions or starting-lineup claims.

## Workflow

- On code pushes, manual dispatch, and the morning scheduled build: up to 24 rosters plus 24 verified prior-game re-fetches, spaced at least 1.2 seconds. Other in-season refreshes recalculate reports without extra roster HTTP requests.
- Morning historical backfill continues source-verified team and new player extraction. It also re-fetches up to 48 archived older games for missing player evidence after its existing bounded chunks.
- All captures and reports are stored under `data/` and committed by the **existing** GitHub Actions data writer; models that cannot be trained remain PASS, not quietly filled by guessed stats.

## Next research improvement

Reconcile more detailed returning **usage/possessions**, verify minutes for incoming transfers and former starters, prioritize roster-heavy games for historical collection, and analyze actual prospective calibration. Introduce team-specific lineup replacement value only when pre-tip player availability is independently source-observed. Test candidate strengths against both v0.4 and the lighter-carryover challenger on matched games, subject to data coverage.

Manual local commands:

```bash
python -m pytest -q
python roster_intelligence.py --root data --max-teams 24 --player-backfill 24
python roster_challenger.py --root data
```

## M4.1 — Verified prior-season sample depth and team-specific weighting

The original roster-aware challenger used the same average roster-retention multiplier for *both teams' scoring observations* in each historical game. This could downweight an otherwise intact opponent simply because the other roster changed.

The revised, still research-only candidate applies each verified team multiplier to that **team's prior-season scoring observations**. Active v0.4 calls remain unchanged, and unverified teams retain full original observation weight.

A second limitation is partial player-history coverage: collecting five prior-season game boxscores does not mean the team's complete prior season is known. We now count the prior-season **verified, source-observed D-I completed games** for each team and report `prior_player_game_coverage` as observed player-game samples divided by its verified prior D-I schedule. This excludes future, disputed and non-D-I games. A coverage fraction below 15%, or absent denominator, prevents roster weight changes entirely. For eligible teams, strength of the roster adjustment scales with both sampled-game count and coverage (reaching full confidence only at 20 sampled games and at least 60% prior-game coverage). This is a conservative predeclared rule, **not a trained optimal weight**.

Limited HTTP requests now prioritize teams with current **source-verified recent roster snapshots** that appear in the next 72 hours of D-I games, so archive collection is targeted at games where a meaningful pre-tip comparison may be possible.

The new evidence fields are tracked in the roster-intelligence report and frozen challenger records. Tests cover sample fractions, future-score exclusions, a thin-data no-promotion gate, and the difference between team-specific and formerly shared game weights.

**Validation status:** A data-coverage and modeling correctness improvement is not yet a predictive accuracy improvement. The prospective promotion gate remains at least 150 independently frozen same-game challenger/v0.4 predictions, plus stability across separate periods. Existing historical replay lacks authentic past roster snapshots, so it cannot be used to claim successful historical roster-model training.

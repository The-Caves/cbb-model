# v0.5 CBB season-transition experiments and promotion gate

**Scope:** `The-Caves/cbb-model` only. The active pre-tip version remains `CBB_SCORE_RIDGE_V0_4_D1_PRIOR30_A1_5` until independent evidence shows that a candidate genuinely improves forecasts.

## Current source-backed benchmark

- Original active-v0.4 frozen pre-tip score report: 40 graded v0.4 games, 67.5% straight-up, margin MAE 10.436, total MAE 12.848. This sample is small and not directly comparable to prior versions, which faced different games.
- Larger 2025–26 retrospective factor experiment selected a factors-only challenger on January but lost on February holdout: 1,361 same games, v0.4 margin MAE 8.919 vs challenger 8.948, total MAE 13.407 vs 13.591, SU 68.11% vs 67.38%. No promotion.
- The next experiment tests **why** early-season team priors may be stale after large roster changes. These new variants are predeclared rather than tuned after February is known.

## Predeclared candidate families

Each uses D-I-only verified ESPN team IDs, fixed 3.5-point home venue advantage, and the same score ridge architecture as v0.4.

| Model | Prior-season observation weight | Ridge alpha |
|---|---|---|
| Active `v04` | 0.30 | 1.50 |
| `roster_fade` | 0.30 * exp(-days since Nov 1 / 65), minimum 0.06 | 1.50 |
| `lighter_carryover` | 0.15 | 1.50 |
| `less_shrinkage` | 0.30 | 0.75 |

These are **hypotheses**, not verified roster continuity metrics. Actual transfers, returning minutes and lineup availability are still unverified. Do not infer power-program superiority solely from conference labels.

## Training and chronological backtesting

`v05_transition_challenger.py` compares the four versions on identical qualified games. Fit a new set of team ratings for every historical UTC game date using only games completed before that date. Report November 2025 early-season performance, use December 2025–January 2026 to select **one** potential candidate, and compare it with v0.4 on February 2026 as a retrospective holdout. These final scores were acquired after the fact: cutoff-safe reconstruction **does not** prove their historical API publication availability. February has already been examined by earlier model experiments, so it is not a truly untouched independent period in the project's overall development history.

Separately, replay each candidate against the **original immutable v0.4 frozen pre-tip game IDs and timestamps** from the current 2026–27 archive. Candidate scores in this replay only use finals already *actually observed* by that capture time. However, those candidate scores were **not really frozen at the time**, so label them `AS_OF_COUNTERFACTUAL_NOT_FROZEN`, not prospective results.

`v05_shadow.py` starts capturing **real pre-tip** challenger snapshots for the three variants at each regular, no-cost in-season scheduled run. The separate append-only `data/v05_shadow_ledger.jsonl` never rewrites `data/forecast_ledger.jsonl`. The research report `data/reports/v05_shadow_prospective.json` grades snapshots only if both the champion and candidate had valid, stable ID, strictly pre-tip forecasts, and the matching game has an observed final score.

## Promotion gate

1. All code/unit/website regression tests pass, identities and source times are validated, and no lookahead or source mismatches.
2. Retrospective February head-to-head shows a stable improvement, **not** solely an improvement on one narrow slice. Report margin MAE, totals MAE, SU hit rate, Brier score, early low-sample and large-favorite strata. A better win percentage on a smaller/different set of games is not sufficient.
3. At least 150 *genuinely frozen* current-season matched v0.5/v0.4 predictions, spread across independent game days and different team strengths. Require meaningful margin or total reduction without unacceptable SU/calibration deterioration; avoid repeatedly selecting and evaluating the same games.
4. Even if the minimum is reached, flag `READY_FOR_INDEPENDENT_REVIEW` rather than automatically switching the model. Require more than one prospective period and no adverse early-season effects before changing `ACTIVE_LIVE_VERSION`. All old predictions remain immutable.
5. Avoid claiming verified historical sportsbook ATS, O/U, closing line value or profitable bets without source-observed pre-tip provider prices and prices for actual bets.

## Run

```bash
python -m pytest -q
python v05_transition_challenger.py --root data --season 2026
python v05_shadow.py --root data
```

Outputs: `data/reports/v05_transition_challenger_2026.json`, `data/reports/v05_shadow_prospective.json`, and the separate immutable candidate ledger. Scheduled workflows collect future prospective samples; no asynchronous assistant execution is assumed.

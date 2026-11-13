# v0.5 opponent-adjusted recency research — immutable model comparison

**Repository:** `The-Caves/cbb-model` only. Active website model remains `CBB_SCORE_RIDGE_V0_4_D1_PRIOR30_A1_5`; original predictions stay in `data/forecast_ledger.jsonl`.

## Hypothesis and implementation

Past models include opponent-adjusted score strength, current- and previous-season observations, home court, and D-I-only opponents. We now test **whether more recent current-season results deserve more weight in that exact same opponent-adjusted ridge fit**. It is *not* weighting raw winning streaks by opponent-blind form and it does not replace ratings with today's team profiles during historical replay.

An optional, default-off `cbb.fit()` weight is applied only to a genuinely completed **current-season** game's two scoring equations at fit cutoff (T): `weight = 0.60 + 0.40 * 0.5**(age_in_days / half_life_days)`. Historical games remain weight 0.30 or 0.15, according to the fixed candidate. This has a bounded floor (60%) and does not suppress distant games completely. The original v0.4 default call gets **exactly weight 1.0 for all current-season games** and unchanged results.

Five fixed candidate identities are trained and compared on **identical verified D-I games** for each historical UTC game day:

| Version | Previous-season game weight | Recency half-life (current season) | Floor |
|---|---:|---:|---:|
| v0.4 champion | 0.30 | None | 1.00 |
| lighter carryover control | 0.15 | None | 1.00 |
| `recency_21` | 0.30 | 21 days | 0.60 |
| `recency_45` | 0.30 | 45 days | 0.60 |
| `recency_45_prior15` | 0.15 | 45 days | 0.60 |

All variants use a fixed 1.5 ridge penalty, 3.5-point home venue adjustment and only stable D-I IDs.

## Evaluation — before any model release

`python recency_challenger.py --root data --historical` reconstructs true **as-of score regressions** using only completed games before each day's midnight UTC cutoff. It evaluates November 2025 early-season diagnostics, uses **December 2025–January 2026 to select a candidate** (only if it improves v0.4 validation margin MAE, does not lose more than 0.5 percentage points of straight-up accuracy, and Brier does not worsen by more than .002), then evaluates the selected configuration on February 2026. Margin MAE + 0.5 × total MAE is the fixed tie-breaking objective. Report all five even when no recency configuration passes selection. Comparison uses the same qualified game IDs, and stratifications are defined by the champion and genuinely preceding games, not by different per-model cuts.

Subgroups: November/early games with under five D-I results; champion 12+ point favorites; 6+ strength-of-schedule gap; and genuine 8+ stronger-opponent schedule step-up (after at least two observed prior opponents). For small strata, report sample size and avoid inferring a robust effect.

**Historical-source limitation:** ESPN 2025–26 scores were acquired retrospectively; this replay excludes future game results at each hypothetical prediction date but cannot authenticate original third-party publication times. Moreover, February 2026 has already been inspected in prior project experiments. Treat this as **historical diagnostic and research**, not untouched independent predictive validation or an ATS/O-U betting backtest.

## Real prospective validation

Every scheduled model update separately freezes all three recency challengers under distinct immutable model names in `data/recency_shadow_ledger.jsonl` prior to kickoff. The scorer only grades a game after an actually source-observed final and only when an original v0.4 pre-tip freeze is within **two hours** of the same game's challenger capture. Validate immutable model identity, team IDs, tip/capture times, training latest game, 72-hour maximum lead, score arithmetic, and hyperparameter identity.

Results: `data/reports/recency_challenger_2026.json` for historical comparisons, and `data/reports/recency_prospective_2027.json` for real paired challenger/v0.4 performance. At least 150 genuinely frozen matched completed games, across multiple independent periods and game types, plus stable calibration and no meaningful SU/totals regression, are needed even to consider review. **No automated model promotion**. Missing or limited coverage causes a truthful PASS/insufficient-data state.

This uses the existing free source and GitHub Actions; no paid API, made-up conference tier or retrospective roster assumption.

```bash
python -m pytest -q
python recency_challenger.py --root data --historical
python recency_challenger.py --root data
```

# CBB model roadmap | No-cost, source-valid development

**Repository scope:** The-Caves/cbb-model ONLY; never write CFB files as part of a CBB milestone.
**Budget constraint:** No paid API keys, premium data feeds, AWS, servers, premium runners, or paid hosting. Public GitHub Actions + Pages and public free sources only.
**Promotion policy:** Retain active v0.3 score-model and immutable pre-tip predictions until an independent, chronological challenger beats it. Do not invent ATS, totals lines, starters, injuries, or historical timestamps.

| Stage | Scope and build order | Exit gate | State |
|---|---|---|---|
| M1 | Verified D-I team IDs; past two seasons + current season boxscore sampling; possession/Four Factors; coverage, quarantine and source capture auditing; repeatable free daily backfill | Multi-season source coverage reported with truthful numerators/denominators; corruption and leakage tests pass; ingestion succeeds in GitHub | **Implemented collector/audit; full historical coverage remains in progress** |
| M2 | Opponent-adjusted offensive and defensive points/100 possessions + pace and Four Factors, with early-season priors and non-D-I exclusions | Walk-forward 2025–26 score/margin/total evaluation vs frozen v0.3 baseline, separate current-season prospective holdout | Not started |
| M3 | Opponent-specific interactions: rebounding matchup, shot selection vs defense, FTA/turnover pressure, 3-point variance, tempo conflicts and venues | Ablations show reliable improvement beyond M2 on unseen games | Not started |
| M4 | Player intelligence: lineup minutes and rotations, prior-season carryovers and transfers, verified availability and usage effects | Coverage provenance + valid pre-tip source time; unknowns stay UNKNOWN | Not started |
| M5 | Simulate possessions and game-score distributions; calibrate winner probabilities, margin/totals uncertainty | Calibration, log-loss/Brier and interval coverage beat challenger baselines out-of-sample | Not started |
| M6 | Automated model competition, locked temporal splits, retrospective vs prospective reports, immutable frozen forecasts | Two-independent-period evidence; automatic rollback on regressions | Not started |
| M7 | Authenticated historical and pre-tip market lines and odds, including provider and capture timestamps, no invented close | Genuine SU/ATS/O-U performance with pushes, closing-line comparisons and honest PASS thresholds | Not started |
| M8 | Data-source coverage, expandable scouting and player/availability context, clear model version and methodology, mobile dashboard improvements | UI regression tests; displays only provenance-backed records | Not started |

## M1 first implementation, 2026-11-03

Baseline foundation already recorded 36/36 valid ESPN postgame team boxscores (28 current-season, 8 prior-season), but no D-I team yet has 5 verified current-season games. This is not enough for an advanced model.

This milestone adds:

1. A **quota-limited, deterministic three-season collector** that prioritizes newly completed games for live coverage and fills older seasons oldest-first for chronological research. It uses ESPN stable D-I team IDs and excludes unsupported opponents when a directory is available. Request quotas are capped (by default: 12 current, 28 last, 8 two-seasons-back, or flexible spillover if a bucket is empty).
2. A **coverage audit** joining collected summaries to real completed D-I scoreboard games by stable ID, tipoff, team ID and final scores. Per-season denominator, missing-game count, verified coverage, D-I team sample counts and quarantine reasons are reported. Historical source capture is explicitly **retrospective**, not falsely backdated.
3. Source validation tests including non-D-I filtering, day/tip cutoff, repeatable selection, missing directory, and mismatched opponent records. Corrupt or contradictory accepted records block model promotion.
4. Scheduled low-rate batch collection (48 game summaries per workflow at 1.2 seconds/request, contingent on availability) on the existing public repository; a partial data-source outage cannot silently become a valid prediction.

**M1 scope still open:** Grow historical coverage substantially over future free scheduled runs; measure rate-limit and missingness characteristics, then make M2 a true walk-forward challenger using only source-valid historic features. No score-pick version change in M1.

## Important caveats

- Estimated possessions = FGA - ORB + TO + 0.475 × FTA; not an authoritative play-by-play possession count.
- eFG%, offensive rebound%, turnover% and free-throw rate depend on source-provided team-level statistics; reports reject missing or contradictory values.
- A game's archived boxscore can be used for a chronological historical replay only after its actual final score, but retroactively downloaded source contents are not evidence of their original publication timestamp.
- Current model v0.3 score predictions and all historical freezes remain immutable and research-only.

## Live-data safeguard added during Milestone 1

A scheduled refresh encountered a revision to a previously archived ESPN final score (game ID 401921808). The existing ingestion stopped rather than overwriting history. Milestone 1 now keeps the original score plus the separately observed revised score and SHA256 under `data/quarantine/score_revisions.json`, marks that game as disputed, and excludes it from all training, historical evaluation and prospective grading. Duplicate observations do not multiply evidence entries; the disputed status does not silently clear if the provider later returns its prior number. Other verified matchups can still update normally. Site JSON exposes `score_source_under_review` for affected games. Resolving the conflict to one authoritative score remains a manual review milestone.

## M1 acceleration — 2026-11-04

Before this change the collector captured only 48 games per scheduled run. That rate would take many weeks to cover two seasons. The verified research collector now selects 80 games per chunk across current season / 2025–26 / 2024–25 with approximately 15% / 55% / 30% quotas, calls the free ESPN summary endpoint no faster than one request every 1.2 seconds, and retries only transient network/server failures with bounded delay. A workflow makes up to three bounded chunks (240 maximum game-summary requests) and commits each completed chunk so a later failure does not discard earlier archived evidence. `[skip ci]` commits avoid starting more jobs. Existing source/team/score/hash checks and the separate data quarantine remain mandatory. No advanced model is promoted by collecting these records.

Research data may increase substantially, but **M1 does not meet exit criteria until sufficient verified historical *D-I vs D-I* coverage exists across full chronological periods**. Quarantined scores and historical source-capture timestamps must stay explicit.

## M1 accelerated ingestion results — November 4, 2026

The first three-batch workflow [41887197215](https://github.com/The-Caves/cbb-model/actions/runs/41887197215) completed its build successfully. Of 240 attempted free ESPN summaries, 238 passed source verification and 2 were rejected for missing/invalid field-goal statistics. Three bounded batches produced 80, 80, and 78 accepted games; archival count rose from 84 to **322**. The D-I audit now counts **312** verified D-I vs D-I games (the other 10 are genuine non-D-I opponent records), with **0 additional quarantined archive mismatches** and the one previously known disputed final still isolated. Coverage by season is **2024–25: 78/5,644 (1.38%)**, **2025–26: 168/5,633 (2.98%)**, **2026–27: 66/136 (48.53%)**. No current-season team yet has five verified D-I games. This is **data-engineering progress, not a trained or promoted new model**. Continue accumulating source-checked historical games and build a separate, timestamp-safe efficiency challenger once sample size supports a fair comparison.


## M2 research scaffold — November 4, 2026

Implemented `efficiency_challenger.py` as a **nonpromotable** opponent-adjusted pace and points-per-100-possessions ridge model with fixed venue treatment, separate score/tempo estimates, and pre-game cutoff enforcement. Historical evaluations pair identical games with the active v0.3 score model. The historical source backfill is retrospective and is labeled as such. An insufficient data verdict is a *correct* result, not a model failure. A minimum 250 audited historical boxscores to fit and at least 100 matched evaluated games are required even to show a preliminary comparison. Actual promotion would require stronger independent validation and prospective frozen results. The GitHub workflow now runs this model after each historical data collection, writing `data/reports/efficiency_challenger_2026.json`. **No live v0.3 picks changed.**

## Priority update — November 4, 2026: roster turnover before historical-team priors

We should **not make full historical boxscore backfill a prerequisite** for improving the 2026–27 model. Prior seasons are useful to establish a restrained program baseline, study matchups and compare methods, but individual team performance can become stale immediately when players transfer, graduate or enter the draft.

1. **Now — season-transition sanity check:** current v0.3 uses `prior_weight=0.60` **per previous-season game**, and does not decay it according to returning production or current games played. For example, 30 prior games and 2 current games give an effective previous-season observation weight of `18/(18+2)=90%`; this is not a fixed 60% share. Compare smaller priors on the same chronological 2025–26 games and report cold-start/established-team results. Never promote on a single retrospectively examined season.
2. **Now — roster/rotation continuity collection:** source-stable player IDs, roster membership by school/season, returning minutes/usage, transfers and notable departures. Retained minutes/possessions should govern how much last year's *team* statistics carry over. Missing roster evidence remains UNKNOWN. Program-level strength can be a separate, modest prior, not a label that all power-conference teams are strong.
3. **As games accrue — current team strength:** emphasize recent opponent-adjusted possessions, offensive and defensive efficiency, turnover/rebounding/shooting indicators, with confidence and effective sample sizes; avoid mistaking blowout wins against non-D-I opponents for calibrated team strength.
4. **Later — matchup, simulations and player availability:** test each feature family using chronological ablations and frozen pre-tip observations.
5. **Background only — free historical backfill:** keep limited rate-limited, auditable multi-season collection for training method comparison and calibration. Do not increase request volume solely to reconstruct stale team ratings, and never let data ingestion outages change active picks.

**Promotion gate:** evaluate early, middle, and late-season games on comparable dates, neutral/home games, and programs with verified high versus low returning production. Test on more than one historical season and on subsequent real frozen picks. Active v0.3 score predictions stay unchanged pending evidence.

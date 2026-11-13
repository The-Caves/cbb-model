# M4.3 — Dated team profiles, trajectory challenger and accuracy experiment

**Repository:** `The-Caves/cbb-model` only. Active version remains `CBB_SCORE_RIDGE_V0_4_D1_PRIOR30_A1_5`.

## What is actually saved

- `data/advanced/team_intelligence_2027.json` and `data/advanced/team_profiles_2027.json` are repeatedly committed to GitHub during source-refresh runs. Earlier Git commits contain **genuine previously saved profile snapshots**. Those snapshots are not a retrospective reconstruction; they have original `as_of_utc` metadata.
- `profile_history_audit.py` reads the history of `team_intelligence_2027.json` directly from **Git revisions** (requires full checkout history) and compares strength, opponent strength, 5-game opponent-adjusted form, win-quality proxy and Four Factors. Reports actual profile changes and ranking movement, with a minimum 18-hour gap. No use of later data to label an older saved profile.
- This comparison by itself says how the team changed, **not** whether either version is an accurate prediction. A snapshot saved November 13 is never treated as if it were available before a November 11 tipoff.

## New research challenger

`profile_trajectory_challenger.py` computes a v0.4 as-of scoring model and a second model with **the exact same evidence cutoff 21 days earlier**, with fixed D-I filtering, 0.30 previous-season game weight, 1.5 ridge regularization and 3.5 home points in both. It measures rating movement, offense/defense movement, deviation of most recent five opponent-adjusted margins from current strength, schedule difficulty and current sample maturity. Each feature is derived entirely from games completed before that cutoff.

Two predeclared feature groups are considered: `trajectory_only` and `trajectory_context`. A conservative residual model uses ridge `alpha=180`, with at most 6 margin-point and 8 total-point corrections. Low-sample or missing opponent profiles are rejected, not filled with invented values.

**Chronological validation:** train on November–December 2025, select only on January 2026, report untouched February 2026 outcomes for the selected group versus v0.4 using **identical game IDs**. Report SU accuracy, margin and total MAE, prediction bias, Brier score and early-season/large-favorite/strength-of-schedule strata. Fixed minimums: training >=180, selection >=100 and test >=100. Fail closed when inadequate.

**Critical evidence boundary:** 2025–26 scoreboard finals were assembled retrospectively. Reconstructing v0.4 at older pre-tip cutoffs and enforcing no future score leakage creates useful historical research, but does **not** authenticate what all source feeds had actually published then; February has also been examined in prior project experiments. This is not a new independently untouched betting backtest and must never be called genuine 2025–26 frozen picks.

For genuine prospective evaluation, after a valid historical experiment the model creates `data/profile_trajectory_shadow_ledger.jsonl`, an append-only ledger of independent v0.5 research forecasts **before actual tipoff**. Unlike reconstructed backtest rows these have real capture timestamps, current and old model training cutoffs, stable team IDs and unchanged researcher version ID. `data/reports/profile_trajectory_prospective.json` grades only valid pre-tip pairs with original v0.4 captures within two hours, identical final scores, compatible original game identity and all profile-time constraints. The review threshold is >=150 genuinely matched completed games spread across independent game dates. No automated promotion.

### Report locations

- `data/reports/profile_history_audit_2027.json` — saved former vs recent team profiles
- `data/reports/profile_trajectory_backtest_2026.json` — historical trajectory challenger versus v0.4
- `data/reports/profile_trajectory_prospective.json` — genuinely frozen new challenger comparisons
- `data/profile_trajectory_shadow_ledger.jsonl` — source-timed pre-tip challenger predictions

The existing GitHub Actions collects, audits, replays and persists these research reports without paid data, new hosting or changing the live website's v0.4 forecasts.

```bash
python -m pytest -q
python profile_history_audit.py --root data --repo . --season 2027
python profile_trajectory_challenger.py --root data --historical
python profile_trajectory_challenger.py --root data
```

**Promotion rule:** Demonstrate robust out-of-sample improvement in margin and/or totals without unacceptable SU or calibration regression on independently frozen games, including early-season and big-strength-gap subsets. Never promote based solely on a reconstructed or already examined February window.

### Legacy raw-profile snapshot comparison

The newer `team_intelligence_2027.json` opponent-adjusted snapshot series started only on November 13, so it does not yet contain a fully day-separated historical comparison at the first checkpoint. A separate, already-existing `team_profiles_2027.json` raw-source series has genuine committed snapshots from November 7 onward.

`profile_history_audit.py` now also compares `team_profiles_2027.json` at two **actual Git commits separated by at least 36 hours**. Unlike `team_intelligence_2027.json`, the legacy raw snapshots do not contain their own `as_of_utc`; the audit therefore reports the **Git commit timestamp**, not an invented original ESPN publication timestamp.

Compare common, valid teams with at least 2 observed games in the old snapshot and at least 3 in the newer one. Track differences in raw offensive rating, estimated pace, eFG%, turnovers, rebounding, free-throw and three-point attempt rates, including biggest movers and mean absolute offensive efficiency change. This describes what the source-based team profile knew at different archive checkpoints; it is not a validated score-model improvement or an opponent-adjusted rating comparison.

The combined report keeps the `raw_saved_profile_history` branch separate from the newer opponent-adjusted `team_intelligence` history, and will fill the latter automatically once genuinely saved dated history is deep enough.

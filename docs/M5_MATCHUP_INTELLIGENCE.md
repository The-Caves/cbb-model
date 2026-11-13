# M5 — Lighter-carryover college basketball matchup intelligence

**Repository:** `The-Caves/cbb-model` only. **The live website champion remains v0.4** until authentic pre-tip ATS/O-U and margin/total validation support a change.

## What we are improving

Early nonconference favorites make straight-up accuracy misleading; a winner predicted correctly by 30 points can still be a bad forecast when the team won by 3. Therefore our score objectives prioritize **margin MAE, total MAE, calibration and actual pre-tip ATS/O-U against observed book quotes**, not raw SU alone.

The leading simpler challenger, **previous-season game weight 0.15**, is retained as the foundation. The research model uses the source-verified ESPN D-I boxscore archive, `team_intelligence.build`, and regularized opponent-adjusted offense/defense statistics. No assumptions that a Power Four school automatically beats a smaller-conference school; the source-observed opponents determine schedule quality.

## Features and interactions

Pre-tip team profiles include verified D-I game counts, schedule-strength opponent points, strength-of-record excess wins, last-five opponent-adjusted margin, separate adjusted offense and opponent-allowed rate estimates, and the seven existing team factors:

- Offensive efficiency (points per estimated 100 possessions)
- Possession pace
- Effective field-goal percentage
- Turnovers per possession
- Offensive rebounding share
- Free-throw attempt rate
- Three-point attempt rate

The `core` version uses the **17 previously proven/source-reconciled opponent-adjusted factor features** (3 context + 7 margin matchup + 7 total matchup). The richer `interaction` version additionally tests **9** offense-versus-defense terms: expected tempo, clash in preferred pace, matchup offensive scoring strength differential and sum, three-point volume × shot-quality gap, shooting-variance proxy, turnover pressure, offensive-rebound mismatch and foul/free-throw pressure.

These are *statistical hypotheses*, not observed lineup or possession-level play-by-play facts, and do not imply calibrated probabilities. We do not add unknown blocks, assists, true shooting, starter status, or injury numbers under fabricated assumptions. Historical ESR game boxscore extraction is source-checked and its missing factors remain missing, never zero.

## Model training and proper comparison

- Per-game baseline: existing score ridge with fixed 3.5-point home court, alpha 1.5, and previous-season carryover **0.15**.
- Train **two** regularized residual correction variants on identical games, using each team's as-of adjusted factor profiles. Ridge alpha 250, absolute margin correction capped at 5 points, total at 7.
- Build a **separate v0.4 score prediction for precisely each matched game and identical UTC cutoff**. Compare v0.4, simpler lighter carryover, core and interaction versions on the *same game IDs*—not v0.4's full season against a feature-complete subset.
- Train from November–December 2025; choose only using January 2026. Historical February is a **revisited, retrospectively sourced** holdout, not fresh independent validation. Report margin and total MAE, straight-up win rates, Brier and bias, with near-pick'em, big favorite, high SOS-gap, low boxscore sample and conference-season slices.
- A variant must improve January *margin MAE by at least .03 points or total MAE by at least .08*, without sacrificing >0.5 percentage points SU or >0.0015 Brier versus lighter carryover, to qualify for research selection. No winner is forced if none pass. These rules were set prior to seeing February outcomes.

`data/reports/matchup_v05_backtest_2026.json` is generated from source-verified game stats but from **retrospective 2025–26 fetches**. Games finalized after a hypothetical cutoff are excluded from profile training, but exact historical source availability is *not independently authenticated*. Those games have **no authenticated historical book lines**, so do NOT claim historical ATS/O-U results or sports betting ROI.

## Genuine prospective ATS and O/U evaluation

The existing no-cost ESPN scoreboard already preserves timestamped DraftKings and other provider **source-observed pre-tip spread/total offers**, with provider identity, original game IDs, raw response hashes and capture time. These are not necessarily official closing prices or FanDuel availability.

After a historical research fit is available, `matchup_v05.py` freezes **both** distinct research variants under immutable model IDs in `data/matchup_v05_shadow_ledger.jsonl`, prior to actual kickoff. No original champion record is changed.

`data/reports/matchup_v05_prospective_2027.json` grades:

- **Identical game IDs and final scores**, with a genuine original v0.4 freeze within 2 hours of the research freeze.
- The **same quote captured before both forecasts** for each v0.4/challenger matchup, preserving actual provider and spread signs; game identity and source time are checked.
- Margin/total MAE versus actual scores **and** provider-book margin/total MAE on the same game set.
- Observed ATS only for model–market spread differences of **at least 3 points**; O/U only for totals differences of **at least 4 points**. Track wins, losses, pushes, selections and actual quote coverage separately, for both challenger and champion. No claim of return on investment without the actual odds/price and offer availability.

## Website integration / release gate

The existing official model and website picks remain `CBB_SCORE_RIDGE_V0_4_D1_PRIOR30_A1_5`. Experimental matchup predictions may appear **inside a separate expandable research preview**, only when a valid frozen v0.4 official game projection exists. They are labeled unvalidated, are never swapped into the official prediction, and cannot publish forecasts generated after tip.

**Promotion to the live model** is blocked until at least 150 genuinely frozen source-observed paired games, enough quoted ATS/O-U selections across multiple independent periods, reliable margin and total improvements (not just winner%), and no strong betting-market or calibration regressions. A historical February score improvement alone does not warrant production promotion.

## Commands

```bash
python -m pytest -q
node --test tests/test_board.cjs
python matchup_v05.py --root data --historical
python matchup_v05.py --root data
```

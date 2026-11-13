# V0.5 Possession-event simulator: source-backed 2P / 3P / free throws / turnovers

**Repository:** `The-Caves/cbb-model` only. Nothing is promoted. Original v0.4 and its published, immutable forecast ledger are unchanged.

## Purpose

The previous score-only ridge reaches attractive SU win percentages in mismatched nonconference slates, yet a 30-point predicted win that finishes as a 10-point victory can miss ATS. The old pace × efficiency model had 11.074 margin MAE versus 9.998 for v0.4 on 1,194 shared retrospective games. We therefore test a **scoring-opportunity process** rather than just a new regression on raw factors.

We retain the better-performing **0.15 previous-season score prior** as a strong baseline and experiment with bounded simulation/blending. Extra complexity is not promotion evidence on its own.

## Real boxscore mechanisms

From source-reconciled ESPN team boxscore fields (both side identities and final scores verified):

- Observed 2-point field goals / attempts = FGM minus 3PM, FGA minus 3PA. Direct 3PM / 3PA, FTM / FTA are also known.
- Pace and possession estimates, offensive rebounds, turnovers, effective FG%, three-point and free-throw attempt rates are already collected.
- Shrunk opponent-adjusted 2P and 3P shooting percentages are fitted using attempts as the sample size, with a modest previous-season 8% boxscore contribution and conservative priors. Opponent defensive FT-percentage effects are intentionally **not** imputed.
- For each team, projected turnovers come from shared matchup possessions and its own adjusted TOV tendency against opponent defensive allowed TOV. Estimated missed-shot offensive rebounds generate additional shooting attempts.
- Shot volume uses the algebraic possession balance `possessions ≈ FGA - offensive_rebounds + turnovers + 0.475*FTA`; 2PA vs 3PA and FT opportunities yield stochastic scoring.
- Shared matchup tempo represents both offenses' pace, with fixed and modest +3.5-point total home advantage split between teams, zero at a neutral venue.

This is a Monte Carlo **aggregate-event approximation**. It is NOT play-by-play or independent calibrated injury/lineup evidence. Simulated shooting and turnovers are uncertain and real-game play is correlated. There is no assumption all recent boxscore patterns persist or any guaranteed betting edge.

## Fixed variants / controls

512 seeded, reproducible pseudo-random draws per game. Identical cutoff/game ID/algorithm version gives the same result. Four predeclared candidates on top of `prior_weight=0.15`:

| Candidate | Forecast |
|---|---|
| `pure` | Simulated score/margin/total directly |
| `blend20` | 20% simulated forecast, 80% lighter-carryover ridge |
| `blend40` | 40% simulated forecast, 60% lighter-carryover ridge |
| `trained_blend` | Independent learned 1-D margin and total blend weights, constrained 0–65%, ridge penalty 900; intercept constrained ±2 |

All candidates restrict simulation corrections to ±12 margin points and ±15 total points to protect the incumbent from data artifacts. Incomplete opponent rates or fewer than two current observed games on either side produce PASS, not an invented factor.

**Historical evaluation:** train blend calibration on November–December 2025, select a model on January 2026 only (margin MAE improvement at least .03 or total MAE at least .08, SU loss no worse than .5 percentage points, Brier regression ≤.0015), report February 2026 historical diagnostics and favorite/near-pick'em/low-sample breakdown. Both v0.4 and lighter carryover are fitted **at exactly the same pregame time and on identical game IDs**. No late games enter earlier as-of score or shooting data.

**Historical limitation:** ESPN 2025–26 source boxscores were downloaded retrospectively, so their publication timestamps at hypothetical 2025 prediction cutoffs cannot be authenticated. February 2026 has already been inspected in previous experiments. All such results are diagnostic, not a truly new independent holdout or verified 2025 ATS / O-U market history.

## Genuine prospective tracking

`possession_simulator_v05.py` separately stores immutable source-time-aware predictions in `data/possession_v05_shadow_ledger.jsonl`, including training cutoffs, stable IDs, actual pre-tip capture, draw count, and simulated pace / margin / total dispersion. It grades independently frozen outcomes only when an original v0.4 pre-tip prediction exists on the **same** matchup within two hours. A sportsbook quote must be actually source-observed before **both** forecast captures; compare each model against the **same provider quote**, preserving signs, pushes and quote time. Predeclared ATS selection: predicted spread-market gap ≥3 points; O/U selection: predicted total-market gap ≥4 points. No ROI claims without real odds, price, line availability, and calibration.

Published research:
- `data/reports/possession_simulator_v05_backtest_2026.json`
- `data/reports/possession_simulator_v05_prospective_2027.json`

Do **not** change `cbb.py` active model, original `data/forecast_ledger.jsonl`, or the official website v0.4 forecast based on historical replay. Promotion requires independently frozen new-season sample, robust margin/total, real observed ATS/O-U data and no calibration regression. Minimum 150 matched pre-tip games across independent calendar periods before review; insufficient data must explicitly fail closed.

```bash
python -m pytest -q tests/test_possession_simulator_v05.py
python possession_simulator_v05.py --root data --historical
python possession_simulator_v05.py --root data
```

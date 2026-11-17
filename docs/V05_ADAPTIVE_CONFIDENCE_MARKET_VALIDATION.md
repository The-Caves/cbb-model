# v0.5 model priorities: current-season evidence, sample calibration and market-relative evaluation

**Scope**: `The-Caves/cbb-model` only. The user-selected **Blend20** stays featured on the website. Original v0.4 predictions, v0.5 frozen forecasts, actual observed book quotes, and original training histories are immutable.

## 1. Recover missing in-season team statistics first

The independent D-I eligibility audit at 2026-11-17T06:13:33Z recorded **628 verified completed D-I games, 435 source-verified box scores and 193 missing game box scores**, with **39 disputed source scores** quarantined. The team boxscore collector previously allocated 15% to current-season, 55% to previous-season and 30% to older historic seasons.

As of PR #18, the no-cost source collector uses **80% current / 15% previous / 5% older** for an 80-request batch while current-season source coverage remains below 95% and at least 25 games remain missing. Once recovered, original historic research quotas resume. Missing/non-D-I/disputed games are never assigned fabricated box scores. Report selected/captured/rejected counts by season, and compare coverage before and after actual collection. Scheduled backfill is maintained; no claim of immediate 193-game recovery before a collector executes.

ESPN summary source intake remains bounded, rate limited, audited and source-hashed. Historical records obtained today cannot retrospectively authenticate publication at old dates. Rejected and quarantined games remain excluded from training.

## 2. Opponent-strength and sample-size reliability

Research challenger `CBB_V05_POSSESSION_EVENTS_20261113_ADAPTIVE_MATURITY` uses the same 512-draw source-validated possession-event simulation and `prior_weight=0.15` lighter carryover base as Blend20. The only new hypothesis is **how much to trust the simulator** on an upcoming specific matchup, using evidence that existed strictly **before that game**:

- Minimum verified D-I games for both teams
- Minimum actual captured D-I team boxscore samples
- Fraction of current results with captured boxscore evidence
- Upcoming opponent's as-of opponent-adjusted strength compared with each team's past average opponent strength (schedule step-up)
- Explicit unknown/uncertainty label when schedule strength data is missing

Fixed prior-to-evaluation weight:

```text
maturity = min(1, min_current_D1_games/10)
         * min(1, min_verified_box_games/8)
         * min(1, source_coverage/0.8)
stepup_discount = max(0.50, 1 - max(0, stepup_points-6)/40)
if schedule quality unavailable: stepup_discount = 0.60
simulation_weight = 0.10 + 0.30 * maturity * stepup_discount
```

Weight ranges **10% to 40%** and applies to both margin and total corrections with existing maximum correction caps. It is not a conference label, retrospective team profile, verified player-injury input, sportsbook-anchored calibration, or new release. All observed inputs and the selected weight are frozen in the independent pretip research record; inconsistencies are rejected. No model is silently updated using game results post-tip.

## 3. Betting-market-relative proof rather than misleading straight-up accuracy

For every genuinely completed, matched frozen research candidate and original v0.4 game, require the *same* source-observed bookmaker quote captured before **both** forecasts. Enhanced diagnostics now include:

- Actual source-quoted game coverage / total frozen paired outcomes
- Challenger vs v0.4 **paired** margin and total MAE differences and vs provider's implied favorite margin / quoted total MAE
- Source-observed ATS decisions with >=3 point model-minus-market edge and O/U decisions with >=4 points of edge (no action on no edge)
- By-market favorite size: under 6, 6–under 15, and 15+ points; unknown lines are explicitly missing
- By verified boxscore sample size, provider identity, and UTC game date
- W/L/push, qualifying selections, quote availability and explicit market-source data limitations
- For every new variant, **direct same-game, within-two-hour matched comparisons against the website-featured Blend20**, using the same pre-tip bookmaker quote observed before both models and the original baseline. Incompatible captures and missing counterpart forecasts are excluded; do not compare a new 2-game challenger denominator with an older 43-game featured denominator.

No unquoted historical betting lines, assumption of closing lines, fabricated FanDuel offerings, profitability or ROI predictions. Source lines should NOT train their own retrospective ATS edge without independent out-of-sample evidence and reliable price capture.

## 4. Adaptive simulation-blend experiment

Keep previous experimental variants (pure, Blend20, Blend40, trained blend, total-only split) with **unchanged** version IDs. Add a new named variant only, evaluated on *exactly the same* game IDs, source timing and as-of score model.

Existing historical selection:
- November–December 2025 fit when a candidate requires training
- January 2026 validation / fixed improvement gate
- February 2026 repeated **retrospective, previously inspected** diagnostic; not an untouched holdout

All historical boxscore records were gathered after some original games. Experimental research is not independently source-authenticated for 2025/26. Future genuine pre-tip frozen 2026/27 games are the primary source of independent grade improvement. At least 150 independent frozen, matched completed results and sufficient source-observed ATS/O-U selections across multiple calendar segments are required **before considering a release**. Do not automatically promote adaptive_maturity; the featured website remains Blend20 until separately justified.

## Verification

```bash
python -m pytest -q tests/test_boxscore_features.py tests/test_possession_simulator_v05.py
python -m pytest -q
python possession_simulator_v05.py --root data --historical
python possession_simulator_v05.py --root data
```

Reports:
- `data/advanced/coverage_audit.json` and `data/advanced/boxscore_coverage.json` capture actual verified source coverage
- `data/reports/possession_simulator_v05_backtest_2026.json` compares unchanged model families on same games
- `data/reports/possession_simulator_v05_prospective_2027.json` captures genuine pre-tip accuracy, paired source-quoted ATS/O-U and new market/context slices
- `data/possession_v05_shadow_ledger.jsonl` stores immutable candidate snapshots

**Promotion decision**: no v0.5 adaptation is justified merely from historical game winner accuracy or a tiny number of prospective book selections. Keep objective margin/total error and authenticated market-relative improvement paramount.

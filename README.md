# CBB Intelligence | 2026–27 NCAA Men's Basketball

**Live site:** https://the-caves.github.io/cbb-model/

Separate college-basketball model. **No paid services**: public ESPN endpoints (unofficial, no API key), Python/numpy/scipy, standard GitHub Actions on a **public** repository, and free GitHub Pages. If the repo becomes private, the workflow is disabled. **Research / `PASS_UNVALIDATED`.** No verified profitable betting strategy, sportsbook prices or player props are published.

## v0.3 corrective release — November 3, 2026

Audit after the first deployment found home-favorite margin clustering (112 frozen v0.2 games, average model home margin ~12.45 points, median 12.61) and misleading school classifications. The prior model estimated a venue parameter from scores in a slate dominated by Division-I home games against much weaker visiting opponents. It carried that spurious venue advantage forward while heavily shrinking team strengths.

**Fixes:**

- Replace freely estimated, confounded home-court value with a **fixed 3.5-point research assumption** for non-neutral sites (zero at neutral sites). Reduce excessive score-strength shrinkage (ridge alpha 15 to 3), and increase prior-season weighting for early-season context (0.12 to 0.60). **These are candidate modeling choices, not optimized betting parameters.** The push workflow replays 2025–26 walk-forward results and preserves the original v0.2 report for comparison. The live site must not present a new-version accuracy number until that replay succeeds.
- Pull an independent **ESPN men's college basketball team directory** (`/teams?limit=500`) and verify its team count before accepting it. Canonical school labels are mapped by **stable ESPN team ID**, never guessed by name. Games against non-listed (non-D-I or unverified) opponents remain on the game board with explicit *Other/unverified opponent* labels, but only directory-listed Division-I teams enter the scouting directory. An invalid directory is rejected, preserving the last verified cache.
- Preserve old **genuine v0.2 pre-tip freezes** in the ledger; new v0.3 predictions get new identifiers, and prior forecasts are never silently rewritten to look like v0.3 predictions. All versions must be pre-tip, model-version allowed, and internally consistent to appear on the board. New records freeze team IDs and tip times.
- Game board **defaults to the viewer's local today**, centers that date in the horizontal date strip, displays *all* games including in-progress games, and on initial load scrolls to the **next not-yet-started game**. Earlier/in-progress games are still accessible by scrolling upward. If no future game exists today, there is no automatic jump. Date navigation and search remain available.
- In-progress games can show authentic in-progress scores from the latest ESPN snapshot. Otherwise, displayed score numbers are visibly marked **PROJ**, not mistaken for live scores. New version labeling and historical-model-comparison labels are explicit.

## Possession/Four Factors collector — first verified run (November 3, 2026)

- [Cloud workflow 41765137313](https://github.com/The-Caves/cbb-model/actions/runs/41765137313) **passed**: 27 Python tests, 2 JavaScript board tests, 36/36 genuine ESPN two-sided game summaries accepted and 0 rejected. Result saved in [coverage report](data/advanced/boxscore_coverage.json).
- Research-only postgame source captures contain estimated possessions, effective field goal %, turnover %, offensive rebound %, free throw and 3-point attempt rates, and points/100 estimated possessions; game and team IDs, final scores, capture time, and source hashes are checked.
- All 36 current-season Division-I profile entries are **INSUFFICIENT** until they have at least five matched D-I vs D-I box scores. The active score model is still **v0.3**; the new features have **not** been used to adjust picks or imply betting edge.
- Free ESPN game-summary API with 1.2-second request pacing, maximum 36 games per scheduled research pass; missing/contradictory stats are explicitly rejected. A research-source failure is visible but will not take down the active site.
- See [possession research milestone](docs/POSSESSION_DATA.md). Next: expand authenticated historical efficiency coverage, build chronological opponent-adjusted pace/efficiency challenger, conduct out-of-sample comparisons before promotion.

## Automated training and data integrity

Workflow: [CBB free model and website](.github/workflows/cbb.yml) — twice daily in-season and manually dispatchable. It tests first; on initial run it downloads historical 2024–25 and 2025–26 finals, then runs chronological 2025–26 replay. On code pushes it recalculates the revised model's backtest against the same historical dataset, writes an immutable copy of the v0.2 summary if present, refreshes 2026–27 scores, captures new pre-tip forecasts, grades completed original forecasts, exports `site/data.json`, and publishes GitHub Pages.

**No synthetic predictions are displayed as actual model picks.** The website only publishes archived forecast records captured *strictly before tip* and no more than 72 hours earlier. Historic data was acquired retrospectively, so the 2025–26 replay is a **score-only reconstruction**, not an independently time-stamped pregame wager simulation. Historic sportsbook ATS and O/U records are unavailable; model margin and total **are not bookmaker spreads or totals**.

Retrospective version tracking is required: the initial **v0.2** evaluation replayed 6,150 games with 67.67% overall straight-up winner accuracy, but the mature-team subset was only 63.34%. Do not extrapolate that number to v0.3 or live wagering. See [`data/reports`](data/reports) and the site's performance page after the latest Actions run.

## Reproduce locally

```bash
python -m pip install -r requirements.txt pytest
python -m pytest -q
node --test tests/test_board.cjs
python cbb.py bootstrap   # first-time historical ingestion; requires network
python cbb.py backtest --season 2026
python cbb.py daily       # capture today's source and pre-tip predictions
python site_export.py --root data --site site
python -m http.server 8000 --directory site
```

## Next milestones (not finished)

1. **Opponent-adjusted possession efficiency:** pace and possessions, eFG%, rebounds, turnovers and free-throw factors using verified, timestamp-safe box scores; correct for non-D-I opponents and remaining schedule strength.
2. **Individual-player context:** returning production, transfers, lineup minutes, roster changes and verifiable availability; no unsupported starter claims.
3. **Model competition:** chronological ablations, season-transition calibration, neutral/home splits and prospective performance; prohibit in-sample-only promotion.
4. **Market evidence:** independently sourced timestamped real spreads/totals and prices; then true ATS and O/U grading, never invented sportsbook lines.
5. **Website polish:** incremental game-state refreshes and richer team scouting, with model metadata and clear data-source warnings.

Do not let work on [`The-Caves/cbb-model`](https://github.com/The-Caves/cbb-model) spill into the separate [`The-Caves/cfb-model`](https://github.com/The-Caves/cfb-model) repository; always confirm the repository name and current `main` before writing.

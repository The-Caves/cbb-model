# CBB Intelligence | 2026–27 NCAA Men's Basketball

A free-only, open-source college basketball prediction research model and responsive website.

**Status: Research / `PASS_UNVALIDATED` — no published betting edge.** Projections are printed only when an actual pre-tip forecast snapshot exists. The score-model foundation is v0.2; future milestones add pace, Four Factors, roster and verified sportsbook data. No paid data API, AWS service, premium runner, hosting plan or payment method is configured.

## Website

- Mobile-friendly **Games** board with chronological matchups, score projections, model-side indicator, opponent records, search, day tabs and status filters
- **Performance** view with real frozen pre-tip forecast tracking, last 30 completed predictions, and a **separate** retrospective 2025–26 historical replay
- **Teams** view with real season win/loss and points scored/allowed summaries
- **Model** view with methodology, roadmap and clear uncertainty flags
- Any missing projection is **shown as missing**, not reconstructed after the result.
- There are **no ATS or total bets** while authenticated sportsbook lines are absent.

## Deployment status (November 3, 2026)

**Model pipeline live, Pages publication blocked by one repository setting.** In the first [GitHub Actions run](https://github.com/The-Caves/cbb-model/actions/runs/41740261197), the tests, historical ingestion, walk-forward baseline, current-season intake, frozen prediction archive, JSON website exporter, snapshot commit, and Pages artifact upload **all succeeded**. The deploy job returned HTTP 404 with an explicit instruction to enable GitHub Pages.

**Required one-time action by repository owner:** [Settings → Pages](https://github.com/The-Caves/cbb-model/settings/pages) → **Build and deployment → Source: GitHub Actions** → Save. Then use the Actions page above to **Re-run failed jobs** (or manually dispatch the workflow). Once the deployment job succeeds, the expected website is https://the-caves.github.io/cbb-model/ . That URL must not be claimed live until deployment succeeds.

## Initial verified model outputs

- Retrospective, day-forward **2025–26** score-only replay: **6,150 evaluated games**, straight-up accuracy **67.67%**, projected margin MAE **12.627** points, projected total MAE **14.177** points. Naive baseline across the same 6,150 games scored **64.05%** SU.
- Subset after each team has played 5 current-season games: **4,907 games**, straight-up accuracy **63.34%**. The sample is not prospectively validated and this is not evidence of profitable bets.
- At initial current-season refresh: **376 displayed games**, **112 verified pre-tip projections** and **0 prospectively graded finals**. All initial projections are **PASS_LOW_SAMPLE**, not qualified betting recommendations.
- View [actual archived model output](site/data.json) and [historical backtest metrics](data/reports/backtest_2026.json).

No special environment variables, API keys, tokens, or cloud account billing is needed. The workflow checks `github.event.repository.private == false` and will not execute on private repositories; the publication source is GitHub Pages. Workflow uses standard Linux runners and free public scoreboard endpoint, never a paid sportsbook or compute service.

### Local run

```bash
python -m pip install -r requirements.txt pytest
python -m pytest -q
python cbb.py bootstrap  # one time historic public data; requires internet
python cbb.py daily      # daily live ingestion and pretip freeze
python site_export.py --root data --site site
python -m http.server 8000 --directory site
# http://localhost:8000/
```

The model successfully ingested historical scores and published current-season site data. It never reconstructs an after-tip pick for a game lacking a frozen pre-tip prediction.

## Data and methodology safeguards

- The baseline (`cbb.py`) uses regularized, opponent-adjusted points scored and allowed, a modeled home-court effect and downweighted prior-season score results. It is **not** possession-normalized yet.
- A game's model record is shown only if the append-only ledger contains a matching ID, teams, scheduled tip, model version, consistent score arithmetic and a snapshot time **strictly before tip** and no more than 72 hours earlier.
- Results are graded only after an observed final score and a qualifying archived forecast. Prospective grading is separate from retrospective historical replay.
- Historic 2025–26 game outcomes are re-evaluated chronologically, but the ESPN data source was acquired after those games: avoid interpreting that evaluation as a historically captured live forecast.
- Basketball lines are not fabricated: `market_spread` and `market_total` are `null` until a real timestamped source is implemented.
- Model win probabilities from the underlying score regression are not calibrated.
- `data/raw/**/` gzipped provider snapshots are excluded from Git commits; small manifest files, full scoreboard CSV and prediction ledger remain in Git source control. Do not treat Git alone as tamperproof records.

## Current limitation

The site’s publication is awaiting the owner's one-time Pages source selection. The underlying GitHub model and data jobs have passed. Prediction quality remains research-only, and market odds, opponent-adjusted possessions, player impact, and calibrated probabilities are future milestones.

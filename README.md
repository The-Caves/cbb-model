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

## Launch on GitHub Pages (free public repo)

1. Enable the GitHub app's installation access to the `The-Caves/cbb-model` repository (under GitHub → Settings → Applications → Installed GitHub Apps → Configure → Repository access). Selecting a repository in the GitHub account alone does **not** grant the connector write access.
2. Commit all files from this package to the `main` branch, preserving `.github/workflows/cbb.yml` and `site/` paths.
3. Go to repository **Settings → Pages → Build and deployment → Source = GitHub Actions**.
4. Trigger **Actions → CBB free model and website → Run workflow** or wait for the initial push. It runs Python tests, backfills 2024–25/2025–26 game scores, performs chronological score-only research backtesting, refreshes 2026–27 games, archives genuine pre-tip model outputs, exports `site/data.json`, and publishes the `site/` directory.
5. After the `deploy` job succeeds, check `https://the-caves.github.io/cbb-model/` (expected URL, **NOT VERIFIED LIVE** until deployment succeeds).

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

The site works before data ingestion but intentionally shows an empty state with no invented games. After real data is collected, `site/data.json` is updated by the exporter.

## Data and methodology safeguards

- The baseline (`cbb.py`) uses regularized, opponent-adjusted points scored and allowed, a modeled home-court effect and downweighted prior-season score results. It is **not** possession-normalized yet.
- A game's model record is shown only if the append-only ledger contains a matching ID, teams, scheduled tip, model version, consistent score arithmetic and a snapshot time **strictly before tip** and no more than 72 hours earlier.
- Results are graded only after an observed final score and a qualifying archived forecast. Prospective grading is separate from retrospective historical replay.
- Historic 2025–26 game outcomes are re-evaluated chronologically, but the ESPN data source was acquired after those games: avoid interpreting that evaluation as a historically captured live forecast.
- Basketball lines are not fabricated: `market_spread` and `market_total` are `null` until a real timestamped source is implemented.
- Model win probabilities from the underlying score regression are not calibrated.
- `data/raw/**/` gzipped provider snapshots are excluded from Git commits; small manifest files, full scoreboard CSV and prediction ledger remain in Git source control. Do not treat Git alone as tamperproof records.

## Current limitation

The source and website are packaged for publication, but the **GitHub app integration currently authorizes `cfb-model` only**. Until access to `cbb-model` is granted, we cannot commit the package or verify a live GitHub Pages URL. The model has not ingested historical data or published real picks in this packaging environment.

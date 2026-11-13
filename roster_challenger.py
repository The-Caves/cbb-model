"""Research-only roster-aware v0.5 challenger with genuine pre-tip freezes.

No roster inference from names, conference tiers or retrospective captures.
Unknown history or lineup continuity fails closed; original v0.4 persists.
"""
import argparse
import json
import math
from datetime import timedelta
from pathlib import Path

from cbb import (ACTIVE_LIVE_VERSION, dt, eligible, fit, iso, predict,
                 read_csv, utcnow)
from team_directory import load_directory
from roster_intelligence import continuity, read_jsonl, prior_d1_game_counts
from v05_transition_challenger import frozen_v04, metrics, model_observation

VERSION = "CBB_ROSTER_PRIOR_V0_5_RESEARCH"
MIN_PRIOR_PLAYER_GAMES = 5
MIN_COMPARISONS_FOR_REVIEW = 150


def adjusted_prior_multipliers(profiles):
    """A conserved, interpretable prior, no false claims of player projections.

    At five verified prior-season games, data confidence is deliberately low.
    At 20+ verified games, a team with no returning sample minutes can reduce
    its prior-era evidence to 40%. Unknown teams remain *unmodified* (1.0).
    """
    weights = {}
    for tid, p in profiles.items():
        if p.get("status") != "RESEARCH_SAMPLE_NOT_FULL_SEASON":
            continue
        n = int(p["prior_games_verified"])
        share = float(p["retained_minutes_share"])
        coverage = p.get("prior_player_game_coverage")
        if (n < MIN_PRIOR_PLAYER_GAMES or coverage is None or
                not math.isfinite(float(coverage)) or
                not 0.15 <= float(coverage) <= 1.0 or
                not math.isfinite(share) or not 0 <= share <= 1):
            continue
        # A selected five-game sample is not the same as full-season roster
        # evidence. Shrink its effect by verified prior-season game coverage.
        confidence = min(1.0, n / 20.0) * min(1.0, float(coverage) / 0.60)
        weights[str(tid)] = round(1.0 - 0.60 * (1.0 - share) * confidence, 5)
    return weights


def valid(candidate, game, now):
    try:
        c, tip = dt(candidate["captured_utc"]), dt(game["tip_utc"])
        return (candidate["model"] == VERSION
                and candidate["game_id"] == str(game["game_id"])
                and str(candidate["home_id"]) == str(game["home_id"])
                and str(candidate["away_id"]) == str(game["away_id"])
                and candidate["tip_utc"] == game["tip_utc"]
                and c <= now and c < tip
                and tip - c <= timedelta(hours=72)
                and dt(candidate["training_latest_tip"]) < c
                and abs(float(candidate["pred_margin"]) -
                        (float(candidate["pred_home"]) -
                         float(candidate["pred_away"]))) <= .02
                and abs(float(candidate["pred_total"]) -
                        (float(candidate["pred_home"]) +
                         float(candidate["pred_away"]))) <= .02
                and str(game["home_id"]) in candidate["verified_roster_teams"]
                and str(game["away_id"]) in candidate["verified_roster_teams"])
    except (KeyError, ValueError, TypeError, OverflowError):
        return False


def freeze(games, verified, player_rows, rosters, ledger, now=None, year=None):
    now = now or utcnow()
    year = year or now.year + int(now.month >= 7)
    prior_counts = prior_d1_game_counts(games, now, year-1, verified)
    profiles = continuity(player_rows, rosters, now, year, verified,
                          prior_d1_games=prior_counts)
    multipliers = adjusted_prior_multipliers(profiles)
    eligible_games = [g for g in games
                      if int(g["season"]) == year
                      and str(g["home_id"]) in multipliers
                      and str(g["away_id"]) in multipliers
                      and str(g.get("score_conflict", "0")) not in ("1", "True", "true")
                      and str(g.get("identity_conflict", "0")) not in ("1", "True", "true")
                      and now < dt(g["tip_utc"]) <= now + timedelta(hours=72)]
    coverage = {"roster_snapshot_teams": len(profiles),
                "teams_with_prior_sample": sum(
                    p.get("prior_games_verified", 0) >= MIN_PRIOR_PLAYER_GAMES
                    for p in profiles.values()),
                "teams_with_adequate_player_coverage": len(multipliers),
                "source_backed_multiplier_teams": len(multipliers),
                "candidate_games_eligible": len(eligible_games),
                "source_backed_team_multipliers": multipliers}
    if not eligible_games:
        return {"status": "PASS_INSUFFICIENT_ROSTER_COVERAGE",
                "captured": 0, "coverage": coverage}
    try:
        candidate_model = fit(games, now, test_season=year, retrospective=False,
                              verified_ids=verified, alpha=1.5,
                              prior_weight=0.30,
                              prior_team_multipliers=multipliers)
    except ValueError:
        return {"status": "PASS_INSUFFICIENT_TEAM_TRAINING",
                "captured": 0, "coverage": coverage}
    path = Path(ledger)
    old_keys = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                obj = json.loads(line)
                old_keys.add((obj.get("game_id"), obj.get("captured_utc")))
    frozen = []
    for g in eligible_games:
        p = predict(candidate_model, g)
        if p is None:
            continue
        p.update({
            "model": VERSION, "captured_utc": iso(now),
            "research_only": True, "champion_version": ACTIVE_LIVE_VERSION,
            "verified_roster_teams": sorted((str(g["home_id"]),
                                             str(g["away_id"]))),
            "retained_minutes": {
                str(g["home_id"]): profiles[str(g["home_id"])]["retained_minutes_share"],
                str(g["away_id"]): profiles[str(g["away_id"])]["retained_minutes_share"]},
            "roster_source_captured_utc": {
                str(g["home_id"]): profiles[str(g["home_id"])]["roster_source_captured_utc"],
                str(g["away_id"]): profiles[str(g["away_id"])]["roster_source_captured_utc"]},
            "roster_multiplier": {
                str(g["home_id"]): multipliers[str(g["home_id"])],
                str(g["away_id"]): multipliers[str(g["away_id"])]},
            "prior_player_game_coverage": {
                str(g["home_id"]): profiles[str(g["home_id"])]["prior_player_game_coverage"],
                str(g["away_id"]): profiles[str(g["away_id"])]["prior_player_game_coverage"]},
        })
        if (p["game_id"], p["captured_utc"]) not in old_keys and valid(p,g,now):
            frozen.append(p)
            old_keys.add((p["game_id"], p["captured_utc"]))
    if frozen:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for row in frozen:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
    return {"status": "FROZEN_AS_OF_ROSTER_RESEARCH_ONLY",
            "captured": len(frozen), "coverage": coverage}


def grade(games, verified, champion_ledger, candidate_ledger, now=None,
          year=None):
    now = now or utcnow()
    year = year or now.year + int(now.month >= 7)
    champion = frozen_v04(games, champion_ledger, now, verified, year)
    by_id = {str(g["game_id"]):g for g in games}
    last = {}
    if Path(candidate_ledger).exists():
        for line in Path(candidate_ledger).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            gid = str(r.get("game_id"))
            g = by_id.get(gid)
            if (g is None or gid not in champion or
                    not eligible(g, now, retrospective=False) or
                    not valid(r, g, now)):
                continue
            # Do not trust a roster captured after the candidate was frozen.
            if any(dt(t) > dt(r["captured_utc"])
                   for t in r.get("roster_source_captured_utc", {}).values()):
                continue
            if gid not in last or last[gid]["captured_utc"] < r["captured_utc"]:
                last[gid] = r
    baseline, challengers = [], []
    for gid, p in last.items():
        g = by_id[gid]
        baseline.append(model_observation(g, champion[gid], 12.0))
        challengers.append(model_observation(g, p, 12.0))
    return {"status": "GENUINE_PRETIP_RESEARCH_ONLY",
            "paired_games": len(baseline), "minimum_for_review": MIN_COMPARISONS_FOR_REVIEW,
            "v04": metrics(baseline), "roster_candidate": metrics(challengers),
            "promotion_gate": ("READY_FOR_REVIEW_NOT_AUTOMATIC_PROMOTION"
                               if len(baseline) >= MIN_COMPARISONS_FOR_REVIEW
                               else "INSUFFICIENT_PAIRED_GAMES"),
            "active_model_unchanged": True,
            "warning": ("Source-backed partial prior player minutes, not full-season "
                        "returning production. No roster forecasts or verified betting edge.")}


def run(root="data", now=None):
    root=Path(root)
    now=now or utcnow()
    try: verified=load_directory(root/"team_directory.json")
    except (OSError, ValueError): verified={}
    games=read_csv(root/"games.csv")
    players=read_jsonl(root/"advanced/player_boxscore_games.jsonl",
                       unique_key=lambda row: str(row["game_id"]))
    snapshots=read_jsonl(root/"advanced/roster_snapshots.jsonl")
    out=freeze(games,verified,players,snapshots,
               root/"roster_shadow_ledger.jsonl",now)
    graded=grade(games,verified,root/"forecast_ledger.jsonl",
                 root/"roster_shadow_ledger.jsonl",now)
    report={"model":VERSION,"as_of_utc":iso(now),
            "collector_player_game_records":len(players),
            "collector_roster_snapshots":len(snapshots),
            "freeze":out,"grade":graded,"live_model_unchanged":True}
    target=root/"reports/roster_challenger_prospective.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",
                      encoding="utf-8")
    return {"status":out["status"],"collected_games":len(players),
            "coverage":out["coverage"],"paired":graded["paired_games"]}


if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",default="data")
    args=ap.parse_args()
    print(json.dumps(run(args.root), indent=2))

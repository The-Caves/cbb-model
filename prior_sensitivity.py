"""Research-only carryover sensitivity replay. Never changes live picks.

Compare last-season observation weights on the same completed D-I games.
Historical score archives were acquired retrospectively, not authentic
historical pregame snapshots. This is a diagnostic, not model promotion.
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path
from cbb import dt, iso, read_csv, fit, predict, metrics
from team_directory import load_directory

WEIGHTS={"v03_60pct_per_game":0.60,"lighter_30pct":0.30,"lighter_10pct":0.10}

def compare(games, directory, season=2026, start="2025-11-01", end="2025-12-20"):
    ids={str(x) for x in directory}
    groups=defaultdict(list)
    for g in games:
        if (int(g["season"])==season and start<=g["tip_utc"][:10]<=end
            and str(g["home_id"]) in ids and str(g["away_id"]) in ids
            and str(g.get("completed")) in ("1","true","True")
            and str(g.get("score_conflict","0")) not in ("1","true","True")
            and g.get("home_score") not in ("",None)
            and g.get("away_score") not in ("",None)):
            groups[g["tip_utc"][:10]].append(g)
    results={k:{"all":[],"cold_start":[],"established_teams":[]} for k in WEIGHTS}
    for day in sorted(groups):
        cutoff=dt(day+"T00:00:00Z")
        models={}
        for k,w in WEIGHTS.items():
            try:models[k]=fit(games,cutoff,test_season=season,retrospective=True,prior_weight=w)
            except ValueError:models[k]=None
        for g in groups[day]:
            forecasts={k:predict(models[k],g) if models[k] else None for k in WEIGHTS}
            if any(p is None for p in forecasts.values()):
                continue  # Same matched games in every comparison.
            margin=float(g["home_score"])-float(g["away_score"])
            total=float(g["home_score"])+float(g["away_score"])
            for k,p in forecasts.items():
                if dt(p["training_latest_tip"])>=cutoff:
                    raise AssertionError("Same-day or future scores used")
                obs={"pred_margin":p["pred_margin"],"pred_total":p["pred_total"],
                     "actual_margin":margin,"actual_total":total,
                     "correct":int((p["pred_margin"]>0)==(margin>0))}
                results[k]["all"].append(obs)
                sub="cold_start" if p["status"]=="PASS_LOW_SAMPLE" else "established_teams"
                results[k][sub].append(obs)
    return {"status":"RETROSPECTIVE_SENSITIVITY_NO_PROMOTION",
            "season":season,"start":start,"end":end,
            "matched_games":len(results[next(iter(WEIGHTS))]["all"]),
            "verified_d1_teams":len(ids),"active_model_unchanged":True,
            "weight_explanation":"Per previous-season game, NOT share of total effective weight",
            "variants":{k:{"prior_season_per_game_weight":WEIGHTS[k],
                           **{s:metrics(v) for s,v in parts.items()}}
                        for k,parts in results.items()},
            "historical_source_time_authenticated":False,
            "warning":"No roster-continuity information in this experiment. Do not tune and promote on the same retrospective season."}

def run(root="data",season=2026):
    root=Path(root)
    try:ids=load_directory(root/"team_directory.json")
    except (OSError,ValueError):ids={}
    report=compare(read_csv(root/"games.csv"),ids,season=season)
    out=root/"reports"/f"prior_sensitivity_{season}.json"
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    return report

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--root",default="data")
    p.add_argument("--season",type=int,default=2026)
    a=p.parse_args()
    print(json.dumps(run(a.root,a.season),indent=2))

"""Compare score-rating shrinkage and roster-era priors on matched D-I games.

Research only. These are retrospectively captured final scores, NOT frozen
historical market lines or pregame provider snapshots. Do not auto-promote.
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path
from cbb import dt, fit, metrics, predict, read_csv
from team_directory import load_directory

DATES={
    "november":["2025-11-08","2025-11-10","2025-11-12","2025-11-14"],
    "january":["2026-01-08","2026-01-10","2026-01-12","2026-01-14"],
    "february":["2026-02-08","2026-02-10","2026-02-12","2026-02-14"],
}
VARIANTS={
    "v03_all_prior60_a3":(False,.60,3.0),
    "d1_prior30_a3":(True,.30,3.0),
    "d1_prior30_a1_5":(True,.30,1.5),
    "d1_prior30_a0_75":(True,.30,.75),
    "d1_prior30_a5":(True,.30,5.0),
}
def run_comparison(rows,directory,windows=None):
    windows=windows or DATES
    ids={str(t) for t in directory}
    if not ids:
        return {"status":"BLOCKED_NO_VERIFIED_D1_TEAMS","promoted":False}
    all_reports={}
    for window,dates in windows.items():
        buckets={name:defaultdict(list) for name in VARIANTS}
        for day in dates:
            cutoff=dt(day+"T00:00:00Z")
            today=[g for g in rows if g["tip_utc"][:10]==day
                and int(g["season"])==2026 and str(g.get("completed")) in ("1","True","true")
                and str(g["home_id"]) in ids and str(g["away_id"]) in ids
                and g.get("home_score") not in ("",None)
                and g.get("away_score") not in ("",None)
                and str(g.get("score_conflict","0")) not in ("1","True","true")
                and str(g.get("identity_conflict","0")) not in ("1","True","true")]
            if not today: continue
            models={}
            for key,(d1,prior,alpha) in VARIANTS.items():
                try:
                    models[key]=fit(rows,cutoff,test_season=2026,retrospective=True,
                        alpha=alpha,prior_weight=prior,verified_ids=ids if d1 else None)
                except ValueError:
                    models[key]=None
            for game in today:
                forecasts={k:predict(v,game) if v is not None else None
                           for k,v in models.items()}
                if any(v is None for v in forecasts.values()): continue
                if any(dt(v["training_latest_tip"])>=cutoff for v in forecasts.values()):
                    raise AssertionError("lookahead detected")
                margin=float(game["home_score"])-float(game["away_score"])
                total=float(game["home_score"])+float(game["away_score"])
                tier="gap12plus" if abs(forecasts["v03_all_prior60_a3"]["pred_margin"])>=12 else "competitive"
                for key,p in forecasts.items():
                    buckets[key]["all"].append({
                        "pred_margin":p["pred_margin"],"pred_total":p["pred_total"],
                        "actual_margin":margin,"actual_total":total,
                        "correct":int((p["pred_margin"]>0)==(margin>0)),
                        "tier":tier})
        report={}
        for key,group in buckets.items():
            recs=group["all"]
            report[key]={"all":metrics(recs),
                "competitive":metrics([r for r in recs if r["tier"]=="competitive"]),
                "gap12plus":metrics([r for r in recs if r["tier"]=="gap12plus"])}
        if len({x["all"]["n"] for x in report.values()})!=1:
            raise AssertionError("mismatched evaluation samples")
        n=report["v03_all_prior60_a3"]["all"]["n"]
        all_reports[window]={"n":n,"models":report}
    return {
        "status":"RETROSPECTIVE_ABLATION_ONLY","promoted":False,
        "active_model":"CBB_SCORE_RIDGE_V0_3","windows":windows,
        "variants":{k:{"d1_only":v[0],"prior_weight":v[1],"ridge_alpha":v[2]} for k,v in VARIANTS.items()},
        "reports":all_reports,
        "warning":"No market odds and no historical pre-tip data capture. Test independent frozen prospective predictions before promotion."
    }

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",default="data")
    args=parser.parse_args()
    root=Path(args.root)
    try:ids=load_directory(root/"team_directory.json")
    except (OSError,ValueError):ids={}
    out=run_comparison(read_csv(root/"games.csv"),ids)
    path=root/"reports"/"margin_shrinkage_ablation_2026.json"
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(out,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"status":out["status"],"windows":{
        k:{"n":v["n"],"mae":{m:r["all"]["margin_mae"] for m,r in v["models"].items()},
           "su":{m:r["all"]["su_accuracy"] for m,r in v["models"].items()}}
        for k,v in out["reports"].items()}},indent=2))

if __name__=="__main__":
    main()

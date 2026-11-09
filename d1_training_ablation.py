"""Research-only D-I score model comparison. Never publishes challenger picks.

Six fixed early-season and six fixed January 2025-26 evaluation dates.
All contestants use the exact same D-I final matchups and pregame cutoff.
Historical score archives were retrospectively downloaded; results do not
constitute authenticated pre-tip predictions or sports betting edges.
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path
from cbb import dt, iso, read_csv, fit, predict, metrics
from team_directory import load_directory

MODELS={
    "deployed_v03_all_opponents_prior60":(None,.60),
    "d1_only_prior60":("D1",.60),
    "d1_only_prior30":("D1",.30),
}
DATES={
 "early_season":["2025-11-08","2025-11-10","2025-11-12",
                 "2025-11-14","2025-11-16","2025-11-18"],
 "mid_season":["2026-01-08","2026-01-10","2026-01-12",
               "2026-01-14","2026-01-16","2026-01-18"],
}
def research_compare(rows,verified_ids,days=None,season=2026):
    ids={str(x) for x in verified_ids}
    days=days or DATES
    if not ids:
        return {"status":"PASS_NO_VERIFIED_D1_DIRECTORY","promotion":False,"by_window":{}}
    out={}
    for window,eval_dates in days.items():
        scores={name:[] for name in MODELS}
        skipped_days=0
        for day in eval_dates:
            cutoff=dt(day+"T00:00:00Z")
            games=[g for g in rows if int(g['season'])==season and
                   g['tip_utc'][:10]==day and
                   str(g['home_id']) in ids and str(g['away_id']) in ids and
                   str(g.get('completed','0')) in ('1','true','True') and
                   str(g.get('identity_conflict','0')) not in ('1','true','True') and
                   str(g.get('score_conflict','0')) not in ('1','true','True') and
                   g.get('home_score') not in ('',None) and g.get('away_score') not in ('',None)]
            if not games:
                skipped_days+=1
                continue
            competitors={}
            for name,(strict,prior) in MODELS.items():
                try:
                    competitors[name]=fit(
                        rows,cutoff,test_season=season,retrospective=True,
                        verified_ids=ids if strict else None,prior_weight=prior)
                except ValueError:
                    competitors[name]=None
            for game in games:
                forecasts={name: predict(model,game) if model else None
                           for name,model in competitors.items()}
                if any(p is None for p in forecasts.values()):
                    continue
                if any(dt(p["training_latest_tip"])>=cutoff for p in forecasts.values()):
                    raise AssertionError("Future score leaked into historical evaluation")
                margin=float(game['home_score'])-float(game['away_score'])
                total=float(game['home_score'])+float(game['away_score'])
                for name,p in forecasts.items():
                    scores[name].append({"actual_margin":margin,"actual_total":total,
                        "pred_margin":p["pred_margin"],"pred_total":p["pred_total"],
                        "correct":int((p["pred_margin"]>0)==(margin>0))})
        ns={len(v) for v in scores.values()}
        assert len(ns)==1,"Challengers must use identical completed D-I games"
        out[window]={"matched_games":next(iter(ns)),
                     "eval_days":len(eval_dates)-skipped_days,
                     "results":{name:metrics(v) for name,v in scores.items()}}
    return {"status":"RETROSPECTIVE_D1_ABLATION_ONLY","promotion":False,
            "model_champion_unchanged":"CBB_SCORE_RIDGE_V0_3",
            "date_windows":days,"by_window":out,
            "warning":"Retrospective historic score data; no authenticated contemporary rosters, odds, or historical pretip capture. Never auto-promote on these dates."}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",default="data")
    args=ap.parse_args()
    root=Path(args.root)
    rows=read_csv(root/"games.csv")
    try: d1=load_directory(root/"team_directory.json")
    except (OSError,ValueError):d1={}
    results=research_compare(rows,d1)
    dest=root/"reports"/"d1_training_ablation.json"
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(results,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(results,indent=2))
if __name__=="__main__":
    main()
